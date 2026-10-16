"""Adapter specs: one YAML file per tool (analysis/adapters/<tool>.yaml) with its mapping and dictionary.

A spec states where each dimension sits in the tool's CBOM and how the tool spells things; the engine
(analysis/normalize/engine.py) interprets it. Specs inherit (`inherits:`) from reader specs (`_cyclonedx.yaml`,
`_ibm-cbom-draft.yaml`) or shared bases (`_cbomkit.yaml`, `_llm.yaml`) and only state their differences:

- `fields`, `findings`, `dictionary`: merged key by key (a child value `null` removes a parent entry);
- `sources`, `locations`, `context`: a child list replaces the parent's, the entry `base` splices the parent list in;
- everything else (`version`, `declared`, `checked_on`, `notes`): the child wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from .vocab import clean_name, fold

ADAPTER_DIR = Path(__file__).resolve().parents[1] / "adapters"
DEFAULT_BASE = "_cyclonedx"
SOURCE_KINDS = ("fields", "property", "parse", "api", "dependencies", "refs", "extract")
TEXT_PREFIXES = ("occurrence:", "property:", "field:", "rule:")
FIELD_DIMS = (
    "asset_type", "material", "family", "algorithm", "primitive", "functions", "mode", "padding", "curve",
    "key_size", "digest", "param_set", "iterations", "oid", "protocol", "protocol_version", "purl", "confidence",
    "param", "ref", "parse", "size",
)  # fmt: skip
LOCATION_EXTRACTORS = ("evidence_occurrences", "property_locations", "detection_context", "src_file_property")
EXTRACTORS = ("json_context",)
ALLOWED_KEYS = {
    "spec", "inherits", "version", "declared", "checked_on", "findings", "fields", "sources", "locations",
    "context", "dictionary", "notes",
}  # fmt: skip


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class SourceDef:
    kind: str
    arg: object = None
    when: dict | None = None  # {"asset_type": [...]} : only for these asset types
    only: tuple = ()  # restrict the dimensions this source may set
    quiet: bool = False  # do not report leftover tokens as unmapped (file names, matched code)

    @property
    def per_occurrence(self) -> bool:
        if self.kind in ("parse", "api"):
            return str(self.arg).startswith("occurrence:")
        return self.kind == "extract"


@dataclass
class AdapterSpec:
    tool: str
    chain: list[str]  # ["cryptoscan@1", "_cyclonedx@1"]
    findings: dict
    fields: dict[str, str]
    sources: list[SourceDef]
    locations: list  # extractor names, or {properties: {file: <name>, line: <name>}}
    context: list[str]
    dictionary: dict[str, dict[str, object]]  # dim -> {folded raw or prefix*: value}
    declared: list = field(default_factory=list)
    checked_on: str = ""
    notes: str = ""
    fallback: bool = False  # no spec for this tool: the generic reader was used

    @property
    def label(self) -> str:
        return " < ".join(self.chain)

    def lookup(self, dim: str, raw) -> object | None:
        """The tool's own dictionary: exact (case/separator-insensitive) or `prefix*` entries."""
        table = self.dictionary.get(dim)
        if not table or raw is None:
            return None
        for key in dict.fromkeys((fold(raw), fold(clean_name(str(raw))))):
            if key in table:
                return table[key]
            for k, v in table.items():
                if k.endswith("*") and key.startswith(k[:-1]):
                    return v
        return None


