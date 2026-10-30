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
  - L4 tool specs: the full normalised record: tool-specific sources (CryptoScan symbols, properties, refs,
                   dependencies), tool dictionaries and API context (analysis/adapters/<tool>.yaml). The specs hold
                   the tools' own spellings and labels, never judgements (CLAUDE.md Rules, 2026-10-19); a former
                   level L4lit (L4 without `interpretive` entries) went with the last judgement entry, since the
                   two had become identical.

  The records of a repository fall into *clusters* at each level, one per key: the cluster of key k at level L
  holds every record whose identity at some level up to L is k. A record with several keys (`hmac.new`: SHA-1 at
  L3, HMAC at L4) sits in several clusters, but clusters never merge through it (decided 2026-10-19: the earlier
  union-find let one bridging name merge SHA-256 with SHA-512 and HMAC with SHA-1). A cluster may hold records of
  several items (two tools wrote the same string, one tool's spec resolves it differently); such multi-unit
  clusters are counted per level (`multi_unit_clusters`): what the level declares "the same". From L2 on a record
  with a generic identity (SHA-2, EC, the bare `SHA` marker) is *attached* to the clusters of the specific
  algorithms it may stand for that exist in the repository: it confirms them and is confirmed by them, but brings no
  key. An item is *confirmed* for a tool at level L when a cluster holding one of the tool's records of that item
  also holds a record of another voter group (common/tools.yaml `voter_group`); `confirmed_strict` counts the
  confirmations that rest on members alone. Matches are never lost at a higher level (two tools that wrote the same
  string agree at every level), so a tool's confirmed share is non-decreasing and each step's gain is attributable
  to one normalisation step. The L0 share has a plain reading: the share of a tool's algorithms for which another
  tool uses a verbatim identical spelling. The L0 to L4 gap is the agreement normalisation creates
  (representational heterogeneity); hypotheses are tested at a conservative level (L2/L3), rankings checked for
  stability across levels. The symmetric view,
  "how many voter groups report this algorithm at level L", is the largest cluster of the item in voter groups
  (`Item.k_at`). Records without an L4 identity (`undefined`, `api`, `non-crypto`, `ambiguous`: the record's
  `resolution`) are not items and never match.

Parameters (key size, mode, padding, curve, digest) get the same ladder from L2 on, with the unit (item, value) over
values from any source: a pair counts as confirmed at L when the tool's value is *admitted* at L (L2: from a
standard field, L3: also parsed from the name, L4: any source) and a
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

LEVELS = ("L0", "L1", "L2", "L3", "L4")
LEVEL_INDEX = {lv: i for i, lv in enumerate(LEVELS)}
UNIT_LEVEL = "L4"
LEVEL_NAMES = {
    "L0": "raw name",
    "L1": "case and separators ignored",
    "L2": "standard vocabulary (whole names, algorithmFamily)",
    "L3": "composite names parsed",
    "L4": "tool specs (tool-specific sources, dictionaries, context)",
}
PARAMETER_DIMS = ("key_size", "mode", "padding", "curve", "digest")
PARAMETER_LEVELS = ("L2", "L3", "L4")  # parameters do not exist in a raw name
# A dimension the standard has no field for exists only once a name is parsed: CycloneDX carries no digest of a
# composite (HMAC-SHA256), so a digest row at L2 would be 0 % by construction; it starts at L3.
DIM_FIRST_LEVEL = {"digest": "L3"}
CLOSURE_LEVELS = ("L3", "L4")  # an implied algorithm is a parsed fact: it exists from L3 on
LEVEL_COLUMNS = [
    "dimension", "level", "tool", "voter_group", "items", "confirmed", "share", "confirmed_strict",
    "multi_unit_clusters",
]  # fmt: skip
ITEM_COLUMNS = [
    "repo", "snapshot", "variant", "run", "language", "tool", "voter_group", "tailored", "unit", "registry",
    "implied", "records", "spellings", *[f"k_{lv}" for lv in LEVELS],
]  # fmt: skip
REGISTRY_COLUMNS = ["kind", "name", "gap", "primitive", "count", "repos", "tools", "note"]
LAYERS = ("family aliases", "algorithms", "algorithm aliases", "names", "apis", "oids", "cipher suites")
_FAMILY_FIELD = "cryptoProperties.algorithmProperties.algorithmFamily"
_STANDARD_SRC = "cryptoProperties."


