"""`bf-cbom experiment ...`: batch runs whose results live on the results-data volume.

The runner enqueues JobInstructions on the normal worker queues and registers each job id in
the coordinator's routing hash under `exp:<name>`. Whoever drains `results:<tool>` (this
runner or the Streamlit UI) forwards such results to `results:exp:<name>`, from where this
command persists them under RESULTS_DIR/<name>/... (see common/experiment.py).
"""

from __future__ import annotations

import json
import os
import time
import uuid
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import redis
import typer
import yaml

from common import corpus as corpus_mod
from common.experiment import (
    GIT_MIRROR_DIR,
    GITSERVER_URL,
    ORIGINAL_VARIANT,
    RESULTS_DIR,
    SPEC_FILE,
    ExperimentError,
    PlannedJob,
    Snapshot,
    count_unsupported,
    expected_jobs,
    experiment_dir,
    is_experiment,
    load_spec,
    mark_unsupported,
    plan_jobs,
    plan_waves,
    remove_experiment,
    resolve_snapshots,
    save_run_log_entry,
    smallest_clean_corpus,
    write_result,
)
from common.memory import HostSampler
from common.models import JobResult
from common.tools_registry import load_tools, tool_memory_gb
from common.worker import live_replicas
from coordinator.redis_io import (
    EXPERIMENT_ROUTE_PREFIX,
    JOB_ROUTING_KEY,
    dead_workers,
    ingest_worker_results,
    now_iso,
)
from misc.cli import overview, progress
from misc.cli.stack_cmd import parse_builds

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


def _tools_key(name: str) -> str:
    # `<tool>:n` and `<tool>:sec`: the results stored this session per tool and their summed durations, for the
    # remaining time (progress.forecast) of the stack board, next to `tool_jobs` in the status hash
    return f"exp:{name}:tools"


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


def image_ids(env: str | None = None) -> dict[str, str]:
    """{image name: image id} of the BF-CBOM images on the host, from EXPERIMENT_IMAGE_IDS as the Makefile's
    `experiment` target sets it (`repo:tag=id,...` from `docker images --no-trunc`): `worker-cdxgen`, `gitserver`."""
    raw = os.getenv("EXPERIMENT_IMAGE_IDS", "") if env is None else env
    out = {}
    for item in raw.split(","):
        ref, _, image_id = item.strip().partition("=")
        name = ref.rsplit(":", 1)[0].rsplit("/", 1)[-1]  # bf-cbom/worker-cdxgen:1.0.3 -> worker-cdxgen
        if name and image_id:
            out[name] = image_id
    return out


def worker_image_ids(tools: list[str], env: str | None = None) -> dict[str, str]:
    """{tool: image id} of the worker images on the host (`image_ids`): the images tagged for the jobs, by content.
    The containers may still run older ones; `EXPERIMENT_BUILDS` says what they were built from."""
    ids = image_ids(env)
    return {t: ids[f"worker-{t}"] for t in tools if f"worker-{t}" in ids}


def foreign_builds(tools: list[str], builds: dict[str, list[str]], own: str | None) -> dict[str, list[str]]:
    """{service: commits} of the spec's workers and the git server whose running containers were built from another
    commit than the runner (`own`; EXPERIMENT_BUILDS as `misc/cli/stack_cmd.py:parse_builds` reads it); none
    without the runner's commit. A worker without a running container has nothing to compare."""
    if not own:
        return {}
    services = ["gitserver", *(f"worker-{t}" for t in tools)]
    return {s: c for s in services if (c := [x for x in builds.get(s, []) if x != own])}


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
            "gitserver_image": image_ids().get("gitserver"),
            # what the running containers were built from (make reads each container's BF_CBOM_COMMIT)
            "builds": parse_builds(os.getenv("EXPERIMENT_BUILDS", "")),
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


def _where(job: PlannedJob) -> str:
    variant = job.snapshot.variant
    return job.snapshot.full_name + ("" if variant == ORIGINAL_VARIANT else f"@{variant}")


