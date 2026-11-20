import functools
import json
import logging
import multiprocessing
import os
import signal
import socket
import time
import traceback
from collections.abc import Callable
from multiprocessing.connection import Connection

import redis

from .config import BLPOP_TIMEOUT_SEC, REDIS_HOST, REDIS_PORT, REDIS_SOCKET_TIMEOUT_SEC
from .jobs import job_is_routed, mark_job_running
from .memory import JobSampler, limit_bytes, mib, working_set_bytes
from .models import JobInstruction, JobResult, Trace
from .utils import normalize_json

# Seconds to wait for a job process to exit after SIGTERM before SIGKILL.
_KILL_GRACE_SEC = 5.0
# How often the parent wakes up while a job runs (used for heartbeats via on_tick).
_TICK_SEC = 1.0
# Attempts to push a result to Redis before giving up on it.
_PUSH_ATTEMPTS = 6
# Liveness key TTL; refreshed every BLPOP timeout while idle and every tick while busy.
HEARTBEAT_TTL_SEC = max(30, BLPOP_TIMEOUT_SEC * 3)
# A worker that cannot reach Redis for this long exits (non-zero), so the container's restart policy brings a fresh
# process: a replaced redis container or a recreated network leaves a long-running process with a dead name
# resolution and no heartbeat, which the board shows as `down` (misc-pyqun, 2026-10-29).
REDIS_GIVE_UP_SEC = int(os.getenv("WORKER_REDIS_GIVE_UP_SEC", "300"))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def heartbeat_key(worker_name: str) -> str:
    return f"worker:{worker_name}:alive"


def replica_key(worker_name: str, host: str | None = None) -> str:
    """One container's own heartbeat: replicas of a worker (`make up-prod REPLICAS=...`) share `heartbeat_key`, which
    the last writer wins, so each also writes this key under its hostname (unique per container)."""
    return f"worker:{worker_name}:replica:{host or socket.gethostname()}"


def live_replicas(r: redis.Redis, workers: list[str]) -> dict[str, int]:
    """How many containers of each worker are alive: its replica heartbeats; 1 for a worker that only writes the shared
    key (an image from before the replica keys); 0 for one that is down."""
    out = {}
    for w in workers:
        n = sum(1 for _ in r.scan_iter(match=replica_key(w, "*"), count=500))
        out[w] = n or int(bool(r.exists(heartbeat_key(w))))
    return out


def publish_heartbeat(
    r: redis.Redis,
    worker_name: str,
    state: str,
    job_id: str | None = None,
    *,
    job: dict | None = None,
    last: dict | None = None,
) -> None:
    """Best-effort liveness marker read by the coordinator and the stack board (missing key == worker down): the
    state, the job it runs (`job`: repo, run, started), the container's memory and the previous job's result; once
    under the worker's shared key and once under this container's replica key."""
    payload = json.dumps(
        {
            "state": state,
            "job_id": job_id,
            "ts": time.time(),
            "job": job,
            "mem_mb": mib(working_set_bytes()),
            "mem_limit_mb": mib(limit_bytes()),
            "last": last,
        }
    )
    try:
        pipe = r.pipeline()
        pipe.set(heartbeat_key(worker_name), payload, ex=HEARTBEAT_TTL_SEC)
        pipe.set(replica_key(worker_name), payload, ex=HEARTBEAT_TTL_SEC)
        pipe.execute()
    except redis.exceptions.RedisError:
        pass


def redis_error(log: logging.Logger, err: Exception, since: float | None, *, now: float | None = None) -> float:
    """A Redis error while waiting for jobs: log it, back off, and give up after REDIS_GIVE_UP_SEC of consecutive
    failures (`since`: when they began, None on the first), exiting so the container restarts with a fresh process.
    Returns `since` for the next call; the caller resets it to None after a success."""
    now = time.monotonic() if now is None else now
    since = now if since is None else since
    if now - since >= REDIS_GIVE_UP_SEC:
        log.error("no Redis for %d s (%s): exiting so the container restarts", int(now - since), err)
        raise SystemExit(1)
    log.error("Redis error while waiting for jobs: %s", err)
    time.sleep(1)
    return since


def _job_process_entry(conn: Connection, handle_instruction: Callable, instr: JobInstruction, worker_name: str) -> None:
    """Child-process body: run the handler and send the JobResult JSON back over the pipe."""
    # Own process group, so a timeout can kill grandchildren too (git, codeql, cdxgen, ...).
    try:
        os.setsid()
    except OSError:
        pass
    try:
        payload = handle_instruction(instr).to_json()
    except BaseException:
        payload = JobResult(
            job_id=instr.job_id,
            status="error",
            repo_info=instr.repo_info,
            json="{}",
            size_bytes=0,
            worker=worker_name,
            error=traceback.format_exc(limit=8),
        ).to_json()
    try:
        conn.send(payload)
    finally:
        conn.close()