def identity(rec: NormRecord, level: str, vocab: Vocabulary | None = None) -> str | None:
    """The algorithm a record names, as seen at `level` (None when the record has no name and no algorithm)."""
    vocab = vocab or load_vocabulary()
    # an API or non-crypto name (`name_kind`, e.g. CipherSift's `hmac.new`) spells no algorithm: nothing is read
    # from it at any level, the identity comes from the fields alone
    name = "" if rec.name_kind else (rec.name or "").strip()
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
    return rec.values.get("algorithm") or rec.values.get("family")  # no family: no unit (`undefined`, `api`)


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
    name, L4 any source (including our derivations). Nothing below L2:
    a raw name carries no normalised value."""
    value, via, src = rec.values.get(dim), rec.via.get(dim), rec.src.get(dim) or ""
    if value is None or level not in PARAMETER_LEVELS:
        return None
    if level == "L2":
        return value if via == "field" and src.startswith(_STANDARD_SRC) else None
    if level == "L3":
        standard = via == "field" and src.startswith(_STANDARD_SRC)
        return value if standard or (via == "parse" and src == "name") else None
    return value


@dataclass
class Cluster:
    """The records carrying one key at one level. `members` have the key at that level or below; `attached` records
    carry no key here but belong by vocabulary knowledge: a generic report (SHA-2 next to SHA-256, `generic_over`
    markers) or, with closure, a record implying the key's algorithm (HMAC-SHA256 -> SHA-256). Attached records
    confirm and are confirmed inside the cluster but never bring their own keys, so clusters never merge."""

    key: str
    level: str
    members: list[NormRecord] = field(default_factory=list)
    attached: list[NormRecord] = field(default_factory=list)
    reason: dict[str, str] = field(default_factory=dict)  # attached record id -> generic | implied
    _ids: set[str] = field(default_factory=set, repr=False)

    def add(self, rec: NormRecord) -> None:
        if rec.record_id not in self._ids:
            self._ids.add(rec.record_id)
            self.members.append(rec)

    def attach(self, rec: NormRecord, why: str) -> None:
        if rec.record_id not in self._ids:
            self._ids.add(rec.record_id)
            self.attached.append(rec)
            self.reason[rec.record_id] = why

    def holds(self, record_id: str) -> bool:
        return record_id in self._ids

    def is_member(self, record_id: str) -> bool:
        return record_id in self._ids and record_id not in self.reason

    @property
    def records(self) -> list[NormRecord]:
        return self.members + self.attached

    def units(self, vocab: Vocabulary) -> set[str]:
        """The L4 identities of the members: more than one means the level calls distinct algorithms the same."""
        return {u for r in self.members if (u := identity(r, UNIT_LEVEL, vocab))}

    def voters(self, voter_of: dict[str, str]) -> set[str]:
        """The voter groups holding a record of the cluster."""
        return {voter_of.get(r.tool, r.tool) for r in self.records}


@dataclass
class Item:
    """One algorithm of one repository (the unit): the tools reporting it and, per level, the clusters holding one of
    its records (as a member, or attached as a generic or implying record)."""

    key: str
    own: dict[str, list[NormRecord]] = field(default_factory=lambda: defaultdict(list))  # tool -> its records
    implied: set[str] = field(default_factory=set)  # record ids that are own records only by implication (closure)
    clusters: dict[str, list[Cluster]] = field(default_factory=dict)  # level -> clusters (sorted)

    @property
    def tools(self) -> list[str]:
        return sorted(self.own)

    def clusters_at(self, level: str) -> list[Cluster]:
        return self.clusters.get(level, [])

    def clusters_of(self, level: str, tool: str) -> list[Cluster]:
        """The item's clusters at `level` that hold one of the tool's records of the item."""
        mine = {r.record_id for r in self.own.get(tool, [])}
        return [c for c in self.clusters_at(level) if any(c.holds(rid) for rid in mine)]

    def k_at(self, level: str, voter_of: dict[str, str]) -> int:
        """Voter groups in the item's largest cluster at `level` (1 when it has no cluster yet, e.g. an implied item
        below L3: its tool reports it, nobody can match it)."""
        return max((len(c.voters(voter_of)) for c in self.clusters_at(level)), default=1)

    def confirmed_at(self, level: str, tool: str, voter_of: dict[str, str]) -> bool:
        """A cluster holding one of the tool's records of the item also holds a record of another voter group."""
        mine = voter_of.get(tool, tool)
        return any(any(voter_of.get(r.tool, r.tool) != mine for r in c.records) for c in self.clusters_of(level, tool))

    def confirmed_strictly_at(self, level: str, tool: str, voter_of: dict[str, str]) -> bool:
        """Confirmed through members alone: the tool's record is a member of a cluster whose members include another
        voter group (no generic or implied attachment involved)."""
        mine = voter_of.get(tool, tool)
        own = {r.record_id for r in self.own.get(tool, [])}
        return any(
            any(c.is_member(rid) for rid in own) and any(voter_of.get(r.tool, r.tool) != mine for r in c.members)
            for c in self.clusters_of(level, tool)
        )


