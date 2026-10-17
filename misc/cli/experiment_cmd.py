"""`bf-cbom experiment ...`: batch runs whose results live on the results-data volume.

The runner enqueues JobInstructions on the normal worker queues and registers each job id in
the coordinator's routing hash under `exp:<name>`. Whoever drains `results:<tool>` (this
runner or the Streamlit UI) forwards such results to `results:exp:<name>`, from where this
command persists them under RESULTS_DIR/<name>/... (see common/experiment.py).
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path

import redis
import typer
import yaml

from common import corpus as corpus_mod
from common.experiment import (
    GIT_MIRROR_DIR,
    GITSERVER_URL,
    RESULTS_DIR,
    ExperimentError,
    PlannedJob,
    Snapshot,
    experiment_dir,
    load_spec,
    mark_unsupported,
    plan_jobs,
    plan_waves,
    resolve_snapshots,
    spec_to_dict,
    summarize,
    write_result,
)
from common.models import JobResult
from common.tools_registry import load_tools, tool_memory_gb
from coordinator.redis_io import (
    EXPERIMENT_ROUTE_PREFIX,
    JOB_ROUTING_KEY,
    dead_workers,
    ingest_worker_results,
    now_iso,
)

experiment_app = typer.Typer(
    help="Batch experiments (reproducible, resumable; results stay on the results-data volume)",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
corpus_app = typer.Typer(help="Build repository corpora", add_completion=False, no_args_is_help=True)
experiment_app.add_typer(corpus_app, name="corpus")

REDIS_HOST_OPT = typer.Option("localhost", envvar="REDIS_HOST", help="Redis host")
REDIS_PORT_OPT = typer.Option(6379, envvar="REDIS_PORT", help="Redis port")
SPEC_ARG = typer.Argument(..., help="Experiment YAML (e.g. /app/experiments/e0.yaml)")
NAME_ARG = typer.Argument(..., help="Experiment name")
DEST_OPT = typer.Option(..., "--dest", "-d", help="Directory to write the ZIP into (mount it from the host)")
CORPUS_OUT_OPT = typer.Option(
    str(RESULTS_DIR / "corpus" / "corpus.yaml"), "--out", "-o", help="Output YAML (default: on the results volume)"
)


def _redis(host: str, port: int) -> redis.Redis:
    r = redis.Redis(host=host, port=port, decode_responses=True, socket_timeout=30)
    try:
        r.ping()
    except redis.exceptions.RedisError as e:
        typer.echo(f"Error: cannot connect to Redis at {host}:{port}: {e}", err=True)
        raise typer.Exit(2) from e
    return r


def _route_key(name: str) -> str:
    return f"{EXPERIMENT_ROUTE_PREFIX}{name}"


def _status_key(name: str) -> str:
    return f"exp:{name}"


def _pending_key(name: str) -> str:
    return f"exp:{name}:pending"


def _running_key(name: str) -> str:
    return f"exp:{name}:running"  # job_id -> started_at, written by the workers (common/jobs.py)


def _job_from_descriptor(job_id: str, desc: dict) -> PlannedJob:
    return PlannedJob(
        job_id=job_id, snapshot=Snapshot(**desc["snapshot"]), tool=desc["tool"], run_index=int(desc["run_index"])
    )


def _descriptor(job: PlannedJob) -> str:
    return json.dumps({"tool": job.tool, "run_index": job.run_index, "snapshot": job.snapshot.__dict__})


def _err(msg: str, code: int = 1):
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


@experiment_app.command("run")
def run(
    spec_path: Path = SPEC_ARG,
    redis_host: str = REDIS_HOST_OPT,
    redis_port: int = REDIS_PORT_OPT,
    idle_timeout: int = typer.Option(1800, help="Stop waiting after this many seconds without any new result"),
    requeue_pending: bool = typer.Option(False, "--requeue-pending", help="Re-issue jobs a previous run left pending"),
    no_wait: bool = typer.Option(
        False, "--no-wait", help="Enqueue and return; collect later with `experiment collect`"
    ),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Also redo stored runs whose status is not ok"),
    skip_unsupported: bool = typer.Option(
        True,
        "--skip-unsupported/--run-unsupported",
        help="Record (tool, repo) pairs outside the tool's language scope (tools.yaml) as status 'unsupported' "
        "instead of running them",
    ),
    memory_budget_gb: float = typer.Option(
        0.0,
        "--memory-budget-gb",
        envvar="EXPERIMENT_MEMORY_BUDGET_GB",
        help="Feed tools in waves so the sum of their worker memory limits (tools.yaml memory_gb) stays within "
        "this many GiB; 0 = no limit (one wave). Compose sets it from EXPERIMENT_MEMORY_BUDGET_GB (Makefile).",
    ),
):
    """Pin, mirror, enqueue and collect: everything missing on disk for the spec gets (re)run.

    Workers take one job at a time, so a batch's memory need is the sum of the limits of the workers that have
    jobs queued. With a memory budget the tools are packed into waves (common/experiment.py:plan_waves); each
    wave is enqueued only after the previous one has been collected.
    """
    try:
        spec = load_spec(spec_path)
    except (ExperimentError, OSError, yaml.YAMLError) as e:
        _err(f"invalid experiment spec: {e}", 2)
    exp_dir = experiment_dir(spec.name)
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "experiment.yaml").write_text(yaml.safe_dump(spec_to_dict(spec), sort_keys=False), encoding="utf-8")
    typer.secho(
        f"experiment {spec.name}: {len(spec.repos)} repos x {spec.tools} x {spec.repetitions} rep(s)",
        fg=typer.colors.BLUE,
        err=True,
    )

    typer.secho("resolving snapshots and mirroring to the git server...", fg=typer.colors.BLUE, err=True)
    try:
        snapshots = resolve_snapshots(spec, exp_dir, log=lambda m: typer.echo(m, err=True))
    except ExperimentError as e:
        _err(str(e), 2)

    r = _redis(redis_host, redis_port)
    pending_raw = r.hgetall(_pending_key(spec.name))
    pending = {jid: _job_from_descriptor(jid, json.loads(d)) for jid, d in pending_raw.items()}
    # Drop descriptors whose result arrived in the meantime.
    for jid, job in list(pending.items()):
        if job.paths(exp_dir)[1].exists():
            r.hdel(_pending_key(spec.name), jid)
            r.hdel(JOB_ROUTING_KEY, jid)
            pending.pop(jid)
    if requeue_pending and pending:
        _cancel_pending(r, spec.name, pending)
        pending = {}

    if skip_unsupported:
        unsupported = mark_unsupported(spec, snapshots, exp_dir)
        if unsupported:
            per_tool: dict[str, int] = {}
            for tool, _ in unsupported:
                per_tool[tool] = per_tool.get(tool, 0) + 1
            typer.secho(
                "language scope (tools.yaml): recorded as unsupported, not run: "
                + ", ".join(f"{t} x{n}" for t, n in sorted(per_tool.items())),
                fg=typer.colors.BLUE,
                err=True,
            )
    jobs, skipped = plan_jobs(spec, snapshots, exp_dir, new_id=lambda: str(uuid.uuid4()), retry_failed=retry_failed)
    covered = {(j.snapshot.full_name, j.snapshot.variant, j.tool, j.run_index) for j in pending.values()}
    jobs = [j for j in jobs if (j.snapshot.full_name, j.snapshot.variant, j.tool, j.run_index) not in covered]
    typer.secho(
        f"{skipped} run(s) already on disk, {len(pending)} still pending from an earlier run, {len(jobs)} to enqueue",
        fg=typer.colors.BLUE,
        err=True,
    )
    registry = load_tools()
    memory = {t: tool_memory_gb(t, registry) for t in spec.tools}
    pending_tools = {j.tool for j in pending.values()}
    active_tools = [t for t in spec.tools if t in pending_tools or any(j.tool == t for j in jobs)]
    waves = plan_waves(active_tools, budget_gb=memory_budget_gb, memory_gb=memory, first=sorted(pending_tools))
    for t in active_tools:
        if t not in registry:
            msg = f"warning: '{t}' is not in common/tools.yaml; assuming {memory[t]:g} GiB"
            typer.secho(msg, fg=typer.colors.YELLOW, err=True)
    if memory_budget_gb > 0:
        for wave in waves:
            load = sum(memory[t] for t in wave)
            over = load > memory_budget_gb
            typer.secho(
                f"wave {waves.index(wave) + 1}/{len(waves)}: {', '.join(f'{t} {memory[t]:g}' for t in wave)}"
                f" = {load:g} GiB" + (" (exceeds the budget: already queued or a single tool)" if over else ""),
                fg=typer.colors.YELLOW if over else typer.colors.BLUE,
                err=True,
            )
        typer.secho(
            f"memory budget {memory_budget_gb:g} GiB -> {len(waves)} wave(s); workers of later waves idle meanwhile",
            fg=typer.colors.BLUE,
            err=True,
        )
    total = len(jobs) + len(pending)
    r.hset(
        _status_key(spec.name),
        mapping={
            "status": "running" if total else "completed",
            "started_at": now_iso(),
            "expected_jobs": str(spec.expected_jobs),
            "issued_jobs": "0",
            "skipped_jobs": str(skipped),
            "results_dir": str(exp_dir),
            "tools": json.dumps(spec.tools),
            "memory_budget_gb": f"{memory_budget_gb:g}",
            "waves": json.dumps(waves),
        },
    )
    if not total:
        _print_summary(exp_dir)
        return
    done = 0
    issued = 0
    for i, wave in enumerate(waves, 1):
        wave_jobs = [j for j in jobs if j.tool in wave]
        pipe = r.pipeline()
        for job in wave_jobs:
            pipe.hset(JOB_ROUTING_KEY, job.job_id, _route_key(spec.name))
            pipe.hset(_pending_key(spec.name), job.job_id, _descriptor(job))
            pipe.rpush(f"jobs:{job.tool}", job.instruction().to_json())
        issued += len(wave_jobs)
        pipe.hset(
            _status_key(spec.name),
            mapping={"issued_jobs": str(issued), "wave": f"{i}/{len(waves)}", "updated_at": now_iso()},
        )
        pipe.execute()
        wave_pending = {jid: j for jid, j in pending.items() if j.tool in wave}
        wave_pending.update({j.job_id: j for j in wave_jobs})
        if len(waves) > 1:
            typer.secho(
                f"wave {i}/{len(waves)} ({', '.join(wave)}): {len(wave_jobs)} job(s) enqueued, "
                f"{len(wave_pending) - len(wave_jobs)} already pending",
                fg=typer.colors.BLUE,
                err=True,
            )
        if no_wait:
            if i < len(waves):
                typer.secho(
                    f"--no-wait: only wave {i} was enqueued; after `experiment collect {spec.name}` rerun this "
                    f"command to issue the remaining {len(waves) - i} wave(s)",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
            _print_summary(exp_dir)
            return
        done = _collect(r, spec.name, spec.tools, exp_dir, wave_pending, idle_timeout, done=done, total=total)
    r.hset(_status_key(spec.name), mapping={"status": "completed", "finished_at": now_iso()})
    r.delete(_running_key(spec.name))
    _print_summary(exp_dir)


def _collect(
    r: redis.Redis,
    name: str,
    tools: list[str],
    exp_dir: Path,
    pending: dict[str, PlannedJob],
    idle_timeout: int,
    *,
    done: int = 0,
    total: int | None = None,
) -> int:
    """Persist results until `pending` is empty; returns the cumulative count of stored results."""
    inbox = f"results:{_route_key(name)}"
    last_progress = time.monotonic()
    total = total if total is not None else done + len(pending)
    warned_dead: set[str] = set()
    redis_failures = 0
    while pending:
        try:
            ingest_worker_results(r, _route_key(name), tools)
            batch = _drain(r, inbox)
        except redis.exceptions.RedisError as e:
            # Transient DNS / connection blips inside the compose network must not kill a long run.
            redis_failures += 1
            typer.secho(f"redis error ({e}); retrying in 5s [{redis_failures}]", fg=typer.colors.YELLOW, err=True)
            if time.monotonic() - last_progress > idle_timeout:
                _err(f"redis unreachable for {idle_timeout}s; {len(pending)} job(s) still pending", 5)
            time.sleep(5.0)
            continue
        got_any = bool(batch)
        for raw in batch:
            try:
                result = JobResult.from_json(raw)
            except Exception:
                typer.secho(f"dropping unparsable result: {raw[:120]}", fg=typer.colors.YELLOW, err=True)
                continue
            job = pending.pop(result.job_id, None)
            if job is None:
                typer.secho(f"result for unknown job {result.job_id}; ignoring", fg=typer.colors.YELLOW, err=True)
                continue
            path = write_result(exp_dir, job, result)
            r.hdel(_pending_key(name), result.job_id)
            r.hdel(_running_key(name), result.job_id)
            done += 1
            r.hset(_status_key(name), mapping={"stored_jobs": str(done), "updated_at": now_iso()})
            colour = typer.colors.GREEN if result.status == "ok" else typer.colors.RED
            typer.secho(
                f"[{done}/{total}] {result.status:<7} {job.tool:<15} {job.snapshot.full_name}@{job.snapshot.variant} "
                f"run{job.run_index} {result.duration_sec or 0:.0f}s -> {path.relative_to(exp_dir)}",
                fg=colour,
                err=True,
            )
        if got_any:
            last_progress = time.monotonic()
        else:
            pending_tools = {j.tool for j in pending.values()}
            dead = set(dead_workers(r, sorted(pending_tools)))
            for t in dead - warned_dead:
                typer.secho(
                    f"warning: worker '{t}' publishes no heartbeat; its pending jobs will not finish",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
                warned_dead.add(t)
            if pending_tools and pending_tools <= dead:
                r.hset(_status_key(name), mapping={"status": "stalled", "updated_at": now_iso()})
                _err(f"all remaining {len(pending)} job(s) belong to workers that are down; start them and rerun", 3)
            if time.monotonic() - last_progress > idle_timeout:
                r.hset(_status_key(name), mapping={"status": "stalled", "updated_at": now_iso()})
                _err(f"no result for {idle_timeout}s; {len(pending)} job(s) still pending (rerun to keep waiting)", 4)
            time.sleep(2.0)
    return done


def _drain(r: redis.Redis, inbox: str) -> list[str]:
    """Pop everything currently in the experiment inbox."""
    batch: list[str] = []
    while True:
        raw = r.lpop(inbox)
        if raw is None:
            return batch
        batch.append(raw)


def _print_summary(exp_dir: Path) -> None:
    typer.echo(json.dumps(summarize(exp_dir), indent=2))


def _cancel_pending(r: redis.Redis, name: str, pending: dict[str, PlannedJob]) -> int:
    removed = 0
    for job in pending.values():
        queue = f"jobs:{job.tool}"
        for raw in r.lrange(queue, 0, -1):
            if f'"job_id": "{job.job_id}"' in raw or f'"job_id":"{job.job_id}"' in raw:
                removed += r.lrem(queue, 1, raw)
        r.hdel(JOB_ROUTING_KEY, job.job_id)
        r.hdel(_pending_key(name), job.job_id)
    return removed


@experiment_app.command("collect")
def collect(
    name: str = typer.Argument(..., help="Experiment name"),
    redis_host: str = REDIS_HOST_OPT,
    redis_port: int = REDIS_PORT_OPT,
    idle_timeout: int = typer.Option(1800),
):
    """Resume collecting results for jobs that are still pending (after `run --no-wait` or an interrupted run)."""
    r = _redis(redis_host, redis_port)
    exp_dir = experiment_dir(name)
    if not exp_dir.exists():
        _err(f"no experiment '{name}' under {RESULTS_DIR}")
    pending = {jid: _job_from_descriptor(jid, json.loads(d)) for jid, d in r.hgetall(_pending_key(name)).items()}
    tools = json.loads(r.hget(_status_key(name), "tools") or "[]") or sorted({j.tool for j in pending.values()})
    if not pending:
        typer.echo("nothing pending")
        _print_summary(exp_dir)
        return
    stored = int(r.hget(_status_key(name), "stored_jobs") or 0)
    _collect(r, name, tools, exp_dir, pending, idle_timeout, done=stored, total=stored + len(pending))
    r.hset(_status_key(name), mapping={"status": "completed", "finished_at": now_iso()})
    wave = r.hget(_status_key(name), "wave") or ""
    if "/" in wave and wave.split("/")[0] != wave.split("/")[1]:
        msg = f"wave {wave} collected; rerun `experiment run` to issue the next wave"
        typer.secho(msg, fg=typer.colors.YELLOW, err=True)
    _print_summary(exp_dir)


@experiment_app.command("cancel")
def cancel(name: str = typer.Argument(...), redis_host: str = REDIS_HOST_OPT, redis_port: int = REDIS_PORT_OPT):
    """Remove this experiment's queued jobs from the worker queues (running jobs finish and are then ignored)."""
    r = _redis(redis_host, redis_port)
    pending = {jid: _job_from_descriptor(jid, json.loads(d)) for jid, d in r.hgetall(_pending_key(name)).items()}
    removed = _cancel_pending(r, name, pending)
    r.hset(_status_key(name), mapping={"status": "cancelled", "finished_at": now_iso()})
    typer.echo(f"removed {removed} queued job(s); {len(pending) - removed} were already taken by workers")


