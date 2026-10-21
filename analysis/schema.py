"""RQ1: do the CBOMs conform to CycloneDX, are they meaningful, are they traceable? (CLAUDE.md Phase 3, `schema.py`)

- **Conformance:** every CBOM that declares a CycloneDX version with a vendored schema (1.6, 1.7) is validated as a
  whole document with `jsonschema` against the official schema (`analysis/data/cyclonedx/`, offline through a
  `referencing` registry). Reported per CBOM: valid, number of errors, error kinds (`<keyword>:<path without
  indices>`). Tools whose CBOM our own worker assembles (`cbom_by: bf-cbom` in common/tools.yaml, the LLM workers) are
  reported apart (refinement 3): their validity would measure us.
- **Meaningful:** per CycloneDX asset type (`assetType`: algorithm, certificate, protocol, related-crypto-material),
  the share of crypto assets (components, not occurrences) carrying the standard's own fields for that type
  (`MEANINGFUL_FIELDS`: an algorithm's primitive, parameter set or curve, mode, padding, functions, OID, ...; a
  key's type, size, algorithm; a certificate's subject, issuer, validity, signature algorithm; a protocol's type,
  version, cipher suites), counted from the raw component (`standard`), and, where the normaliser reads such a value
  from any source (names, properties, symbols, derivations), from the records (`any`). The gap is what sits outside
  the standard. The asset-type set and the field sets are CycloneDX's, so the paper can cite them as such.
- **Traceable:** per asset type: a record names a file (`located_file`) or a file and a line (`located_line`; a line
  is an addition to the file, so both are counted); a record with a line is traceable (weak) when its file exists at
  the commit and the line is inside it; strong (refinement 4) when lines line±k also hold a token of the vocabulary
  (algorithm and family spellings, curve names, whole-name and API identifiers). File contents come from a
  `SnapshotStore` (the VM's git mirror or a fetched snapshot cache); without one the traceability columns stay empty.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from .normalize.conformance import SCHEMA_DIR
from .normalize.vocab import Vocabulary, fold, load_vocabulary
from .snapshots import SnapshotStore
from .tables import LIST_SEP, full_name

VALIDATED_VERSIONS = ("1.6", "1.7")
SCHEMA_COLUMNS = [
    "repo", "snapshot", "variant", "language", "tool", "run", "cbom_by", "bom_format", "spec_version",
    "schema", "valid", "errors", "error_kinds",
]  # fmt: skip
# Per CycloneDX asset type: (dimension label, standard JSON paths relative to `cryptoProperties`, record dimension the
# normaliser fills from any source or None). The paths are the type's own property set in CycloneDX 1.6/1.7.
MEANINGFUL_FIELDS: dict[str, tuple[tuple[str, tuple[str, ...], str | None], ...]] = {
    "algorithm": (
        ("primitive", ("algorithmProperties.primitive",), "primitive"),
        ("parameter", ("algorithmProperties.parameterSetIdentifier", "algorithmProperties.curve",
                       "algorithmProperties.ellipticCurve"), "parameter"),
        ("key_size", ("algorithmProperties.parameterSetIdentifier",), "key_size"),
        ("mode", ("algorithmProperties.mode",), "mode"),
        ("padding", ("algorithmProperties.padding",), "padding"),
        ("functions", ("algorithmProperties.cryptoFunctions",), "functions"),
        ("oid", ("oid",), "oid"),
        ("security_level", ("algorithmProperties.classicalSecurityLevel",
                            "algorithmProperties.nistQuantumSecurityLevel"), None),
    ),
    "related-crypto-material": (
        ("type", ("relatedCryptoMaterialProperties.type",), "material"),
        ("size", ("relatedCryptoMaterialProperties.size",), "key_size"),
        ("algorithm", ("relatedCryptoMaterialProperties.algorithmRef",), "family"),
        ("format", ("relatedCryptoMaterialProperties.format",), None),
        ("secured_by", ("relatedCryptoMaterialProperties.securedBy",), None),
    ),
    "certificate": (
        ("subject", ("certificateProperties.subjectName",), None),
        ("issuer", ("certificateProperties.issuerName",), None),
        ("validity", ("certificateProperties.notValidBefore", "certificateProperties.notValidAfter"), None),
        ("signature_algorithm", ("certificateProperties.signatureAlgorithmRef",), "family"),
        ("public_key", ("certificateProperties.subjectPublicKeyRef",), None),
        ("format", ("certificateProperties.certificateFormat",), None),
    ),
    "protocol": (
        ("type", ("protocolProperties.type",), "protocol"),
        ("version", ("protocolProperties.version",), "protocol_version"),
        ("cipher_suites", ("protocolProperties.cipherSuites",), None),
        ("crypto_refs", ("protocolProperties.cryptoRefArray",), None),
    ),
}  # fmt: skip
MEANINGFUL_COLUMNS = [
    "tool", "cbom_by", "asset_type", "assets", "dimension", "standard", "any", "standard_share", "any_share",
]  # fmt: skip
TRACE_COLUMNS = [
    "repo", "snapshot", "variant", "language", "tool", "run", "asset_type", "source", "records", "located_file",
    "located_line", "file_exists", "line_in_range", "strong", "share_located_file", "share_located_line",
    "share_weak", "share_strong",
]  # fmt: skip
_CRYPTO_TYPES = {"algorithm", "certificate", "protocol", "related-crypto-material"}
_NO_INFO = {"other", "unknown", "none", ""}
# Protocols of the vocabulary that CycloneDX's protocolProperties.type enum has no value for (they are `other` there)
PROTOCOLS_TYPE_OTHER = frozenset({"SSL", "OpenPGP", "OTR"})
_STANDARD_SRC = "cryptoProperties."
_MAX_KINDS = 6


# ----- conformance -----


_TRUE, _FALSE = object(), object()


def _hashable(v):
    """A JSON value as a hashable one with jsonschema's equality (key order ignored, 1 == 1.0, true != 1)."""
    if isinstance(v, dict):
        return frozenset((k, _hashable(x)) for k, x in v.items())
    if isinstance(v, list):
        return tuple(_hashable(x) for x in v)
    if isinstance(v, bool):
        return _TRUE if v else _FALSE
    return v


