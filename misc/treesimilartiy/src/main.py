import logging
import os
import queue
import time
import multiprocessing
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


def _match_components(documents: list[list[str]]) ->  list[dict]:
    logger.info("Starting n-way component matching for %d documents...", len(documents))
    # The C++ function expects a list of documents, where each document is a list of component JSON strings.
    # It returns a list of chains, where each chain is a list of component IDs.
    # A component ID is a pair [document_index, component_index_in_document].
    logger.info("Documents:\n%s", documents)
    # TODO: transform format
    matches = json_matching.n_way_match_pivot(documents, cost_thresh=10000.0)

    serialized = [_serialize_match(match) for match in matches]
    logger.info("Found %d component match(es)", len(serialized))
    logger.info("Matches:\n%s", serialized)
    return serialized


def _run_match_with_timeout(func, args, timeout_seconds):
    """
    Run a function in a separate process with a timeout.
    """
    with multiprocessing.Pool(processes=1) as pool:
        result = pool.apply_async(func, args=(args,))
        try:
            return result.get(timeout=timeout_seconds)
        except multiprocessing.TimeoutError:
            logger.error("Component matching timed out after %d seconds.", timeout_seconds)
            # The pool is automatically terminated, which should kill the child process
            return None


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
    
    
    # Transform the list of CbomJson objects into a list of documents (list[list[str]])
    # as expected by the C++ matching function.
    documents = [
        entry.components_as_json
        for entry in instruction.CbomJsons
        if entry.components_as_json
    ]
    
    tools = [entry.tool for entry in instruction.CbomJsons if entry.components_as_json]
    if len(documents) < 2:
        logger.warning("Need at least two documents with components to match. Aborting.")
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
        len(documents),
    )

    status = "ok"
    error_msg: str | None = None
    matches: list = []

    start_time = time.perf_counter()

    match_results = None

    try:
        match_results = _run_match_with_timeout(
            _match_components, documents, timeout_seconds=TIMEOUT_SEC
        )
    except TimeoutError as err:
        status = "timeout"
        error_msg = str(err)
        logger.warning("Job %s timed out after %ss", instruction.job_id, TIMEOUT_SEC)
    except Exception as err:
        status = "error"
        error_msg = str(err)
        logger.error("json_matching failed for job %s: %s", instruction.job_id, err, exc_info=True)

    if status == "ok":
        if match_results is None:
            status = "timeout"
            error_msg = (
                f"Component matching timed out after {TIMEOUT_SEC} seconds"
            )
            matches = []
        else:
            matches = match_results
    else:
        matches = []

    duration = time.perf_counter() - start_time

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
