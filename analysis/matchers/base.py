"""Match groups and per-dimension agreement, shared by every matcher."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from ..levels import PARAMETER_DIMS, parameter_at
from ..normalize.record import NormRecord

AGREEMENT_DIMS = (*PARAMETER_DIMS, "location")


@dataclass
class MatchGroup:
    """Records several tools report as the same thing. `scope` is `repo` (location ignored) or `site` (the located
    records of a repo group that sit in the same file within the matcher's line window)."""

    group_id: str
    scope: str
    key: str  # the canonical algorithm identity the members share (at the matcher's ladder level)
    members: list[NormRecord]
    extra: dict = field(default_factory=dict)  # matcher-specific columns (file, line range, located voters, ...)
    implied: frozenset[str] = frozenset()  # record ids that are members only by implication (closure)
    matched: frozenset[str] = frozenset()  # record ids whose own identity differs: matched in at a lower level
    attached: frozenset[str] = frozenset()  # record ids attached by vocabulary knowledge (generic or implied)

    @property
    def tools(self) -> list[str]:
        return sorted({r.tool for r in self.members})

    @property
    def k(self) -> int:
        return len(self.tools)

    def voters(self, voter_of: dict[str, str] | None = None) -> list[str]:
        voter_of = voter_of or {}
        return sorted({voter_of.get(t, t) for t in self.tools})


class Matcher(Protocol):
    name: str

    def match(self, records: list[NormRecord]) -> list[MatchGroup]:
        """All records of one (snapshot, variant), every tool -> its match groups."""
        ...


def agreement(
    group: MatchGroup,
    dims: tuple[str, ...] = AGREEMENT_DIMS,
    *,
    voter_of: dict[str, str] | None = None,
    level: str | None = None,
) -> list[dict]:
    """Per dimension: how many voters report a value inside the group, how many of them carry the majority value,
    that value and every value seen. A voter with several values counts for each. Values are read as admitted at
    `level` (default: the group's level, else L4: any source; `levels.parameter_at`), so a group at L2 compares
    standard-field values only. `location` (repo groups only) reads the matcher's `located_voters` /
    `colocated_voters`: voters that locate the asset at all, and the most that share one call site."""
    voter_of = voter_of or {}
    level = level or group.extra.get("level") or "L4"
    rows = []
    for dim in dims:
        if dim == "location":
            if group.scope != "repo":
                continue
            located, colocated = group.extra.get("located_voters"), group.extra.get("colocated_voters")
            row = {"dimension": dim, "reporting": located or 0, "agreeing": colocated or 0}
            rows.append({**row, "majority": None, "values": []})
            continue
        by_voter: dict[str, set[str]] = {}
        for r in group.members:
            v = parameter_at(r, level, dim)
            if v is not None:
                by_voter.setdefault(voter_of.get(r.tool, r.tool), set()).add(str(v))
        if not by_voter:
            rows.append({"dimension": dim, "reporting": 0, "agreeing": 0, "majority": None, "values": []})
            continue
        counts = Counter(v for vs in by_voter.values() for v in vs)
        majority = sorted(counts, key=lambda v: (-counts[v], v))[0]
        rows.append(
            {
                "dimension": dim,
                "reporting": len(by_voter),
                "agreeing": sum(majority in vs for vs in by_voter.values()),
                "majority": majority,
                "values": sorted(counts),
            }
        )
    return rows
