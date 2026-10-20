"""`bf-cbom analysis ...`: offline analysis of stored experiment results (no Redis).

Reads RESULTS_DIR/<exp>/... written by `bf-cbom experiment run` and writes derived data next to
it under RESULTS_DIR/<exp>/_ANALYSIS/: JSON reports (`micro`, `normalize`) and flat CSV tables (`tables`, `levels`,
`schema`, see analysis/tables.py). `normalize` also writes the normalised CBOM `run<k>.norm.json` next to each stored
`run<k>.json`. `report` does all of it in one pass and adds `_SUMMARY.md` (analysis/summary.py); `experiment export`
runs it first. Everything stays on the results-data volume; use `experiment export` to get it out.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml

from analysis import levels as lv, mutation as mu, schema as sch, score as sc, summary as sm, tables as tbl
from analysis.groundtruth import SCORED_ASSET_TYPES, load_ground_truth, project_for_repo, score_records
from analysis.matchers import ExactMatcher, raqun as rq
from analysis.matchers.compare import MATCHERS_COLUMNS, compare
from analysis.normalize import (
    CONFORMANCE_COLUMNS,
    COVERAGE_COLUMNS,
    VOCAB_COLUMNS,
    NormalizedCbom,
    Report,
    normalize_cbom,
)
from analysis.snapshots import SnapshotStore
from analysis.stability import STABILITY_COLUMNS, stability_rows
from common.experiment import (
    ORIGINAL_VARIANT,
    RESULTS_DIR,
    analysis_dir,
    experiment_dir,
    is_experiment,
    iter_results,
    load_mutants,
    pinned_repos,
    spec_path,
)
from common.tools_registry import VoterMap, load_tools, role, tailored_map, voter_groups

# (meta, path of run<k>.json, its normalised CBOM)
Normalized = tuple[dict, Path, NormalizedCbom]
Writer = Callable[[str, list[dict], list[str]], None]

analysis_app = typer.Typer(
    help="Offline analysis of stored experiments (normalisation, RQ1 schema, matching, micro ground truth, CSV tables)",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

EXP_ARG = typer.Argument(..., help="Experiment name (directory under RESULTS_DIR)")
RUN_OPT = typer.Option(1, "--run", help="Which repetition to analyse (run<k>.json)")
ALL_ASSETS_OPT = typer.Option(False, "--all-assets", help="Keep non-algorithm assets (keys, certificates, protocols)")
MERGE_LLM_OPT = typer.Option(
    False, "--merge-llm", help="Count every LLM worker as one voter (tools.yaml merge_group; sensitivity analysis)"
)
CLOSURE_OPT = typer.Option(
    False,
    "--closure",
    help="Close every tool's algorithm set under the vocabulary's `implies` relation (refinement 2): the ladder and "
    "items-run<k>.csv (implied rows marked) count HMAC-SHA256 as HMAC and SHA-256 for every tool",
)
RAQUN_OPT = typer.Option(
    None,
    "--raqun/--no-raqun",
    help="Also run RaQuN (matcher (c), analysis/matchers/raqun.py) and compare it with the exact matcher; default: "
    "when the PyQuN package is importable",
)
SAMPLES_OPT = typer.Option(200, "--samples", help="Bootstrap resamples over repositories for the score CIs (0: none)")
CONSENSUS_OPT = typer.Option(
    True,
    "--consensus/--no-consensus",
    help="With repeated runs, score a tool's consensus set (items in more than half of its runs) instead of "
    "run k's set (analysis/stability.py)",
)
FETCH_OPT = typer.Option(
    False,
    "--fetch",
    help="Fetch every pinned commit of the experiment into the snapshot cache (SNAPSHOT_CACHE_DIR, default "
    "~/.cache/bf-cbom/snapshots; one shallow git fetch per repo) so file_exists and the traceability check work on "
    "a copied export; on the VM the git mirror is used anyway",
)
OBSERVERS_VOTE_OPT = typer.Option(
    False,
    "--observers-vote",
    help="Let the observer tools (tools.yaml role: observer) vote: they confirm others, count in k and are ranked "
    "(the sensitivity check of the observer role; by default they are confirmed by the voters but confirm nobody)",
)
INCLUDE_TAILORED_OPT = typer.Option(
    False,
    "--include-tailored",
    help="Keep (tool, repo) pairs the tool was tuned on (tools.yaml tailored_on) in the agreement numbers",
)
WINDOW_OPT = typer.Option(2, help="Line window of the exact matcher's call-site groups (same file, lines within it)")
LEVEL_OPT = typer.Option("L3", "--level", help="Ladder level of the exact matcher's algorithm key (L0-L4)")


def _err(msg: str, code: int = 1):
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _spec(exp_dir: Path) -> dict:
    return yaml.safe_load(spec_path(exp_dir).read_text(encoding="utf-8")) or {}


def _languages(exp_dir: Path) -> dict[str, str | None]:
    return {r["full_name"]: r.get("language") for r in _spec(exp_dir).get("repos") or []}


def _expects(exp_dir: Path) -> dict[str, str | None]:
    return {r["full_name"]: r.get("expect_crypto") for r in _spec(exp_dir).get("repos") or []}


def _voter_of(merge_llm: bool = False, observers_vote: bool = False) -> VoterMap:
    return voter_groups(merge_llm=merge_llm, observers_vote=observers_vote)


def _is_tailored(tailored: dict[str, frozenset[str]], tool: str, repo: str) -> bool:
    return repo in tailored.get(tool, ())


def _experiment(name: str) -> Path:
    exp_dir = experiment_dir(name)
    if not is_experiment(exp_dir):
        _err(f"no experiment '{name}' under {RESULTS_DIR}")
    return exp_dir


def _micro_entries(
    exp_dir: Path, run: int, all_assets: bool, normalized: list[Normalized] | None = None
) -> list[tbl.MicroEntry]:
    """Every (micro project, tool) cell of the experiment: the run's status and the score of its normalised records
    at both levels (analysis/groundtruth.py:score_records). Empty when the experiment has no micro projects."""
    truth = load_ground_truth()
    spec = _spec(exp_dir)
    tools = list(spec.get("tools") or [])
    projects = {p.name: p for r in spec.get("repos") or [] if (p := project_for_repo(r["full_name"], truth))}
    repo_of = {
        p.name: r["full_name"] for r in spec.get("repos") or [] if (p := project_for_repo(r["full_name"], truth))
    }
    statuses: dict[tuple[str, str], str] = {}
    for meta, cbom_path in iter_results(exp_dir):
        repo = tbl.full_name(cbom_path.parents[2].name)
        project = project_for_repo(repo, truth)
        if project is None or cbom_path.name != f"run{run}.json" or cbom_path.parents[1].name != "original":
            continue
        tool = meta.get("worker") or cbom_path.parent.name
        statuses[(project.name, tool)] = meta.get("status") or "?"
        projects.setdefault(project.name, project)
        repo_of.setdefault(project.name, repo)
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
    tailored = tailored_map()
    return [
        tbl.MicroEntry(
            projects[p],
            t,
            statuses.get((p, t), "missing"),
            scores.get((p, t)),
            tailored=_is_tailored(tailored, t, repo_of[p]),
        )
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


def _store(exp_dir: Path, *, fetch: bool = False) -> SnapshotStore:
    """The snapshot files behind the experiment: the git mirror on the VM, or the local cache (`--fetch` fills it
    with the pinned commits of `_experiment.yaml` from their source URLs; fixtures are published, not fetched)."""
    store = SnapshotStore()
    if fetch:
        for name, repo in sorted(pinned_repos(exp_dir).items()):
            if repo.commit and not repo.is_fixture:
                store.fetch(name, repo.source_url, repo.commit, log=lambda m: typer.echo(m, err=True))
    return store


def _original(item: Normalized) -> bool:
    """A run on an original snapshot. Mutants (variants) feed the mutation step only: in the agreement analyses a
    mutated repository would count once per mutant (E3 is computed on the originals)."""
    return item[1].parents[1].name == ORIGINAL_VARIANT


def _normalize_runs(
    exp_dir: Path, *, run: int | None = None, store: SnapshotStore | None = None, mutants: bool = False
):
    """Yield (meta, cbom path, NormalizedCbom) for every stored ok CBOM (all repetitions, or only `run`) of the
    original snapshots, with `mutants` also of the mutants."""
    languages = _languages(exp_dir)
    store = store or SnapshotStore()
    files_cache: dict[tuple[str, str | None], frozenset[str] | None] = {}
    for meta, cbom_path in iter_results(exp_dir):
        if meta.get("status") != "ok" or not cbom_path.exists():
            continue
        if run is not None and cbom_path.name != f"run{run}.json":
            continue
        if not mutants and cbom_path.parents[1].name != ORIGINAL_VARIANT:
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
            files_cache[(full, commit)] = store.files(full, commit)
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


def _normalize_step(
    exp_dir: Path, write: Writer, *, store: SnapshotStore | None = None
) -> tuple[Report, list[Normalized]]:
    """Normalise every stored ok CBOM (all repetitions, mutants included): run<k>.norm.json next to each, and over
    the original variants normalize.json, vocab.csv, coverage.csv and conformance.csv (a mutated repository would
    count once per mutant). Returns the report and the normalised CBOMs."""
    report = Report()
    done: list[Normalized] = []
    fallback: set[str] = set()
    for meta, cbom_path, n in _normalize_runs(exp_dir, store=store, mutants=True):
        _write_json(norm_path(cbom_path), n.to_dict())
        done.append((meta, cbom_path, n))
        if _original(done[-1]):
            report.add(n)
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


def _schema_step(exp_dir: Path, run: int, normalized: list[Normalized], write: Writer, *, store: SnapshotStore):
    """RQ1 (analysis/schema.py): schema-run<k>.csv (whole-document validation), meaningful-run<k>.csv and
    traceability-run<k>.csv (needs the snapshot files: the mirror on the VM or `--fetch`)."""
    languages = _languages(exp_dir)
    native = {t: info.get("native_cyclonedx") for t, info in load_tools().items()}
    schema_rows = []
    for meta, cbom_path, _ in normalized:
        try:
            cbom = json.loads(cbom_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        language = languages.get(tbl.full_name(cbom_path.parents[2].name))
        tool = meta.get("worker") or cbom_path.parent.name
        schema_rows.append(sch.schema_row(meta, cbom_path, cbom, language=language, native=native.get(tool)))
    write(f"schema-run{run}.csv", schema_rows, sch.SCHEMA_COLUMNS)
    write(f"meaningful-run{run}.csv", sch.meaningful_rows(normalized, native), sch.MEANINGFUL_COLUMNS)
    trace = sch.traceability_rows(normalized, store, languages=languages)
    write(f"traceability-run{run}.csv", trace, sch.TRACE_COLUMNS)
    if trace and not any(r.get("source") for r in trace):
        typer.secho(
            "traceability: no snapshot files (no git mirror here; `--fetch` fills the snapshot cache)",
            fg=typer.colors.YELLOW,
            err=True,
        )


@analysis_app.command("schema")
def schema_cmd(name: str = EXP_ARG, run: int = RUN_OPT, fetch: bool = FETCH_OPT):
    """RQ1: validate every CBOM of repetition `run` against the CycloneDX schema it declares, measure how many assets
    are meaningful (parameters in standard fields vs anywhere) and traceable (file exists at the commit, line in
    range, vocabulary token nearby). See analysis/schema.py."""
    exp_dir = _experiment(name)
    store = _store(exp_dir, fetch=fetch)
    counts: dict[str, int] = {}
    normalized = list(_normalize_runs(exp_dir, run=run, store=store))
    if not normalized:
        _err(f"no ok results for run{run} in {exp_dir}")
    _schema_step(exp_dir, run, normalized, _writer(analysis_dir(exp_dir), counts), store=store)


def _levels_step(
    exp_dir: Path,
    run: int,
    normalized: list[Normalized],
    write: Writer,
    *,
    merge_llm: bool = False,
    observers_vote: bool = False,
    include_tailored: bool = False,
    closure: bool = False,
) -> list[lv.LevelRow]:
    """The L0-L4 ladder (levels-run<k>.csv) and which items each tool reports (items-run<k>.csv).
    (tool, repo) pairs the tool was tuned on (refinement 13) are marked in items-run<k>.csv and left out of
    the ladder unless `include_tailored`; `closure` adds the implied items (refinement 2)."""
    ladder_in: dict[tuple[str, str], list] = {}
    presence_in: dict[tuple[str, str, str], list] = {}
    tailored = tailored_map()
    excluded: set[tuple[str, str]] = set()
    for _, cbom_path, n in normalized:
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        ladder_in[(f"{snapshot}/{variant}", n.tool)] = n.records
        presence_in[(snapshot, variant, n.tool)] = n.records
        if _is_tailored(tailored, n.tool, tbl.full_name(snapshot)):
            excluded.add((f"{snapshot}/{variant}", n.tool))
    if not ladder_in:
        _err(f"no ok results for run{run} in {exp_dir}")
    voter_of = _voter_of(merge_llm, observers_vote)
    rows = lv.ladder(ladder_in, voter_of, exclude=set() if include_tailored else excluded, closure=closure)
    write(f"levels-run{run}.csv", [r.to_dict() for r in rows], lv.LEVEL_COLUMNS)
    items = lv.item_rows(
        presence_in, voter_of, run=run, languages=_languages(exp_dir), tailored=tailored, closure=closure
    )
    write(f"items-run{run}.csv", items, lv.ITEM_COLUMNS)
    return rows


def _tables_step(
    exp_dir: Path,
    run: int,
    normalized: list[Normalized],
    write: Writer,
    *,
    window: int,
    level: str = "L3",
    closure: bool = False,
    merge_llm: bool = False,
    observers_vote: bool = False,
    raqun: bool | None = None,
    all_assets: bool,
) -> None:
    """runs.csv, the normalised records, the exact matcher's groups/members/agreement, RaQuN's groups and the matcher
    comparison when available, and the micro tables (if any). Returns the exact groups per (snapshot, variant)."""
    languages = _languages(exp_dir)
    write(
        "runs.csv",
        tbl.run_rows(exp_dir, languages, tailored=tailored_map(), expects=_expects(exp_dir)),
        tbl.RUNS_COLUMNS,
    )
    voter_of = _voter_of(merge_llm, observers_vote)
    norm_rows: list[dict] = []
    per_snapshot: dict[tuple[str, str], list] = defaultdict(list)
    commits: dict[str, str | None] = {}
    for meta, cbom_path, n in normalized:
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        commits.setdefault(snapshot, meta.get("base_commit"))
        per_snapshot[(snapshot, variant)] += n.records
        norm_rows += tbl.record_rows(
            n.records,
            snapshot=snapshot,
            variant=variant,
            run=run,
            commit=meta.get("base_commit"),
            language=languages.get(tbl.full_name(snapshot)),
        )
    write(f"records-run{run}.csv", norm_rows, tbl.RECORDS_COLUMNS)
    write(f"units-run{run}.csv", tbl.unit_rows([n for _, _, n in normalized], voter_of), tbl.UNIT_COLUMNS)
    # the exact matcher at every level feeds matchlevels-run<k>.csv (RQ3, matcher x level); the full group, member
    # and agreement tables are written at the scoring level only
    matchlevel_rows: list[dict] = []
    groups_by_snapshot: dict[tuple[str, str], list] = {}
    exact_by_level: dict[str, dict[tuple[str, str], list]] = {}
    for lv_name in lv.LEVELS:
        matcher = ExactMatcher(level=lv_name, window=window, closure=closure, voter_of=voter_of)
        at_level = {key: matcher.match(records) for key, records in sorted(per_snapshot.items())}
        exact_by_level[lv_name] = at_level
        matchlevel_rows.append(tbl.matchlevel_row(matcher.name, lv_name, run, at_level, voter_of))
    groups_by_snapshot = exact_by_level[level]
    group_rows: list[dict] = []
    member_rows: list[dict] = []
    agreement_rows: list[dict] = []
    for (snapshot, variant), groups in groups_by_snapshot.items():
        g, m, a = tbl.group_rows(
            "exact",
            groups,
            snapshot=snapshot,
            variant=variant,
            run=run,
            commit=commits.get(snapshot),
            language=languages.get(tbl.full_name(snapshot)),
            voter_of=voter_of,
        )
        group_rows += g
        member_rows += m
        agreement_rows += a
    write(f"groups-exact-run{run}.csv", group_rows, tbl.GROUP_COLUMNS)
    write(f"members-exact-run{run}.csv", member_rows, tbl.MEMBERS_COLUMNS)
    write(f"agreement-exact-run{run}.csv", agreement_rows, tbl.AGREEMENT_COLUMNS)
    if raqun is None:
        raqun = rq.available()
    if raqun and not rq.available():
        typer.secho("RaQuN not importable (install the `raqun` extra and the PyQuN package); skipped", fg="yellow")
    elif raqun:
        matchlevel_rows += _raqun_step(
            run,
            per_snapshot,
            exact_by_level,
            write,
            level=level,
            voter_of=voter_of,
            commits=commits,
            languages=languages,
        )
    write(f"matchlevels-run{run}.csv", matchlevel_rows, tbl.MATCHLEVELS_COLUMNS)
    entries = _micro_entries(exp_dir, run, all_assets, normalized)
    if entries:
        write(f"micro-run{run}.csv", tbl.micro_rows(entries, run=run), tbl.MICRO_COLUMNS)
        write(f"micro-items-run{run}.csv", tbl.micro_item_rows(entries, run=run), tbl.MICRO_ITEM_COLUMNS)
    else:
        typer.echo("no micro projects in this experiment: micro tables skipped")
    return groups_by_snapshot


def _raqun_step(run, per_snapshot, exact_by_level, write: Writer, *, level, voter_of, commits, languages) -> list[dict]:
    """RaQuN at every level: groups/members/agreement-raqun-run<k>.csv at the scoring level, matchers-run<k>.csv
    (RaQuN vs the exact matcher, per repository and level); returns its matchlevels rows."""
    g_rows: list[dict] = []
    m_rows: list[dict] = []
    a_rows: list[dict] = []
    cmp_rows: list[dict] = []
    level_rows: list[dict] = []
    for lv_name in lv.LEVELS:
        matcher = rq.RaqunMatcher(level=lv_name, voter_of=voter_of)
        at_level = {key: matcher.match(records) for key, records in sorted(per_snapshot.items())}
        level_rows.append(tbl.matchlevel_row(matcher.name, lv_name, run, at_level, voter_of))
        for (snapshot, variant), groups in at_level.items():
            row = compare(exact_by_level[lv_name].get((snapshot, variant), []), groups, "exact", "raqun")
            cmp_rows.append(
                {"repo": tbl.full_name(snapshot), "snapshot": snapshot, "variant": variant, "run": run,
                 "level": lv_name, **row}
            )  # fmt: skip
            if lv_name != level:
                continue
            g, m, a = tbl.group_rows(
                matcher.name,
                groups,
                snapshot=snapshot,
                variant=variant,
                run=run,
                commit=commits.get(snapshot),
                language=languages.get(tbl.full_name(snapshot)),
                voter_of=voter_of,
            )
            g_rows += g
            m_rows += m
            a_rows += a
    write(f"groups-raqun-run{run}.csv", g_rows, tbl.GROUP_COLUMNS)
    write(f"members-raqun-run{run}.csv", m_rows, tbl.MEMBERS_COLUMNS)
    write(f"agreement-raqun-run{run}.csv", a_rows, tbl.AGREEMENT_COLUMNS)
    write(f"matchers-run{run}.csv", cmp_rows, MATCHERS_COLUMNS)
    return level_rows


def _mutation_step(
    exp_dir: Path, all_runs: list[Normalized], write: Writer, *, window: int, merge_llm: bool, observers_vote: bool,
    samples: int,
) -> None:  # fmt: skip
    """E4 (analysis/mutation.py), for an experiment with mutants (mutants.json): mutants.csv, mutation.csv (one row
    per mutant and tool, majority over the repetitions) and mutation-rates.csv."""
    manifest = load_mutants(exp_dir)
    if not manifest.get("repos"):
        return
    records: dict[tuple[str, str, str], dict[int, list]] = defaultdict(dict)
    for meta, cbom_path, n in all_runs:
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        records[(snapshot, variant, n.tool)][int(meta.get("run_index") or 1)] = n.records
    statuses: dict[tuple[str, str, str], dict[int, str]] = defaultdict(dict)
    for meta, cbom_path in iter_results(exp_dir):
        key = (cbom_path.parents[2].name, cbom_path.parents[1].name, meta.get("worker") or cbom_path.parent.name)
        statuses[key][int(meta.get("run_index") or 1)] = str(meta.get("status") or "?")
    languages = _languages(exp_dir)
    rows = mu.detection_rows(
        manifest,
        records,
        statuses,
        list(_spec(exp_dir).get("tools") or []),
        window=window,
        languages=languages,
        voter_of=_voter_of(merge_llm, observers_vote),
    )
    write("mutants.csv", mu.mutant_rows(manifest, languages=languages), mu.MUTANTS_COLUMNS)
    write("mutation.csv", rows, mu.MUTATION_COLUMNS)
    write("mutation-rates.csv", mu.rate_rows(rows, samples=samples), mu.RATE_COLUMNS)


def _stability_step(all_runs: list[Normalized], write: Writer, *, level: str) -> dict[tuple[str, str, str], set[str]]:
    """stability.csv over every repetition (analysis/stability.py); returns the consensus sets of repeated runs."""
    runs: dict[tuple[str, str, str], dict[int, list]] = defaultdict(dict)
    for meta, cbom_path, n in all_runs:
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        runs[(snapshot, variant, n.tool)][int(meta.get("run_index") or 1)] = n.records
    rows, consensus = stability_rows(runs, level=level, repo_of=tbl.full_name)
    write("stability.csv", rows, STABILITY_COLUMNS)
    return consensus


def _score_step(
    exp_dir: Path,
    run: int,
    normalized: list[Normalized],
    groups_by_snapshot: dict[tuple[str, str], list],
    write: Writer,
    *,
    level: str,
    merge_llm: bool,
    observers_vote: bool,
    include_tailored: bool,
    samples: int,
    consensus: dict[tuple[str, str, str], set[str]] | None = None,
) -> None:
    """score-run<k>.csv and loo-run<k>.csv (analysis/score.py) from the exact matcher's groups."""
    voter_of = _voter_of(merge_llm, observers_vote)
    tools_ran: dict[str, set[str]] = defaultdict(set)
    repo_of: dict[str, str] = {}
    for _, cbom_path, n in normalized:
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        tools_ran[f"{snapshot}/{variant}"].add(n.tool)
        repo_of[f"{snapshot}/{variant}"] = tbl.full_name(snapshot)
    groups = {f"{s}/{v}": g for (s, v), g in groups_by_snapshot.items()}
    obs = sc.observe(
        groups,
        level=level,
        voter_of=voter_of,
        tools_ran=tools_ran,
        tailored=None if include_tailored else tailored_map(),
        repo_of=repo_of,
    )
    for (snapshot, variant, tool), keys in (consensus or {}).items():
        obs.restrict(f"{snapshot}/{variant}", tool, set(keys))  # keep the items in the tool's consensus set
    rows, loo = sc.score_rows(obs, samples=samples)
    write(f"score-run{run}.csv", rows, sc.SCORE_COLUMNS)
    write(f"loo-run{run}.csv", loo, sc.LOO_COLUMNS)


