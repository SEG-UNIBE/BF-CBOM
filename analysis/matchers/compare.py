"""Comparing two matchers on the same records (RQ3): do they put the same records together?

Both matchers partition the algorithm records of a repository into groups (repo scope). Over the records both
matchers grouped, the co-membership pairs (two records in one group) are compared: the Jaccard of the two pair sets,
and per group how often all of a group's members land in a single group of the other matcher. The pairs are counted
from the group sizes, never listed: a group of n records holds n(n-1)/2 of them, which on a large repository are tens
of millions (listing them killed the report on corpus-pilot-medium, 2026-10-23).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from math import comb

from .base import MatchGroup

MATCHERS_COLUMNS = [
    "repo", "snapshot", "variant", "run", "level", "matcher_a", "matcher_b", "records_common", "groups_a", "groups_b",
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


def _pairs(member_of: dict, records: set[str]) -> int:
    """The co-membership pairs among `records`: two records share a group of `member_of` (a group id, or a pair of
    group ids for the pairs both matchers share)."""
    return sum(comb(n, 2) for n in Counter(member_of[rid] for rid in records).values())


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
    both = _pairs({rid: (a[rid], b[rid]) for rid in common}, common)
    union = pairs_a + pairs_b - both
    return {
        "matcher_a": name_a,
        "matcher_b": name_b,
        "records_common": len(common),
        "groups_a": len({a[r] for r in common}),
        "groups_b": len({b[r] for r in common}),
        "pairs_a": pairs_a,
        "pairs_b": pairs_b,
        "pairs_both": both,
        "pair_jaccard": round(both / union, 3) if union else None,
        "a_in_one_b": _in_one(a, b, common),
        "b_in_one_a": _in_one(b, a, common),
    }
