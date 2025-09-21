import logging
import os
import queue
import time
from multiprocessing import get_context
from pathlib import Path

import json_matching  # type: ignore  # pylint: disable=import-error,wrong-import-position
import redis

from common.config import REDIS_HOST, REDIS_PORT
from common.models import ComponentMatchJobInstruction, ComponentMatchJobResult

NAME = Path(__file__).resolve().parents[1].name
JOB_QUEUE = f"jobs:{NAME}"
RESULT_LIST = f"results:{NAME}"
TIMEOUT_SEC = int(os.getenv("CALC_TIMEOUT_SEC", "120"))


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


redis_client: redis.Redis | None = None


def _match_worker(payloads: list[str], result_queue) -> None:
    try:
        result_queue.put(("ok", _match_components(payloads)))
    except Exception as err:  # pragma: no cover - child process
        result_queue.put(("error", str(err)))


def _serialize_match(match_group: list) -> list:
    return [
        {
            "file": component.doc_id,
            "component": component.comp_id,
            "cost": component.cost
        } for component in match_group
    ]


def _match_components(json_payloads: list[list[str]]) -> list[dict]:
    matches = json_matching.n_way_match_pivot(json_payloads, cost_thresh=10000.0)

    serialized = [_serialize_match(match) for match in matches]
    logger.info("Found %d component match(es)", len(serialized))
    
    return serialized


def _run_match_with_timeout(json_payloads: list[str]) -> list[dict]:
    timeout = TIMEOUT_SEC
    if timeout and timeout > 0:
        ctx = get_context("spawn")
        result_queue = ctx.Queue()
        process = ctx.Process(target=_match_worker, args=(json_payloads, result_queue))
        process.start()
        try:
            status, payload = result_queue.get(timeout=timeout)
        except queue.Empty as err:
            process.terminate()
            process.join(timeout=1)
            raise TimeoutError(f"component similarity timed out after {timeout} seconds") from err
        finally:
            if process.is_alive():
                process.join()

        if status == "error":
            raise RuntimeError(payload)
        return payload
    return _match_components(json_payloads)


def _handle_instruction(raw_payload: str) -> None:
    global redis_client

    try:
        instruction = ComponentMatchJobInstruction.from_json(raw_payload)
    except Exception as err:
        logger.error("Failed to decode ComponentMatchJobInstruction: %s", err, exc_info=True)
        return

    logger.info(
        "📨 Received job instruction for job %s (repo: %s)", instruction.job_id, instruction.repo_info.full_name
    )
    cbom_strings = [entry.components_as_json for entry in instruction.CbomJsons if entry.components_as_json]
    # TODO: instruction.CbomJsons holds now a list of strings in its attribute 'components_as_json' (used to be only a single string 'json')
    tools = [entry.tool for entry in instruction.CbomJsons if entry.json]
    if len(cbom_strings) < 2:
        logger.warning(
            "Received job %s but only %d CBOM(s); cannot compute similarity",
            instruction.job_id,
            len(cbom_strings),
        )
        insufficient_result = ComponentMatchJobResult(
            job_id=instruction.job_id,
            benchmark_id=instruction.benchmark_id,
            repo_full_name=instruction.repo_info.full_name,
            tools=tools,
            match_count=0,
            matches=[],
            duration_sec=0.0,
            status="error",
            error="Need at least two CBOM payloads to compute similarity",
        )
        if redis_client is not None:
            try:
                redis_client.rpush(RESULT_LIST, insufficient_result.to_json())
            except Exception as err:  # pragma: no cover
                logger.warning(
                    "Failed to persist insufficient-input result for job %s: %s",
                    instruction.job_id,
                    err,
                )
        return

    logger.info(
        "Processing job %s from benchmark %s with %d CBOM payload(s)",
        instruction.job_id,
        instruction.benchmark_id,
        len(cbom_strings),
    )

    status = "ok"
    error_msg: str | None = None
    matches: list[dict] = []

    start_time = time.perf_counter()

    try:
        matches = _run_match_with_timeout(cbom_strings)
    except TimeoutError as err:
        status = "timedout"
        error_msg = str(err)
        logger.warning("Job %s timed out after %ss", instruction.job_id, TIMEOUT_SEC)
    except Exception as err:
        status = "error"
        error_msg = str(err)
        logger.error("json_matching failed for job %s: %s", instruction.job_id, err, exc_info=True)

    if status != "ok":
        matches = []

    duration = time.perf_counter() - start_time

    if status != "ok":
        matches = []

    result_payload = ComponentMatchJobResult(
        job_id=instruction.job_id,
        benchmark_id=instruction.benchmark_id,
        repo_full_name=instruction.repo_info.full_name,
        tools=tools,
        match_count=len(matches),
        matches=matches,
        duration_sec=duration,
        status=status,
        error=error_msg,
    )

    try:
        if redis_client is not None:
            redis_client.rpush(RESULT_LIST, result_payload.to_json())
            logger.info(
                "📤 Sent job result for job %s (repo: %s)", result_payload.job_id, result_payload.repo_full_name
            )
    except Exception as err:  # pragma: no cover - best-effort persistence
        logger.warning("Failed to persist result for job %s: %s", instruction.job_id, err)


def main() -> None:
    global redis_client

    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    logger.info("%s listening for component match jobs... (queue: %s)", NAME, JOB_QUEUE)

    try:
        while True:
            try:
                _, raw_payload = redis_client.blpop(JOB_QUEUE)
            except redis.exceptions.RedisError as err:
                logger.error("Redis error while waiting for jobs: %s", err)
                time.sleep(1)
                continue
            if not raw_payload:
                continue
            _handle_instruction(raw_payload)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal, stopping listener")


if __name__ == "__main__":
    logger.info("starting up...")
    main()
