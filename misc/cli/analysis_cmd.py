"""`bf-cbom analysis ...`: offline analysis of stored experiment results (no Redis).

Reads RESULTS_DIR/<exp>/... written by `bf-cbom experiment run` and writes derived data next to
it under RESULTS_DIR/<exp>/_ANALYSIS/: JSON reports (`micro`, `normalize`) and flat CSV tables (`tables`, `levels`,
`schema`, see analysis/tables.py). `normalize` also writes the normalised CBOM `run<i>.norm.json` next to each stored
`run<i>.json`. `report` does all of it in one pass and adds `_SUMMARY.md` (analysis/summary.py); `experiment export`
runs it first. Everything stays on the results-data volume; use `experiment export` to get it out.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml

from analysis import levels as lv, mutation as mu, schema as sch, score as sc, summary as sm, tables as tbl, tasks
from analysis.groundtruth import (
    DISQUALIFIED,
    NOT_JUDGED,
    PASSED,
    SCORED_ASSET_TYPES,
    entry_verdict,
    load_ground_truth,
    project_for_repo,
    score_records,
)
from analysis.matchers import ExactMatcher, raqun as rq
from analysis.matchers.compare import MATCHERS_COLUMNS
from analysis.normalize import (
    CONFORMANCE_COLUMNS,
    COVERAGE_COLUMNS,
    SOURCES_COLUMNS,
    VOCAB_COLUMNS,
    NormalizedCbom,
    Report,
)
from analysis.snapshots import SnapshotStore
from analysis.stability import STABILITY_COLUMNS, baseline_flux, stability_rows
from analysis.tasks import write_json
from common.experiment import (
    ORIGINAL_VARIANT,
    RESULTS_DIR,
    SnapshotSize,
    analysis_dir,
    experiment_dir,
    fixture_dir,
    is_experiment,
    iter_results,
    load_mutants,
    pinned_repos,
    spec_path,
    stored_status,
)
from common.languages import source_files
from common.tools_registry import load_tools, tailored_map, voter_groups
from misc.cli import progress
from misc.cli.parallel import pmap

# (meta, path of run<i>.json, its normalised CBOM)
Normalized = tuple[dict, Path, NormalizedCbom]
Writer = Callable[[str, list[dict], list[str]], None]


def _label(x: tuple) -> str:
    """The run an item of a step is about, for the progress line: `<owner>/<repo>[@variant] · <tool> · run<i>`."""
    cbom_path = x[1]
    variant = cbom_path.parents[1].name
    where = tbl.full_name(cbom_path.parents[2].name) + ("" if variant == ORIGINAL_VARIANT else f"@{variant}")
    return f"{where} · {cbom_path.parent.name} · {cbom_path.stem}"


def _sized(x: tuple) -> str:
    """`_label` with the CBOM's size, which is what makes one item slow."""
    try:
        return f"{_label(x)} · {x[1].stat().st_size / 1e6:.1f} MB"
    except OSError:
        return _label(x)


