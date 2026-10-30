"""Run-to-run stability of a tool (the stability run, CLAUDE.md Phase 5) and consensus sets for repeated runs.

For every (tool, snapshot, variant) with at least two stored ok runs: the Jaccard similarity of the item sets
(identity at a ladder level, `analysis/levels.py`) between runs, the coefficient of variation of the record count,
and the consensus set: the items (L4 identity, as in the ladder and the scores) present in more than half of the runs
(an inserted snippet of Phase 4 counts as detected only when it is in most runs). Scoring uses the
consensus sets instead of run i's sets when repetitions exceed one (`--consensus`, the default then).
`flux` is the noise floor of RQ5's "changes off the edit": the share of the items (the consensus unit, L4) a tool
reported on the unchanged snapshot in some run but not in every run; `baseline_flux` summarises it per tool for
`mutation-scores.csv` (`report --baseline`).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from itertools import combinations

from .levels import UNIT_LEVEL, algorithms_at
from .normalize.record import NormRecord
from .normalize.vocab import Vocabulary, load_vocabulary

STABILITY_COLUMNS = [
    "repo", "snapshot", "variant", "tool", "level", "runs", "records_mean", "records_cv", "items_mean",
    "jaccard_mean", "jaccard_min", "flux", "consensus", "unstable",
]  # fmt: skip


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def stability_rows(
    runs: dict[tuple[str, str, str], dict[int, list[NormRecord]]],
    *,
    level: str = "L3",
    vocab: Vocabulary | None = None,
    repo_of=None,
) -> tuple[list[dict], dict[tuple[str, str, str], set[str]]]:
    """`runs`: {(snapshot, variant, tool): {run index: records}}. Returns the rows and the consensus sets
    {(snapshot, variant, tool): {algorithm}} of every (tool, snapshot) with at least two runs."""
    vocab = vocab or load_vocabulary()
    rows, consensus = [], {}
    for (snapshot, variant, tool), by_run in sorted(runs.items()):
        if len(by_run) < 2:
            continue
        sets = {k: set(algorithms_at(recs, level, vocab)) for k, recs in by_run.items()}
        units = {k: set(algorithms_at(recs, UNIT_LEVEL, vocab)) for k, recs in by_run.items()}  # the consensus unit
        counts = [len([r for r in recs if r.values.get("asset_type") == "algorithm"]) for recs in by_run.values()]
        pairs = [jaccard(sets[a], sets[b]) for a, b in combinations(sorted(sets), 2)]
        seen: dict[str, int] = defaultdict(int)
        for s in units.values():
            for key in s:
                seen[key] += 1
        majority = {key for key, n in seen.items() if n > len(by_run) / 2}
        consensus[(snapshot, variant, tool)] = majority
        in_every = sum(1 for n in seen.values() if n == len(by_run))
        mean = statistics.mean(counts)
        rows.append(
            {
                "repo": repo_of(snapshot) if repo_of else snapshot,
                "snapshot": snapshot,
                "variant": variant,
                "tool": tool,
                "level": level,
                "runs": len(by_run),
                "records_mean": round(mean, 1),
                "records_cv": round(statistics.pstdev(counts) / mean, 3) if mean else None,
                "items_mean": round(statistics.mean(len(s) for s in sets.values()), 1),
                "jaccard_mean": round(statistics.mean(pairs), 3),
                "jaccard_min": round(min(pairs), 3),
                "flux": round((len(seen) - in_every) / len(seen), 3) if seen else 0.0,
                "consensus": sorted(majority),
                "unstable": sorted(set(seen) - majority),
            }
        )
    return rows, consensus


def baseline_flux(rows: list[dict]) -> dict[str, dict[str, float]]:
    """Per tool over a stability table's rows (stability.csv, read back or fresh): `flux`, the mean share of items
    that come and go between repeated runs of unchanged code, and `changed`, the share of (snapshot, variant) pairs
    whose repeated runs differ at all; the noise floor RQ5's off-edit changes are read against."""
    by_tool: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        f = r.get("flux")
        if f in (None, ""):
            continue
        by_tool[str(r["tool"])].append(float(f))
    return {
        tool: {"flux": round(statistics.mean(fs), 3), "changed": round(sum(1 for f in fs if f > 0) / len(fs), 3)}
        for tool, fs in sorted(by_tool.items())
    }
