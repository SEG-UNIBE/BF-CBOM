"""How much of the agreement between tools does normalisation create? The L0-L4 ladder (CLAUDE.md, refinement 14).

One dimension, location-free: which algorithms a tool reports per repository (snapshot and variant). The ladder
varies only *when two reports count as the same algorithm*; the unit stays fixed:

- **Unit:** an *item* is one canonical algorithm a tool reports in a repository, its identity under the full
  normalisation (L4: `values.algorithm`, else `values.family`, else the folded name). A tool's items are its distinct
  L4 identities; the union of items over the tools is the repository's algorithm population. Counting at the finest
  level (raw spellings) would weight every share by how many spellings a tool uses; counting at each level's own
  granularity would compare fractions of different populations (parsing merges AES-256-GCM and AES-128-CBC into one
  AES). With the unit fixed, every level's share is a fraction of the same thing.
- **Relation, cumulative:** two records match at level L when they are equal under *any* key function up to L:

  - L0 raw:        the component name exactly as reported
  - L1 folded:     the name with case and separators ignored (SHA-256 = sha256)
  - L2 standard:   the whole name, or CycloneDX 1.7 `algorithmFamily`, mapped through the shared standard
                   vocabulary (CycloneDX registry, JOSE/SSH/JCA identifiers: DESede -> 3DES, HS256 -> HMAC)
  - L3 parsed:     composite names parsed (AES-256-GCM -> AES, SHA256withECDSA -> ECDSA)
  - L4lit:         L4 without the interpretive dictionary entries (`interpretive: true` in a tool spec: a judgement
                   such as "Certificate Validation Bypass" -> TLS); such records keep their L3 identity
  - L4 tool specs: the full normalised record: tool-specific sources (CryptoScan symbols, properties, refs,
                   dependencies), tool dictionaries and API context (analysis/adapters/<tool>.yaml)

  The records of a repository fall into *clusters* at each level: the connected sets under the relation (a
  union-find over all algorithm records, extended level by level, so clusters only ever merge). An item's clusters
  at L are the clusters holding one of its records; a cluster may also hold records of another item that matched in
  through a lower level (two tools wrote the same string, one tool's spec resolves it differently). An item is
  *confirmed* for a tool at level L when a cluster holding one of the tool's records of that item also holds a
  record of another voter group (common/tools.yaml `voter_group`). Matches are never lost at a higher level (two
  tools that wrote the same string agree at every level), so a tool's confirmed share is non-decreasing and each
  step's gain is attributable to one normalisation step. The L0 share has a plain reading: the share of a tool's
  algorithms for which another tool uses a verbatim identical spelling. The L0 to L4 gap is the agreement
  normalisation creates (representational heterogeneity); hypotheses are tested at a conservative level (L2/L3),
  rankings checked for stability across levels. The L4lit to L4 gap is the part that rests on judgement entries.
  The symmetric view, "how many voter groups report this algorithm at level L", is the largest cluster of the item
  in voter groups (`Item.k_at`): at L4 every record of the item is in one cluster, so it is the plain count.

Parameters (key size, mode, padding, curve, digest) get the same ladder from L2 on, with the unit (item, value) over
values from any source: a pair counts as confirmed at L when the tool's value is *admitted* at L (L2: from a
standard field, L3: also parsed from the name, L4lit: any source but interpretive entries, L4: any source) and a
record of another voter group in the tool's cluster carries the same admitted value. `closure=True` adds the
algorithms a record implies (Vocabulary.implied, refinement 2) as items of the tool: an implication is parsed
knowledge, so such a record joins the implied item's clusters from L3 on (it never merges clusters: HMAC-SHA256
counts as a SHA-256 report, it does not make HMAC and SHA-256 one thing). A tool expanding HMAC-SHA256 into HMAC
and SHA-256 and one that does not are then compared on equal terms; both variants are reported, never pooled.

The exact matcher (analysis/matchers/exact.py) emits these clusters as its groups, so its groups at a level, the
ladder and the tools-per-algorithm distribution are one definition.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from common.experiment import repo_full_name as full_name

from .normalize.record import NormRecord
from .normalize.vocab import Vocabulary, fold, load_vocabulary

LEVELS = ("L0", "L1", "L2", "L3", "L4lit", "L4")
LEVEL_INDEX = {lv: i for i, lv in enumerate(LEVELS)}
UNIT_LEVEL = "L4"
LEVEL_NAMES = {
    "L0": "raw name",
    "L1": "case and separators ignored",
    "L2": "standard vocabulary (whole names, algorithmFamily)",
    "L3": "composite names parsed",
    "L4lit": "tool specs without interpretive entries (judgements keep their L3 identity)",
    "L4": "tool specs (tool-specific sources, dictionaries, context)",
}
PARAMETER_DIMS = ("key_size", "mode", "padding", "curve", "digest")
PARAMETER_LEVELS = ("L2", "L3", "L4lit", "L4")  # parameters do not exist in a raw name
CLOSURE_LEVELS = ("L3", "L4lit", "L4")  # an implied algorithm is a parsed fact: it exists from L3 on
LEVEL_COLUMNS = ["dimension", "level", "tool", "voter_group", "algorithms", "confirmed", "share"]
ALGORITHM_COLUMNS = [
    "repo", "snapshot", "variant", "run", "language", "tool", "voter_group", "tailored", "algorithm", "implied",
    "records", "spellings", *[f"k_{lv}" for lv in LEVELS],
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


def keys(rec: NormRecord, vocab: Vocabulary | None = None) -> dict[str, str]:
    """{level: identity at that level} for every level at which the record has one."""
    vocab = vocab or load_vocabulary()
    out = {}
    for lv in LEVELS:
        k = identity(rec, lv, vocab)
        if k:
            out[lv] = k
    return out


def parameter_at(rec: NormRecord, level: str, dim: str):
    """The record's value of a dimension as admitted at `level`, else None: L2 standard fields, L3 also the parsed
    name, L4lit any source but interpretive entries, L4 any source (including our derivations). Nothing below L2:
    a raw name carries no normalised value."""
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
class Item:
    """One algorithm of one repository (the unit): the tools reporting it and, per level, the clusters holding its
    records (every record of a cluster, those of other items included)."""

    key: str
    own: dict[str, list[NormRecord]] = field(default_factory=lambda: defaultdict(list))  # tool -> its records
    implied: set[str] = field(default_factory=set)  # record ids that are own records only by implication (closure)
    clusters: dict[str, list[list[NormRecord]]] = field(default_factory=dict)  # level -> clusters (sorted)

    @property
    def tools(self) -> list[str]:
        return sorted(self.own)

    def clusters_at(self, level: str) -> list[list[NormRecord]]:
        return self.clusters.get(level, [])

    def clusters_of(self, level: str, tool: str) -> list[list[NormRecord]]:
        """The item's clusters at `level` that hold one of the tool's records of the item."""
        mine = {r.record_id for r in self.own.get(tool, [])}
        return [c for c in self.clusters_at(level) if any(r.record_id in mine for r in c)]

    def k_at(self, level: str, voter_of: dict[str, str]) -> int:
        """Voter groups in the item's largest cluster at `level` (1 when it has no cluster yet, e.g. an implied item
        below L3: its tool reports it, nobody can match it)."""
        return max((len({voter_of.get(r.tool, r.tool) for r in c}) for c in self.clusters_at(level)), default=1)

    def confirmed_at(self, level: str, tool: str, voter_of: dict[str, str]) -> bool:
        """A cluster holding one of the tool's records of the item also holds a record of another voter group."""
        mine = voter_of.get(tool, tool)
        return any(any(voter_of.get(r.tool, r.tool) != mine for r in c) for c in self.clusters_of(level, tool))


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def level_matches(
    records: list[NormRecord], vocab: Vocabulary | None = None, *, closure: bool = False
) -> dict[str, Item]:
    """The algorithm records of one repository (every tool) -> {L4 identity: Item} with the clusters per level.
    With `closure` a record is also an own record (flagged implied) of every algorithm it implies and joins that
    item's clusters from L3 on (attached, never merging clusters)."""
    vocab = vocab or load_vocabulary()
    algorithms = [r for r in records if r.values.get("asset_type") == "algorithm"]
    rec_keys = {r.record_id: keys(r, vocab) for r in algorithms}
    items: dict[str, Item] = {}
    implied_by: dict[str, list[NormRecord]] = defaultdict(list)  # implied item -> records implying it
    uf = _UnionFind()
    for r in algorithms:
        unit = rec_keys[r.record_id].get(UNIT_LEVEL)
        if not unit:
            continue
        uf.add(r.record_id)
        items.setdefault(unit, Item(unit)).own[r.tool].append(r)
        if closure:
            for other in vocab.implied(r.values):
                if other != unit:
                    item = items.setdefault(other, Item(other))
                    item.own[r.tool].append(r)
                    item.implied.add(r.record_id)
                    implied_by[other].append(r)
    real = [r for r in algorithms if r.record_id in uf.parent]
    for lv in LEVELS:
        by_key: dict[str, list[str]] = defaultdict(list)
        for r in real:
            k = rec_keys[r.record_id].get(lv)
            if k:
                by_key[k].append(r.record_id)
        for ids in by_key.values():
            for other in ids[1:]:
                uf.union(ids[0], other)
        members: dict[str, list[NormRecord]] = defaultdict(list)
        for r in real:
            members[uf.find(r.record_id)].append(r)
        for unit, item in items.items():
            real_own = [r for recs in item.own.values() for r in recs if r.record_id not in item.implied]
            roots = sorted({uf.find(r.record_id) for r in real_own})
            clusters = [sorted(members[root], key=lambda r: (r.tool, r.record_id)) for root in roots]
            if lv in CLOSURE_LEVELS and implied_by.get(unit):
                extra = sorted(implied_by[unit], key=lambda r: (r.tool, r.record_id))
                if clusters:
                    clusters = [sorted(c + extra, key=lambda r: (r.tool, r.record_id)) for c in clusters]
                else:
                    clusters = [extra]
            item.clusters[lv] = sorted(clusters, key=lambda c: (c[0].tool, c[0].record_id))
    return items