def _unique_items(validator, unique, instance, schema):
    """`uniqueItems` in linear time. jsonschema compares unhashable items pair by pair, and a CBOM's `components`
    are unique items: 55 s for one 10'141-component CBOM, quadratic in the components."""
    if unique and validator.is_type(instance, "array") and len({_hashable(x) for x in instance}) < len(instance):
        from jsonschema.exceptions import ValidationError

        yield ValidationError("has non-unique elements")


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
    cls = jsonschema.validators.extend(jsonschema.Draft7Validator, {"uniqueItems": _unique_items})
    return cls(docs[names[0]], registry=registry)


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


def schema_row(meta: dict, cbom_path: Path, cbom: dict, *, language: str | None, cbom_by: str | None) -> dict:
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
        "cbom_by": cbom_by,
        "bom_format": cbom.get("bomFormat"),
        "spec_version": cbom.get("specVersion"),
        "schema": result["schema"],
        "valid": result["valid"],
        "errors": result["errors"],
        "error_kinds": LIST_SEP.join(f"{k}={n}" for k, n in kinds),
    }


# ----- meaningful -----


def _present(component: dict, paths: tuple[str, ...]) -> bool:
    """One of the standard fields holds a value that carries information (not `other`/`unknown`, not empty)."""
    for path in paths:
        cur: object = component.get("cryptoProperties") or {}
        for part in path.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur in (None, "", [], {}) or (isinstance(cur, str) and cur.strip().lower() in _NO_INFO):
            continue
        return True
    return False


def _record_has(values: dict, dim: str | None) -> bool:
    if dim is None:
        return False
    # parameterSetIdentifier is read as a key size for keyed families, a parameter set or a curve otherwise
    dims = ("param_set", "curve", "key_size") if dim == "parameter" else (dim,)
    return any(values.get(d) not in (None, "", (), []) for d in dims)