ATTACH_LEVELS = ("L2", "L3", "L4")  # a generic identity is vocabulary knowledge: it exists from L2 on


def level_matches(
    records: list[NormRecord], vocab: Vocabulary | None = None, *, closure: bool = False
) -> dict[str, Item]:
    """The algorithm records of one repository (every tool) -> {L4 identity: Item} with the clusters per level.

    Only `mapped` records with an L4 identity take part (a name nobody understands is `undefined`, an API name
    `api`: never an item, analysis/normalize/engine.py). At level L the cluster of key k holds every record whose
    identity at some level up to L is k (cumulative, so a match is never lost); a record with several keys sits in
    several clusters, and clusters never merge through it: `hmac.new` (L3 SHA-1, L4 HMAC) is in the SHA-1 and the
    HMAC cluster without making them one. From L2 on a record with a generic identity (SHA-2, EC, the `SHA` marker:
    `Vocabulary.generic`) is attached to the clusters of the specific algorithms it may mean (`specifics`) that
    exist in the repository. With `closure` a record is also an own record (flagged implied) of every algorithm it
    implies and is attached to that algorithm's cluster from L3 on."""
    vocab = vocab or load_vocabulary()
    algorithms = [r for r in records if r.values.get("asset_type") == "algorithm" and r.resolution in (None, "mapped")]
    rec_keys = {r.record_id: keys(r, vocab) for r in algorithms}
    real = sorted((r for r in algorithms if rec_keys[r.record_id].get(UNIT_LEVEL)), key=lambda r: (r.tool, r.record_id))
    items: dict[str, Item] = {}
    implied_by: dict[str, list[NormRecord]] = defaultdict(list)  # implied item -> records implying it
    for r in real:
        unit = rec_keys[r.record_id][UNIT_LEVEL]
        items.setdefault(unit, Item(unit)).own[r.tool].append(r)
        if closure:
            for other in vocab.implied(r.values):
                if other != unit:
                    item = items.setdefault(other, Item(other))
                    item.own[r.tool].append(r)
                    item.implied.add(r.record_id)
                    implied_by[other].append(r)
    for lv in LEVELS:
        upto = LEVELS[: LEVEL_INDEX[lv] + 1]
        clusters: dict[str, Cluster] = {}
        keyset: dict[str, set[str]] = {}
        for r in real:
            ks = {k for level in upto if (k := rec_keys[r.record_id].get(level))}
            keyset[r.record_id] = ks
            for k in ks:
                clusters.setdefault(k, Cluster(k, lv)).add(r)
        clusters = _maximal(clusters, rec_keys)
        if lv in ATTACH_LEVELS:
            for r in real:
                for k in keyset[r.record_id]:
                    if vocab.generic(k):
                        for specific in vocab.specifics(k):
                            c = clusters.get(specific)
                            if c is not None:
                                c.attach(r, "generic")
        if closure and lv in CLOSURE_LEVELS:
            for other, recs in implied_by.items():
                c = clusters.setdefault(other, Cluster(other, lv))
                for r in recs:
                    c.attach(r, "implied")
        by_record: dict[str, list[Cluster]] = defaultdict(list)
        for c in clusters.values():
            for r in c.records:
                by_record[r.record_id].append(c)
        for unit, item in items.items():
            held: dict[int, Cluster] = {}
            for recs in item.own.values():
                for r in recs:
                    for c in by_record.get(r.record_id, []):
                        if r.record_id in item.implied and c.key != unit:
                            continue  # an implying record belongs to the implied item through that cluster only
                        held[id(c)] = c
            item.clusters[lv] = sorted(held.values(), key=lambda c: (c.records[0].tool, c.records[0].record_id))
    return items


