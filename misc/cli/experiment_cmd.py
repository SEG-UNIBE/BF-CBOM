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
    ANALYSIS_DIR,
    GIT_MIRROR_DIR,
    GITSERVER_URL,
    RESULTS_DIR,
    SPEC_FILE,
    ExperimentError,
    PlannedJob,
    Snapshot,
    clear_stored_runs,
    experiment_dir,
    is_experiment,
    load_spec,
    mark_unsupported,
    plan_jobs,
    plan_waves,
    resolve_snapshots,
    save_run_log_entry,
    write_result,
    write_spec_copy,
)
from common.memory import HostSampler
from common.models import JobResult
from common.tools_registry import load_tools, tool_memory_gb
from coordinator.redis_io import (
    EXPERIMENT_ROUTE_PREFIX,
    JOB_ROUTING_KEY,
    dead_workers,
    ingest_worker_results,
    now_iso,
)
from misc.cli import overview

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
        job_id=job_id,
        snapshot=Snapshot(**desc["snapshot"]),
        tool=desc["tool"],
        run_index=int(desc["run_index"]),
        enqueued_at=desc.get("enqueued_at"),
    )


def _descriptor(job: PlannedJob) -> str:
    return json.dumps(
        {
            "tool": job.tool,
            "run_index": job.run_index,
            "enqueued_at": job.enqueued_at,
            "snapshot": job.snapshot.__dict__,
        }
    )


def parse_replicas(raw: str | None) -> dict[str, int]:
    """`"cryptoscan=3,xgrep=2"` -> {"cryptoscan": 3, "xgrep": 2}: how many containers of a worker run (started with
    `docker compose up -d --scale worker-<tool>=N`); the memory budget counts every replica."""
    out = {}
    for item in (raw or "").split(","):
        tool, _, n = item.strip().partition("=")
        if tool and n.strip().isdigit() and int(n) > 1:
            out[tool] = int(n)
    return out


def worker_image_ids(tools: list[str], env: str | None = None) -> dict[str, str]:
    """{tool: image id} of the worker images on the host, from EXPERIMENT_IMAGE_IDS as the Makefile's `experiment`
    target sets it (`repo:tag=id,...` from `docker images --no-trunc`): the images that run the jobs, by content."""
    raw = os.getenv("EXPERIMENT_IMAGE_IDS", "") if env is None else env
    by_tool = {}
    for item in raw.split(","):
        ref, _, image_id = item.strip().partition("=")
        name = ref.rsplit(":", 1)[0].rsplit("/", 1)[-1]  # bf-cbom/worker-cdxgen:1.0.3 -> worker-cdxgen
        if name.startswith("worker-") and image_id:
            by_tool[name[len("worker-") :]] = image_id
    return {t: by_tool[t] for t in tools if t in by_tool}


def _err(msg: str, code: int = 1):
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


