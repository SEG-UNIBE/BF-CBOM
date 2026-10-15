"""`bf-cbom analysis ...`: offline analysis of stored experiment results (no Redis).

Reads RESULTS_DIR/<exp>/... written by `bf-cbom experiment run` and writes derived data next to
it under RESULTS_DIR/<exp>/analysis/. Everything stays on the results-data volume; use
`experiment export` to get it out.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import typer
import yaml

from analysis.groundtruth import load_ground_truth, project_for_repo, score_sites
from analysis.match import cluster_sites, summarize_clusters
from analysis.sites import extract_sites
from common.experiment import RESULTS_DIR, experiment_dir, iter_results

analysis_app = typer.Typer(
    help="Offline analysis of stored experiments (call-site matching, micro ground truth)",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

EXP_ARG = typer.Argument(..., help="Experiment name (directory under RESULTS_DIR)")
RUN_OPT = typer.Option(1, "--run", help="Which repetition to analyse (run<k>.json)")
ALL_ASSETS_OPT = typer.Option(False, "--all-assets", help="Keep non-algorithm assets (keys, certificates, protocols)")


def _err(msg: str, code: int = 1):
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _languages(exp_dir: Path) -> dict[str, str | None]:
    spec = yaml.safe_load((exp_dir / "experiment.yaml").read_text(encoding="utf-8")) or {}
    return {r["full_name"]: r.get("language") for r in spec.get("repos") or []}


def _full_name(repo_dir_name: str) -> str:
    return repo_dir_name.split("@", 1)[0].replace("__", "/", 1)


def _load_runs(exp_dir: Path, run: int) -> dict[tuple[str, str], dict[str, dict]]:
    """{(repo_dir, variant): {tool: cbom dict}} for stored ok runs of repetition `run`."""
    out: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for meta, cbom_path in iter_results(exp_dir):
        if cbom_path.name != f"run{run}.json" or meta.get("status") != "ok":
            continue
        tool = meta.get("worker") or cbom_path.parent.name
        repo_dir = cbom_path.parents[2].name
        variant = cbom_path.parents[1].name
        try:
            out[(repo_dir, variant)][tool] = json.loads(cbom_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            typer.secho(f"skipping unparsable {cbom_path}", fg=typer.colors.YELLOW, err=True)
    return out


@analysis_app.command("sites")
def sites(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    window: int = typer.Option(2, help="Max line distance for two sites to match"),
    match_unknown: bool = typer.Option(False, "--match-unknown", help="Let unknown-family sites join clusters"),
    all_assets: bool = ALL_ASSETS_OPT,
):
    """Call-site matching across tools: clusters per (repo, variant), k distribution, per-tool agreement."""
    exp_dir = experiment_dir(name)
    if not (exp_dir / "experiment.yaml").exists():
        _err(f"no experiment '{name}' under {RESULTS_DIR}")
    languages = _languages(exp_dir)
    runs = _load_runs(exp_dir, run)
    if not runs:
        _err(f"no ok results for run{run} in {exp_dir}")
    out_dir = exp_dir / "analysis"
    out_dir.mkdir(exist_ok=True)
    all_clusters = []
    report: dict[str, dict] = {}
    for (repo_dir, variant), cboms in sorted(runs.items()):
        full_name = _full_name(repo_dir)
        language = languages.get(full_name)
        site_list = []
        extraction = {}
        for tool, cbom in sorted(cboms.items()):
            s, st = extract_sites(cbom, tool, language=language, asset_types=None if all_assets else ("algorithm",))
            site_list += s
            extraction[tool] = st.to_dict()
        clusters = cluster_sites(site_list, window=window, match_unknown=match_unknown)
        summary = summarize_clusters(clusters, sorted(cboms))
        target = out_dir / repo_dir / variant
        target.mkdir(parents=True, exist_ok=True)
        with (target / "clusters.jsonl").open("w", encoding="utf-8") as fh:
            for c in clusters:
                fh.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
        report[f"{full_name}@{variant}"] = {"extraction": extraction, "agreement": summary.to_dict()}
        all_clusters += clusters
        k_dist = summary.to_dict()["k_distribution"]
        typer.echo(f"{full_name}@{variant}: {len(site_list)} sites -> {len(clusters)} clusters, k={k_dist}")
        for t, a in summary.per_tool.items():
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


@analysis_app.command("micro")
def micro(name: str = EXP_ARG, run: int = RUN_OPT, all_assets: bool = ALL_ASSETS_OPT):
    """Score every tool on the hand-written micro projects (corpus/micro) against their ground truth."""
    exp_dir = experiment_dir(name)
    if not (exp_dir / "experiment.yaml").exists():
        _err(f"no experiment '{name}' under {RESULTS_DIR}")
    truth = load_ground_truth()
    runs = _load_runs(exp_dir, run)
    rows = []
    for (repo_dir, variant), cboms in sorted(runs.items()):
        project = project_for_repo(_full_name(repo_dir), truth)
        if project is None or variant != "original":
            continue
        for tool, cbom in sorted(cboms.items()):
            s, _ = extract_sites(
                cbom, tool, language=project.language, asset_types=None if all_assets else ("algorithm",)
            )
            rows.append(score_sites(s, project, tool))
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
