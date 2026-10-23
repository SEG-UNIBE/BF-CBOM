"""A process pool for the analysis report's per-item steps (CLAUDE.md "Analysis").

`pmap` runs a top-level function over a list of items and returns the results in input order, so every table the
report writes in iteration order stays byte-identical to a sequential run. The workers are spawned, never forked: the
report process runs rich's refresh thread and holds `git cat-file` pipes (analysis/snapshots.py), which a fork would
share. Workers import `analysis.tasks` only; they never touch the progress display, typer or the parent's store.

`jobs`: 0 = auto, min(CPUs, MAX_AUTO_JOBS) when a step has at least AUTO_MIN_ITEMS items, else sequential (small
experiments and the tests stay in-process); 1 = sequential; N > 1 = that many workers. The CLI option `--jobs`
(env `BF_CBOM_JOBS`) carries it. The runner's memory cap (`RUNNER_MEMORY`, Makefile) bounds the pool: each worker
holds one parsed CBOM or one repository's records plus numpy and scikit-learn for RaQuN.
"""

from __future__ import annotations

import multiprocessing
import os
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from misc.cli import progress

MAX_AUTO_JOBS = 8  # memory, not CPUs, bounds the runner (RUNNER_MEMORY)
AUTO_MIN_ITEMS = 16  # below this a pool start costs more than it saves
_THREAD_CAPS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")  # one BLAS thread per worker


def resolve_jobs(jobs: int, n_items: int) -> int:
    """The worker count for `n_items`: `jobs` as given (1 = sequential), 0 = auto (module docstring)."""
    if jobs > 0:
        return jobs
    return min(os.cpu_count() or 1, MAX_AUTO_JOBS) if n_items >= AUTO_MIN_ITEMS else 1


def pmap(fn: Callable, items: list, *, jobs: int, name: str, item: Callable | None = None) -> list:
    """`fn` over `items`, results in input order, counted on the step line `name` (progress.track); in a spawned
    process pool of `resolve_jobs(jobs, len(items))` workers, or in this process when that is 1 or there is one item.
    A worker's exception propagates; a worker that died (the kernel kills one at the memory cap) raises
    BrokenProcessPool with what to change. On a failure the items still queued are dropped, not run to the end."""
    n = resolve_jobs(jobs, len(items))
    if n <= 1 or len(items) < 2:
        return [fn(x) for x in progress.track(items, name, item=item)]
    for var in _THREAD_CAPS:
        os.environ.setdefault(var, "1")  # inherited by the workers; numpy is not imported yet in the parent
    pool = ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn"))
    try:
        results: Iterator = pool.map(fn, items, chunksize=1)
        return [next(results) for _ in progress.track(items, name, item=item)]
    except BrokenProcessPool as exc:
        raise BrokenProcessPool(
            "a worker process died, most likely out of memory: raise RUNNER_MEMORY or lower --jobs (BF_CBOM_JOBS)"
        ) from exc
    finally:
        pool.shutdown(cancel_futures=True)