class _Session:
    """One `experiment run` / `collect` invocation in <exp>/_run-log.json: wall times, the Docker VM's RAM and CPUs,
    the memory budget and waves, and the peak RAM in use on the VM while it ran (sampled with every poll)."""

    SAVE_EVERY_SEC = 60.0

    def __init__(self, exp_dir: Path, command: str, *, tools: list[str] = (), **fields):
        self.exp_dir = exp_dir
        self.host = HostSampler()
        self.entry = {
            "session": uuid.uuid4().hex[:12],
            "command": command,
            "started_at": now_iso(),
            "status": "running",
            "runner_image": os.getenv("BF_CBOM_IMAGE"),
            "runner_commit": os.getenv("BF_CBOM_COMMIT"),  # `-dirty` = built from uncommitted changes
            "images": worker_image_ids(list(tools)),
            "host": {
                "cpus": os.cpu_count(),
                "ram_total_mb": self.host.total_mb,
                "ram_used_at_start_mb": self.host.start_used_mb,
                "gitserver_memory": os.getenv("GITSERVER_MEMORY"),  # the git server's compose limit (Makefile)
            },
            **fields,
        }
        self._saved = 0.0
        self.save()

    def tick(self) -> None:
        self.host.sample()
        if time.monotonic() - self._saved >= self.SAVE_EVERY_SEC:
            self.save()

    def save(self, **fields) -> None:
        self.entry.update(fields)
        self.entry["ram_peak_used_mb"] = self.host.peak_used_mb
        self.entry["updated_at"] = now_iso()
        self._saved = time.monotonic()
        try:
            save_run_log_entry(self.exp_dir, self.entry)
        except OSError as e:  # bookkeeping must never stop a batch
            typer.secho(f"warning: cannot write the run log ({e})", fg=typer.colors.YELLOW, err=True)

    def finish(self, status: str, **fields) -> None:
        self.host.sample()
        self.save(status=status, finished_at=now_iso(), **fields)


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
    retry_failed: bool = typer.Option(
        False,
        "--retry-failed",
        help="Also redo stored runs whose status is not ok (an ok run with OOM kills counts as not ok)",
    ),
    rerun: str = typer.Option(
        "", "--rerun", help="Comma-separated tools whose stored runs are all redone (e.g. after a worker change)"
    ),
    overwrite_old: bool = typer.Option(
        False,
        "--overwrite-old",
        help="Run the whole spec afresh: remove every stored run first (also of tools and repos the spec no longer "
        "names) and the old _ANALYSIS/, cancel jobs an earlier run left pending; the pinned commits (lock.json) and "
        "the run log are kept",
    ),
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
    started_at = now_iso()  # the session includes pinning and mirroring
    try:
        spec = load_spec(spec_path)
    except (ExperimentError, OSError, yaml.YAMLError) as e:
        _err(f"invalid experiment spec: {e}", 2)
    exp_dir = experiment_dir(spec.name)
    exp_dir.mkdir(parents=True, exist_ok=True)
    write_spec_copy(exp_dir, spec)
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
    overwritten = 0
    if overwrite_old:  # only now: a spec or snapshot that fails to resolve leaves the stored runs untouched
        _cancel_pending(r, spec.name, pending)  # a late result of a cancelled job is ignored (`_collect`)
        overwritten = clear_stored_runs(exp_dir)
        typer.secho(
            f"--overwrite-old: removed {overwritten} stored run(s) and the old {ANALYSIS_DIR}/, cancelled "
            f"{len(pending)} pending job(s); commits stay pinned (lock.json)",
            fg=typer.colors.BLUE,
            err=True,
        )
        pending = {}
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
    rerun_tools = {t.strip() for t in rerun.split(",") if t.strip()}
    if unknown := rerun_tools - set(spec.tools):
        _err(f"--rerun names tools outside the spec: {', '.join(sorted(unknown))}", 2)
    jobs, skipped = plan_jobs(
        spec, snapshots, exp_dir, new_id=lambda: str(uuid.uuid4()), retry_failed=retry_failed, rerun=rerun_tools
    )
    covered = {(j.snapshot.full_name, j.snapshot.variant, j.tool, j.run_index) for j in pending.values()}
    jobs = [j for j in jobs if (j.snapshot.full_name, j.snapshot.variant, j.tool, j.run_index) not in covered]
    typer.secho(
        f"{skipped} run(s) already on disk, {len(pending)} still pending from an earlier run, {len(jobs)} to enqueue",
        fg=typer.colors.BLUE,
        err=True,
    )
    registry = load_tools()
    replicas = parse_replicas(os.getenv("EXPERIMENT_REPLICAS"))
    memory = {t: tool_memory_gb(t, registry) * replicas.get(t, 1) for t in spec.tools}
    if replicas:
        typer.secho(
            "replicas (EXPERIMENT_REPLICAS): " + ", ".join(f"{t} x{n}" for t, n in sorted(replicas.items())),
            fg=typer.colors.BLUE,
            err=True,
        )
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
        _print_overview(exp_dir, spec_file=_display_spec(spec_path))
        return
    session = _Session(
        exp_dir,
        "run",
        tools=active_tools,
        started_at=started_at,
        memory_budget_gb=memory_budget_gb,
        waves=waves,
        memory_gb={t: memory[t] for t in active_tools},
        replicas=replicas,
        jobs_enqueued=len(jobs),
        jobs_pending_before=len(pending),
        jobs_on_disk_before=skipped,
        runs_overwritten=overwritten,
    )
    outcome = "interrupted"
    try:
        _run_waves(r, spec, exp_dir, waves, jobs, pending, total, idle_timeout, no_wait, session,
                   spec_file=_display_spec(spec_path))  # fmt: skip
        outcome = "enqueued (--no-wait)" if no_wait else "completed"
    except typer.Exit as e:
        outcome = "stopped" if e.exit_code else "completed"
        raise
    finally:
        session.finish(outcome, results_stored=session.entry.get("results_stored", 0))
    if no_wait:
        return
    r.hset(_status_key(spec.name), mapping={"status": "completed", "finished_at": now_iso()})
    r.delete(_running_key(spec.name))
    _print_overview(exp_dir, outcome="completed", session=session.entry, spec_file=_display_spec(spec_path))