def _read(name: str, dirs: list[Path]) -> dict:
    path = next((d / f"{name}.yaml" for d in dirs if (d / f"{name}.yaml").exists()), None)
    if path is None:
        raise SpecError(f"no adapter spec {name}.yaml in {[str(d) for d in dirs]}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    unknown = set(data) - ALLOWED_KEYS
    if unknown:
        raise SpecError(f"{path.name}: unknown keys {sorted(unknown)}")
    return data


def _splice(parent: list | None, child: list | None) -> list:
    if child is None:
        return list(parent or [])
    out: list = []
    for item in child:
        if item == "base":
            out.extend(parent or [])
        else:
            out.append(item)
    return out


def _merge(parent: dict, child: dict) -> dict:
    out = dict(parent)
    for key, value in child.items():
        if key in ("fields", "findings"):
            merged = dict(parent.get(key) or {})
            for k, v in (value or {}).items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            out[key] = merged
        elif key == "dictionary":
            merged = {d: dict(t) for d, t in (parent.get(key) or {}).items()}
            for dim, table in (value or {}).items():
                merged.setdefault(dim, {}).update(table or {})
            out[key] = merged
        elif key in ("sources", "locations", "context"):
            out[key] = _splice(parent.get(key), value)
        else:
            out[key] = value
    return out


def _source(entry, where: str) -> SourceDef:
    if isinstance(entry, str):
        if entry not in ("dependencies", "refs"):
            raise SpecError(f"{where}: unknown source {entry!r}")
        return SourceDef(kind=entry)
    if not isinstance(entry, dict):
        raise SpecError(f"{where}: a source must be a mapping, got {entry!r}")
    kinds = [k for k in entry if k in SOURCE_KINDS]
    if len(kinds) != 1:
        raise SpecError(f"{where}: a source needs exactly one of {SOURCE_KINDS}, got {sorted(entry)}")
    kind = kinds[0]
    extra = set(entry) - {kind, "when", "only", "quiet"}
    if extra:
        raise SpecError(f"{where}: unknown source options {sorted(extra)}")
    arg = entry[kind]
    if kind in ("parse", "api"):
        if not (arg == "name" or str(arg).startswith(TEXT_PREFIXES)):
            raise SpecError(f"{where}: {kind} needs `name` or one of {TEXT_PREFIXES}, got {arg!r}")
    if kind == "property" and not isinstance(arg, dict):
        raise SpecError(f"{where}: property needs a mapping property name -> dimension")
    if kind == "property":
        bad = [d for d in arg.values() if d not in FIELD_DIMS]
        if bad:
            raise SpecError(f"{where}: unknown dimensions {bad}")
    if kind == "extract" and arg not in EXTRACTORS:
        raise SpecError(f"{where}: unknown extractor {arg!r} (known: {EXTRACTORS})")
    only = tuple(entry.get("only") or ())
    when = entry.get("when")
    return SourceDef(kind=kind, arg=arg, when=when, only=only, quiet=bool(entry.get("quiet")))


def build_spec(tool: str, data: dict, chain: list[str], *, fallback: bool = False) -> AdapterSpec:
    fields = dict(data.get("fields") or {})
    bad = sorted({d for d in fields.values() if d not in FIELD_DIMS})
    if bad:
        raise SpecError(f"{tool}: unknown field dimensions {bad}")
    locations = list(data.get("locations") or [])
    for loc in locations:
        if isinstance(loc, dict):  # {properties: {file: <property>, line: <property>}}
            pair = loc.get("properties")
            if set(loc) != {"properties"} or not isinstance(pair, dict) or "file" not in pair:
                raise SpecError(f"{tool}: a location mapping needs `properties: {{file: <name>, line: <name>}}`")
        elif loc not in LOCATION_EXTRACTORS:
            raise SpecError(f"{tool}: unknown location extractor {loc!r} (known: {LOCATION_EXTRACTORS})")
    dictionary = {
        str(dim): {(fold(k[:-1]) + "*" if str(k).endswith("*") else fold(k)): v for k, v in (table or {}).items()}
        for dim, table in (data.get("dictionary") or {}).items()
    }
    declared = data.get("declared") or []
    return AdapterSpec(
        tool=tool,
        chain=chain,
        findings=dict(data.get("findings") or {}),
        fields=fields,
        sources=[_source(s, f"{tool} sources[{i}]") for i, s in enumerate(data.get("sources") or [])],
        locations=locations,
        context=[str(c) for c in data.get("context") or []],
        dictionary=dictionary,
        declared=declared if isinstance(declared, list) else [declared],
        checked_on=str(data.get("checked_on") or ""),
        notes=str(data.get("notes") or ""),
        fallback=fallback,
    )


@lru_cache(maxsize=64)
def load_adapter(tool: str, adapter_dir: str | None = None) -> AdapterSpec:
    """The merged spec of `tool`; tools without a spec get the generic CycloneDX reader (`fallback=True`)."""
    directory = Path(adapter_dir) if adapter_dir else ADAPTER_DIR
    dirs = list(dict.fromkeys([directory, ADAPTER_DIR]))  # specs elsewhere may inherit the shipped bases
    name = tool if (directory / f"{tool}.yaml").exists() else DEFAULT_BASE
    chain_data: list[tuple[str, dict]] = []
    seen: set[str] = set()
    while name:
        if name in seen:
            raise SpecError(f"inheritance cycle at {name}")
        seen.add(name)
        data = _read(name, dirs)
        chain_data.append((name, data))
        name = data.get("inherits")
    merged: dict = {}
    for _, data in reversed(chain_data):
        merged = _merge(merged, data)
    chain = [f"{n}@{d.get('version', 1)}" for n, d in chain_data]
    return build_spec(tool, merged, chain, fallback=chain_data[0][0] != tool)


def spec_files(adapter_dir: Path = ADAPTER_DIR) -> list[Path]:
    return sorted(p for p in adapter_dir.glob("*.yaml"))