def _maximal(clusters: dict[str, Cluster], rec_keys: dict[str, dict[str, str]]) -> dict[str, Cluster]:
    """Drop clusters whose members are a subset of another cluster's members (a lower-level key that brings nothing
    together the higher key does not: `aes` inside AES). Of two clusters with the same members the key of the higher
    level survives (SHA-256 over sha256). What stays are the clusters that say something of their own."""
    by_record: dict[str, list[Cluster]] = defaultdict(list)
    for c in clusters.values():
        for r in c.members:
            by_record[r.record_id].append(c)

    def rank(c: Cluster) -> tuple[int, str]:  # the highest level at which a member carries the key, then the key
        best = max(
            (LEVEL_INDEX[lv] for r in c.members for lv, k in rec_keys[r.record_id].items() if k == c.key), default=-1
        )
        return (best, c.key)

    keep: dict[str, Cluster] = {}
    for c in clusters.values():
        ids = c._ids
        covered = False
        for other in by_record[c.members[0].record_id]:
            if other is c or len(other._ids) < len(ids) or not ids <= other._ids:
                continue
            if len(other._ids) > len(ids) or rank(other) > rank(c):
                covered = True
                break
        if not covered:
            keep[c.key] = c
    return keep


def clusters_at(items: dict[str, Item], level: str) -> list[Cluster]:
    """Every cluster of a repository at `level`, once, sorted by key (the exact matcher's groups)."""
    seen: dict[int, Cluster] = {}
    for item in items.values():
        for c in item.clusters_at(level):
            seen.setdefault(id(c), c)
    return sorted(seen.values(), key=lambda c: c.key)


@dataclass
class LevelRow:
    level: str
    tool: str
    voter_group: str
    items: int  # the tool's items (distinct units per repository); for a parameter: (item, value) pairs
    confirmed: int  # ... that a record of another voter group matches at this level or below
    dimension: str = "algorithm"  # `algorithm`, or one of PARAMETER_DIMS
    confirmed_strict: int = 0  # ... confirmed through cluster members alone (no generic or implied attachment)
    multi_unit_clusters: int = 0  # clusters at this level whose members span several L4 identities (all repos)

    @property
    def share(self) -> float:
        return self.confirmed / self.items if self.items else 0.0

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "level": self.level,
            "tool": self.tool,
            "voter_group": self.voter_group,
            "items": self.items,
            "confirmed": self.confirmed,
            "share": round(self.share, 3),
            "confirmed_strict": self.confirmed_strict,
            "multi_unit_clusters": self.multi_unit_clusters,
        }


def parameter_levels(dim: str) -> tuple[str, ...]:
    """The ladder levels a parameter dimension exists at (from L2, or later when the standard has no field for it)."""
    first = LEVEL_INDEX[DIM_FIRST_LEVEL.get(dim, PARAMETER_LEVELS[0])]
    return tuple(lv for lv in PARAMETER_LEVELS if LEVEL_INDEX[lv] >= first)


