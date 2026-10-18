"""`bf-cbom analysis ...`: offline analysis of stored experiment results (no Redis).

Reads RESULTS_DIR/<exp>/... written by `bf-cbom experiment run` and writes derived data next to
it under RESULTS_DIR/<exp>/_ANALYSIS/: JSON reports (`sites`, `micro`, `normalize`) and flat CSV tables (`tables`,
`levels`, see analysis/tables.py). `normalize` also writes the normalised CBOM `run<k>.norm.json` next to each stored
`run<k>.json`. `report` does all of it in one pass and adds `_SUMMARY.md` (analysis/summary.py); `experiment export`
runs it first. Everything stays on the results-data volume; use `experiment export` to get it out.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml

from analysis import levels as lv, summary as sm, tables as tbl
from analysis.groundtruth import SCORED_ASSET_TYPES, load_ground_truth, project_for_repo, score_records
from analysis.match import AgreementSummary, Cluster, cluster_sites, summarize_clusters
from analysis.normalize import (
    CONFORMANCE_COLUMNS,
    COVERAGE_COLUMNS,
    VOCAB_COLUMNS,
    NormalizedCbom,
    Report,
    normalize_cbom,
)
from analysis.sites import ExtractionStats, Site, extract_sites
from common.experiment import (
    RESULTS_DIR,
    analysis_dir,
    experiment_dir,
    is_experiment,
    iter_results,
    migrate_layout,
    snapshot_files,
    spec_path,
)
from common.tools_registry import load_tools

# (meta, path of run<k>.json, its normalised CBOM)
Normalized = tuple[dict, Path, NormalizedCbom]
Writer = Callable[[str, list[dict], list[str]], None]

analysis_app = typer.Typer(
    help="Offline analysis of stored experiments (normalisation, call-site matching, micro ground truth, CSV tables)",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

EXP_ARG = typer.Argument(..., help="Experiment name (directory under RESULTS_DIR)")
RUN_OPT = typer.Option(1, "--run", help="Which repetition to analyse (run<k>.json)")
ALL_ASSETS_OPT = typer.Option(False, "--all-assets", help="Keep non-algorithm assets (keys, certificates, protocols)")
WINDOW_OPT = typer.Option(2, help="Max line distance for two sites to match")
MATCH_UNKNOWN_OPT = typer.Option(False, "--match-unknown", help="Let unknown-family sites join clusters")


def _err(msg: str, code: int = 1):
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _spec(exp_dir: Path) -> dict:
    return yaml.safe_load(spec_path(exp_dir).read_text(encoding="utf-8")) or {}


def _languages(exp_dir: Path) -> dict[str, str | None]:
    return {r["full_name"]: r.get("language") for r in _spec(exp_dir).get("repos") or []}


def _voter_of() -> dict[str, str]:
    return {t: str(info.get("voter_group") or t) for t, info in load_tools().items()}


def _load_runs(exp_dir: Path, run: int) -> tuple[dict[tuple[str, str], dict[str, dict]], dict[str, str | None]]:
    """({(repo_dir, variant): {tool: cbom dict}} for stored ok runs of repetition `run`, {repo_dir: base commit})."""
    out: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    commits: dict[str, str | None] = {}
    for meta, cbom_path in iter_results(exp_dir):
        if cbom_path.name != f"run{run}.json" or meta.get("status") != "ok":
            continue
        tool = meta.get("worker") or cbom_path.parent.name
        repo_dir = cbom_path.parents[2].name
        variant = cbom_path.parents[1].name
        commits.setdefault(repo_dir, meta.get("base_commit"))
        try:
            out[(repo_dir, variant)][tool] = json.loads(cbom_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            typer.secho(f"skipping unparsable {cbom_path}", fg=typer.colors.YELLOW, err=True)
    return out, commits


@dataclass
class MatchedSnapshot:
    repo_dir: str
    variant: str
    language: str | None
    commit: str | None
    tools: list[str]
    sites: list[Site]
    extraction: dict[str, ExtractionStats]
    clusters: list[Cluster]
    summary: AgreementSummary


def _experiment(name: str) -> Path:
    exp_dir = experiment_dir(name)
    if not is_experiment(exp_dir):
        _err(f"no experiment '{name}' under {RESULTS_DIR}")
    _migrate(exp_dir)
    return exp_dir


def _migrate(exp_dir: Path) -> None:
    for change in migrate_layout(exp_dir):
        typer.secho(f"layout: {change}", fg=typer.colors.BLUE, err=True)


def _match_experiment(
    exp_dir: Path, run: int, *, window: int, match_unknown: bool, all_assets: bool
) -> list[MatchedSnapshot]:
    """Call-site extraction and matching for every (snapshot, variant) with ok results of repetition `run`."""
    languages = _languages(exp_dir)
    runs, commits = _load_runs(exp_dir, run)
    if not runs:
        _err(f"no ok results for run{run} in {exp_dir}")
    out = []
    for (repo_dir, variant), cboms in sorted(runs.items()):
        language = languages.get(tbl.full_name(repo_dir))
        site_list: list[Site] = []
        extraction = {}
        for tool, cbom in sorted(cboms.items()):
            s, st = extract_sites(cbom, tool, language=language, asset_types=None if all_assets else ("algorithm",))
            site_list += s
            extraction[tool] = st
        clusters = cluster_sites(site_list, window=window, match_unknown=match_unknown)
        summary = summarize_clusters(clusters, sorted(cboms))
        out.append(
            MatchedSnapshot(
                repo_dir=repo_dir,
                variant=variant,
                language=language,
                commit=commits.get(repo_dir),
                tools=sorted(cboms),
                sites=site_list,
                extraction=extraction,
                clusters=clusters,
                summary=summary,
            )
        )
    return out


@analysis_app.command("sites")
def sites(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    window: int = WINDOW_OPT,
    match_unknown: bool = MATCH_UNKNOWN_OPT,
    all_assets: bool = ALL_ASSETS_OPT,
):
    """Call-site matching across tools: clusters per (repo, variant), k distribution, per-tool agreement."""
    exp_dir = _experiment(name)
    matched = _match_experiment(exp_dir, run, window=window, match_unknown=match_unknown, all_assets=all_assets)
    out_dir = analysis_dir(exp_dir)
    out_dir.mkdir(exist_ok=True)
    all_clusters = []
    report: dict[str, dict] = {}
    for m in matched:
        full_name = tbl.full_name(m.repo_dir)
        target = out_dir / m.repo_dir / m.variant
        target.mkdir(parents=True, exist_ok=True)
        with (target / "clusters.jsonl").open("w", encoding="utf-8") as fh:
            for c in m.clusters:
                fh.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
        report[f"{full_name}@{m.variant}"] = {
            "extraction": {t: st.to_dict() for t, st in m.extraction.items()},
            "agreement": m.summary.to_dict(),
        }
        all_clusters += m.clusters
        k_dist = m.summary.to_dict()["k_distribution"]
        typer.echo(f"{full_name}@{m.variant}: {len(m.sites)} sites -> {len(m.clusters)} clusters, k={k_dist}")
        for t, a in m.summary.per_tool.items():
            typer.echo(
                f"    {t:<15} sites={a.sites:<5} clusters={a.clusters:<5} "
                f"confirmed={a.confirmed:<5} solo={a.solo:<5} ({a.confirmed_share:.0%})"
            )
    overall = summarize_clusters(all_clusters)
    (out_dir / f"sites-run{run}.json").write_text(
        json.dumps(
            {
                "run": run,
                "window": window,
                "match_unknown": match_unknown,
                "per_repo": report,
                "overall": overall.to_dict(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    typer.echo(f"overall k distribution: {overall.to_dict()['k_distribution']}  (written to {out_dir})")


def _micro_entries(
    exp_dir: Path, run: int, all_assets: bool, normalized: list[Normalized] | None = None
) -> list[tbl.MicroEntry]:
    """Every (micro project, tool) cell of the experiment: the run's status and the score of its normalised records
    at both levels (analysis/groundtruth.py:score_records). Empty when the experiment has no micro projects."""
    truth = load_ground_truth()
    spec = _spec(exp_dir)
    tools = list(spec.get("tools") or [])
    projects = {p.name: p for r in spec.get("repos") or [] if (p := project_for_repo(r["full_name"], truth))}
    statuses: dict[tuple[str, str], str] = {}
    for meta, cbom_path in iter_results(exp_dir):
        project = project_for_repo(tbl.full_name(cbom_path.parents[2].name), truth)
        if project is None or cbom_path.name != f"run{run}.json" or cbom_path.parents[1].name != "original":
            continue
        tool = meta.get("worker") or cbom_path.parent.name
        statuses[(project.name, tool)] = meta.get("status") or "?"
        projects.setdefault(project.name, project)
        if tool not in tools:
            tools.append(tool)
    if not projects:
        return []
    kinds = None if all_assets else SCORED_ASSET_TYPES
    scores = {}
    for _, cbom_path, n in normalized if normalized is not None else _normalize_runs(exp_dir, run=run):
        project = project_for_repo(tbl.full_name(cbom_path.parents[2].name), truth)
        if project is None or cbom_path.name != f"run{run}.json" or cbom_path.parents[1].name != "original":
            continue
        scores[(project.name, n.tool)] = score_records(n.records, project, n.tool, asset_types=kinds)
    return [
        tbl.MicroEntry(projects[p], t, statuses.get((p, t), "missing"), scores.get((p, t)))
        for p in sorted(projects)
        for t in sorted(tools)
    ]


@analysis_app.command("micro")
def micro(name: str = EXP_ARG, run: int = RUN_OPT, all_assets: bool = ALL_ASSETS_OPT):
    """Score every tool on the hand-written micro projects (corpus/micro) against their ground truth, at call sites
    (the inclusion bar) and at repository level (location ignored)."""
    exp_dir = _experiment(name)
    entries = _micro_entries(exp_dir, run, all_assets)
    if not entries:
        _err("no results for micro projects in this experiment (repos must be named <owner>/micro-<language>)")
    typer.echo(f"{'project':<18}{'tool':<15}{'strict':>8}{'lenient':>9}{'repo':>7}{'precision':>11}  bar  missed")
    for e in entries:
        r = e.score
        if r is None:
            typer.echo(f"{e.project.name:<18}{e.tool:<15}  {tbl.micro_verdict(e.status, None)} ({e.status})")
            continue
        typer.echo(
            f"{r.project:<18}{r.tool:<15}{len(r.hits_strict):>5}/{r.n_assets:<2}{len(r.hits_lenient):>6}/{r.n_assets:<2}"
            f"{len(r.hits_repo or []):>4}/{r.n_assets:<2}{r.sites_correct:>7}/{r.sites_in_file:<3} "
            f"{'pass' if r.passes_bar else 'FAIL':<5}{','.join(r.missed)}"
        )
    out_dir = analysis_dir(exp_dir)
    out_dir.mkdir(exist_ok=True)
    data = [
        {"status": e.status, "verdict": tbl.micro_verdict(e.status, e.score)}
        | (e.score.to_dict() if e.score else {"tool": e.tool, "project": e.project.name})
        for e in entries
    ]
    (out_dir / f"micro-run{run}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def norm_path(cbom_path: Path) -> Path:
    """`run<k>.json` -> `run<k>.norm.json` (the normalised CBOM sits next to the original)."""
    return cbom_path.with_name(cbom_path.name[: -len(".json")] + ".norm.json")


def _normalize_runs(exp_dir: Path, *, run: int | None = None):
    """Yield (meta, cbom path, NormalizedCbom) for every stored ok CBOM (all repetitions, or only `run`)."""
    languages = _languages(exp_dir)
    files_cache: dict[tuple[str, str | None], frozenset[str] | None] = {}
    for meta, cbom_path in iter_results(exp_dir):
        if meta.get("status") != "ok" or not cbom_path.exists():
            continue
        if run is not None and cbom_path.name != f"run{run}.json":
            continue
        body = cbom_path.read_bytes()
        try:
            cbom = json.loads(body)
        except json.JSONDecodeError:
            typer.secho(f"skipping unparsable {cbom_path}", fg=typer.colors.YELLOW, err=True)
            continue
        repo_dir, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        full = tbl.full_name(repo_dir)
        commit = meta.get("variant_commit") or meta.get("base_commit")
        if (full, commit) not in files_cache:
            files_cache[(full, commit)] = snapshot_files(full, commit)
        tool = meta.get("worker") or cbom_path.parent.name
        source = {
            "file": cbom_path.name,
            "sha256": hashlib.sha256(body).hexdigest(),
            "snapshot": repo_dir,
            "variant": variant,
            "run": meta.get("run_index"),
            "tool_version": meta.get("tool_version"),
        }
        n = normalize_cbom(
            cbom, tool, language=languages.get(full), repo_files=files_cache[(full, commit)], source=source
        )
        yield meta, cbom_path, n


def _write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _writer(out_dir: Path, counts: dict[str, int]) -> Writer:
    """A CSV writer into `out_dir` that records row counts (for tables-run<k>.json) and echoes them."""
    out_dir.mkdir(parents=True, exist_ok=True)

    def write(filename: str, rows: list[dict], columns: list[str]) -> None:
        counts[filename] = tbl.write_csv(out_dir / filename, rows, columns)
        typer.echo(f"{filename:<32} {counts[filename]:>7} rows")

    return write


def _normalize_step(exp_dir: Path, write: Writer) -> tuple[Report, list[Normalized]]:
    """Normalise every stored ok CBOM (all repetitions): run<k>.norm.json next to each, normalize.json, vocab.csv,
    coverage.csv and conformance.csv. Returns the report and the normalised CBOMs."""
    report = Report()
    done: list[Normalized] = []
    fallback: set[str] = set()
    for meta, cbom_path, n in _normalize_runs(exp_dir):
        _write_json(norm_path(cbom_path), n.to_dict())
        report.add(n)
        done.append((meta, cbom_path, n))
        if n.adapter.get("fallback"):
            fallback.add(n.tool)
    if not done:
        _err(f"no ok results in {exp_dir}")
    out_dir = analysis_dir(exp_dir)
    out_dir.mkdir(exist_ok=True)
    _write_json(out_dir / "normalize.json", report.to_dict())
    write("vocab.csv", report.vocab_rows(), VOCAB_COLUMNS)
    write("coverage.csv", report.coverage_rows(), COVERAGE_COLUMNS)
    write("conformance.csv", report.conformance_rows(), CONFORMANCE_COLUMNS)
    if fallback:
        typer.secho(f"no adapter spec, generic CycloneDX reader used: {sorted(fallback)}", fg=typer.colors.YELLOW)
    return report, done


def _levels_step(exp_dir: Path, run: int, normalized: list[Normalized], write: Writer) -> list[lv.LevelRow]:
    """The L0-L4 ladder (levels-run<k>.csv) and which algorithms each tool reports (algorithms-run<k>.csv)."""
    ladder_in: dict[tuple[str, str], list] = {}
    presence_in: dict[tuple[str, str, str], list] = {}
    for _, cbom_path, n in normalized:
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        ladder_in[(f"{snapshot}/{variant}", n.tool)] = n.records
        presence_in[(snapshot, variant, n.tool)] = n.records
    if not ladder_in:
        _err(f"no ok results for run{run} in {exp_dir}")
    voter_of = _voter_of()
    rows = lv.ladder(ladder_in, voter_of)
    write(f"levels-run{run}.csv", [r.to_dict() for r in rows], lv.LEVEL_COLUMNS)
    algorithms = lv.algorithm_rows(presence_in, voter_of, run=run, languages=_languages(exp_dir))
    write(f"algorithms-run{run}.csv", algorithms, lv.ALGORITHM_COLUMNS)
    return rows


def _tables_step(
    exp_dir: Path,
    run: int,
    normalized: list[Normalized],
    write: Writer,
    *,
    window: int,
    match_unknown: bool,
    all_assets: bool,
) -> None:
    """runs.csv, the normalised records, the legacy call-site tables and the micro tables (if any)."""
    languages = _languages(exp_dir)
    write("runs.csv", tbl.run_rows(exp_dir, languages), tbl.RUNS_COLUMNS)
    site_rows: list[dict] = []
    group_rows: list[dict] = []
    member_rows: list[dict] = []
    for m in _match_experiment(exp_dir, run, window=window, match_unknown=match_unknown, all_assets=all_assets):
        key = {"snapshot": m.repo_dir, "variant": m.variant, "run": run, "commit": m.commit}
        site_rows += tbl.site_rows(m.sites, language=m.language, **key)
        g, mem = tbl.group_rows("callsite", tbl.callsite_groups(m.clusters), **key)
        group_rows += g
        member_rows += mem
    write(f"sites-run{run}.csv", site_rows, tbl.SITES_COLUMNS)
    norm_rows: list[dict] = []
    for meta, cbom_path, n in normalized:
        norm_rows += tbl.record_rows(
            n.records,
            snapshot=cbom_path.parents[2].name,
            variant=cbom_path.parents[1].name,
            run=run,
            commit=meta.get("base_commit"),
            language=languages.get(tbl.full_name(cbom_path.parents[2].name)),
        )
    write(f"records-run{run}.csv", norm_rows, tbl.RECORDS_COLUMNS)
    write(f"groups-callsite-run{run}.csv", group_rows, tbl.CALLSITE_GROUP_COLUMNS)
    write(f"members-callsite-run{run}.csv", member_rows, tbl.MEMBERS_COLUMNS)
    entries = _micro_entries(exp_dir, run, all_assets, normalized)
    if entries:
        write(f"micro-run{run}.csv", tbl.micro_rows(entries, run=run), tbl.MICRO_COLUMNS)
        write(f"micro-assets-run{run}.csv", tbl.micro_asset_rows(entries, run=run), tbl.MICRO_ASSET_COLUMNS)
    else:
        typer.echo("no micro projects in this experiment: micro tables skipped")


def _write_info(out_dir: Path, run: int, settings: dict, counts: dict[str, int], normalized: list[Normalized]):
    """tables-run<k>.json: settings, row counts, and the vocabulary and adapter spec versions used."""
    vocabulary = next((n.vocabulary for _, _, n in normalized), {})
    adapters = {n.tool: n.adapter.get("spec") for _, _, n in normalized}
    info = {
        "settings": settings,
        "rows": counts,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "vocabulary": vocabulary,
        "adapters": dict(sorted(adapters.items())),
    }
    (out_dir / f"tables-run{run}.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


@analysis_app.command("normalize")
def normalize(name: str = EXP_ARG):
    """Normalise every stored CBOM with its tool's adapter spec (analysis/adapters/<tool>.yaml).

    Writes run<k>.norm.json next to each run<k>.json, analysis/normalize.json (coverage per tool: fill rates,
    sources, spec issues, conflicts, unmapped values), analysis/vocab.csv (what every raw value mapped to),
    analysis/coverage.csv (fill rates, long format) and analysis/conformance.csv (spec issues per kind).
    """
    exp_dir = _experiment(name)
    counts: dict[str, int] = {}
    report, done = _normalize_step(exp_dir, _writer(analysis_dir(exp_dir), counts))
    typer.echo("algorithm records with a value reported by the tool (not derived):")
    for line in report.summary_lines():
        typer.echo("  " + line)
    typer.echo(f"{len(done)} CBOMs normalised (run<k>.norm.json next to each), in {exp_dir / 'analysis'}")


@analysis_app.command("levels")
def levels(name: str = EXP_ARG, run: int = RUN_OPT):
    """How much agreement normalisation creates: share of each tool's algorithms (per repository) that another
    voter group also reports, at levels L0 (raw names) to L4 (full adapter specs). See analysis/levels.py.
    Also writes algorithms-run<k>.csv: which algorithms each tool reports per repository and level."""
    exp_dir = _experiment(name)
    counts: dict[str, int] = {}
    rows = _levels_step(exp_dir, run, list(_normalize_runs(exp_dir, run=run)), _writer(analysis_dir(exp_dir), counts))
    for level in lv.LEVELS:
        typer.echo(f"  {level}: {lv.LEVEL_NAMES[level]}")
    typer.echo("share of a tool's algorithms (per repository) that another voter group also reports:")
    for line in lv.table_lines(rows):
        typer.echo("  " + line)


@analysis_app.command("tables")
def tables(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    window: int = WINDOW_OPT,
    match_unknown: bool = MATCH_UNKNOWN_OPT,
    all_assets: bool = ALL_ASSETS_OPT,
):
    """Flat CSV tables for pandas/R: runs, call sites and normalised records, call-site groups + members, micro."""
    exp_dir = _experiment(name)
    out_dir = analysis_dir(exp_dir)
    counts: dict[str, int] = {}
    normalized = list(_normalize_runs(exp_dir, run=run))
    settings = {"run": run, "window": window, "match_unknown": match_unknown, "all_assets": all_assets}
    write = _writer(out_dir, counts)
    _tables_step(exp_dir, run, normalized, write, window=window, match_unknown=match_unknown, all_assets=all_assets)
    _write_info(out_dir, run, settings, counts, normalized)
    typer.echo(f"written to {out_dir}")


def run_report(
    exp_dir: Path, *, run: int = 1, window: int = 2, match_unknown: bool = False, all_assets: bool = False
) -> Path:
    """`normalize`, `levels` and `tables` in one pass (every CBOM normalised once), then _SUMMARY.md."""
    _migrate(exp_dir)
    out_dir = analysis_dir(exp_dir)
    counts: dict[str, int] = {}
    write = _writer(out_dir, counts)
    _, done = _normalize_step(exp_dir, write)
    normalized = [x for x in done if x[1].name == f"run{run}.json"]
    _levels_step(exp_dir, run, normalized, write)
    _tables_step(exp_dir, run, normalized, write, window=window, match_unknown=match_unknown, all_assets=all_assets)
    settings = {"run": run, "window": window, "match_unknown": match_unknown, "all_assets": all_assets}
    _write_info(out_dir, run, settings, counts, normalized)
    return sm.write(out_dir, run=run)


@analysis_app.command("report")
def report(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    window: int = WINDOW_OPT,
    match_unknown: bool = MATCH_UNKNOWN_OPT,
    all_assets: bool = ALL_ASSETS_OPT,
):
    """Everything in one pass: normalised CBOMs, every CSV table (runs, records, micro at two levels, algorithms per
    tool and level, ladder, coverage, conformance, legacy call sites) and _ANALYSIS/_SUMMARY.md with Markdown tables."""
    exp_dir = _experiment(name)
    path = run_report(exp_dir, run=run, window=window, match_unknown=match_unknown, all_assets=all_assets)
    typer.echo(f"written to {path.parent} (summary: {path.name})")