def _running_for(started_at: str) -> float | None:
    """Seconds since a worker's `started_at` (UTC ISO, common/jobs.py:now_iso)."""
    try:
        return time.time() - datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class _Board:
    """The live view of a batch on a terminal (misc/cli/progress.py): one line for all results, with the wave and the
    statuses so far, then one per tool with its count, the job it runs now and for how long, what is still queued and
    what failed, and the remaining time per tool and for the batch (progress.forecast, from the durations so far).
    The per-result lines keep scrolling above it; off a terminal they are all there is."""

    def __init__(self, jobs: list[PlannedJob], *, stored: int = 0, waves: list[list[str]] | None = None):
        per_tool = Counter(j.tool for j in jobs)
        self.per_tool = per_tool
        self.waves = waves or [sorted(per_tool)]
        self.wave_label = ""
        self.statuses: Counter = Counter()
        self.done: Counter = Counter()
        self.seconds: Counter = Counter()
        self.failed: dict[str, Counter] = defaultdict(Counter)
        self.started: set[str] = set()
        self.all = progress.line("results", total=stored + len(jobs), done=stored)
        tools = [t for wave in self.waves for t in wave if per_tool[t]]
        self.lines = {t: progress.line(f"  {t}", total=per_tool[t], start=False) for t in tools}

    def wave(self, i: int) -> None:
        """Wave `i` (from 1) is enqueued: its tools' clocks start."""
        if len(self.waves) > 1:
            self.wave_label = f"wave {i}/{len(self.waves)}"
        for t in self.waves[i - 1]:
            progress.begin(self.lines.get(t))
            self.started.add(t)
        self._summary()

    def stored(self, job: PlannedJob, status: str, done: int, duration: float | None = None) -> None:
        self.statuses[status] += 1
        self.done[job.tool] += 1
        self.seconds[job.tool] += duration or 0.0
        if status != "ok":
            self.failed[job.tool][status] += 1
        progress.update(self.all, done=done)
        progress.update(self.lines.get(job.tool), done=self.done[job.tool])
        self._summary()

    def poll(self, r: redis.Redis, name: str, pending: dict[str, PlannedJob]) -> None:
        """Name what every tool runs now (the start times its workers write to Redis), what waits and what failed."""
        if not progress.live():
            return
        try:
            running = r.hgetall(_running_key(name))
            slots = live_replicas(r, list(self.lines))  # the containers alive now share a tool's queue
        except redis.exceptions.RedisError:
            return
        busy: dict[str, list[tuple[float, PlannedJob]]] = defaultdict(list)
        queued: Counter = Counter()
        for jid, job in pending.items():
            secs = _running_for(running[jid]) if jid in running else None
            if secs is None:
                queued[job.tool] += 1
            else:
                busy[job.tool].append((secs, job))
        for i, wave in enumerate(self.waves, 1):
            for t in wave:
                if t not in self.lines:
                    continue
                parts = []
                if busy[t]:  # the longest-running job first: that is the one that might be stuck
                    secs, job = max(busy[t], key=lambda x: x[0])
                    parts.append(f"{_where(job)} · run{job.run_index} · {progress.clock(secs)}")
                    if len(busy[t]) > 1:
                        parts.append(f"+{len(busy[t]) - 1} running")
                if queued[t]:
                    parts.append(f"{progress.count(queued[t])} queued")
                if t not in self.started:
                    parts.append(f"wave {i}")
                parts += [f"{progress.count(n)} {s}" for s, n in sorted(self.failed[t].items())]
                progress.update(self.lines[t], item=" · ".join(parts))
        left = {t: n - self.done[t] for t, n in self.per_tool.items()}
        mean = {t: self.seconds[t] / n for t, n in self.done.items() if n}
        elapsed = {t: [secs for secs, _ in jobs] for t, jobs in busy.items()}
        for t, task in self.lines.items():
            fc = progress.forecast(left, mean, elapsed, [[t]], slots=slots)
            progress.update(task, eta=progress.remaining(fc))
        fc = progress.forecast(left, mean, elapsed, self.waves, slots=slots)
        progress.update(self.all, eta=progress.remaining(fc))

    def _summary(self) -> None:
        statuses = sorted(self.statuses, key=lambda s: (s != "ok", s))
        parts = [self.wave_label] if self.wave_label else []
        parts += [f"{progress.count(self.statuses[s])} {s}" for s in statuses]
        progress.update(self.all, item=" · ".join(parts))


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
        help="Run the whole spec afresh, as if it had never run: the experiment folder (every stored run, the record, "
        "the run log, mutants.json, _ANALYSIS/) is removed and the jobs an earlier run left are cancelled first",
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
    exp_dir = experiment_dir(spec.name)  # created with its record once the snapshots resolve
    typer.secho(
        f"experiment {spec.name}: {len(spec.repos)} repos x {spec.tools} x {spec.repetitions} rep(s)",
        fg=typer.colors.BLUE,
        err=True,
    )
    own = os.getenv("BF_CBOM_COMMIT")
    if foreign := foreign_builds(spec.tools, parse_builds(os.getenv("EXPERIMENT_BUILDS", "")), own):
        listed = ", ".join(f"{s} {', '.join(c)}" for s, c in sorted(foreign.items()))
        _err(f"built from another commit than the runner ({own}): {listed}. Their runs would measure other code; "
             "rebuild with `make up-prod` (`make board` shows each container's build)", 2)  # fmt: skip
    r = _redis(redis_host, redis_port)
    overwritten = 0
    if overwrite_old:  # nothing of a former run is left: the spec alone decides what runs (its pinned commits)
        cancelled = _forget(r, spec.name)
        overwritten = remove_experiment(exp_dir)
        typer.secho(
            f"--overwrite-old: removed the experiment folder ({overwritten} stored run(s)) and cancelled {cancelled} "
            "pending job(s)",
            fg=typer.colors.BLUE,
            err=True,
        )

    typer.secho("resolving snapshots and mirroring to the git server...", fg=typer.colors.BLUE, err=True)
    try:
        with progress.session():
            snapshots = resolve_snapshots(
                spec,
                exp_dir,
                log=lambda m: typer.echo(m, err=True),
                each=lambda repos: progress.track(repos, "pin and mirror", item=lambda repo: repo.full_name),
            )
    except ExperimentError as e:
        _err(str(e), 2)

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
    rerun_tools = {t.strip() for t in rerun.split(",") if t.strip()}
    if unknown := rerun_tools - set(spec.tools):
        _err(f"--rerun names tools outside the spec: {', '.join(sorted(unknown))}", 2)
    jobs, skipped = plan_jobs(
        spec, snapshots, exp_dir, new_id=lambda: str(uuid.uuid4()), retry_failed=retry_failed, rerun=rerun_tools
    )
    covered = {(j.snapshot.full_name, j.snapshot.variant, j.tool, j.run_index) for j in pending.values()}
    jobs = [j for j in jobs if (j.snapshot.full_name, j.snapshot.variant, j.tool, j.run_index) not in covered]
    # `skipped` holds the unsupported markers too (the board counts them as done); the text counts them apart
    unsupported_runs = count_unsupported(spec, snapshots, exp_dir)
    typer.secho(
        f"{skipped - unsupported_runs} run(s) already on disk, {unsupported_runs} unsupported (not run), "
        f"{len(pending)} still pending from an earlier run, {len(jobs)} to enqueue",
        fg=typer.colors.BLUE,
        err=True,
    )
    registry = load_tools()
    # replicas are chosen at `make up-prod REPLICAS=...`; their heartbeats say how many containers each tool has
    replicas = {t: n for t, n in live_replicas(r, spec.tools).items() if n > 1}
    memory = {t: tool_memory_gb(t, registry) * replicas.get(t, 1) for t in spec.tools}
    if replicas:
        typer.secho(
            "replicas: " + ", ".join(f"{t} x{n}" for t, n in sorted(replicas.items())), fg=typer.colors.BLUE, err=True
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
    r.delete(_tools_key(spec.name))
    r.hset(
        _status_key(spec.name),
        mapping={
            "status": "running" if total else "completed",
            "started_at": now_iso(),
            "expected_jobs": str(expected_jobs(spec, exp_dir)),
            "tool_jobs": json.dumps(Counter(j.tool for j in [*jobs, *pending.values()])),  # this session's
            "issued_jobs": "0",
            "failed_jobs": "0",  # stored this session with a status other than ok (the stack board shows it)
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
        jobs_on_disk_before=skipped - unsupported_runs,
        unsupported=unsupported_runs,
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


@progress.session()
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
    board = None if no_wait else _Board([*pending.values(), *jobs], waves=waves)
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
        board.wave(i)
        done = _collect(r, spec.name, spec.tools, exp_dir, wave_pending, idle_timeout, done=done, total=total,
                        session=session, board=board)  # fmt: skip
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
    board: _Board | None = None,
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
            if result.status != "ok":
                r.hincrby(_status_key(name), "failed_jobs", 1)
            r.hincrby(_tools_key(name), f"{job.tool}:n", 1)
            r.hincrbyfloat(_tools_key(name), f"{job.tool}:sec", result.duration_sec or 0.0)
            colour = typer.colors.GREEN if result.status == "ok" else typer.colors.RED
            typer.secho(
                f"[{done}/{total}] {result.status:<7} {job.tool:<15} {job.snapshot.full_name}@{job.snapshot.variant} "
                f"run{job.run_index} {result.duration_sec or 0:.0f}s -> {path.relative_to(exp_dir)}",
                fg=colour,
                err=True,
            )
            if board is not None:
                board.stored(job, result.status, done, result.duration_sec)
        if board is not None:
            board.poll(r, name, pending)
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


def _forget(r: redis.Redis, name: str) -> int:
    """Everything Redis holds for an experiment goes: its queued jobs leave the worker queues (a job a worker already
    took finishes, and its result, no longer routed to the experiment, is dropped), the pending, running and status
    entries and any result not collected yet. Returns the number of pending jobs."""
    pending = {jid: _job_from_descriptor(jid, json.loads(d)) for jid, d in r.hgetall(_pending_key(name)).items()}
    _cancel_pending(r, name, pending)
    r.delete(_status_key(name), _pending_key(name), _running_key(name), _tools_key(name), f"results:{_route_key(name)}")
    return len(pending)


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
        with progress.session():
            board = _Board(list(pending.values()), stored=stored)
            board.wave(1)
            _collect(r, name, tools, exp_dir, pending, idle_timeout, done=stored, total=stored + len(pending),
                     session=session, board=board)  # fmt: skip
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
    name: str | None = typer.Argument(None, help="Experiment name (omit with --all)"),
    dest: Path = DEST_OPT,
    all_: bool = typer.Option(False, "--all", help="Every complete experiment (no run missing); overwrites the ZIPs"),
    no_report: bool = typer.Option(False, "--no-report", help="Zip as stored, without running `analysis report`"),
    jobs: int = typer.Option(0, "--jobs", envvar="BF_CBOM_JOBS", help="Worker processes of the report (0 = auto)"),
):
    """Zip one experiment (its record, run log, all CBOMs and meta files, _ANALYSIS/ with _SUMMARY.md). The only way
    data leaves the volume. Runs `analysis report` first, so the CSV tables and _SUMMARY.md in the zip are current.
    With `--all`, every experiment whose runs are all stored, one after the other; an incomplete one is skipped."""
    if all_ == bool(name):
        _err("name one experiment or pass --all")
    if not all_:
        exp_dir = experiment_dir(name)
        if not exp_dir.exists():
            _err(f"no experiment '{name}' under {RESULTS_DIR}")
        _export_one(exp_dir, dest, no_report=no_report, jobs=jobs)
        return
    folders = sorted(p for p in RESULTS_DIR.glob("*") if p.is_dir() and is_experiment(p))
    for exp_dir in folders:
        missing = overview.build(exp_dir).missing
        if missing != 0:
            typer.echo(f"skipped {exp_dir.name}: " + ("no expected run count" if missing is None else
                                                      f"{missing} run(s) missing"))  # fmt: skip
            continue
        _export_one(exp_dir, dest, no_report=no_report, jobs=jobs)
    if not folders:
        typer.echo(f"(no experiments under {RESULTS_DIR})")


def _export_one(exp_dir: Path, dest: Path, *, no_report: bool, jobs: int) -> None:
    name = exp_dir.name
    with progress.session():
        if not no_report:
            from misc.cli.analysis_cmd import run_report  # local import: the analysis is not needed for the runs

            try:
                run_report(exp_dir, jobs=jobs)
            except Exception as e:  # incl. typer.Exit; an export must never fail because the analysis did
                typer.secho(
                    f"analysis report failed ({progress.reason(e)}); exporting the stored files only",
                    fg=typer.colors.YELLOW,
                )
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{name}.zip"
        files = [p for p in sorted(exp_dir.rglob("*")) if p.is_file()]
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in progress.track(files, "zip", item=lambda p: str(p.relative_to(exp_dir))):
                zf.write(path, arcname=str(Path(name) / path.relative_to(exp_dir)))
    typer.echo(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


@experiment_app.command("delete")
def delete(
    name: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", help="Do not ask"),
    redis_host: str = REDIS_HOST_OPT,
    redis_port: int = REDIS_PORT_OPT,
):
    """Delete an experiment: its folder on the volume and everything Redis holds for it (pending jobs cancelled)."""
    exp_dir = experiment_dir(name)
    if not exp_dir.exists():
        _err(f"no experiment '{name}' under {RESULTS_DIR}")
    if not yes and not typer.confirm(f"delete {exp_dir} with all stored CBOMs?"):
        raise typer.Exit(0)
    cancelled = _forget(_redis(redis_host, redis_port), name)
    remove_experiment(exp_dir)
    typer.echo(f"deleted {exp_dir}" + (f", cancelled {cancelled} pending job(s)" if cancelled else ""))


@experiment_app.command("env")
def env():
    """Show where the runner reads and writes."""
    typer.echo(
        json.dumps(
            {"RESULTS_DIR": str(RESULTS_DIR), "GIT_MIRROR_DIR": str(GIT_MIRROR_DIR), "GITSERVER_URL": GITSERVER_URL},
            indent=2,
        )
    )


FRAME_OPT = typer.Option(
    None,
    "--frame",
    help="Reuse a frame file (`<corpus>-frame.yaml` of an earlier build): no search, its search date stands; same "
    "languages, bins and cap; crypto checks of unchanged commits are reused",
)


@corpus_app.command("build")
def corpus_build(
    out: Path = CORPUS_OUT_OPT,
    languages: str = typer.Option("Java,Python,Go", help="Comma-separated GitHub languages (the paper's three)"),
    per_cell: int = typer.Option(2, "--per-cell", help="Repos drawn per (language, stars, size) cell"),
    star_bins: str = typer.Option(
        "50-300,300-2000,2000-", "--star-bins", help="Star bins `lo-hi,...` ([lo, hi); an open last bin `2000-`)"
    ),
    size_bins: str = typer.Option(
        "300-3000,3000-30000,30000-",
        "--size-bins",
        help="Size bins in KB (GitHub `size`, the packed repository) `lo-hi,...`; an open last bin ends at the cap",
    ),
    pushed_within_days: int = typer.Option(365),
    no_crypto_filter: bool = typer.Option(False, "--no-crypto-filter", help="Skip the clone+grep crypto pre-filter"),
    exclude: str = typer.Option("", help="Extra comma-separated regexes on full_name to exclude"),
    seed: int = typer.Option(20261015),
    max_repo_size_mb: int | None = typer.Option(
        None,
        "--max-repo-size-mb",
        help="Largest packed repo size (GitHub `size`) sampled; default CORPUS_MAX_REPO_SIZE_MB, else "
        f"{corpus_mod.DEFAULT_MAX_REPO_SIZE_MB} (common/corpus.py); 0 = no cap",
    ),
    frame_in: Path | None = FRAME_OPT,
):
    """Sample a stratified corpus: per language every (star bin x size bin) cell is enumerated completely and
    `--per-cell` repos are drawn at random (seeded), pre-filtered for first-party crypto usage and pinned to commits;
    the frame (every cell's population) is recorded in the file."""
    try:
        params = corpus_mod.CorpusParams(
            languages=[x.strip() for x in languages.split(",") if x.strip()],
            per_cell=per_cell,
            star_bins=corpus_mod.parse_bins(star_bins),
            size_bins_kb=corpus_mod.parse_bins(size_bins),
            pushed_within_days=pushed_within_days,
            crypto_filter=not no_crypto_filter,
            seed=seed,
        )
    except ValueError as e:
        _err(f"bins: {e}")
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
    frame_out = out.with_name(f"{out.stem}-frame.yaml")
    frame: dict = {}
    if frame_in is not None and not frame_in.exists():
        _err(f"no frame file {frame_in}")
    with progress.session():
        if frame_in is not None:
            with progress.step(f"Reading the frame ({frame_in.stat().st_size / 1e6:.0f} MB)"):
                frame = corpus_mod.read_frame(frame_in)
        with progress.step("GitHub API check"):
            try:
                typer.echo(corpus_mod.github_check(token), err=True)
            except RuntimeError as e:
                _err(str(e))
        lines = {lang: progress.line(lang, total=params.per_language, start=False) for lang in params.languages}
        begun: set[str] = set()

        def status(language: str, picked: int, now: str | None) -> None:
            if language not in begun:
                begun.add(language)
                progress.begin(lines[language])
            progress.update(lines[language], done=picked, item=now or "")
            if now is None:
                progress.end(lines[language])

        try:
            corpus = corpus_mod.build_corpus(
                params, token, log=lambda m: typer.echo(m, err=True), status=status, frame=frame
            )
        finally:  # an interrupted build keeps what it searched and checked
            if frame.get("cells") or frame.get("crypto_checks"):
                with progress.step("Writing the frame"):
                    corpus_mod.write_frame(frame, frame_out)
    corpus["frame_file"] = frame_out.name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(corpus, sort_keys=False), encoding="utf-8")
    typer.echo(f"wrote {len(corpus['repos'])} repos to {out} (stats: {corpus['stats']})")
    typer.echo(f"excluded with reasons: {len(corpus['excluded'])} (listed under `excluded` in the file)")
    typer.echo(f"frame (every cell's population, the crypto checks): {frame_out}")


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
    """Drop the repos where a tool timed out in an earlier experiment (the stability run; CLAUDE.md refinement 11),
    with reasons."""
    exp_dir = experiment_dir(from_experiment)
    if not is_experiment(exp_dir):
        _err(f"no experiment '{from_experiment}' under {RESULTS_DIR}")
    corpus = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    kinds = tuple(k.strip() for k in statuses.split(",") if k.strip())
    pruned = corpus_mod.prune_corpus(corpus, exp_dir, statuses=kinds)
    target = out or source.with_name(source.stem + "-pruned.yaml")
    target.write_text(yaml.safe_dump(pruned, sort_keys=False), encoding="utf-8")
    typer.echo(f"kept {len(pruned['repos'])} repos, pruned {len(pruned['pruned'])}: {target}")


SMALLEST_FROM_OPT = typer.Option(..., "--from", help="Corpus experiment whose record and runs the selection reads")
SMALLEST_PER_LANGUAGE_OPT = typer.Option(30, "--per-language", help="Repositories per language")
SMALLEST_VARIANTS_OPT = typer.Option(
    31, "--variants", help="Runs per repository in the mutation experiment, for the projection (1 + calls + decoys)"
)
SMALLEST_OUT_OPT = typer.Option(..., "--out", "-o", help="Output corpus YAML")


@corpus_app.command("smallest")
def corpus_smallest(
    from_experiment: str = SMALLEST_FROM_OPT,
    per_language: int = SMALLEST_PER_LANGUAGE_OPT,
    variants: int = SMALLEST_VARIANTS_OPT,
    out: Path = SMALLEST_OUT_OPT,
):
    """The corpus of a mutation experiment: per language the smallest repositories (source size) of a corpus
    experiment among those where every supported tool's run was ok, at their pinned commits, and the projected
    length of the mutation run over every repository and over the selection (mutation-main, decided 2026-10-31)."""
    exp_dir = experiment_dir(from_experiment)
    if not is_experiment(exp_dir):
        _err(f"no experiment '{from_experiment}' under {RESULTS_DIR}")
    corpus = smallest_clean_corpus(exp_dir, per_language=per_language, variants=variants)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(corpus, sort_keys=False), encoding="utf-8")
    counts = Counter(r.get("language") for r in corpus["repos"])
    typer.echo(f"wrote {len(corpus['repos'])} repos ({', '.join(f'{k} {v}' for k, v in counts.items())}) to {out}")
    typer.echo(f"excluded {len(corpus['excluded'])} repos with a failed run (listed under `excluded` in the file)")
    hours = corpus["projection"]["hours"]
    for label, i in (("every repository", 0), ("the selection", 1)):
        slowest = max(hours, key=lambda t: hours[t][i])
        total = sum(h[i] for h in hours.values())
        typer.echo(
            f"projected with {variants} runs per repository, one container per tool, {label}: slowest lane "
            f"{slowest} {hours[slowest][i]:.1f} h ({hours[slowest][i] / 24:.1f} days), all tools {total:.1f} tool-hours"
        )
