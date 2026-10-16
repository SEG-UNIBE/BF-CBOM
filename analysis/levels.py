"""How much of the agreement between tools does normalisation create? The L0-L4 ladder (CLAUDE.md, refinement 14).

One dimension, location-free: which algorithms a tool reports per repository (snapshot and variant). A tool's
algorithm is "confirmed" when at least one tool of another voter group (common/tools.yaml `voter_group`) reports the
same one in the same snapshot. The confirmed share is computed at five normalisation levels, each allowed more
information than the one before:

- L0 raw:        the component name exactly as reported
- L1 folded:     the name with case and separators ignored (SHA-256 = sha256)
- L2 standard:   the whole name, or CycloneDX 1.7 `algorithmFamily`, mapped through the shared standard vocabulary
                 (CycloneDX registry, JOSE/SSH/JCA identifiers: DESede -> 3DES, HS256 -> HMAC); no splitting of names
- L3 parsed:     composite names parsed (AES-256-GCM -> AES, SHA256withECDSA -> ECDSA)
- L4 tool specs: the full normalised record: tool-specific sources (CryptoScan symbols, properties, refs,
                 dependencies), tool dictionaries and API context (analysis/adapters/<tool>.yaml)

A value not available at a level falls back to the folded name, so every record keeps a key at every level. The
gap from L0 to L4 is the share of agreement that normalisation creates (representational heterogeneity); hypotheses
are tested at a conservative level (L2/L3), rankings checked for stability across levels.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .normalize.record import NormRecord
from .normalize.vocab import Vocabulary, fold, load_vocabulary

LEVELS = ("L0", "L1", "L2", "L3", "L4")
LEVEL_NAMES = {
    "L0": "raw name",
    "L1": "case and separators ignored",
    "L2": "standard vocabulary (whole names, algorithmFamily)",
    "L3": "composite names parsed",
    "L4": "tool specs (tool-specific sources, dictionaries, context)",
}
LEVEL_COLUMNS = ["level", "tool", "voter_group", "algorithms", "confirmed", "share"]
_FAMILY_FIELD = "cryptoProperties.algorithmProperties.algorithmFamily"


def identity(rec: NormRecord, level: str, vocab: Vocabulary | None = None) -> str | None:
    """The algorithm a record names, as seen at `level` (None when the record has no name and no algorithm)."""
    vocab = vocab or load_vocabulary()
    name = (rec.name or "").strip()
    folded = fold(name) or None
    if level == "L0":
        return name or None
    if level == "L1":
        return folded
    if level == "L2":
        std = vocab.standard_name(name) if name else None
        if std:
            return std
        if rec.src.get("family") == _FAMILY_FIELD:
            return rec.values.get("family")
        return folded
    if level == "L3":
        parsed = vocab.parse(name).values if name else {}
        found = parsed.get("algorithm") or parsed.get("family")
        if found:
            return found
        if rec.src.get("family") == _FAMILY_FIELD:
            return rec.values.get("family")
        return folded
    return rec.values.get("algorithm") or rec.values.get("family") or folded


@dataclass
class LevelRow:
    level: str
    tool: str
    voter_group: str
    algorithms: int  # distinct (snapshot, algorithm) pairs the tool reports
    confirmed: int  # ... that a tool of another voter group reports too

    @property
    def share(self) -> float:
        return self.confirmed / self.algorithms if self.algorithms else 0.0

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "tool": self.tool,
            "voter_group": self.voter_group,
            "algorithms": self.algorithms,
            "confirmed": self.confirmed,
            "share": round(self.share, 3),
        }


def ladder(
    records: dict[tuple[str, str], list[NormRecord]], voter_of: dict[str, str], vocab: Vocabulary | None = None
) -> list[LevelRow]:
    """`records`: {(snapshot key, tool): records of that tool's CBOM}. Only algorithm records count.
    Returns one row per (level, tool) plus `all` (all tools pooled) per level."""
    vocab = vocab or load_vocabulary()
    rows: list[LevelRow] = []
    tools = sorted({t for _, t in records})
    for level in LEVELS:
        sets: dict[tuple[str, str], set[str]] = {}
        for (snap, tool), recs in records.items():
            keys = {identity(r, level, vocab) for r in recs if r.values.get("asset_type") == "algorithm"}
            sets[(snap, tool)] = {k for k in keys if k}
        by_snap: dict[str, dict[str, set[str]]] = defaultdict(dict)
        for (snap, tool), keys in sets.items():
            by_snap[snap][tool] = keys
        total_alg = total_conf = 0
        for tool in tools:
            group = voter_of.get(tool, tool)
            n = conf = 0
            for per_tool in by_snap.values():
                mine = per_tool.get(tool, set())
                others = set().union(*(k for t, k in per_tool.items() if voter_of.get(t, t) != group))
                n += len(mine)
                conf += len(mine & others)
            rows.append(LevelRow(level, tool, group, n, conf))
            total_alg += n
            total_conf += conf
        rows.append(LevelRow(level, "all", "-", total_alg, total_conf))
    return rows


def table_lines(rows: list[LevelRow]) -> list[str]:
    """Tools x levels: confirmed share (algorithms at L0 and L4)."""
    by = {(r.tool, r.level): r for r in rows}
    tools = [t for t in dict.fromkeys(r.tool for r in rows) if t != "all"] + ["all"]
    head = f"{'tool':<15}" + "".join(f"{lv:>8}" for lv in LEVELS) + f"{'algos L0':>10}{'algos L4':>10}"
    lines = [head]
    for t in tools:
        cells = "".join(f"{by[(t, lv)].share:>8.0%}" for lv in LEVELS)
        lines.append(f"{t:<15}{cells}{by[(t, 'L0')].algorithms:>10}{by[(t, 'L4')].algorithms:>10}")
    return lines