def _kill_process_group(proc: multiprocessing.process.BaseProcess, log: logging.Logger) -> None:
    """Terminate a job process and everything it spawned (it runs in its own process group)."""
    pid = proc.pid
    if pid is None:
        return
    for sig, wait in ((signal.SIGTERM, _KILL_GRACE_SEC), (signal.SIGKILL, 2.0)):
        if not proc.is_alive():
            return
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            # Not our process group (setsid failed); fall back to the process itself.
            proc.terminate() if sig == signal.SIGTERM else proc.kill()
        proc.join(wait)
    if proc.is_alive():
        log.error("Job process %s survived SIGKILL", pid)


def run_job_with_timeout(
    handle_instruction: Callable[[JobInstruction], JobResult],
    instr: JobInstruction,
    timeout_sec: float,
    *,
    worker_name: str,
    log: logging.Logger | None = None,
    on_tick: Callable[[], None] | None = None,
) -> JobResult:
    """Run `handle_instruction(instr)` in a forked subprocess with a hard deadline.

    On timeout the whole process group is killed, so hung clones, CodeQL runs or API
    calls cannot block the worker. A crash of the job process yields an "error" result
    instead of taking the worker down.
    """
    log = log or logging.getLogger(worker_name)
    ctx = multiprocessing.get_context("fork")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_job_process_entry,
        args=(child_conn, handle_instruction, instr, worker_name),
        name=f"{worker_name}-job-{instr.job_id[:8]}",
    )
    sampler = JobSampler()
    started_at = _now_iso()
    start = time.monotonic()
    proc.start()
    child_conn.close()

    payload: str | None = None
    deadline = start + timeout_sec
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sampler.sample()
        if parent_conn.poll(min(_TICK_SEC, remaining)):
            try:
                payload = parent_conn.recv()
            except EOFError:
                payload = None
            break
        if on_tick is not None:
            try:
                on_tick()
            except Exception:
                log.debug("on_tick failed", exc_info=True)
        if not proc.is_alive():
            # Exited without sending; drain anything that may have arrived in between.
            if parent_conn.poll(0.1):
                payload = parent_conn.recv()
            break
    elapsed = time.monotonic() - start

    if payload is not None:
        proc.join(_KILL_GRACE_SEC)
        if proc.is_alive():
            _kill_process_group(proc, log)
        parent_conn.close()
        return _with_provenance(JobResult.from_json(payload), sampler, started_at)

    if proc.is_alive():
        log.error("Job %s timed out after %.1fs; killing job process", instr.job_id, elapsed)
        _kill_process_group(proc, log)
        status, error = "timeout", f"timeout after {elapsed:.1f}s (limit {timeout_sec:g}s); job process killed"
    else:
        log.error("Job %s process exited with code %s without a result", instr.job_id, proc.exitcode)
        status, error = "error", f"job process exited with code {proc.exitcode} without returning a result"
    parent_conn.close()
    result = JobResult(
        job_id=instr.job_id,
        status=status,
        repo_info=instr.repo_info,
        json="{}",
        duration_sec=elapsed,
        size_bytes=0,
        worker=worker_name,
        error=error,
    )
    return _with_provenance(result, sampler, started_at)


def _with_provenance(result: JobResult, sampler: JobSampler, started_at: str) -> JobResult:
    """Timing and container resources of the job, measured by the parent process."""
    result.started_at, result.finished_at = started_at, _now_iso()
    for key, value in sampler.result().items():
        setattr(result, key, value)
    return result


def _push_result(r: redis.Redis, name: str, result: JobResult, log: logging.Logger) -> bool:
    payload = result.to_json()
    for attempt in range(_PUSH_ATTEMPTS):
        try:
            r.rpush(f"results:{name}", payload)
            return True
        except redis.exceptions.RedisError as err:
            delay = min(2**attempt, 10)
            log.warning("Redis push failed for job %s (%s); retrying in %ss", result.job_id, err, delay)
            time.sleep(delay)
    log.error("Giving up on pushing result for job %s after %d attempts", result.job_id, _PUSH_ATTEMPTS)
    return False