def _run_waves(
    r: redis.Redis,
    spec,
    exp_dir: Path,
    waves: list[list[str]],
    jobs: list[PlannedJob],
    pending: dict[str, PlannedJob],
    total: int,
    idle_timeout: int,
    no_wait: bool,
    session: _Session,
    *,
    spec_file: str | None = None,
) -> int:
    """Enqueue wave by wave and collect each before the next; returns the number of results stored."""
    done = 0
    issued = 0
    for i, wave in enumerate(waves, 1):
        wave_jobs = [j for j in jobs if j.tool in wave]
        pipe = r.pipeline()
        for job in wave_jobs:
            job.enqueued_at = now_iso()
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
            _print_overview(exp_dir, outcome="enqueued (--no-wait)", spec_file=spec_file)
            return done
        session.save(wave=f"{i}/{len(waves)}")
        done = _collect(
            r, spec.name, spec.tools, exp_dir, wave_pending, idle_timeout, done=done, total=total, session=session
        )
    return done


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
    session: _Session | None = None,
) -> int:
    """Persist results until `pending` is empty; returns the cumulative count of stored results."""
    inbox = f"results:{_route_key(name)}"
    last_progress = time.monotonic()
    total = total if total is not None else done + len(pending)
    warned_dead: set[str] = set()
    redis_failures = 0
    while pending:
        if session is not None:
            session.tick()
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
            if session is not None:
                session.entry["results_stored"] = session.entry.get("results_stored", 0) + 1
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


def _print_overview(exp_dir: Path, **kwargs) -> None:
    """The human-readable overview (misc/cli/overview.py) on stdout; progress lines go to stderr."""
    for line in overview.render(overview.build(exp_dir), **kwargs):
        typer.echo(line)


def _display_spec(path: Path) -> str:
    """The spec path as the user typed it on the host: the runner sees `/app/experiments/<x>.yaml`."""
    return f"experiments/{path.name}" if path.parent.name == "experiments" else str(path)


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
        typer.secho("nothing pending in Redis", fg=typer.colors.BLUE, err=True)
        _print_overview(exp_dir)
        return
    stored = int(r.hget(_status_key(name), "stored_jobs") or 0)
    session = _Session(exp_dir, "collect", tools=tools, jobs_pending_before=len(pending))
    outcome = "interrupted"
    try:
        _collect(
            r, name, tools, exp_dir, pending, idle_timeout, done=stored, total=stored + len(pending), session=session
        )
        outcome = "completed"
    except typer.Exit as e:
        outcome = "stopped" if e.exit_code else "completed"
        raise
    finally:
        session.finish(outcome, results_stored=session.entry.get("results_stored", 0))
    r.hset(_status_key(name), mapping={"status": "completed", "finished_at": now_iso()})
    wave = r.hget(_status_key(name), "wave") or ""
    if "/" in wave and wave.split("/")[0] != wave.split("/")[1]:
        msg = f"wave {wave} collected; rerun `experiment run` to issue the next wave"
        typer.secho(msg, fg=typer.colors.YELLOW, err=True)
    _print_overview(exp_dir, outcome="collected", session=session.entry)


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
    exp_dir = experiment_dir(name)
    if not exp_dir.exists():
        _err(f"no experiment '{name}' under {RESULTS_DIR}")
    r = _redis(redis_host, redis_port)
    meta = r.hgetall(_status_key(name))
    live = None
    if meta:
        pending = {jid: json.loads(d) for jid, d in r.hgetall(_pending_key(name)).items()}
        running = {jid: ts for jid, ts in r.hgetall(_running_key(name)).items() if jid in pending}
        live = {
            "status": meta.get("status"),
            "wave": meta.get("wave"),
            "updated_at": meta.get("updated_at"),
            "queued": len(pending) - len(running),
            "running": sorted(
                (
                    {"tool": pending[jid]["tool"], "repo": pending[jid]["snapshot"]["full_name"], "started_at": ts}
                    for jid, ts in running.items()
                ),
                key=lambda j: (j["started_at"] or "", j["tool"]),
            ),
        }
    _print_overview(exp_dir, live=live)