def meaningful_rows(normalized, cbom_by: dict[str, str | None]) -> list[dict]:
    """One row per (tool, asset type, dimension) over all CBOMs of `normalized` ((meta, path, NormalizedCbom)
    triples): `standard` counts assets whose raw component carries the field (the CBOM next to the normalised
    one), `any` counts assets with a value from any source in the records (None for fields the normaliser does
    not read). Components are counted once however many occurrences they have."""
    assets: dict[tuple[str, str], int] = Counter()
    standard: dict[tuple[str, str], Counter] = defaultdict(Counter)
    any_src: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for _, cbom_path, n in normalized:
        try:
            comps = _flat(json.loads(Path(cbom_path).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            comps = []
        seen: set[int] = set()
        for r in n.records:
            at = r.values.get("asset_type")
            if at not in MEANINGFUL_FIELDS or r.component_index in seen:
                continue
            seen.add(r.component_index)
            key = (n.tool, at)
            assets[key] += 1
            comp = comps[r.component_index] if r.component_index < len(comps) else {}
            for label, paths, dim in MEANINGFUL_FIELDS[at]:
                standard[key][label] += _present(comp, paths)
                if dim is not None:
                    any_src[key][label] += _record_has(r.values, dim)
    rows = []
    for (tool, at), total in sorted(assets.items()):
        for label, _, dim in MEANINGFUL_FIELDS[at]:
            s_, a = standard[(tool, at)][label], any_src[(tool, at)][label] if dim is not None else None
            rows.append(
                {
                    "tool": tool,
                    "cbom_by": cbom_by.get(tool),
                    "asset_type": at,
                    "assets": total,
                    "dimension": label,
                    "standard": s_,
                    "any": a,
                    "standard_share": round(s_ / total, 3) if total else 0.0,
                    "any_share": round(a / total, 3) if total and a is not None else None,
                }
            )
    return rows


def _flat(cbom: dict) -> list[dict]:
    """The components in the order the normaliser indexes them (nested components included, depth first)."""
    out: list[dict] = []

    def walk(components) -> None:
        for c in components or []:
            if isinstance(c, dict):
                out.append(c)
                walk(c.get("components"))

    walk((cbom or {}).get("components"))
    return out


# ----- traceable -----


def alias_tokens(vocab: Vocabulary | None = None) -> frozenset[str]:
    """Folded spellings a source line may contain near a reported location: algorithm, family and curve aliases,
    whole-name identifiers (JOSE, SSH, JCA, APIs) and the API table; very short ones are left out."""
    vocab = vocab or load_vocabulary()
    keys = set(vocab.algorithm_alias) | set(vocab.family_alias) | set(vocab.curve_alias) | set(vocab.names)
    keys |= {fold(k) for k, _ in vocab.apis}
    return frozenset(k for k in keys if len(k) >= 3 and k not in vocab.noise)


LOCATION_KINDS = ("line", "window", "import", "file", "none", "unread")
# An import statement, by file extension (a line inside Go's `import ( ... )` block is a quoted path on its own)
_IMPORT_LINE = {
    ".java": re.compile(r"^\s*import\s+(static\s+)?[\w.]+(\.\*)?\s*;"),
    ".py": re.compile(r"^\s*(import\s+[\w.]|from\s+[.\w]+\s+import\s)"),
    ".go": re.compile(r'^\s*(import\s*(\(|"|[\w.]+\s+")|([\w.]+\s+)?"[\w./-]+"\s*(//.*)?$)'),
    ".js": re.compile(r"^\s*(import\s|export\s.*\sfrom\s|(const|let|var)\s.*=\s*require\s*\()"),
    ".c": re.compile(r"^\s*#\s*include\b"),
    ".rs": re.compile(r"^\s*(pub\s+)?(use\s|extern\s+crate\b)"),
}
for _ext, _same in ((".ts", ".js"), (".mjs", ".js"), (".cjs", ".js"), (".jsx", ".js"), (".tsx", ".js"),
                    (".h", ".c"), (".cc", ".c"), (".cpp", ".c"), (".hpp", ".c")):  # fmt: skip
    _IMPORT_LINE[_ext] = _IMPORT_LINE[_same]


def location_kind(rec, store: SnapshotStore, repo: str, commit: str | None) -> str:
    """What kind of place a record names (LOCATION_KINDS): `none` no file, `file` a file without a line, `window` a
    span of several lines, `import` a single line holding an import statement, `line` any other single line (in
    practice the call or the declaration), `unread` a single line whose file the snapshot does not have here (no
    mirror and no `--fetch`, or a file missing at the commit)."""
    if not rec.file:
        return "none"
    if not rec.line:
        return "file"
    if rec.line_end and int(rec.line_end) > int(rec.line):
        return "window"
    files = store.files(repo, commit)
    text = store.read(repo, commit, rec.file) if files is not None and rec.file in files else None
    lines = text.splitlines() if text is not None else []
    if not 1 <= int(rec.line) <= len(lines):
        return "unread"
    pattern = _IMPORT_LINE.get(Path(rec.file).suffix.lower())
    return "import" if pattern and pattern.match(lines[int(rec.line) - 1]) else "line"


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
    """One row per (snapshot, variant, tool, asset type): how many records name a file, a file and a line, exist at
    the commit, point inside the file (weak) and near a vocabulary token (strong). Library records are left out; key
    material gets its own rows (secrets in configuration files would otherwise dominate)."""
    tokens = alias_tokens(vocab)
    rows = []
    for meta, cbom_path, n in normalized:
        snapshot, variant = cbom_path.parents[2].name, cbom_path.parents[1].name
        repo = full_name(snapshot)
        commit = meta.get("variant_commit") or meta.get("base_commit")
        source = store.source(repo, commit)
        by_type: dict[str, list] = defaultdict(list)
        for r in n.records:
            at = r.values.get("asset_type")
            if at != "library":
                by_type[at or "unknown"].append(r)
        for at in sorted(by_type):
            records = by_type[at]
            counts: Counter = Counter()
            for r in records:
                counts["located_file"] += bool(r.file)
                located, exists, in_range, strong = trace_record(r, store, repo, commit, tokens, window=window)
                counts["located_line"] += located
                counts["file_exists"] += bool(exists)
                counts["line_in_range"] += bool(in_range)
                counts["strong"] += bool(strong)
            total, with_line = len(records), counts["located_line"]
            rows.append(
                {
                    "repo": repo,
                    "snapshot": snapshot,
                    "variant": variant,
                    "language": languages.get(repo),
                    "tool": n.tool,
                    "run": meta.get("run_index"),
                    "asset_type": at,
                    "source": source,
                    "records": total,
                    "located_file": counts["located_file"],
                    "located_line": with_line,
                    "file_exists": counts["file_exists"] if source else None,
                    "line_in_range": counts["line_in_range"] if source else None,
                    "strong": counts["strong"] if source else None,
                    "share_located_file": round(counts["located_file"] / total, 3) if total else None,
                    "share_located_line": round(with_line / total, 3) if total else None,
                    "share_weak": round(counts["line_in_range"] / with_line, 3) if source and with_line else None,
                    "share_strong": round(counts["strong"] / with_line, 3) if source and with_line else None,
                }
            )
    return rows
