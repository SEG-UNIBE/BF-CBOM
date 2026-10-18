"""RQ1: do the CBOMs conform to CycloneDX, are they meaningful, are they traceable? (CLAUDE.md Phase 3, `schema.py`)

- **Conformance:** every CBOM that declares a CycloneDX version with a vendored schema (1.6, 1.7) is validated as a
  whole document with `jsonschema` against the official schema (`analysis/data/cyclonedx/`, offline through a
  `referencing` registry). Reported per CBOM: valid, number of errors, error kinds (`<keyword>:<path without
  indices>`). Tools whose CBOM our own adapter writes (`native_cyclonedx: false` in common/tools.yaml) are reported
  apart (refinement 3): their validity would measure us.
- **Meaningful:** the share of crypto assets (components, not occurrences) that carry an asset type, a primitive, a
  parameter (parameter set or curve), a key size, a mode and an OID: counted once from the standard CycloneDX fields
  only (`via == field` and a `cryptoProperties.` source) and once from any source the normaliser reads (names,
  properties, symbols, derivations). The gap is what sits outside the standard.
- **Traceable:** a located record is traceable (weak) when its file exists at the commit and its line is inside the
  file; strong (refinement 4) when lines line±k also hold a token of the vocabulary (algorithm and family spellings,
  curve names, whole-name and API identifiers). File contents come from a `SnapshotStore` (the VM's git mirror or a
  fetched snapshot cache); without one the traceability columns stay empty.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from .normalize.conformance import SCHEMA_DIR
from .normalize.vocab import Vocabulary, fold, load_vocabulary
from .snapshots import SnapshotStore
from .tables import LIST_SEP, full_name

VALIDATED_VERSIONS = ("1.6", "1.7")
SCHEMA_COLUMNS = [
    "repo", "snapshot", "variant", "language", "tool", "run", "native_cyclonedx", "bom_format", "spec_version",
    "schema", "valid", "errors", "error_kinds",
]  # fmt: skip
MEANINGFUL_DIMS = ("asset_type", "primitive", "parameter", "key_size", "mode", "oid")
MEANINGFUL_COLUMNS = [
    "tool", "native_cyclonedx", "crypto_assets", "dimension", "standard", "any", "standard_share", "any_share",
]  # fmt: skip
TRACE_COLUMNS = [
    "repo", "snapshot", "variant", "language", "tool", "run", "source", "records", "located", "file_exists",
    "line_in_range", "strong", "share_located", "share_weak", "share_strong",
]  # fmt: skip
_CRYPTO_TYPES = {"algorithm", "certificate", "protocol", "related-crypto-material"}
_STANDARD_SRC = "cryptoProperties."
_MAX_KINDS = 6


# ----- conformance -----


@lru_cache(maxsize=4)
def validator(version: str):
    """Draft-07 validator of the vendored bom-<version> schema with its local references, or None."""
    if version not in VALIDATED_VERSIONS:
        return None
    import jsonschema
    from referencing import Registry, Resource

    names = (f"bom-{version}.schema.json", "spdx.schema.json", "jsf-0.82.schema.json")
    if version == "1.7":
        names += ("cryptography-defs.schema.json",)
    docs = {n: json.loads((SCHEMA_DIR / n).read_text(encoding="utf-8")) for n in names}
    registry = Registry().with_resources((d["$id"], Resource.from_contents(d)) for d in docs.values())
    return jsonschema.Draft7Validator(docs[names[0]], registry=registry)


def _path_class(path) -> str:
    return ".".join(str(p) for p in path if not isinstance(p, int)) or "$"


def validate_document(cbom: dict) -> dict:
    """{schema, valid, errors, error_kinds}: `schema` None (and `valid` None) when the CBOM declares no CycloneDX
    version a vendored schema covers (the IBM draft, 1.5 and older)."""
    fmt, version = str(cbom.get("bomFormat") or ""), str(cbom.get("specVersion") or "")
    v = validator(version) if fmt == "CycloneDX" else None
    if v is None:
        return {"schema": None, "valid": None, "errors": None, "error_kinds": {}}
    kinds: Counter = Counter()
    for err in v.iter_errors(cbom):
        kinds[f"{err.validator}:{_path_class(err.absolute_path)}"] += 1
    return {"schema": version, "valid": not kinds, "errors": sum(kinds.values()), "error_kinds": dict(kinds)}


def schema_row(meta: dict, cbom_path: Path, cbom: dict, *, language: str | None, native: bool | None) -> dict:
    snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
    result = validate_document(cbom)
    kinds = sorted(result["error_kinds"].items(), key=lambda kv: (-kv[1], kv[0]))[:_MAX_KINDS]
    return {
        "repo": full_name(snapshot),
        "snapshot": snapshot,
        "variant": variant,
        "language": language,
        "tool": meta.get("worker") or cbom_path.parent.name,
        "run": meta.get("run_index"),
        "native_cyclonedx": native,
        "bom_format": cbom.get("bomFormat"),
        "spec_version": cbom.get("specVersion"),
        "schema": result["schema"],
        "valid": result["valid"],
        "errors": result["errors"],
        "error_kinds": LIST_SEP.join(f"{k}={n}" for k, n in kinds),
    }


# ----- meaningful -----


def _component_values(records) -> dict[int, tuple[dict, dict, dict]]:
    """{component index: (values, src, via)} of the crypto-asset components behind the records."""
    out: dict[int, tuple[dict, dict, dict]] = {}
    for r in records:
        if r.values.get("asset_type") in _CRYPTO_TYPES and r.component_index not in out:
            out[r.component_index] = (r.values, r.src, r.via)
    return out


def _has(values: dict, src: dict, via: dict, dim: str, *, standard: bool) -> bool:
    dims = ("param_set", "curve") if dim == "parameter" else (dim,)
    for d in dims:
        if values.get(d) is None:
            continue
        if not standard or (via.get(d) == "field" and str(src.get(d) or "").startswith(_STANDARD_SRC)):
            return True
    return False


def meaningful_rows(normalized, native_of: dict[str, bool | None]) -> list[dict]:
    """One row per (tool, dimension) over all CBOMs of `normalized` ((meta, path, NormalizedCbom) triples)."""
    assets: Counter = Counter()
    standard: dict[str, Counter] = defaultdict(Counter)
    any_src: dict[str, Counter] = defaultdict(Counter)
    for _, _, n in normalized:
        for values, src, via in _component_values(n.records).values():
            assets[n.tool] += 1
            for dim in MEANINGFUL_DIMS:
                standard[n.tool][dim] += _has(values, src, via, dim, standard=True)
                any_src[n.tool][dim] += _has(values, src, via, dim, standard=False)
    rows = []
    for tool in sorted(assets):
        total = assets[tool]
        for dim in MEANINGFUL_DIMS:
            s, a = standard[tool][dim], any_src[tool][dim]
            rows.append(
                {
                    "tool": tool,
                    "native_cyclonedx": native_of.get(tool),
                    "crypto_assets": total,
                    "dimension": dim,
                    "standard": s,
                    "any": a,
                    "standard_share": round(s / total, 3) if total else 0.0,
                    "any_share": round(a / total, 3) if total else 0.0,
                }
            )
    return rows


# ----- traceable -----


def alias_tokens(vocab: Vocabulary | None = None) -> frozenset[str]:
    """Folded spellings a source line may contain near a reported location: algorithm, family and curve aliases,
    whole-name identifiers (JOSE, SSH, JCA, APIs) and the API table; very short ones are left out."""
    vocab = vocab or load_vocabulary()
    keys = set(vocab.algorithm_alias) | set(vocab.family_alias) | set(vocab.curve_alias) | set(vocab.names)
    keys |= {fold(k) for k, _ in vocab.apis}
    return frozenset(k for k in keys if len(k) >= 3 and k not in vocab.noise)


def trace_record(rec, store: SnapshotStore, repo: str, commit: str | None, tokens: frozenset[str], *, window: int):
    """(located, file_exists, line_in_range, strong) for one record; None entries when the store has no snapshot."""
    located = bool(rec.file and rec.line)
    if not located:
        return located, None, None, None
    files = store.files(repo, commit)
    if files is None:
        return located, None, None, None
    exists = rec.file in files
    if not exists:
        return located, False, False, False
    text = store.read(repo, commit, rec.file)
    lines = text.splitlines() if text is not None else []
    in_range = 1 <= int(rec.line) <= len(lines)
    if not in_range:
        return located, True, False, False
    lo, hi = max(0, int(rec.line) - 1 - window), min(len(lines), int(rec.line) + window)
    nearby = fold(" ".join(lines[lo:hi]))
    strong = any(tok in nearby for tok in tokens)
    return located, True, True, strong


def traceability_rows(
    normalized,
    store: SnapshotStore,
    *,
    languages: dict[str, str | None],
    vocab: Vocabulary | None = None,
    window: int = 2,
) -> list[dict]:
    """One row per (snapshot, variant, tool): how many records are located, exist at the commit, point inside the
    file (weak) and near a vocabulary token (strong). Library records are left out."""
    tokens = alias_tokens(vocab)
    rows = []
    for meta, cbom_path, n in normalized:
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        repo = full_name(snapshot)
        commit = meta.get("variant_commit") or meta.get("base_commit")
        counts = Counter()
        records = [r for r in n.records if r.values.get("asset_type") != "library"]
        for r in records:
            located, exists, in_range, strong = trace_record(r, store, repo, commit, tokens, window=window)
            counts["located"] += located
            counts["file_exists"] += bool(exists)
            counts["line_in_range"] += bool(in_range)
            counts["strong"] += bool(strong)
        source = store.source(repo, commit)
        total, located = len(records), counts["located"]
        rows.append(
            {
                "repo": repo,
                "snapshot": snapshot,
                "variant": variant,
                "language": languages.get(repo),
                "tool": n.tool,
                "run": meta.get("run_index"),
                "source": source,
                "records": total,
                "located": located,
                "file_exists": counts["file_exists"] if source else None,
                "line_in_range": counts["line_in_range"] if source else None,
                "strong": counts["strong"] if source else None,
                "share_located": round(located / total, 3) if total else None,
                "share_weak": round(counts["line_in_range"] / located, 3) if source and located else None,
                "share_strong": round(counts["strong"] / located, 3) if source and located else None,
            }
        )
    return rows
