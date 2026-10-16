"""`bf-cbom analysis ...`: offline analysis of stored experiment results (no Redis).

Reads RESULTS_DIR/<exp>/... written by `bf-cbom experiment run` and writes derived data next to
it under RESULTS_DIR/<exp>/analysis/: JSON reports (`sites`, `micro`) and flat CSV tables (`tables`,
see analysis/tables.py). Everything stays on the results-data volume; use `experiment export` to get it out.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import typer
import yaml

from analysis import tables as tbl
from analysis.groundtruth import TruthScore, load_ground_truth, project_for_repo, score_sites
from analysis.match import AgreementSummary, Cluster, cluster_sites, summarize_clusters
from analysis.sites import ExtractionStats, Site, extract_sites
from common.experiment import RESULTS_DIR, experiment_dir, iter_results

analysis_app = typer.Typer(
    help="Offline analysis of stored experiments (call-site matching, micro ground truth, CSV tables)",
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


def _languages(exp_dir: Path) -> dict[str, str | None]:
    spec = yaml.safe_load((exp_dir / "experiment.yaml").read_text(encoding="utf-8")) or {}
    return {r["full_name"]: r.get("language") for r in spec.get("repos") or []}


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
    if not (exp_dir / "experiment.yaml").exists():
        _err(f"no experiment '{name}' under {RESULTS_DIR}")
    return exp_dir


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
    out_dir = exp_dir / "analysis"
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


def _micro_scores(exp_dir: Path, run: int, all_assets: bool) -> tuple[list[TruthScore], dict[str, str]]:
    """Ground-truth scores of every tool on the micro projects, and {project: language}."""
    truth = load_ground_truth()
    runs, _ = _load_runs(exp_dir, run)
    rows = []
    languages: dict[str, str] = {}
    for (repo_dir, variant), cboms in sorted(runs.items()):
        project = project_for_repo(tbl.full_name(repo_dir), truth)
        if project is None or variant != "original":
            continue
        languages[project.name] = project.language
        for tool, cbom in sorted(cboms.items()):
            s, _ = extract_sites(
                cbom, tool, language=project.language, asset_types=None if all_assets else ("algorithm",)
            )
            rows.append(score_sites(s, project, tool))
    return rows, languages


@analysis_app.command("micro")
def micro(name: str = EXP_ARG, run: int = RUN_OPT, all_assets: bool = ALL_ASSETS_OPT):
    """Score every tool on the hand-written micro projects (corpus/micro) against their ground truth."""
    exp_dir = _experiment(name)
    rows, _ = _micro_scores(exp_dir, run, all_assets)
    if not rows:
        _err("no results for micro projects in this experiment (repos must be named <owner>/micro-<language>)")
    typer.echo(f"{'project':<18}{'tool':<15}{'strict':>8}{'lenient':>9}{'precision':>11}  bar  missed")
    for r in rows:
        typer.echo(
            f"{r.project:<18}{r.tool:<15}{len(r.hits_strict):>5}/{r.n_assets:<2}{len(r.hits_lenient):>6}/{r.n_assets:<2}"
            f"{r.sites_correct:>7}/{r.sites_in_file:<3} {'pass' if r.passes_bar else 'FAIL':<5}{','.join(r.missed)}"
        )
    out_dir = exp_dir / "analysis"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"micro-run{run}.json").write_text(
        json.dumps([r.to_dict() for r in rows], indent=2) + "\n", encoding="utf-8"
    )


@analysis_app.command("tables")
def tables(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    window: int = WINDOW_OPT,
    match_unknown: bool = MATCH_UNKNOWN_OPT,
    all_assets: bool = ALL_ASSETS_OPT,
):
    """Flat CSV tables for pandas/R: runs, call sites (records), call-site matcher groups + members, micro scores."""
    exp_dir = _experiment(name)
    out_dir = exp_dir / "analysis"
    languages = _languages(exp_dir)
    counts: dict[str, int] = {}

    def write(filename: str, rows: list[dict], columns: list[str]) -> None:
        counts[filename] = tbl.write_csv(out_dir / filename, rows, columns)
        typer.echo(f"{filename:<32} {counts[filename]:>7} rows")

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
    write(f"groups-callsite-run{run}.csv", group_rows, tbl.CALLSITE_GROUP_COLUMNS)
    write(f"members-callsite-run{run}.csv", member_rows, tbl.MEMBERS_COLUMNS)
    scores, micro_languages = _micro_scores(exp_dir, run, all_assets)
    if scores:
        write(f"micro-run{run}.csv", tbl.micro_rows(scores, run=run, languages=micro_languages), tbl.MICRO_COLUMNS)
    else:
        typer.echo("no micro projects in this experiment: micro table skipped")
    settings = {"run": run, "window": window, "match_unknown": match_unknown, "all_assets": all_assets}
    (out_dir / f"tables-run{run}.json").write_text(
        json.dumps({"settings": settings, "rows": counts}, indent=2) + "\n", encoding="utf-8"
    )
    typer.echo(f"written to {out_dir}")
