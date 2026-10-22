"""Matcher (b): exact matching on the canonical algorithm identity, at a ladder level.
Repository level: the groups are the clusters of `analysis/levels.py:level_matches`, one per key at the level: the
records whose identity at that level or below is the key (at L0 only verbatim identical names, at L3 parsed names, at
L4 everything the tool specs know), plus the records attached by vocabulary knowledge (a generic report such as SHA-2
next to SHA-256; with `closure`, refinement 2, a record implying the algorithm, HMAC-SHA256 -> SHA-256). A record with
several keys is in several groups; groups never merge through it. `key` is the level's key (a raw name at L0, a
canonical identity at L4); `extra.units` lists the L4 identities of the members, so a group whose members span
several units shows what the level declares "the same". A member whose own L4 identity differs from the key is
flagged `matched`; attached records are flagged `attached` (and `implied` when they joined by implication). Tools
without locations take part; records without an L4 identity (`undefined`, `api`, `non-crypto`) are not matched.
Call-site level: inside a repo group, the located records are chained by file and line: sorted by line, a record
joins the current site group when it starts within `window` lines of the group's last line (a window record spans
`line` .. `line_end`). The location rule of the retired call-site matcher survives here.
Group ids: `E00007` per repo group (clusters sorted by key), sites `E00007.001`. The ladder (`levels.ladder`), the
tools-per-algorithm distribution and the scores read the same clusters, so this matcher at a level, the ladder's
column for that level and the k distribution are one definition.
"""

from __future__ import annotations

from common.tools_registry import votes

from ..levels import UNIT_LEVEL, clusters_at, identity, level_matches
from ..normalize.record import NormRecord
from ..normalize.vocab import Vocabulary, load_vocabulary
from .base import MatchGroup


class ExactMatcher:
    name = "exact"

    def __init__(
        self,
        *,
        level: str = "L4",
        window: int = 2,
        closure: bool = False,
        voter_of: dict[str, str] | None = None,
        vocab: Vocabulary | None = None,
    ):
        self.level, self.window, self.closure = level, window, closure
        self.voter_of = voter_of if voter_of is not None else {}
        self.vocab = vocab or load_vocabulary()

    def _voter(self, tool: str) -> str:
        return self.voter_of.get(tool, tool)

    def match(self, records: list[NormRecord]) -> list[MatchGroup]:
        items = level_matches(records, self.vocab, closure=self.closure)
        groups: list[MatchGroup] = []
        for n, cluster in enumerate(clusters_at(items, self.level), start=1):
            gid = f"E{n:05d}"
            members = cluster.records
            # the L4 identities the level declares the same, among the voters (observers only when nobody else is in)
            voting = [r for r in cluster.members if votes(self.voter_of, r.tool)] or cluster.members
            units = sorted({u for r in voting if (u := identity(r, UNIT_LEVEL, self.vocab))})
            attached = frozenset(cluster.reason)
            implied = frozenset(rid for rid, why in cluster.reason.items() if why == "implied")
            matched = frozenset(
                r.record_id for r in cluster.members if identity(r, UNIT_LEVEL, self.vocab) != cluster.key
            )
            sites = self._site_groups(members, cluster.key, gid)
            located = {self._voter(r.tool) for r in members if r.file and r.line and votes(self.voter_of, r.tool)}
            colocated = max(
                (len({self._voter(r.tool) for r in s.members if votes(self.voter_of, r.tool)}) for s in sites),
                default=0,
            )
            extra = {
                "level": self.level,
                "n_records": len(members),
                "n_sites": len(sites),
                "units": units,
                "n_units": len(units),
                "n_attached": len(attached),
                "located_voters": len(located),
                "colocated_voters": colocated,
            }
            groups.append(MatchGroup(gid, "repo", cluster.key, members, extra, implied, matched, attached))
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