@experiment_app.command("status")
def status(name: str = typer.Argument(...), redis_host: str = REDIS_HOST_OPT, redis_port: int = REDIS_PORT_OPT):
    """Progress from Redis plus what is stored on disk."""
    r = _redis(redis_host, redis_port)
    meta = r.hgetall(_status_key(name))
    meta["pending_jobs"] = r.hlen(_pending_key(name))
    running = r.hgetall(_running_key(name))
    pending_ids = set(r.hkeys(_pending_key(name)))
    meta["running_jobs"] = {jid: ts for jid, ts in running.items() if jid in pending_ids}
    exp_dir = experiment_dir(name)
    typer.echo(json.dumps({"redis": meta, "disk": summarize(exp_dir) if exp_dir.exists() else None}, indent=2))


@experiment_app.command("list")
def list_experiments():
    """Experiments present on the results volume."""
    if not RESULTS_DIR.exists():
        typer.echo(f"(no results yet under {RESULTS_DIR})")
        return
    for d in sorted(p for p in RESULTS_DIR.iterdir() if p.is_dir() and (p / "experiment.yaml").exists()):
        s = summarize(d)
        typer.echo(f"{d.name:<30} {s['stored_runs']:>6} run(s)  {json.dumps(s['per_tool'])}")


@experiment_app.command("export")
def export(
    name: str = NAME_ARG,
    dest: Path = DEST_OPT,
    no_report: bool = typer.Option(False, "--no-report", help="Zip as stored, without running `analysis report`"),
):
    """Zip one experiment (spec, lock, all CBOMs and meta files, analysis/ with SUMMARY.md). The only way data
    leaves the volume. Runs `analysis report` first, so the CSV tables and SUMMARY.md in the zip are current."""
    exp_dir = experiment_dir(name)
    if not exp_dir.exists():
        _err(f"no experiment '{name}' under {RESULTS_DIR}")
    if not no_report:
        from misc.cli.analysis_cmd import run_report  # local import: the analysis is not needed for the runs

        try:
            run_report(exp_dir)
        except Exception as e:  # incl. typer.Exit; an export must never fail because the analysis did
            typer.secho(f"analysis report failed ({e!r}); exporting the stored files only", fg=typer.colors.YELLOW)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{name}.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(exp_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(Path(name) / path.relative_to(exp_dir)))
    typer.echo(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


@experiment_app.command("delete")
def delete(name: str = typer.Argument(...), yes: bool = typer.Option(False, "--yes", help="Do not ask")):
    """Delete an experiment's results from the volume."""
    exp_dir = experiment_dir(name)
    if not exp_dir.exists():
        _err(f"no experiment '{name}' under {RESULTS_DIR}")
    if not yes and not typer.confirm(f"delete {exp_dir} with all stored CBOMs?"):
        raise typer.Exit(0)
    shutil.rmtree(exp_dir)
    typer.echo(f"deleted {exp_dir}")


@experiment_app.command("env")
def env():
    """Show where the runner reads and writes."""
    typer.echo(
        json.dumps(
            {"RESULTS_DIR": str(RESULTS_DIR), "GIT_MIRROR_DIR": str(GIT_MIRROR_DIR), "GITSERVER_URL": GITSERVER_URL},
            indent=2,
        )
    )


@corpus_app.command("build")
def corpus_build(
    out: Path = CORPUS_OUT_OPT,
    languages: str = typer.Option("Java,Python", help="Comma-separated GitHub languages"),
    per_language: int = typer.Option(100),
    pushed_within_days: int = typer.Option(365),
    no_crypto_filter: bool = typer.Option(False, "--no-crypto-filter", help="Skip the clone+grep crypto pre-filter"),
    exclude: str = typer.Option("", help="Extra comma-separated regexes on full_name to exclude"),
    seed: int = typer.Option(20261015),
):
    """Sample a stratified corpus (stars x size cells per language), pre-filter for crypto usage, pin commits."""
    params = corpus_mod.CorpusParams(
        languages=[x.strip() for x in languages.split(",") if x.strip()],
        per_language=per_language,
        pushed_within_days=pushed_within_days,
        crypto_filter=not no_crypto_filter,
        seed=seed,
    )
    params.exclude_patterns += [x.strip() for x in exclude.split(",") if x.strip()]
    token = os.getenv("GITHUB_TOKEN") or None
    if not token:
        typer.secho(
            "no GITHUB_TOKEN: unauthenticated search is limited to 10 requests/min; this will be slow",
            fg=typer.colors.YELLOW,
            err=True,
        )
    corpus = corpus_mod.build_corpus(params, token, log=lambda m: typer.echo(m, err=True))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(corpus, sort_keys=False), encoding="utf-8")
    typer.echo(f"wrote {len(corpus['repos'])} repos to {out} (stats: {corpus['stats']})")
