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
- L4lit:         L4 without the interpretive dictionary entries (`interpretive: true` in a tool spec: a judgement
                 such as "Certificate Validation Bypass" -> TLS); such records fall back to their L3 identity
- L4 tool specs: the full normalised record: tool-specific sources (CryptoScan symbols, properties, refs,
                 dependencies), tool dictionaries and API context (analysis/adapters/<tool>.yaml)

A value not available at a level falls back to the folded name, so every record keeps a key at every level. The
gap from L0 to L4 is the share of agreement that normalisation creates (representational heterogeneity); hypotheses
are tested at a conservative level (L2/L3), rankings checked for stability across levels. The L4lit-L4 gap is the
part that rests on judgement entries.

Parameters (key size, mode, padding, curve, digest) get the same ladder from L2 on: a (algorithm, value) pair counts
at L2 when the value comes from a standard field, at L3 also when parsed from the name, at L4 from any source
(tool properties, symbols, API context, our derivations). `closure=True` adds the algorithms a record implies
(Vocabulary.implied, refinement 2) to every set from L3 on, so a tool expanding HMAC-SHA256 into HMAC and SHA-256
and one that does not are compared on equal terms; both variants are reported, never pooled.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from common.experiment import repo_full_name as full_name

from .normalize.record import NormRecord
from .normalize.vocab import Vocabulary, fold, load_vocabulary

LEVELS = ("L0", "L1", "L2", "L3", "L4lit", "L4")
LEVEL_NAMES = {
    "L0": "raw name",
    "L1": "case and separators ignored",
    "L2": "standard vocabulary (whole names, algorithmFamily)",
    "L3": "composite names parsed",
    "L4lit": "tool specs without interpretive entries (judgements fall back to L3)",
    "L4": "tool specs (tool-specific sources, dictionaries, context)",
}
PARAMETER_DIMS = ("key_size", "mode", "padding", "curve", "digest")
PARAMETER_LEVELS = ("L2", "L3", "L4lit", "L4")  # parameters do not exist in a raw name
LEVEL_COLUMNS = ["dimension", "level", "tool", "voter_group", "algorithms", "confirmed", "share"]
ALGORITHM_COLUMNS = [
    "repo", "snapshot", "variant", "run", "language", "level", "tool", "voter_group", "tailored", "algorithm",
    "implied", "records",
]  # fmt: skip
_FAMILY_FIELD = "cryptoProperties.algorithmProperties.algorithmFamily"
_STANDARD_SRC = "cryptoProperties."


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
    if level == "L4lit" and "interpretive" in (rec.via.get("algorithm"), rec.via.get("family")):
        return identity(rec, "L3", vocab)
    return rec.values.get("algorithm") or rec.values.get("family") or folded


def parameter_at(rec: NormRecord, level: str, dim: str):
    """The record's value of a parameter dimension as admitted at `level`, else None: L2 standard fields, L3 also
    the parsed name, L4lit any source but interpretive entries, L4 any source (including our derivations)."""
    value, via, src = rec.values.get(dim), rec.via.get(dim), rec.src.get(dim) or ""
    if value is None or level not in PARAMETER_LEVELS:
        return None
    if level == "L2":
        return value if via == "field" and src.startswith(_STANDARD_SRC) else None
    if level == "L3":
        standard = via == "field" and src.startswith(_STANDARD_SRC)
        return value if standard or (via == "parse" and src == "name") else None
    if level == "L4lit" and via == "interpretive":
        return None
    return value


@dataclass
class LevelRow:
    level: str
    tool: str
    voter_group: str
    algorithms: int  # distinct (snapshot, algorithm) pairs the tool reports; for a parameter: (algorithm, value) pairs
    confirmed: int  # ... that a tool of another voter group reports too
    dimension: str = "algorithm"  # `algorithm`, or one of PARAMETER_DIMS

    @property
    def share(self) -> float:
        return self.confirmed / self.algorithms if self.algorithms else 0.0

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "level": self.level,
            "tool": self.tool,
            "voter_group": self.voter_group,
            "algorithms": self.algorithms,
            "confirmed": self.confirmed,
            "share": round(self.share, 3),
        }


def algorithms_at(
    records: list[NormRecord], level: str, vocab: Vocabulary | None = None, *, closure: bool = False
) -> Counter:
    """{algorithm as seen at `level`: number of records} over the algorithm records of one CBOM. With `closure`
    (from L3 on) the algorithms a record implies count as reported too (Vocabulary.implied, refinement 2)."""
    vocab = vocab or load_vocabulary()
    out: Counter = Counter()
    for r in records:
        if r.values.get("asset_type") == "algorithm":
            key = identity(r, level, vocab)
            if key:
                out[key] += 1
            if closure and level not in ("L0", "L1", "L2"):
                for implied in vocab.implied(r.values):
                    out[implied] += 1
    return out