def algorithms_at(
    records: list[NormRecord], level: str, vocab: Vocabulary | None = None, *, closure: bool = False
) -> Counter:
    """{identity at `level`: number of records} over the algorithm records of one CBOM; the per-level key sets used
    for run-to-run stability. With `closure` (from L3 on) the algorithms a record implies count as reported too."""
    vocab = vocab or load_vocabulary()
    out: Counter = Counter()
    for r in records:
        if (
            r.values.get("asset_type") == "algorithm"
            and r.resolution in (None, "mapped")
            and identity(r, UNIT_LEVEL, vocab)
        ):
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
        for r in c.records
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
    (every tool pooled); parameter dimensions exist from L2 on."""
    vocab = vocab or load_vocabulary()
    records = {k: v for k, v in records.items() if k not in (exclude or set())}
    by_snap: dict[str, list[NormRecord]] = defaultdict(list)
    for (snap, _), recs in records.items():
        by_snap[snap] += recs
    items_by_snap = {snap: level_matches(recs, vocab, closure=closure) for snap, recs in by_snap.items()}
    tools = sorted({t for _, t in records})
    rows: list[LevelRow] = []
    for dim in dims:
        for level in LEVELS if dim == "algorithm" else parameter_levels(dim):
            multi = 0
            if dim == "algorithm":
                for items in items_by_snap.values():
                    multi += sum(
                        len(c.units(vocab)) > 1 for c in clusters_at(items, level)
                    )  # what the level declares "the same"
            total_n = total_conf = total_strict = 0
            for tool in tools:
                group = voter_of.get(tool, tool)
                n = conf = strict = 0
                for items in items_by_snap.values():
                    for item in items.values():
                        if tool not in item.own:
                            continue
                        if dim == "algorithm":
                            n += 1
                            conf += item.confirmed_at(level, tool, voter_of)
                            strict += item.confirmed_strictly_at(level, tool, voter_of)
                        else:
                            pairs = _parameter_pairs(item, tool, dim)
                            n += len(pairs)
                            conf += sum(_pair_confirmed(item, tool, dim, v, level, voter_of) for _, v in pairs)
                rows.append(LevelRow(level, tool, group, n, conf, dim, strict, multi))
                total_n += n
                total_conf += conf
                total_strict += strict
            rows.append(LevelRow(level, "all", "-", total_n, total_conf, dim, total_strict, multi))
    return rows


def item_rows(
    records: dict[tuple[str, str, str], list[NormRecord]],
    voter_of: dict[str, str],
    *,
    run: int,
    languages: dict[str, str | None] | None = None,
    vocab: Vocabulary | None = None,
    tailored: dict[str, frozenset[str]] | None = None,
    closure: bool = False,
) -> list[dict]:
    """Which items each tool reports per repository: one row per (snapshot, variant, tool, item; `unit` = its L4
    identity) with the
    number of records and distinct spellings behind it and, per level, the voter groups in the item's largest
    cluster (`k_<level>`, the tool's own group included). `records`:
    {(snapshot, variant, tool): records}; `tailored`: {tool: repos it was tuned on} (the `tailored` column,
    refinement 13); with `closure` the items the records imply get rows too (`implied` true, refinement 2).
    Location-free, so every tool takes part; the basis for UpSet plots and the tools-per-item distribution."""
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
                        "unit": key,
                        "registry": unit_in_registry(key, vocab),
                        "implied": all(r.record_id in item.implied for r in own),
                        "records": len(own),
                        "spellings": len({(r.name or "").strip() for r in own}),
                        **{f"k_{lv}": ks[lv] for lv in LEVELS},
                    }
                )
    return rows


def unit_family(unit: str, vocab: Vocabulary) -> str:
    """The family an item's unit (an L4 identity: an algorithm or a family) rests on."""
    return vocab.family_of_algorithm(unit) or unit


def unit_in_registry(unit: str, vocab: Vocabulary) -> bool:
    """Whether the item's family is one of the CycloneDX registry's (`FamilyInfo.registry`), not an addition of
    `algorithms.yaml` (RQ4: the share of items the registry alone would not name)."""
    info = vocab.family_info(unit_family(unit, vocab))
    return bool(info and info.registry)


def layer_sizes(vocab: Vocabulary) -> dict[str, int]:
    """The identifier layer the registry lacks and the vocabulary adds (RQ4), as the size of each table: family
    aliases (spellings of families beyond the canonical names), algorithms (concrete algorithms under the families),
    algorithm aliases, whole names (JOSE, SSH, JCA identifiers), API identifiers, OIDs (beyond the registry's curve
    OIDs) and the cipher-suite grammar's cipher entries."""
    canonical = {fold(n) for n in vocab.families}
    return {
        "family aliases": sum(1 for k in vocab.family_alias if k not in canonical),
        "algorithms": len(vocab.algorithms),
        "algorithm aliases": sum(1 for k in vocab.algorithm_alias if k not in {fold(n) for n in vocab.algorithms}),
        "names": len(vocab.names),
        "apis": len(vocab.apis),
        "oids": len(vocab.oids) - len(vocab.curve_oids),
        "cipher suites": len(vocab.suite_ciphers),
    }


def registry_rows(items: list[dict], vocab: Vocabulary | None = None) -> list[dict]:
    """registry-run<i>.csv, the delta between the CycloneDX registry and what real CBOMs need (RQ4): one row per
    addition of `algorithms.yaml` (`kind: addition`; every addition, also one no item rests on, so the table is the
    complete delta) with its gap kind, primitive, the items of `items` (item_rows) resting on it, the repositories and
    tools behind them and the addition's reason; one row per unit that rests on no known family at all (`kind:
    unknown`: a tool's field value taken as written); then one row per table of the identifier layer (`kind: layer`,
    `layer_sizes`) with its size as `count`."""
    vocab = vocab or load_vocabulary()
    per_family: dict[str, list[dict]] = defaultdict(list)
    unknown: dict[str, list[dict]] = defaultdict(list)
    for r in items:
        fam = unit_family(str(r["unit"]), vocab)
        info = vocab.family_info(fam)
        if info is None:
            unknown[str(r["unit"])].append(r)  # a tool's field value as written, or an algorithm under no family
        elif not info.registry:
            per_family[fam].append(r)
    rows = []
    for name, info in vocab.families.items():
        if info.registry:
            continue
        rs = per_family.get(name, [])
        rows.append(
            {
                "kind": "addition",
                "name": name,
                "gap": info.gap or "",
                "primitive": info.primitive or "",
                "count": len(rs),
                "repos": len({r["repo"] for r in rs}),
                "tools": ";".join(sorted({str(r["tool"]) for r in rs})),
                "note": info.note or "",
            }
        )
    rows.sort(key=lambda r: (r["gap"], -r["count"], r["name"]))
    for name in sorted(unknown, key=lambda u: (-len(unknown[u]), u)):
        rs = unknown[name]
        rows.append(
            {
                "kind": "unknown",
                "name": name,
                "gap": "",
                "primitive": "",
                "count": len(rs),
                "repos": len({r["repo"] for r in rs}),
                "tools": ";".join(sorted({str(r["tool"]) for r in rs})),
                "note": "no family: a tool's field value taken as written, or an algorithm the vocabulary places under "
                "no family",
            }
        )
    for layer, n in layer_sizes(vocab).items():
        rows.append({"kind": "layer", "name": layer, "gap": "", "primitive": "", "count": n, "repos": "", "tools": "",
                     "note": ""})  # fmt: skip
    return rows


def table_lines(rows: list[LevelRow]) -> list[str]:
    """Tools x levels: confirmed share and the number of items; the algorithm dimension only."""
    rows = [r for r in rows if r.dimension == "algorithm"]
    by = {(r.tool, r.level): r for r in rows}
    tools = [t for t in dict.fromkeys(r.tool for r in rows) if t != "all"] + ["all"]
    head = f"{'tool':<15}" + "".join(f"{lv:>8}" for lv in LEVELS) + f"{'items':>12}"
    lines = [head]
    for t in tools:
        cells = "".join(f"{by[(t, lv)].share:>8.0%}" for lv in LEVELS)
        lines.append(f"{t:<15}{cells}{by[(t, 'L4')].items:>12}")
    return lines
