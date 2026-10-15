"""Location-aware n-way matching of call sites (exact matcher, call-site level).

Rule (from the pilot of 2026-10-08, kept as the deterministic baseline for RQ3/RQ4):
- two sites of *different* tools match when they are in the same file, at most `window` lines
  apart and their family sets intersect;
- a site with an unknown family matches nothing unless `match_unknown` is set;
- every site links only to the *nearest* compatible site of each other tool (ties: all at the
  minimum distance), which stops chains from forming in dense crypto code;
- clusters are the connected components; `k` is the number of distinct tools in a cluster.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .sites import Site


@dataclass
class Cluster:
    file: str
    members: list[Site]
    id: str = ""

    @property
    def tools(self) -> list[str]:
        return sorted({m.tool for m in self.members})

    @property
    def k(self) -> int:
        return len({m.tool for m in self.members})

    @property
    def line_min(self) -> int:
        return min(m.line for m in self.members)

    @property
    def line_max(self) -> int:
        return max(m.line for m in self.members)

    @property
    def families(self) -> Counter:
        return Counter(f for m in self.members for f in m.families)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file": self.file,
            "line_min": self.line_min,
            "line_max": self.line_max,
            "k": self.k,
            "tools": self.tools,
            "families": dict(self.families.most_common()),
            "members": [m.to_dict() for m in sorted(self.members, key=lambda s: (s.line, s.tool))],
        }


def compatible(a: Site, b: Site, *, match_unknown: bool = False) -> bool:
    if a.unknown or b.unknown:
        return match_unknown
    return bool(a.families & b.families)


def cluster_sites(
    sites: list[Site], *, window: int = 2, match_unknown: bool = False, in_scope_only: bool = True
) -> list[Cluster]:
    pool = [s for s in sites if s.in_scope or not in_scope_only]
    parent = list(range(len(pool)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_file: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(pool):
        by_file[s.file].append(i)
    for idx in by_file.values():
        idx.sort(key=lambda i: pool[i].line)
        for i in idx:
            best: dict[str, tuple[int, list[int]]] = {}
            for j in idx:
                if pool[j].tool == pool[i].tool:
                    continue
                d = abs(pool[j].line - pool[i].line)
                if d > window or not compatible(pool[i], pool[j], match_unknown=match_unknown):
                    continue
                t = pool[j].tool
                if t not in best or d < best[t][0]:
                    best[t] = (d, [j])
                elif d == best[t][0]:
                    best[t][1].append(j)
            for _, js in best.values():
                for j in js:
                    parent[find(i)] = find(j)
    groups: dict[int, list[Site]] = defaultdict(list)
    for i, s in enumerate(pool):
        groups[find(i)].append(s)
    clusters = [Cluster(file=ms[0].file, members=ms) for ms in groups.values()]
    clusters.sort(key=lambda c: (c.file, c.line_min, c.tools))
    for n, c in enumerate(clusters):
        c.id = f"C{n:05d}"
    return clusters


@dataclass
class ToolAgreement:
    tool: str
    sites: int = 0
    clusters: int = 0
    confirmed: int = 0  # clusters with >= 2 tools
    solo: int = 0
    unknown_sites: int = 0

    @property
    def confirmed_share(self) -> float:
        return self.confirmed / self.clusters if self.clusters else 0.0

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["confirmed_share"] = round(self.confirmed_share, 3)
        return d


@dataclass
class AgreementSummary:
    k_distribution: dict[int, int] = field(default_factory=dict)
    per_tool: dict[str, ToolAgreement] = field(default_factory=dict)
    pairwise_jaccard: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "k_distribution": {str(k): v for k, v in sorted(self.k_distribution.items())},
            "per_tool": {t: a.to_dict() for t, a in sorted(self.per_tool.items())},
            "pairwise_jaccard": dict(sorted(self.pairwise_jaccard.items())),
        }


def summarize_clusters(clusters: list[Cluster], tools: list[str] | None = None) -> AgreementSummary:
    """k distribution, per-tool confirmed/solo counts and pairwise Jaccard over clusters."""
    tools = tools or sorted({t for c in clusters for t in c.tools})
    summary = AgreementSummary(k_distribution=dict(Counter(c.k for c in clusters)))
    touched: dict[str, set[str]] = {t: set() for t in tools}
    for t in tools:
        agg = summary.per_tool[t] = ToolAgreement(tool=t)
        for c in clusters:
            members = [m for m in c.members if m.tool == t]
            if not members:
                continue
            touched[t].add(c.id)
            agg.sites += len(members)
            agg.unknown_sites += sum(1 for m in members if m.unknown)
            agg.clusters += 1
            if c.k >= 2:
                agg.confirmed += 1
            else:
                agg.solo += 1
    for i, a in enumerate(tools):
        for b in tools[i + 1 :]:
            union = touched[a] | touched[b]
            summary.pairwise_jaccard[f"{a}|{b}"] = round(len(touched[a] & touched[b]) / len(union), 3) if union else 0.0
    return summary