@experiment_app.command("list")
def list_experiments():
    """Experiments present on the results volume."""
    if not RESULTS_DIR.exists():
        typer.echo(f"(no results yet under {RESULTS_DIR})")
        return
    folders = sorted(p for p in RESULTS_DIR.iterdir() if p.is_dir() and p.name != "corpus")
    experiments = [overview.build(d) for d in folders if is_experiment(d)]
    if experiments:
        for line in overview.list_rows(experiments):
            typer.echo(line)
    else:
        typer.echo(f"(no experiments under {RESULTS_DIR})")
    stale = [d for d in folders if not is_experiment(d)]
    if stale:
        typer.echo(f"\nno {SPEC_FILE} (older layout, not read by any command; rerun, then delete):")
        for d in stale:
            typer.echo(f"  {d.name:<28} make experiment-delete NAME={d.name}")


@experiment_app.command("export")
def export(
    name: str = NAME_ARG,
    dest: Path = DEST_OPT,
    no_report: bool = typer.Option(False, "--no-report", help="Zip as stored, without running `analysis report`"),
):
    """Zip one experiment (spec, run log, lock, all CBOMs and meta files, _ANALYSIS/ with _SUMMARY.md). The only way
    data leaves the volume. Runs `analysis report` first, so the CSV tables and _SUMMARY.md in the zip are current."""
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
    max_repo_size_mb: int | None = typer.Option(
        None,
        "--max-repo-size-mb",
        help="Largest packed repo size (GitHub `size`) sampled; default CORPUS_MAX_REPO_SIZE_MB, else "
        f"GITSERVER_MEMORY / {corpus_mod.SIZE_CAP_RATIO} (the git server must serve a full clone); 0 = no cap",
    ),
):
    """Sample a stratified corpus (stars x size cells per language), pre-filter for crypto usage, pin commits."""
    params = corpus_mod.CorpusParams(
        languages=[x.strip() for x in languages.split(",") if x.strip()],
        per_language=per_language,
        pushed_within_days=pushed_within_days,
        crypto_filter=not no_crypto_filter,
        seed=seed,
    )
    if max_repo_size_mb is not None:
        params.max_repo_size_mb = max_repo_size_mb or None
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
    typer.echo(f"excluded with reasons: {len(corpus['excluded'])} (listed under `excluded` in the file)")


PRUNE_IN_OPT = typer.Option(..., "--in", help="Corpus YAML to prune")
PRUNE_FROM_OPT = typer.Option(..., "--from", help="Experiment whose timed-out runs drop a repo")
PRUNE_OUT_OPT = typer.Option(None, "--out", "-o", help="Output YAML (default: <in>-pruned.yaml next to it)")
PRUNE_STATUSES_OPT = typer.Option("timeout", help="Comma-separated run statuses that drop a repo")


@corpus_app.command("prune")
def corpus_prune(
    source: Path = PRUNE_IN_OPT,
    from_experiment: str = PRUNE_FROM_OPT,
    out: Path = PRUNE_OUT_OPT,
    statuses: str = PRUNE_STATUSES_OPT,
):
    """Drop the repos where a tool timed out in an earlier experiment (E0; CLAUDE.md refinement 11), with reasons."""
    exp_dir = experiment_dir(from_experiment)
    if not is_experiment(exp_dir):
        _err(f"no experiment '{from_experiment}' under {RESULTS_DIR}")
    corpus = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    kinds = tuple(k.strip() for k in statuses.split(",") if k.strip())
    pruned = corpus_mod.prune_corpus(corpus, exp_dir, statuses=kinds)
    target = out or source.with_name(source.stem + "-pruned.yaml")
    target.write_text(yaml.safe_dump(pruned, sort_keys=False), encoding="utf-8")
    typer.echo(f"kept {len(pruned['repos'])} repos, pruned {len(pruned['pruned'])}: {target}")