def run_worker(
    name: str,
    handle_instruction: Callable[[JobInstruction], JobResult],
    *,
    default_timeout: int = 60,
    timeout_env_var: str = "WORKER_TIMEOUT_SEC",
    logger: logging.Logger | None = None,
    tool_version: str | None = None,
):
    """
    Generic worker runner that:
    - Listens on Redis list `jobs:{name}` for JobInstruction JSON
    - Skips a job that `jobs:routing` no longer lists (cancelled or deleted while queued; common/jobs.py)
    - Executes `handle_instruction` in a subprocess with a hard timeout
    - Catches timeouts/crashes and returns structured JobResult
    - Pushes results to `results:{name}`

    Provenance: every result carries `tool_version` (argument, else env TOOL_VERSION), the
    image it ran in (env BF_CBOM_IMAGE, set in docker-compose.yml), the BF-CBOM commit the image was built from
    (env BF_CBOM_COMMIT, stamped by the Dockerfiles) and the instruction's run_index.
    """

    log = logger or logging.getLogger(name)
    timeout_sec = int(os.getenv(timeout_env_var, str(default_timeout)))
    tool_version = tool_version or os.getenv("TOOL_VERSION") or None
    image_ref = os.getenv("BF_CBOM_IMAGE") or None
    bfcbom_commit = os.getenv("BF_CBOM_COMMIT") or None
    log.info("tool_version=%s image=%s bf-cbom=%s", tool_version, image_ref, bfcbom_commit)

    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_timeout=REDIS_SOCKET_TIMEOUT_SEC)
    log.info(f"{name} worker listening for jobs... (queue: jobs:{name})")

    last = None  # the previous job's result, for the stack board
    while True:
        log.info("awaiting new jobs...\n\n")
        item = None
        failing_since: float | None = None
        while item is None:
            publish_heartbeat(r, name, "idle", last=last)
            try:
                item = r.blpop(f"jobs:{name}", timeout=BLPOP_TIMEOUT_SEC)  # None on timeout -> keep waiting
                failing_since = None
            except redis.exceptions.RedisError as err:
                failing_since = redis_error(log, err, failing_since)
        _, raw = item
        try:
            instr = JobInstruction.from_json(raw)
        except Exception:
            log.exception("Dropping malformed job payload: %.300s", raw)
            continue
        log.info(
            "📨 Received job instruction for job %s (repo: %s)",
            instr.job_id,
            instr.repo_info.full_name,
        )
        if not job_is_routed(r, instr.job_id):
            log.warning("Skipping job %s: no longer routed (cancelled or deleted while queued)", instr.job_id)
            continue
        mark_job_running(r, instr.job_id, name)  # queued -> running in the coordinator's job record

        repo = instr.repo_info.full_name + (f"@{instr.repo_info.commit[:7]}" if instr.repo_info.commit else "")
        job = {"repo": repo, "run": instr.run_index, "started": time.time()}
        result = run_job_with_timeout(
            handle_instruction,
            instr,
            timeout_sec,
            worker_name=name,
            log=log,
            on_tick=functools.partial(publish_heartbeat, r, name, "busy", instr.job_id, job=job, last=last),
        )
        last = {"repo": repo, "status": result.status, "duration_sec": result.duration_sec, "finished_at": time.time()}

        result.tool_version = result.tool_version or tool_version
        result.image_ref = image_ref
        result.bfcbom_commit = bfcbom_commit
        result.run_index = instr.run_index

        if _push_result(r, name, result, log):
            log.info(
                "📤 Sent job result for job %s (repo: %s)",
                result.job_id,
                result.repo_info.full_name,
            )
        time.sleep(0.2)


def build_handle_instruction(
    name: str,
    produce_cbom: Callable[[JobInstruction, Trace], str | tuple[str, float]],
) -> Callable[[JobInstruction], JobResult]:
    """
    Create a standardized handle_instruction for a worker.

    - Injects a Trace aggregator passed to the producer
    - Normalizes JSON via common.utils.normalize_json
    - Maps TimeoutError to status="timeout"
    - Aggregates trace text into JobResult.error when any error occurs

    Usage in a worker:
        def produce(instr, trace):
            # return raw JSON string OR (raw JSON string, duration_sec)
            return MyClient().generate_cbom(..., trace=trace)
        handle_instruction = build_handle_instruction(NAME, produce)
    """

    log = logging.getLogger(name)

    def _handle(instr: JobInstruction) -> JobResult:
        start = time.monotonic()
        log.info("[%s] Running on repo %s", name, instr.repo_info.full_name)
        trace = Trace()
        override_duration: float | None = None
        notes: str | None = None
        try:
            raw_or_tuple = produce_cbom(instr, trace)
            # Allow producers to override duration by returning (raw, duration_sec)
            if isinstance(raw_or_tuple, tuple) and len(raw_or_tuple) == 2:
                raw, override_duration = raw_or_tuple  # type: ignore[assignment]
            else:
                raw = raw_or_tuple  # type: ignore[assignment]

            cbom_as_json, norm_err = normalize_json(raw)  # type: ignore[arg-type]
            if norm_err is None:
                status = "ok"
                error_msg = None
                notes = trace.text()
            else:
                status = "error"
                tt = trace.text()
                error_msg = "\n".join([s for s in [tt, norm_err] if s])
        except TimeoutError as e:
            cbom_as_json = "{}"
            status = "timeout"
            error_msg = trace.text() or str(e)
        except Exception:
            cbom_as_json = "{}"
            status = "error"
            err = traceback.format_exc(limit=6)
            tt = trace.text()
            error_msg = (tt + "\n" if tt else "") + err

        duration = time.monotonic() - start
        # If producer provided a duration, prefer it
        if isinstance(override_duration, int | float):
            duration = float(override_duration)
        return JobResult(
            job_id=instr.job_id,
            status=status,
            repo_info=instr.repo_info,
            json=cbom_as_json,
            duration_sec=duration,
            size_bytes=len(cbom_as_json.encode("utf-8")),
            worker=name,
            error=error_msg,
            notes=notes,
            usage=trace.fields.get("usage"),
        )

    return _handle
