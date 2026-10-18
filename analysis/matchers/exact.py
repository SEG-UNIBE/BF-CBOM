"""Matcher (b): exact matching on the canonical algorithm identity.

Repository level: every algorithm record of every tool is keyed by its identity at a ladder level
(`analysis/levels.py:identity`, default L3: composite names parsed, no tool-specific sources); one group per key, so
tools without locations take part. Call-site level: inside a repo group, the located records are chained by file
and line: sorted by line, a record joins the current site group when it starts within `window` lines of the group's
last line (a window record spans `line` .. `line_end`). The location rule of the retired call-site matcher survives
here; its family-compatibility rule is replaced by the identity key, and unknown names (no identity beyond the folded
name) match only the same folded name.

With `closure` (refinement 2) a record also joins the groups of the algorithms it implies (HMAC-SHA256 -> SHA-256);
such memberships are marked `implied`, so tables can keep them apart.
"""

from __future__ import annotations

from collections import defaultdict

from ..levels import identity
from ..normalize.record import NormRecord
from ..normalize.vocab import Vocabulary, load_vocabulary
from .base import MatchGroup


class ExactMatcher:
    name = "exact"

    def __init__(
        self,
        *,
        level: str = "L3",
        window: int = 2,
        closure: bool = False,
        voter_of: dict[str, str] | None = None,
        vocab: Vocabulary | None = None,
    ):
        self.level, self.window, self.closure = level, window, closure
        self.voter_of = voter_of or {}
        self.vocab = vocab or load_vocabulary()

    def _voter(self, tool: str) -> str:
        return self.voter_of.get(tool, tool)

    def match(self, records: list[NormRecord]) -> list[MatchGroup]:
        by_key: dict[str, list[NormRecord]] = defaultdict(list)
        implied_in: dict[str, set[str]] = defaultdict(set)
        for r in records:
            if r.values.get("asset_type") != "algorithm":
                continue
            key = identity(r, self.level, self.vocab)
            if not key:
                continue
            by_key[key].append(r)
            if self.closure:
                for other in self.vocab.implied(r.values):
                    if other != key and r not in by_key[other]:
                        by_key[other].append(r)
                        implied_in[other].add(r.record_id)
        groups: list[MatchGroup] = []
        for n, key in enumerate(sorted(by_key), start=1):
            members = by_key[key]
            gid = f"E{n:05d}"
            sites = self._site_groups(members, key, gid)
            located = {self._voter(r.tool) for r in members if r.file and r.line}
            colocated = max((len({self._voter(r.tool) for r in s.members}) for s in sites), default=0)
            extra = {
                "level": self.level,
                "n_records": len(members),
                "n_sites": len(sites),
                "located_voters": len(located),
                "colocated_voters": colocated,
            }
            groups.append(MatchGroup(gid, "repo", key, members, extra, frozenset(implied_in.get(key, ()))))
            groups += sites
        return groups

    def _site_groups(self, members: list[NormRecord], key: str, parent: str) -> list[MatchGroup]:
        located = sorted(
            (r for r in members if r.file and r.line),
            key=lambda r: (r.file, int(r.line), int(r.line_end or r.line), r.tool, r.record_id),
        )
        chains: list[list[NormRecord]] = []
        end_of_chain = -1
        for r in located:
            end = int(r.line_end) if r.line_end and int(r.line_end) > int(r.line) else int(r.line)
            if chains and r.file == chains[-1][0].file and int(r.line) <= end_of_chain + self.window:
                chains[-1].append(r)
                end_of_chain = max(end_of_chain, end)
            else:
                chains.append([r])
                end_of_chain = end
        out = []
        for i, chain in enumerate(chains, start=1):
            lines = [int(r.line) for r in chain]
            ends = [int(r.line_end) if r.line_end else int(r.line) for r in chain]
            extra = {
                "level": self.level,
                "parent": parent,
                "file": chain[0].file,
                "line_min": min(lines),
                "line_max": max(ends),
                "n_records": len(chain),
            }
            out.append(MatchGroup(f"{parent}.{i:03d}", "site", key, chain, extra))
        return out