@analysis_app.command("score")
def score_cmd(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    level: str = LEVEL_OPT,
    window: int = WINDOW_OPT,
    closure: bool = CLOSURE_OPT,
    merge_llm: bool = MERGE_LLM_OPT,
    observers_vote: bool = OBSERVERS_VOTE_OPT,
    include_tailored: bool = INCLUDE_TAILORED_OPT,
    samples: int = SAMPLES_OPT,
    consensus: bool = CONSENSUS_OPT,
):
    """RQ4: score and rank the tools without ground truth (confirmation rate, exclusivity, Dawid-Skene support,
    leave-one-voter-out, bootstrap CIs) on the exact matcher's groups; stability.csv over repeated runs."""
    exp_dir = _experiment(name)
    counts: dict[str, int] = {}
    write = _writer(analysis_dir(exp_dir), counts)
    all_runs = list(_normalize_runs(exp_dir))
    normalized = [x for x in all_runs if x[1].name == f"run{run}.json"]
    if not normalized:
        _err(f"no ok results for run{run} in {exp_dir}")
    matcher = ExactMatcher(level=level, window=window, closure=closure, voter_of=_voter_of(merge_llm, observers_vote))
    per_snapshot: dict[tuple[str, str], list] = defaultdict(list)
    for _, cbom_path, n in normalized:
        per_snapshot[(cbom_path.parents[2].name, cbom_path.parents[1].name)] += n.records
    groups = {key: matcher.match(records) for key, records in sorted(per_snapshot.items())}
    sets = _stability_step(all_runs, write, level=level)
    _score_step(
        exp_dir,
        run,
        normalized,
        groups,
        write,
        level=level,
        merge_llm=merge_llm,
        observers_vote=observers_vote,
        include_tailored=include_tailored,
        samples=samples,
        consensus=sets if consensus else None,
    )