@dataclass
class LevelRow:
    level: str
    tool: str
    voter_group: str
    algorithms: int  # the tool's items (distinct algorithms per repository); for a parameter: (item, value) pairs
    confirmed: int  # ... that a record of another voter group matches at this level or below
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
    """{identity at `level`: number of records} over the algorithm records of one CBOM; the per-level key sets used
    for run-to-run stability. With `closure` (from L3 on) the algorithms a record implies count as reported too."""
    vocab = vocab or load_vocabulary()
    out: Counter = Counter()
    for r in records:
        if r.values.get("asset_type") == "algorithm":
            key = identity(r, level, vocab)
            if key:
                out[key] += 1
            if closure and level in CLOSURE_LEVELS:
                for implied in vocab.implied(r.values):
                    out[implied] += 1
    return out


def _parameter_pairs(item: Item, tool: str, dim: str) -> set:
    """The tool's (item, value) pairs of a dimension, values from any source."""
    return {(item.key, v) for r in item.own[tool] if (v := parameter_at(r, "L4", dim)) is not None}


def _pair_confirmed(item: Item, tool: str, dim: str, value, level: str, voter_of: dict[str, str]) -> bool:
    mine = voter_of.get(tool, tool)
    if not any(parameter_at(r, level, dim) == value for r in item.own[tool]):
        return False  # the tool's own value is not admitted at this level
    return any(
        voter_of.get(r.tool, r.tool) != mine and parameter_at(r, level, dim) == value
        for c in item.clusters_of(level, tool)
        for r in c
    )


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
    by_snap: dict[str, list[NormRecord]] = defaultdict(list)
    for (snap, _), recs in records.items():
        by_snap[snap] += recs
    items_by_snap = {snap: level_matches(recs, vocab, closure=closure) for snap, recs in by_snap.items()}
    tools = sorted({t for _, t in records})
    rows: list[LevelRow] = []
    for dim in dims:
        for level in LEVELS if dim == "algorithm" else PARAMETER_LEVELS:
            total_n = total_conf = 0
            for tool in tools:
                group = voter_of.get(tool, tool)
                n = conf = 0
                for items in items_by_snap.values():
                    for item in items.values():
                        if tool not in item.own:
                            continue
                        if dim == "algorithm":
                            n += 1
                            conf += item.confirmed_at(level, tool, voter_of)
                        else:
                            pairs = _parameter_pairs(item, tool, dim)
                            n += len(pairs)
                            conf += sum(_pair_confirmed(item, tool, dim, v, level, voter_of) for _, v in pairs)
                rows.append(LevelRow(level, tool, group, n, conf, dim))
                total_n += n
                total_conf += conf
            rows.append(LevelRow(level, "all", "-", total_n, total_conf, dim))
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
    """Which algorithms each tool reports per repository: one row per (snapshot, variant, tool, item) with the
    number of records and distinct spellings behind it and, per level, the voter groups in the item's largest
    cluster (`k_<level>`, the tool's own group included). `records`: {(snapshot, variant, tool): records};
    `tailored`: {tool: repos it was tuned on} (the `tailored` column, refinement 13); with `closure` the algorithms
    the records imply get rows too (`implied` true, refinement 2). Location-free, so every tool takes part; the
    basis for UpSet plots and the tools-per-algorithm distribution."""
    vocab = vocab or load_vocabulary()
    languages = languages or {}
    tailored = tailored or {}
    by_snap: dict[tuple[str, str], list[NormRecord]] = defaultdict(list)
    for (snapshot, variant, _), recs in records.items():
        by_snap[(snapshot, variant)] += recs
    rows = []
    for (snapshot, variant), recs in sorted(by_snap.items()):
        repo = full_name(snapshot)
        items = level_matches(recs, vocab, closure=closure)
        for key in sorted(items):
            item = items[key]
            ks = {lv: item.k_at(lv, voter_of) for lv in LEVELS}
            for tool in item.tools:
                own = item.own[tool]
                rows.append(
                    {
                        "repo": repo,
                        "snapshot": snapshot,
                        "variant": variant,
                        "run": run,
                        "language": languages.get(repo),
                        "tool": tool,
                        "voter_group": voter_of.get(tool, tool),
                        "tailored": repo in tailored.get(tool, ()),
                        "algorithm": key,
                        "implied": all(r.record_id in item.implied for r in own),
                        "records": len(own),
                        "spellings": len({(r.name or "").strip() for r in own}),
                        **{f"k_{lv}": ks[lv] for lv in LEVELS},
                    }
                )
    return rows


def table_lines(rows: list[LevelRow]) -> list[str]:
    """Tools x levels: confirmed share and the number of items; the algorithm dimension only."""
    rows = [r for r in rows if r.dimension == "algorithm"]
    by = {(r.tool, r.level): r for r in rows}
    tools = [t for t in dict.fromkeys(r.tool for r in rows) if t != "all"] + ["all"]
    head = f"{'tool':<15}" + "".join(f"{lv:>8}" for lv in LEVELS) + f"{'algorithms':>12}"
    lines = [head]
    for t in tools:
        cells = "".join(f"{by[(t, lv)].share:>8.0%}" for lv in LEVELS)
        lines.append(f"{t:<15}{cells}{by[(t, 'L4')].algorithms:>12}")
    return lines
