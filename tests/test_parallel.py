"""misc/cli/parallel.py: ordered results from a spawned pool, the sequential fallback, the jobs rule."""

import os

import pytest

from misc.cli.parallel import AUTO_MIN_ITEMS, MAX_AUTO_JOBS, pmap, resolve_jobs


def _square(x: int) -> int:
    return x * x


def _boom(x: int) -> int:
    raise ValueError(f"no {x}")


def test_resolve_jobs_auto_only_for_enough_items():
    assert resolve_jobs(0, AUTO_MIN_ITEMS - 1) == 1
    assert resolve_jobs(0, AUTO_MIN_ITEMS) == min(os.cpu_count() or 1, MAX_AUTO_JOBS)
    assert resolve_jobs(1, 1000) == 1 and resolve_jobs(3, 2) == 3


def test_pmap_keeps_the_input_order_in_and_out_of_the_pool():
    items = list(range(40))
    assert pmap(_square, items, jobs=1, name="t") == [x * x for x in items]  # in-process
    assert pmap(_square, items, jobs=2, name="t") == [x * x for x in items]  # two spawned workers
    assert pmap(_square, [7], jobs=2, name="t") == [49]  # one item: no pool
    assert pmap(_square, [], jobs=2, name="t") == []


def test_pmap_raises_what_a_worker_raised():
    with pytest.raises(ValueError, match="no 1"):
        pmap(_boom, [1, 2], jobs=2, name="t")