def _write_info(
    out_dir: Path, run: int, settings: dict, counts: dict[str, int], normalized: list[Normalized], voter_of: VoterMap
):
    """tables-run<k>.json: settings, row counts, the vocabulary and adapter spec versions used, and the voter map the
    numbers rest on (each spec tool's voter group, role and engine, after --merge-llm / --observers-vote)."""
    vocabulary = next((n.vocabulary for _, _, n in normalized), {})
    adapters = {n.tool: n.adapter.get("spec") for _, _, n in normalized}
    registry = load_tools()
    voters = {
        t: {"voter_group": voter_of.get(t, t), "role": role(voter_of, t), "engine": registry.get(t, {}).get("engine")}
        for t in _spec(out_dir.parent).get("tools") or []
    }
    info = {
        "settings": settings,
        "rows": counts,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "vocabulary": vocabulary,
        "adapters": dict(sorted(adapters.items())),
        "voters": voters,
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
    typer.echo("algorithm assets of the original variants: mapped to an algorithm, with a value the tool reported:")
    for line in report.summary_lines():
        typer.echo("  " + line)
    typer.echo(f"{len(done)} CBOMs normalised (run<k>.norm.json next to each), in {analysis_dir(exp_dir)}")


@analysis_app.command("levels")
def levels(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    merge_llm: bool = MERGE_LLM_OPT,
    observers_vote: bool = OBSERVERS_VOTE_OPT,
    include_tailored: bool = INCLUDE_TAILORED_OPT,
    closure: bool = CLOSURE_OPT,
):
    """How much agreement normalisation creates: share of each tool's items (per repository) that another
    voter group also reports, at levels L0 (raw names) to L4 (full adapter specs). See analysis/levels.py.
    Also writes items-run<k>.csv: which items each tool reports per repository and level."""
    exp_dir = _experiment(name)
    counts: dict[str, int] = {}
    rows = _levels_step(
        exp_dir,
        run,
        list(_normalize_runs(exp_dir, run=run)),
        _writer(analysis_dir(exp_dir), counts),
        merge_llm=merge_llm,
        observers_vote=observers_vote,
        include_tailored=include_tailored,
        closure=closure,
    )
    for level in lv.LEVELS:
        typer.echo(f"  {level}: {lv.LEVEL_NAMES[level]}")
    typer.echo("share of a tool's items (per repository) that another voter group also reports:")
    for line in lv.table_lines(rows):
        typer.echo("  " + line)


@analysis_app.command("tables")
def tables(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    window: int = WINDOW_OPT,
    level: str = LEVEL_OPT,
    closure: bool = CLOSURE_OPT,
    merge_llm: bool = MERGE_LLM_OPT,
    observers_vote: bool = OBSERVERS_VOTE_OPT,
    raqun: bool | None = RAQUN_OPT,
    all_assets: bool = ALL_ASSETS_OPT,
):
    """Flat CSV tables for pandas/R: runs, normalised records, the exact matcher's groups, members and agreement
    (and RaQuN's, with the comparison), micro."""
    exp_dir = _experiment(name)
    out_dir = analysis_dir(exp_dir)
    counts: dict[str, int] = {}
    normalized = list(_normalize_runs(exp_dir, run=run))
    settings = {"run": run, "window": window, "level": level, "closure": closure, "all_assets": all_assets}
    write = _writer(out_dir, counts)
    _tables_step(
        exp_dir,
        run,
        normalized,
        write,
        window=window,
        level=level,
        closure=closure,
        merge_llm=merge_llm,
        observers_vote=observers_vote,
        raqun=raqun,
        all_assets=all_assets,
    )
    _write_info(out_dir, run, settings, counts, normalized, _voter_of(merge_llm, observers_vote))
    typer.echo(f"written to {out_dir}")


def run_report(
    exp_dir: Path,
    *,
    run: int = 1,
    window: int = 2,
    level: str = "L3",
    all_assets: bool = False,
    merge_llm: bool = False,
    observers_vote: bool = False,
    include_tailored: bool = False,
    closure: bool = False,
    fetch: bool = False,
    samples: int = 200,
    consensus: bool = True,
    raqun: bool | None = None,
) -> Path:
    """`normalize`, `schema`, `levels`, `tables` and `score` in one pass (every CBOM normalised once), then
    _SUMMARY.md.
    `_ANALYSIS/` holds regenerated output only, so it is emptied first: no stale file from an earlier version."""
    out_dir = analysis_dir(exp_dir)
    shutil.rmtree(out_dir, ignore_errors=True)
    counts: dict[str, int] = {}
    write = _writer(out_dir, counts)
    store = _store(exp_dir, fetch=fetch)
    _, done = _normalize_step(exp_dir, write, store=store)
    originals = [x for x in done if _original(x)]
    normalized = [x for x in originals if x[1].name == f"run{run}.json"]
    _schema_step(exp_dir, run, normalized, write, store=store)
    _levels_step(
        exp_dir,
        run,
        normalized,
        write,
        merge_llm=merge_llm,
        observers_vote=observers_vote,
        include_tailored=include_tailored,
        closure=closure,
    )
    groups = _tables_step(
        exp_dir,
        run,
        normalized,
        write,
        window=window,
        level=level,
        closure=closure,
        merge_llm=merge_llm,
        observers_vote=observers_vote,
        raqun=raqun,
        all_assets=all_assets,
    )
    sets = _stability_step(originals, write, level=level)
    _score_step(
        exp_dir,
        run,
        normalized,
        groups,
        write,
        level=level,
        merge_llm=merge_llm,
        observers_vote=observers_vote,
        include_tailored=include_tailored,
        samples=samples,
        consensus=sets if consensus else None,
    )
    _mutation_step(exp_dir, done, write, window=window, merge_llm=merge_llm, observers_vote=observers_vote,
                   samples=samples)  # fmt: skip
    settings = {
        "run": run,
        "window": window,
        "level": level,
        "samples": samples,
        "consensus": consensus,
        "raqun": rq.available() if raqun is None else raqun,
        "all_assets": all_assets,
        "merge_llm": merge_llm,
        "observers_vote": observers_vote,
        "include_tailored": include_tailored,
        "closure": closure,
        "fetch": fetch,
    }
    _write_info(out_dir, run, settings, counts, normalized, _voter_of(merge_llm, observers_vote))
    return sm.write(out_dir, run=run)


@analysis_app.command("report")
def report(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    window: int = WINDOW_OPT,
    level: str = LEVEL_OPT,
    all_assets: bool = ALL_ASSETS_OPT,
    merge_llm: bool = MERGE_LLM_OPT,
    observers_vote: bool = OBSERVERS_VOTE_OPT,
    include_tailored: bool = INCLUDE_TAILORED_OPT,
    closure: bool = CLOSURE_OPT,
    fetch: bool = FETCH_OPT,
    samples: int = SAMPLES_OPT,
    consensus: bool = CONSENSUS_OPT,
    raqun: bool | None = RAQUN_OPT,
):
    """Everything in one pass: normalised CBOMs, every CSV table (runs, records, RQ1 schema/meaningful/traceability,
    the exact matcher's groups/members/agreement, scores and leave-one-out, stability, micro at two levels,
    items per tool and level, ladder, coverage, conformance; with mutants the E4 tables mutants.csv,
    mutation.csv and mutation-rates.csv, the agreement tables on the originals only) and _ANALYSIS/_SUMMARY.md with
    Markdown tables."""
    exp_dir = _experiment(name)
    path = run_report(
        exp_dir,
        run=run,
        window=window,
        level=level,
        all_assets=all_assets,
        merge_llm=merge_llm,
        observers_vote=observers_vote,
        include_tailored=include_tailored,
        closure=closure,
        fetch=fetch,
        samples=samples,
        consensus=consensus,
        raqun=raqun,
    )
    typer.echo(f"written to {path.parent} (summary: {path.name})")