def parameters_at(records: list[NormRecord], level: str, dim: str, vocab: Vocabulary | None = None) -> set[tuple]:
    """{(algorithm at `level`, value)} of the algorithm records that carry `dim` at that level (parameter_at)."""
    vocab = vocab or load_vocabulary()
    out: set[tuple] = set()
    for r in records:
        if r.values.get("asset_type") != "algorithm":
            continue
        value = parameter_at(r, level, dim)
        key = identity(r, level, vocab)
        if value is not None and key:
            out.add((key, value))
    return out


def ladder(
    records: dict[tuple[str, str], list[NormRecord]],
    voter_of: dict[str, str],
    vocab: Vocabulary | None = None,
    *,
    exclude: set[tuple[str, str]] | None = None,
    closure: bool = False,
    dims: tuple[str, ...] = ("algorithm", *PARAMETER_DIMS),
) -> list[LevelRow]:
    """`records`: {(snapshot key, tool): records of that tool's CBOM}. Only algorithm records count.
    `exclude`: (snapshot key, tool) pairs left out entirely, e.g. repos the tool was tuned on (refinement 13): they
    neither count for the tool nor confirm the others. Returns one row per (dimension, level, tool) plus `all`
    (all tools pooled); parameter dimensions exist from L2 on."""
    vocab = vocab or load_vocabulary()
    records = {k: v for k, v in records.items() if k not in (exclude or set())}
    rows: list[LevelRow] = []
    tools = sorted({t for _, t in records})
    for dim in dims:
        for level in LEVELS if dim == "algorithm" else PARAMETER_LEVELS:
            by_snap: dict[str, dict[str, set]] = defaultdict(dict)
            for (snap, tool), recs in records.items():
                if dim == "algorithm":
                    by_snap[snap][tool] = set(algorithms_at(recs, level, vocab, closure=closure))
                else:
                    by_snap[snap][tool] = parameters_at(recs, level, dim, vocab)
            total_alg = total_conf = 0
            for tool in tools:
                group = voter_of.get(tool, tool)
                n = conf = 0
                for per_tool in by_snap.values():
                    mine = per_tool.get(tool, set())
                    others = set().union(*(k for t, k in per_tool.items() if voter_of.get(t, t) != group))
                    n += len(mine)
                    conf += len(mine & others)
                rows.append(LevelRow(level, tool, group, n, conf, dim))
                total_alg += n
                total_conf += conf
            rows.append(LevelRow(level, "all", "-", total_alg, total_conf, dim))
    return rows


def algorithm_rows(
    records: dict[tuple[str, str, str], list[NormRecord]],
    voter_of: dict[str, str],
    *,
    run: int,
    languages: dict[str, str | None] | None = None,
    vocab: Vocabulary | None = None,
    tailored: dict[str, frozenset[str]] | None = None,
    closure: bool = False,
) -> list[dict]:
    """Which algorithms each tool reports per repository, at every level: one row per (snapshot, variant, level,
    tool, algorithm) with the number of records behind it. `records`: {(snapshot, variant, tool): records};
    `tailored`: {tool: repos it was tuned on} (the `tailored` column, refinement 13); with `closure` the algorithms
    the records imply get rows too (`implied` true, refinement 2). Location-free, so every tool takes part; the basis
    for UpSet plots, the tools-per-algorithm distribution and the confirmation shares of `ladder`."""
    vocab = vocab or load_vocabulary()
    languages = languages or {}
    tailored = tailored or {}
    rows = []
    for (snapshot, variant, tool), recs in sorted(records.items()):
        repo = full_name(snapshot)
        base = {
            "repo": repo,
            "snapshot": snapshot,
            "variant": variant,
            "run": run,
            "language": languages.get(repo),
            "tailored": repo in tailored.get(tool, ()),
        }
        for level in LEVELS:
            reported = algorithms_at(recs, level, vocab)
            closed = algorithms_at(recs, level, vocab, closure=True) if closure else reported
            for algorithm, n in sorted(closed.items()):
                rows.append(
                    {
                        **base,
                        "level": level,
                        "tool": tool,
                        "voter_group": voter_of.get(tool, tool),
                        "algorithm": algorithm,
                        "implied": algorithm not in reported,
                        "records": n,
                    }
                )
    return rows


def table_lines(rows: list[LevelRow]) -> list[str]:
    """Tools x levels: confirmed share (algorithms at L0 and L4); the algorithm dimension only."""
    rows = [r for r in rows if r.dimension == "algorithm"]
    by = {(r.tool, r.level): r for r in rows}
    tools = [t for t in dict.fromkeys(r.tool for r in rows) if t != "all"] + ["all"]
    head = f"{'tool':<15}" + "".join(f"{lv:>8}" for lv in LEVELS) + f"{'algos L0':>10}{'algos L4':>10}"
    lines = [head]
    for t in tools:
        cells = "".join(f"{by[(t, lv)].share:>8.0%}" for lv in LEVELS)
        lines.append(f"{t:<15}{cells}{by[(t, 'L0')].algorithms:>10}{by[(t, 'L4')].algorithms:>10}")
    return lines
