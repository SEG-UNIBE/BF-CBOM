"""Background releaser for wave-scheduled inspections.

The Execution page ingests results and releases waves only while someone has it open. This daemon
thread (one per Streamlit server process) does the same every few seconds for every running inspection,
so a batch started in the UI and then left alone still advances from wave to wave (see
`coordinator/redis_io.py:release_due_waves`). It is idempotent next to the page's own polling: results
are popped exactly once and a wave is released only when its predecessor is complete.
"""

from __future__ import annotations

import os
import threading
import time

from coordinator.logger_config import logger
from coordinator.redis_io import (
    count_done_jobs,
    get_insp_workers,
    get_redis,
    ingest_worker_results,
    list_inspections,
    now_iso,
    release_due_waves,
)

INTERVAL_SEC = float(os.getenv("WAVE_SCHEDULER_INTERVAL_SEC", "5"))
_lock = threading.Lock()
_thread: threading.Thread | None = None


def _tick() -> None:
    r = get_redis()
    for insp_id, meta in list_inspections(r):
        if meta.get("status") != "running":
            continue
        workers = get_insp_workers(r, insp_id)
        ingest_worker_results(r, insp_id, workers)
        release_due_waves(r, insp_id)
        done, total = count_done_jobs(r, insp_id)
        if total and done >= total:
            r.hset(f"insp:{insp_id}", mapping={"status": "completed", "finished_at": now_iso()})
            logger.info("Inspection %s completed (%d jobs), marked by the wave scheduler", insp_id, total)


def _loop() -> None:
    while True:
        try:
            _tick()
        except Exception as e:  # keep the thread alive across Redis blips
            logger.warning("wave scheduler: %s", e)
        time.sleep(INTERVAL_SEC)


def ensure_scheduler() -> None:
    """Start the releaser thread once per process; safe to call from every page."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, name="wave-scheduler", daemon=True)
        _thread.start()
        logger.info("wave scheduler started (every %gs)", INTERVAL_SEC)
