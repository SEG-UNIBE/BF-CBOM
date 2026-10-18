"""Matcher (b): exact matching on the canonical algorithm identity, at a ladder level.

Repository level: the groups are the clusters of `analysis/levels.py:level_matches`: records connected by the
cumulative relation of the ladder (equal under any key function up to `level`: at L0 only verbatim identical names,
at L3 parsed names, at L4 everything the tool specs know). Every group carries as `key` the *item* it belongs to,
an algorithm as the full normalisation sees it (its L4 identity); an item has one group at L4 and may have several
at a lower level (its tools' spellings are not yet recognised as one thing). Tools without locations take part. A
member whose own identity differs from the group's key (it joined through a lower level: the same raw string that
another tool's spec resolves differently) is flagged `matched`; with `closure` (refinement 2) a record is also a
member of the groups of the algorithms it implies (HMAC-SHA256 -> SHA-256), flagged `implied`.

Call-site level: inside a repo group, the located members are chained by file and line: sorted by line, a record
joins the current site group when it starts within `window` lines of the group's last line (a window record spans
`line` .. `line_end`). The location rule of the retired call-site matcher survives here.

Group ids: `E00007` for an item's first group, `E00007-2` for its second cluster at a lower level, sites
`E00007.001`. The ladder (`levels.ladder`) and the tools-per-algorithm distribution read the same clusters, so this
matcher at a level, the ladder's column for that level and the k distribution are one definition.
"""

from __future__ import annotations

from ..levels import level_matches
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
        items = level_matches(records, self.vocab, closure=self.closure)
        groups: list[MatchGroup] = []
        for n, key in enumerate(sorted(items), start=1):
            item = items[key]
            own = {r.record_id for recs in item.own.values() for r in recs}
            clusters = item.clusters_at(self.level) or [[]]
            for c, members in enumerate(clusters, start=1):
                gid = f"E{n:05d}" if c == 1 else f"E{n:05d}-{c}"
                matched = frozenset(r.record_id for r in members if r.record_id not in own)
                implied = frozenset(r.record_id for r in members if r.record_id in item.implied)
                sites = self._site_groups(members, key, gid)
                located = {self._voter(r.tool) for r in members if r.file and r.line}
                colocated = max((len({self._voter(r.tool) for r in s.members}) for s in sites), default=0)
                extra = {
                    "level": self.level,
                    "n_records": len(members),
                    "n_sites": len(sites),
                    "cluster": c,
                    "clusters": len(clusters),
                    "located_voters": len(located),
                    "colocated_voters": colocated,
                }
                groups.append(MatchGroup(gid, "repo", key, members, extra, implied, matched))
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