analysis_app = typer.Typer(
    help="Offline analysis of stored experiments (normalisation, RQ3 schema, matching, micro ground truth, CSV tables)",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

EXP_ARG = typer.Argument(..., help="Experiment name (directory under RESULTS_DIR)")
RUN_OPT = typer.Option(1, "--run", help="Which repetition to analyse (run<i>.json)")
ALL_ASSETS_OPT = typer.Option(False, "--all-assets", help="Keep non-algorithm assets (keys, certificates, protocols)")
MERGE_LLM_OPT = typer.Option(
    False, "--merge-llm", help="Count every LLM worker as one voter (tools.yaml merge_group; sensitivity analysis)"
)
CLOSURE_OPT = typer.Option(
    False,
    "--closure",
    help="Close every tool's algorithm set under the vocabulary's `implies` relation (refinement 2): the ladder and "
    "items-run<i>.csv (implied rows marked) count HMAC-SHA256 as HMAC and SHA-256 for every tool",
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
    "run i's set (analysis/stability.py)",
)
FETCH_OPT = typer.Option(
    False,
    "--fetch",
    help="Fetch every pinned commit of the experiment into the snapshot cache (SNAPSHOT_CACHE_DIR, default "
    "~/.cache/bf-cbom/snapshots; one shallow git fetch per repo) so file_exists and the traceability check work on "
    "a copied export; on the VM the git mirror is used anyway. A fixture (`path:`, the micro projects) needs no "
    "flag: it is published from its local directory",
)
JOBS_OPT = typer.Option(
    0,
    "--jobs",
    envvar="BF_CBOM_JOBS",
    help="Worker processes for normalising, validating and matching (misc/cli/parallel.py): 0 = auto, min(CPUs, 8) "
    "when a step has 16 or more items, else sequential; 1 = sequential; N = that many. The results do not depend on it",
)
INCLUDE_TAILORED_OPT = typer.Option(
    False,
    "--include-tailored",
    help="Keep (tool, repo) pairs the tool was tuned on (tools.yaml tailored_on) in the agreement numbers",
)
BASELINE_OPT = typer.Option(
    None,
    "--baseline",
    help="A stability experiment (repeated runs, reported already) whose stability.csv gives the noise floor of "
    "RQ6's changes off the edit (`baseline_flux`, `baseline_changed` in mutation-scores.csv): how much a tool's "
    "report of unchanged code varies between its own runs",
)
WINDOW_OPT = typer.Option(2, help="Line window of the exact matcher's call-site groups (same file, lines within it)")
LEVEL_OPT = typer.Option("L4", "--level", help="Ladder level of the exact matcher's algorithm key (L0-L4)")


def _err(msg: str, code: int = 1):
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _spec(exp_dir: Path) -> dict:
    return yaml.safe_load(spec_path(exp_dir).read_text(encoding="utf-8")) or {}


def _languages(exp_dir: Path) -> dict[str, str | None]:
    return {r["full_name"]: r.get("language") for r in _spec(exp_dir).get("repos") or []}


def _expects(exp_dir: Path) -> dict[str, str | None]:
    return {r["full_name"]: r.get("expect_crypto") for r in _spec(exp_dir).get("repos") or []}


def _voter_of(merge_llm: bool = False) -> dict[str, str]:
    return voter_groups(merge_llm=merge_llm)


def _is_tailored(tailored: dict[str, frozenset[str]], tool: str, repo: str) -> bool:
    return repo in tailored.get(tool, ())


def _experiment(name: str) -> Path:
    exp_dir = experiment_dir(name)
    if not is_experiment(exp_dir):
        _err(f"no experiment '{name}' under {RESULTS_DIR}")
    return exp_dir


def _micro_entries(
    exp_dir: Path, run: int, all_assets: bool, normalized: list[Normalized] | None = None, *, jobs: int = 0
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
        statuses[(project.name, tool)] = stored_status(meta) or "?"
        projects.setdefault(project.name, project)
        repo_of.setdefault(project.name, repo)
        if tool not in tools:
            tools.append(tool)
    if not projects:
        return []
    kinds = None if all_assets else SCORED_ASSET_TYPES
    scores = {}
    for _, cbom_path, n in normalized if normalized is not None else _normalize_runs(exp_dir, run=run, jobs=jobs):
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
def micro(name: str = EXP_ARG, run: int = RUN_OPT, all_assets: bool = ALL_ASSETS_OPT, jobs: int = JOBS_OPT):
    """Score every tool on the micro projects (experiments/corpus/micro, generated by mutation/micro.py) against
    their ground truth, at call sites (the inclusion bar) and at repository level (location ignored),
    and count decoy hits.
    """
    exp_dir = _experiment(name)
    entries = _micro_entries(exp_dir, run, all_assets, jobs=jobs)
    if not entries:
        _err("no results for micro projects in this experiment (repos must be named <owner>/micro-<language>)")
    typer.echo(
        f"{'project':<18}{'tool':<15}{'strict':>8}{'lenient':>9}{'repo':>7}{'precision':>11}{'F1':>6}{'decoys':>8}"
        "  bar  missed"
    )
    for e in entries:
        r = e.score
        if r is None:
            typer.echo(f"{e.project.name:<18}{e.tool:<15}  {tbl.micro_verdict(e.status, None)} ({e.status})")
            continue
        precision = "–" if r.precision is None else f"{r.precision:.0%}"
        typer.echo(
            f"{r.project:<18}{r.tool:<15}{len(r.hits_strict):>5}/{r.n_assets:<2}{len(r.hits_lenient):>6}/{r.n_assets:<2}"
            f"{len(r.hits_repo or []):>4}/{r.n_assets:<2}{precision:>11}{r.f1:>6.0%}"
            f"{len(r.decoys_hit):>5}/{r.n_decoys:<2} {'pass' if r.passes_bar else 'FAIL':<5}{','.join(r.missed)}"
        )
    verdicts: dict[str, list[str]] = defaultdict(list)
    for tool in dict.fromkeys(e.tool for e in entries):
        verdicts[entry_verdict([e.score for e in entries if e.tool == tool])].append(tool)
    bar = next((f"{e.score.bar_items} of {e.score.n_assets}" for e in entries if e.score), "half of the")
    typer.echo(f"\nentry bar ({bar} calls, lenient, in at least one language):")
    for verdict in (PASSED, DISQUALIFIED, NOT_JUDGED):
        if verdicts.get(verdict):
            typer.echo(f"  {verdict}: {', '.join(sorted(verdicts[verdict]))}")
    out_dir = analysis_dir(exp_dir)
    out_dir.mkdir(exist_ok=True)
    data = [
        {"status": e.status, "verdict": tbl.micro_verdict(e.status, e.score)}
        | (e.score.to_dict() if e.score else {"tool": e.tool, "project": e.project.name})
        for e in entries
    ]
    (out_dir / f"micro-run{run}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _store(exp_dir: Path, *, fetch: bool = False) -> SnapshotStore:
    """The snapshot files behind the experiment: the git mirror on the VM, or the local cache (`--fetch` fills it
    with the pinned commits of `_experiment.yaml` from their source URLs). A fixture (`path:`, the micro projects)
    the store lacks is published into the cache from its local directory without `--fetch`: no network, and only
    when the directory still gives the recorded commit."""
    store = SnapshotStore()

    def log(message: str) -> None:
        typer.echo(message, err=True)

    pinned = sorted(pinned_repos(exp_dir).items())
    for name, repo in pinned:
        if repo.is_fixture and repo.commit and store.repo(name, repo.commit) is None:
            local = fixture_dir(repo.path)
            if local is None:
                log(f"  {name}: fixture directory {repo.path} not found here: no snapshot files")
            else:
                store.publish_fixture(name, local, repo.commit, log=log)
    if fetch:
        repos = [(name, repo) for name, repo in pinned if repo.commit and not repo.is_fixture]
        for name, repo in progress.track(repos, "fetch snapshots", item=lambda x: x[0]):
            store.fetch(name, repo.source_url, repo.commit, log=log)
    return store


SIZE_FIELDS = SnapshotSize._fields  # the record's size fields, runs.csv's `repo_` columns


def _sizes(exp_dir: Path, store: SnapshotStore, languages: dict[str, str | None]) -> dict[str, dict[str, int]]:
    """{snapshot: {files, source_files, checkout_bytes, source_bytes}} at the pinned commit, for runs.csv and the
    run-time table's size columns: from the record when the runner counted them (every run since 2026-10-24, so an
    export carries them), else the two counts from the snapshot store; a snapshot neither holds (an older record on
    a copied export without `--fetch`) is left out."""
    recorded = {
        r["full_name"]: {k: int(r[k]) for k in SIZE_FIELDS if r.get(k) is not None}
        for r in _spec(exp_dir).get("repos") or []
        if r.get("files") is not None and r.get("source_files") is not None
    }
    out: dict[str, dict[str, int]] = {}
    for meta, cbom_path in iter_results(exp_dir):
        snapshot = cbom_path.parents[2].name
        if snapshot in out:
            continue
        full = tbl.full_name(snapshot)
        if full in recorded:
            out[snapshot] = recorded[full]
            continue
        files = store.files(full, meta.get("base_commit") or (meta.get("repo_info") or {}).get("commit"))
        if files is not None:
            out[snapshot] = {"files": len(files), "source_files": source_files(files, languages.get(full))}
    return out


def _original(item: Normalized) -> bool:
    """A run on an original snapshot. Mutants (variants) feed the mutation step only: in the agreement analyses a
    mutated repository would count once per mutant (the agreement analyses are computed on the originals)."""
    return item[1].parents[1].name == ORIGINAL_VARIANT


def _normalize_runs(
    exp_dir: Path,
    *,
    run: int | None = None,
    store: SnapshotStore | None = None,
    mutants: bool = False,
    jobs: int = 0,
    write_norm: bool = False,
) -> list[Normalized]:
    """(meta, cbom path, NormalizedCbom) for every stored ok CBOM (all repetitions, or only `run`) of the original
    snapshots, with `mutants` also of the mutants, in the stored order. One CBOM per task in a process pool (`jobs`,
    misc/cli/parallel.py; analysis/tasks.py:normalize_one); with `write_norm` the worker also writes run<i>.norm.json
    next to its CBOM. The snapshot's file list comes from the store here, once per snapshot."""
    languages = _languages(exp_dir)
    store = store or SnapshotStore()
    files_cache: dict[tuple[str, str | None], frozenset[str] | None] = {}
    stored = [
        (meta, cbom_path)
        for meta, cbom_path in iter_results(exp_dir)
        if stored_status(meta) == "ok"
        and cbom_path.exists()
        and (run is None or cbom_path.name == f"run{run}.json")
        and (mutants or cbom_path.parents[1].name == ORIGINAL_VARIANT)
    ]
    items: list[tasks.NormalizeItem] = []
    for meta, cbom_path in stored:
        full = tbl.full_name(cbom_path.parents[2].name)
        commit = meta.get("variant_commit") or meta.get("base_commit")
        if (full, commit) not in files_cache:
            files_cache[(full, commit)] = store.files(full, commit)
        tool = meta.get("worker") or cbom_path.parent.name
        items.append((meta, cbom_path, tool, languages.get(full), files_cache[(full, commit)], write_norm))
    done: list[Normalized] = []
    for item, result in zip(items, pmap(tasks.normalize_one, items, jobs=jobs, name="normalise CBOMs", item=_sized),
                            strict=True):  # fmt: skip
        if result is None:
            typer.secho(f"skipping unparsable {item[1]}", fg=typer.colors.YELLOW, err=True)
        else:
            done.append(result)
    return done


BIG_TABLE_ROWS = 50_000  # a table this long (about a second of writing for records-run<i>.csv) gets a line


def _writer(out_dir: Path, counts: dict[str, int]) -> Writer:
    """A CSV writer into `out_dir` that records row counts (for tables-run<i>.json) and echoes them; a big table
    is written on a counted line of its own instead, which also says its rows."""
    out_dir.mkdir(parents=True, exist_ok=True)

    def write(filename: str, rows: list[dict], columns: list[str]) -> None:
        if len(rows) >= BIG_TABLE_ROWS:
            counts[filename] = tbl.write_csv(out_dir / filename, progress.track(rows, filename, every=10_000), columns)
            return
        counts[filename] = tbl.write_csv(out_dir / filename, rows, columns)
        typer.echo(f"{filename:<32} {counts[filename]:>7} rows")

    return write


def _normalize_step(
    exp_dir: Path, write: Writer, *, store: SnapshotStore | None = None, jobs: int = 0
) -> tuple[Report, list[Normalized]]:
    """Normalise every stored ok CBOM (all repetitions, mutants included): run<i>.norm.json next to each, and over
    the original variants of every repetition normalize.json (the normaliser's diagnostics) and vocab.csv (the
    mapping table: every spelling any run used; a mutated repository would count once per mutant). The per-run
    tables of what the tools' assets hold (coverage-run<i>.csv, conformance-run<i>.csv) are `_tables_step`'s.
    Returns the report and the normalised CBOMs."""
    report = Report()
    done: list[Normalized] = []
    fallback: set[str] = set()
    for meta, cbom_path, n in _normalize_runs(exp_dir, store=store, mutants=True, jobs=jobs, write_norm=True):
        done.append((meta, cbom_path, n))
        if _original(done[-1]):
            report.add(n)
        if n.adapter.get("fallback"):
            fallback.add(n.tool)
    if not done:
        _err(f"no ok results in {exp_dir}")
    out_dir = analysis_dir(exp_dir)
    out_dir.mkdir(exist_ok=True)
    write_json(out_dir / "normalize.json", report.to_dict(), sort_keys=True)
    write("vocab.csv", report.vocab_rows(), VOCAB_COLUMNS)
    if fallback:
        typer.secho(f"no adapter spec, generic CycloneDX reader used: {sorted(fallback)}", fg=typer.colors.YELLOW)
    return report, done


def _schema_step(
    exp_dir: Path, run: int, normalized: list[Normalized], write: Writer, *, store: SnapshotStore, jobs: int = 0
):
    """RQ3 (analysis/schema.py): schema-run<i>.csv (whole-document validation, one CBOM per task in the process pool),
    meaningful-run<i>.csv and traceability-run<i>.csv (needs the snapshot files: the mirror on the VM or `--fetch`)."""
    languages = _languages(exp_dir)
    cbom_by = {t: info.get("cbom_by") for t, info in load_tools().items()}
    items: list[tasks.SchemaItem] = [
        (meta, cbom_path, languages.get(tbl.full_name(cbom_path.parents[2].name)),
         cbom_by.get(meta.get("worker") or cbom_path.parent.name))
        for meta, cbom_path, _ in normalized
    ]  # fmt: skip
    rows = pmap(tasks.schema_one, items, jobs=jobs, name="RQ3: schema validation", item=_sized)
    write(f"schema-run{run}.csv", [r for r in rows if r is not None], sch.SCHEMA_COLUMNS)
    meaningful = sch.meaningful_rows(progress.track(normalized, "RQ3: meaningful", item=_label), cbom_by)
    write(f"meaningful-run{run}.csv", meaningful, sch.MEANINGFUL_COLUMNS)
    trace = sch.traceability_rows(progress.track(normalized, "RQ2: traceable", item=_label), store, languages=languages)
    write(f"traceability-run{run}.csv", trace, sch.TRACE_COLUMNS)
    if trace and not any(r.get("source") for r in trace):
        typer.secho(
            "traceability: no snapshot files (no git mirror here; `--fetch` fills the snapshot cache)",
            fg=typer.colors.YELLOW,
            err=True,
        )


@analysis_app.command("schema")
def schema_cmd(name: str = EXP_ARG, run: int = RUN_OPT, fetch: bool = FETCH_OPT, jobs: int = JOBS_OPT):
    """RQ3: validate every CBOM of repetition `run` against the CycloneDX schema it declares, measure how many assets
    are meaningful (parameters in standard fields vs anywhere) and traceable (file exists at the commit, line in
    range, vocabulary token nearby). See analysis/schema.py."""
    exp_dir = _experiment(name)
    store = _store(exp_dir, fetch=fetch)
    counts: dict[str, int] = {}
    normalized = _normalize_runs(exp_dir, run=run, store=store, jobs=jobs)
    if not normalized:
        _err(f"no ok results for run{run} in {exp_dir}")
    _schema_step(exp_dir, run, normalized, _writer(analysis_dir(exp_dir), counts), store=store, jobs=jobs)


def _match_input(normalized: list[Normalized], include_tailored: bool) -> dict[tuple[str, str], list]:
    """The matcher's input per (snapshot, variant): the run's records, without the (tool, repo) pairs the tool was
    tuned on (refinement 13) unless `include_tailored`, so the matcher's groups, their k and agreement and the scores
    leave them out as the ladder does."""
    tailored = {} if include_tailored else tailored_map()
    out: dict[tuple[str, str], list] = defaultdict(list)
    for _, cbom_path, n in normalized:
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        if not _is_tailored(tailored, n.tool, tbl.full_name(snapshot)):
            out[(snapshot, variant)] += n.records
    return dict(sorted(out.items()))


def _levels_step(
    exp_dir: Path,
    run: int,
    normalized: list[Normalized],
    write: Writer,
    *,
    merge_llm: bool = False,
    include_tailored: bool = False,
    closure: bool = False,
) -> list[lv.LevelRow]:
    """The L0-L4 ladder (levels-run<i>.csv), which items each tool reports (items-run<i>.csv, with whether the
    item's family is the registry's) and the delta to the CycloneDX registry (registry-run<i>.csv, RQ4).
    (tool, repo) pairs the tool was tuned on (refinement 13) are marked in items-run<i>.csv and left out of
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
    voter_of = _voter_of(merge_llm)
    with progress.step("ladder L0-L4"):
        rows = lv.ladder(ladder_in, voter_of, exclude=set() if include_tailored else excluded, closure=closure)
    write(f"levels-run{run}.csv", [r.to_dict() for r in rows], lv.LEVEL_COLUMNS)
    with progress.step("items per tool and level"):
        items = lv.item_rows(
            presence_in, voter_of, run=run, languages=_languages(exp_dir), tailored=tailored, closure=closure
        )
    write(f"items-run{run}.csv", items, lv.ITEM_COLUMNS)
    with progress.step("the registry and its additions"):
        registry = lv.registry_rows(items)
    write(f"registry-run{run}.csv", registry, lv.REGISTRY_COLUMNS)
    return rows


def _tables_step(
    exp_dir: Path,
    run: int,
    normalized: list[Normalized],
    write: Writer,
    *,
    window: int,
    level: str = "L4",
    closure: bool = False,
    merge_llm: bool = False,
    raqun: bool | None = None,
    all_assets: bool,
    include_tailored: bool = False,
    store: SnapshotStore | None = None,
    jobs: int = 0,
) -> None:
    """runs.csv, the normalised records (with each record's location kind, read from the snapshot files in `store`),
    the units, coverage and conformance of this run, the exact matcher's groups/members/agreement (without tailored
    pairs, `_match_input`), RaQuN's groups and the matcher comparison when available, and the micro tables (if any).
    Returns the exact groups per (snapshot, variant)."""
    store = store or SnapshotStore()
    languages = _languages(exp_dir)
    with progress.step("runs"):
        sizes = _sizes(exp_dir, store, languages)
        runs = tbl.run_rows(exp_dir, languages, tailored=tailored_map(), expects=_expects(exp_dir), sizes=sizes)
    write("runs.csv", runs, tbl.RUNS_COLUMNS)
    write("runtime.csv", tbl.runtime_rows(runs), tbl.RUNTIME_COLUMNS)
    voter_of = _voter_of(merge_llm)
    norm_rows: list[dict] = []
    commits: dict[str, str | None] = {}
    location_kinds: list[dict[str, str]] = []
    for meta, cbom_path, n in progress.track(normalized, "records and their places", item=_label):
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        commits.setdefault(snapshot, meta.get("base_commit"))
        repo, commit = tbl.full_name(snapshot), meta.get("variant_commit") or meta.get("base_commit")
        kinds = {r.record_id: sch.location_kind(r, store, repo, commit) for r in n.records}
        location_kinds.append(kinds)
        norm_rows += tbl.record_rows(
            n.records,
            snapshot=snapshot,
            variant=variant,
            run=run,
            commit=meta.get("base_commit"),
            language=languages.get(repo),
            location_kinds=kinds,
        )
    write(f"records-run{run}.csv", norm_rows, tbl.RECORDS_COLUMNS)
    # one run of the original variants, like every table of the summary's RQ2 section
    with progress.step("units, coverage and conformance"):
        cboms = [n for _, _, n in normalized]
        write(f"units-run{run}.csv", tbl.unit_rows(cboms, location_kinds), tbl.UNIT_COLUMNS)
        repos = [tbl.full_name(p.parents[2].name) for _, p, _ in normalized]
        units_repo = tbl.unit_repo_rows(cboms, repos, languages)
        write(f"units-repo-run{run}.csv", units_repo, tbl.UNIT_REPO_COLUMNS)
        per_run = Report()
        for n in cboms:
            per_run.add(n)
        write(f"coverage-run{run}.csv", per_run.coverage_rows(), COVERAGE_COLUMNS)
        write(f"conformance-run{run}.csv", per_run.conformance_rows(), CONFORMANCE_COLUMNS)
        write(f"sources-run{run}.csv", per_run.sources_rows(), SOURCES_COLUMNS)
    # both matchers at every level, one repository per task, feed matchlevels-run<i>.csv (RQ4 and RQ5, matcher x
    # level); the full group, member and agreement tables are written at the scoring level only
    with progress.step("matcher input"):  # the records per repository; PyQuN's import
        per_snapshot = _match_input(normalized, include_tailored)
        if raqun is None:
            raqun = rq.available()
        if raqun and not rq.available():
            typer.secho("RaQuN not importable (uv sync --extra raqun); skipped", fg="yellow")
            raqun = False
    groups_by_snapshot, matchlevel_rows = _match_step(
        run, per_snapshot, write, window=window, closure=closure, voter_of=voter_of, level=level, raqun=raqun,
        commits=commits, languages=languages, jobs=jobs,
    )  # fmt: skip
    write(f"matchlevels-run{run}.csv", matchlevel_rows, tbl.MATCHLEVELS_COLUMNS)
    entries = _micro_entries(exp_dir, run, all_assets, normalized)
    if entries:
        write(f"micro-run{run}.csv", tbl.micro_rows(entries, run=run), tbl.MICRO_COLUMNS)
        write(f"micro-items-run{run}.csv", tbl.micro_item_rows(entries, run=run), tbl.MICRO_ITEM_COLUMNS)
    else:
        typer.echo("no micro projects in this experiment: micro tables skipped")
    return groups_by_snapshot


def _match_step(
    run: int, per_snapshot: dict[tuple[str, str], list], write: Writer, *, window: int, closure: bool,
    voter_of: dict[str, str], level: str, raqun: bool, commits: dict, languages: dict, jobs: int,
) -> tuple[dict[tuple[str, str], list], list[dict]]:  # fmt: skip
    """Both matchers at every level, one repository per task (analysis/tasks.py:match_snapshot, in the process pool):
    groups/members/agreement-exact-run<i>.csv at the scoring level and, with RaQuN, the same -raqun tables and
    matchers-run<i>.csv (RaQuN vs the exact matcher per repository and level, with each matcher's seconds there) and
    raqun-<level>-run<i>.csv (RaQuN's groups to read, one column per tool). Returns the exact matcher's groups at
    the scoring level and the matchlevels rows (exact per level, then RaQuN per level, each with the matcher's
    seconds summed over the repositories)."""
    keys = list(per_snapshot)  # sorted by _match_input; every table below follows this order
    items: list[tasks.MatchItem] = [
        (key, per_snapshot[key], lv.LEVELS, window, closure, voter_of, raqun) for key in keys
    ]
    name = "exact and RaQuN matcher, per repository" if raqun else "exact matcher, per repository"
    matched = pmap(tasks.match_snapshot, items, jobs=jobs, name=name, item=lambda it: _repo_of(it[0]))
    results = dict(zip(keys, matched, strict=True))
    exact_by_level = {lv_name: {key: results[key][0][lv_name] for key in keys} for lv_name in lv.LEVELS}

    def level_rows(matcher: str, by_level: dict[str, dict]) -> list[dict]:
        out = []
        for lv_name in lv.LEVELS:
            row = tbl.matchlevel_row(matcher, lv_name, run, by_level[lv_name], voter_of)
            row["seconds"] = round(sum(results[key][3][matcher][lv_name] for key in keys), 3)
            out.append(row)
        return out

    def group_tables(matcher: str, by_key: dict[tuple[str, str], list]) -> None:
        g_rows: list[dict] = []
        m_rows: list[dict] = []
        a_rows: list[dict] = []
        for snapshot, variant in progress.track(keys, f"{matcher}: groups, members, agreement", item=_repo_of):
            g, m, a = tbl.group_rows(
                matcher,
                by_key[(snapshot, variant)],
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
        write(f"groups-{matcher}-run{run}.csv", g_rows, tbl.GROUP_COLUMNS)
        write(f"members-{matcher}-run{run}.csv", m_rows, tbl.MEMBERS_COLUMNS)
        write(f"agreement-{matcher}-run{run}.csv", a_rows, tbl.AGREEMENT_COLUMNS)

    group_tables("exact", exact_by_level[level])
    raqun_by_level = None
    if raqun:
        raqun_by_level = {lv_name: {key: results[key][1][lv_name] for key in keys} for lv_name in lv.LEVELS}
        tools = sorted({r.tool for records in per_snapshot.values() for r in records})
        for lv_name in lv.LEVELS:
            inspection, columns = tbl.raqun_inspection_rows(
                raqun_by_level[lv_name], level=lv_name, run=run, tools=tools
            )
            write(f"raqun-{lv_name}-run{run}.csv", inspection, columns)
        cmp_rows = [
            {"repo": tbl.full_name(snapshot), "snapshot": snapshot, "variant": variant, "run": run, "level": lv_name,
             **results[(snapshot, variant)][2][lv_name],
             "exact_seconds": round(results[(snapshot, variant)][3]["exact"][lv_name], 4),
             "raqun_seconds": round(results[(snapshot, variant)][3]["raqun"][lv_name], 4)}
            for lv_name in lv.LEVELS
            for snapshot, variant in keys
        ]  # fmt: skip
        group_tables("raqun", raqun_by_level[level])
        # the two timing columns are the report's measurement, not the comparison's
        write(f"matchers-run{run}.csv", cmp_rows, [*MATCHERS_COLUMNS, "exact_seconds", "raqun_seconds"])
    with progress.step("matchers per level"):
        rows = level_rows("exact", exact_by_level) + (level_rows("raqun", raqun_by_level) if raqun_by_level else [])
    return exact_by_level[level], rows


def _repo_of(key: tuple[str, str]) -> str:
    """A (snapshot, variant) key's repository, the item a per-repository line names."""
    return tbl.full_name(key[0])


def _baseline(name: str | None) -> dict[str, dict[str, float]] | None:
    """The per-tool noise floor from another experiment's stability.csv (`--baseline`), or None without one."""
    if not name:
        return None
    path = analysis_dir(_experiment(name)) / "stability.csv"
    if not path.exists():
        _err(f"--baseline {name}: no {path.name} in its _ANALYSIS (run `report` on it first, with repeated runs)")
    rows = baseline_flux(sm.read_csv(path))
    if not rows:
        _err(f"--baseline {name}: its stability.csv has no rows (the experiment needs repetitions > 1)")
    return rows


def _mutation_step(
    exp_dir: Path, all_runs: list[Normalized], write: Writer, *, run: int, window: int, merge_llm: bool,
    samples: int, obs: sc.Observations | None = None,
    baseline: dict[str, dict[str, float]] | None = None,
) -> None:  # fmt: skip
    """RQ6 (analysis/mutation.py), for an experiment with mutants (mutants.json): mutants.csv, mutation.csv (one row
    per mutant and tool, majority over the repetitions), mutation-rates.csv, mutation-scores.csv (recall, precision,
    F1 on the planted calls and decoys; the changes off the edit with the noise floor of `baseline`) and, with the
    scores' observations, bridge-run<i>.csv (RQ5's confirmation rate per language against F1)."""
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
        statuses[key][int(meta.get("run_index") or 1)] = str(stored_status(meta) or "?")
    languages = _languages(exp_dir)
    with progress.step("RQ6: planted calls and decoys"):
        rows = mu.detection_rows(
            manifest,
            records,
            statuses,
            list(_spec(exp_dir).get("tools") or []),
            window=window,
            languages=languages,
            voter_of=_voter_of(merge_llm),
        )
        rates = mu.rate_rows(rows, samples=samples)
        scores = mu.score_rows(rows, samples=samples, baseline=baseline)
    write("mutants.csv", mu.mutant_rows(manifest, languages=languages), mu.MUTANTS_COLUMNS)
    write("mutation.csv", rows, mu.MUTATION_COLUMNS)
    write("mutation-rates.csv", rates, mu.RATE_COLUMNS)
    write("mutation-scores.csv", scores, mu.SCORE_COLUMNS)
    if obs is not None:
        keys_by_language: dict[str, list[str]] = defaultdict(list)
        for _, cbom_path, _n in all_runs:
            snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
            if variant == ORIGINAL_VARIANT:
                keys_by_language[languages.get(tbl.full_name(snapshot)) or ""].append(f"{snapshot}/{variant}")
        confirmation: dict[tuple[str, str], float] = {}
        for language, keys in keys_by_language.items():
            for tool, per_repo in sc.repo_rates(obs, "algorithm", sorted(set(keys))).items():
                if per_repo:
                    confirmation[(tool, language)] = sum(per_repo) / len(per_repo)
        write(f"bridge-run{run}.csv", mu.bridge_rows(confirmation, scores), mu.BRIDGE_COLUMNS)


def _stability_step(all_runs: list[Normalized], write: Writer, *, level: str) -> dict[tuple[str, str, str], set[str]]:
    """stability.csv over every repetition (analysis/stability.py); returns the consensus sets of repeated runs."""
    runs: dict[tuple[str, str, str], dict[int, list]] = defaultdict(dict)
    for meta, cbom_path, n in all_runs:
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        runs[(snapshot, variant, n.tool)][int(meta.get("run_index") or 1)] = n.records
    with progress.step("run-to-run stability"):
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
    include_tailored: bool,
    samples: int,
    consensus: dict[tuple[str, str, str], set[str]] | None = None,
) -> sc.Observations:
    """score-run<i>.csv, loo-run<i>.csv and grouping-run<i>.csv (analysis/score.py) from the exact matcher's groups;
    returns the observations under the declared voter groups (the bridge of the mutation step reads them).
    grouping-run<i>.csv ranks the tools again with every tool its own voter group, with the LLM workers merged and
    with the tools of one pattern engine family merged (the sensitivity checks of the declared voter groups)."""
    voter_of = _voter_of(merge_llm)
    tools_ran: dict[str, set[str]] = defaultdict(set)
    repo_of: dict[str, str] = {}
    for _, cbom_path, n in normalized:
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        tools_ran[f"{snapshot}/{variant}"].add(n.tool)
        repo_of[f"{snapshot}/{variant}"] = tbl.full_name(snapshot)
    groups = {f"{s}/{v}": g for (s, v), g in groups_by_snapshot.items()}

    def observe(voters: dict[str, str]) -> sc.Observations:
        obs = sc.observe(
            groups,
            level=level,
            voter_of=voters,
            tools_ran=tools_ran,
            tailored=None if include_tailored else tailored_map(),
            repo_of=repo_of,
        )
        for (snapshot, variant, tool), keys in (consensus or {}).items():
            obs.restrict(f"{snapshot}/{variant}", tool, set(keys))  # keep the items in the tool's consensus set
        return obs

    with progress.step(f"RQ5: per-tool rates, {samples} bootstraps"):
        obs = observe(voter_of)
        rows, loo = sc.score_rows(obs, samples=samples)
    write(f"score-run{run}.csv", rows, sc.SCORE_COLUMNS)
    write(f"loo-run{run}.csv", loo, sc.LOO_COLUMNS)
    with progress.step("RQ5: other voter groupings"):
        groupings = {"declared": obs, "per-tool": observe({t: t for t in voter_of})}
        if not merge_llm:
            groupings["llm-merged"] = observe(_voter_of(True))
        groupings["engine-merged"] = observe(voter_groups(merge_llm=merge_llm, engine_family=True))
        grouping = sc.grouping_rows(groupings)
    write(f"grouping-run{run}.csv", grouping, sc.GROUPING_COLUMNS)
    return obs


@analysis_app.command("score")
def score_cmd(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    level: str = LEVEL_OPT,
    window: int = WINDOW_OPT,
    closure: bool = CLOSURE_OPT,
    merge_llm: bool = MERGE_LLM_OPT,
    include_tailored: bool = INCLUDE_TAILORED_OPT,
    samples: int = SAMPLES_OPT,
    consensus: bool = CONSENSUS_OPT,
    jobs: int = JOBS_OPT,
):
    """RQ5: the per-tool rates without ground truth (confirmation rate, exclusivity, Dawid-Skene support,
    leave-one-voter-out, bootstrap CIs) on the exact matcher's groups; stability.csv over repeated runs."""
    exp_dir = _experiment(name)
    counts: dict[str, int] = {}
    write = _writer(analysis_dir(exp_dir), counts)
    all_runs = _normalize_runs(exp_dir, jobs=jobs)
    normalized = [x for x in all_runs if x[1].name == f"run{run}.json"]
    if not normalized:
        _err(f"no ok results for run{run} in {exp_dir}")
    matcher = ExactMatcher(level=level, window=window, closure=closure, voter_of=_voter_of(merge_llm))
    groups = {key: matcher.match(records) for key, records in _match_input(normalized, include_tailored).items()}
    sets = _stability_step(all_runs, write, level=level)
    _score_step(
        exp_dir,
        run,
        normalized,
        groups,
        write,
        level=level,
        merge_llm=merge_llm,
        include_tailored=include_tailored,
        samples=samples,
        consensus=sets if consensus else None,
    )


def _write_info(
    out_dir: Path,
    run: int,
    settings: dict,
    counts: dict[str, int],
    normalized: list[Normalized],
    voter_of: dict[str, str],
):
    """tables-run<i>.json: settings, row counts, the vocabulary and adapter spec versions used, and the voter map the
    numbers rest on (each spec tool's voter group and engine, after --merge-llm)."""
    vocabulary = next((n.vocabulary for _, _, n in normalized), {})
    adapters = {n.tool: n.adapter.get("spec") for _, _, n in normalized}
    registry = load_tools()
    voters = {
        t: {"voter_group": voter_of.get(t, t), "engine": registry.get(t, {}).get("engine")}
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
def normalize(name: str = EXP_ARG, jobs: int = JOBS_OPT):
    """Normalise every stored CBOM with its tool's adapter spec (analysis/adapters/<tool>.yaml).

    Writes run<i>.norm.json next to each run<i>.json, _ANALYSIS/normalize.json (per tool over every run: what was
    read, value sources, spec issues, conflicts, unmapped values) and _ANALYSIS/vocab.csv (what every raw value
    mapped to). The per-run fill and conformance tables come with `tables` and `report`.
    """
    exp_dir = _experiment(name)
    counts: dict[str, int] = {}
    report, done = _normalize_step(exp_dir, _writer(analysis_dir(exp_dir), counts), jobs=jobs)
    typer.echo("algorithm assets (original variants, every run): mapped to an algorithm, with a value the tool gave:")
    for line in report.summary_lines():
        typer.echo("  " + line)
    typer.echo(f"{len(done)} CBOMs normalised (run<i>.norm.json next to each), in {analysis_dir(exp_dir)}")


@analysis_app.command("levels")
def levels(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    merge_llm: bool = MERGE_LLM_OPT,
    include_tailored: bool = INCLUDE_TAILORED_OPT,
    closure: bool = CLOSURE_OPT,
    jobs: int = JOBS_OPT,
):
    """How much agreement normalisation creates: share of each tool's items (per repository) that another
    voter group also reports, at levels L0 (raw names) to L4 (full adapter specs). See analysis/levels.py.
    Also writes items-run<i>.csv: which items each tool reports per repository and level."""
    exp_dir = _experiment(name)
    counts: dict[str, int] = {}
    rows = _levels_step(
        exp_dir,
        run,
        _normalize_runs(exp_dir, run=run, jobs=jobs),
        _writer(analysis_dir(exp_dir), counts),
        merge_llm=merge_llm,
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
    raqun: bool | None = RAQUN_OPT,
    all_assets: bool = ALL_ASSETS_OPT,
    include_tailored: bool = INCLUDE_TAILORED_OPT,
    jobs: int = JOBS_OPT,
):
    """Flat CSV tables for pandas/R: runs, normalised records, the exact matcher's groups, members and agreement
    (and RaQuN's, with the comparison), micro."""
    exp_dir = _experiment(name)
    out_dir = analysis_dir(exp_dir)
    counts: dict[str, int] = {}
    normalized = _normalize_runs(exp_dir, run=run, jobs=jobs)
    settings = {"run": run, "window": window, "level": level, "closure": closure, "all_assets": all_assets,
                "include_tailored": include_tailored}  # fmt: skip
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
        raqun=raqun,
        all_assets=all_assets,
        include_tailored=include_tailored,
        jobs=jobs,
    )
    _write_info(out_dir, run, settings, counts, normalized, _voter_of(merge_llm))
    typer.echo(f"written to {out_dir}")


@progress.session()
def run_report(
    exp_dir: Path,
    *,
    run: int = 1,
    window: int = 2,
    level: str = "L4",
    all_assets: bool = False,
    merge_llm: bool = False,
    include_tailored: bool = False,
    closure: bool = False,
    fetch: bool = False,
    samples: int = 200,
    consensus: bool = True,
    raqun: bool | None = None,
    jobs: int = 0,
    baseline: str | None = None,
) -> Path:
    """`normalize`, `schema`, `levels`, `tables` and `score` in one pass (every CBOM normalised once), then
    _SUMMARY.md. `baseline`: the stability experiment whose stability.csv gives RQ6's noise floor.
    `_ANALYSIS/` holds regenerated output only, so it is emptied first: no stale file from an earlier version."""
    floor = _baseline(baseline)  # first: a missing baseline fails before anything runs
    out_dir = analysis_dir(exp_dir)
    shutil.rmtree(out_dir, ignore_errors=True)
    counts: dict[str, int] = {}
    write = _writer(out_dir, counts)
    store = _store(exp_dir, fetch=fetch)
    _, done = _normalize_step(exp_dir, write, store=store, jobs=jobs)
    originals = [x for x in done if _original(x)]
    normalized = [x for x in originals if x[1].name == f"run{run}.json"]
    _schema_step(exp_dir, run, normalized, write, store=store, jobs=jobs)
    _levels_step(
        exp_dir,
        run,
        normalized,
        write,
        merge_llm=merge_llm,
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
        raqun=raqun,
        all_assets=all_assets,
        include_tailored=include_tailored,
        store=store,
        jobs=jobs,
    )
    sets = _stability_step(originals, write, level=level)
    obs = _score_step(
        exp_dir,
        run,
        normalized,
        groups,
        write,
        level=level,
        merge_llm=merge_llm,
        include_tailored=include_tailored,
        samples=samples,
        consensus=sets if consensus else None,
    )
    _mutation_step(exp_dir, done, write, run=run, window=window, merge_llm=merge_llm,
                   samples=samples, obs=obs, baseline=floor)  # fmt: skip
    settings = {
        "run": run,
        "window": window,
        "level": level,
        "samples": samples,
        "consensus": consensus,
        "raqun": rq.available() if raqun is None else raqun,
        "all_assets": all_assets,
        "merge_llm": merge_llm,
        "include_tailored": include_tailored,
        "closure": closure,
        "fetch": fetch,
        "baseline": baseline,
    }
    _write_info(out_dir, run, settings, counts, normalized, _voter_of(merge_llm))
    with progress.step(sm.SUMMARY_FILE):
        return sm.write(out_dir, run=run)


@analysis_app.command("report")
def report(
    name: str = EXP_ARG,
    run: int = RUN_OPT,
    window: int = WINDOW_OPT,
    level: str = LEVEL_OPT,
    all_assets: bool = ALL_ASSETS_OPT,
    merge_llm: bool = MERGE_LLM_OPT,
    include_tailored: bool = INCLUDE_TAILORED_OPT,
    closure: bool = CLOSURE_OPT,
    fetch: bool = FETCH_OPT,
    samples: int = SAMPLES_OPT,
    consensus: bool = CONSENSUS_OPT,
    raqun: bool | None = RAQUN_OPT,
    jobs: int = JOBS_OPT,
    baseline: str | None = BASELINE_OPT,
):
    """Everything in one pass: normalised CBOMs, every CSV table (runs, records, RQ3 schema/meaningful/sources and
    RQ2 traceability, the exact matcher's groups/members/agreement, scores and leave-one-out, stability, micro at two
    levels, items per tool and level, ladder, the registry delta, coverage, conformance; with mutants the RQ6 tables
    mutants.csv, mutation.csv, mutation-rates.csv, mutation-scores.csv and bridge-run<i>.csv, the agreement tables on
    the originals only) and _ANALYSIS/_SUMMARY.md with Markdown tables."""
    exp_dir = _experiment(name)
    path = run_report(
        exp_dir,
        run=run,
        window=window,
        level=level,
        all_assets=all_assets,
        merge_llm=merge_llm,
        include_tailored=include_tailored,
        closure=closure,
        fetch=fetch,
        samples=samples,
        consensus=consensus,
        raqun=raqun,
        jobs=jobs,
        baseline=baseline,
    )
    typer.echo(f"written to {path.parent} (summary: {path.name})")
