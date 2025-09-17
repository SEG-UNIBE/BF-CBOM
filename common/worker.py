import logging
import os
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import redis

from .config import REDIS_HOST, REDIS_PORT
from .models import JobInstruction, JobResult, Trace
from .utils import normalize_json


def run_worker(
    name: str,
    handle_instruction: Callable[[JobInstruction], JobResult],
    *,
    default_timeout: int = 60,
    timeout_env_var: str = "WORKER_TIMEOUT_SEC",
    logger: logging.Logger | None = None,
):
    """
    Generic worker runner that:
    - Listens on Redis list `jobs:{name}` for JobInstruction JSON
    - Executes `handle_instruction` with a thread + timeout
    - Catches timeouts/errors and returns structured JobResult
    - Pushes results to `results:{name}`
    """

    log = logger or logging.getLogger(name)
    timeout_sec = int(os.getenv(timeout_env_var, str(default_timeout)))

    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    log.info(f"{name} worker listening for jobs... (queue: jobs:{name})")

    while True:
        log.info("awaiting new jobs...\n\n")
        _, raw = r.blpop(f"jobs:{name}")  # blocking pop
        instr = JobInstruction.from_json(raw)
        log.info(
            "📨 Received job instruction for job %s (repo: %s)",
            instr.job_id,
            instr.repo_info.full_name,
        )

        start = time.monotonic()
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(handle_instruction, instr)
                result: JobResult = fut.result(timeout=timeout_sec)
        except TimeoutError:
            elapsed = time.monotonic() - start
            log.error("Job %s timed out after %.1fs", instr.job_id, elapsed)
            result = JobResult(
                job_id=instr.job_id,
                status="timeout",
                repo_info=instr.repo_info,
                json="{}",
                duration_sec=elapsed,
                size_bytes=0,
                worker=name,
                error=f"timeout after {elapsed:.1f}s (limit {timeout_sec}s)",
            )
        except Exception:
            elapsed = time.monotonic() - start
            err = traceback.format_exc(limit=8)
            log.exception("Unhandled error while processing job %s", instr.job_id)
            result = JobResult(
                job_id=instr.job_id,
                status="error",
                repo_info=instr.repo_info,
                json="{}",
                duration_sec=elapsed,
                size_bytes=0,
                worker=name,
                error=err,
            )

        r.rpush(f"results:{name}", result.to_json())
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
        try:
            raw_or_tuple = produce_cbom(instr, trace)
            # Allow producers to override duration by returning (raw, duration_sec)
            override_duration: float | None = None
            if isinstance(raw_or_tuple, tuple) and len(raw_or_tuple) == 2:
                raw, override_duration = raw_or_tuple  # type: ignore[assignment]
            else:
                raw = raw_or_tuple  # type: ignore[assignment]

            cbom_as_json, norm_err = normalize_json(raw)  # type: ignore[arg-type]
            if norm_err is None:
                status = "ok"
                error_msg = None
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
        try:
            if "override_duration" in locals() and isinstance(override_duration, int | float):
                duration = float(override_duration)
        except Exception:
            pass
        return JobResult(
            job_id=instr.job_id,
            status=status,
            repo_info=instr.repo_info,
            json=cbom_as_json,
            duration_sec=duration,
            size_bytes=len(cbom_as_json.encode("utf-8")),
            worker=name,
            error=error_msg,
        )

    return _handle
