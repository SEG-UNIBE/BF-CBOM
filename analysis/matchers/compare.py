"""Comparing two matchers on the same records (RQ3): do they put the same records together?

Both matchers partition the algorithm records of a repository into groups (repo scope). Over the records both
matchers grouped, the co-membership pairs (two records in one group) are compared: the Jaccard of the two pair sets,
and per group how often all of a group's members land in a single group of the other matcher.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from .base import MatchGroup

MATCHERS_COLUMNS = [
    "repo", "snapshot", "variant", "run", "matcher_a", "matcher_b", "records_common", "groups_a", "groups_b",
    "pairs_a", "pairs_b", "pairs_both", "pair_jaccard", "a_in_one_b", "b_in_one_a",
]  # fmt: skip


def _membership(groups: list[MatchGroup]) -> dict[str, str]:
    """record id -> group id over the repo-scope groups (a record sits in one such group per matcher)."""
    out = {}
    for g in groups:
        if g.scope == "repo":
            for r in g.members:
                out.setdefault(r.record_id, g.group_id)
    return out


def _pairs(member_of: dict[str, str], records: set[str]) -> set[frozenset[str]]:
    by_group: dict[str, list[str]] = defaultdict(list)
    for rid in records:
        by_group[member_of[rid]].append(rid)
    return {frozenset(p) for rids in by_group.values() for p in combinations(sorted(rids), 2)}


def _in_one(member_of_a: dict[str, str], member_of_b: dict[str, str], records: set[str]) -> float | None:
    """Share of A's groups (restricted to the common records) whose members all fall into one B group."""
    by_group: dict[str, set[str]] = defaultdict(set)
    for rid in records:
        by_group[member_of_a[rid]].add(member_of_b[rid])
    return round(sum(len(bs) == 1 for bs in by_group.values()) / len(by_group), 3) if by_group else None


def compare(groups_a: list[MatchGroup], groups_b: list[MatchGroup], name_a: str, name_b: str) -> dict:
    a, b = _membership(groups_a), _membership(groups_b)
    common = set(a) & set(b)
    pairs_a, pairs_b = _pairs(a, common), _pairs(b, common)
    both = pairs_a & pairs_b
    union = pairs_a | pairs_b
    return {
        "matcher_a": name_a,
        "matcher_b": name_b,
        "records_common": len(common),
        "groups_a": len({a[r] for r in common}),
        "groups_b": len({b[r] for r in common}),
        "pairs_a": len(pairs_a),
        "pairs_b": len(pairs_b),
        "pairs_both": len(both),
        "pair_jaccard": round(len(both) / len(union), 3) if union else None,
        "a_in_one_b": _in_one(a, b, common),
        "b_in_one_a": _in_one(b, a, common),
    }
