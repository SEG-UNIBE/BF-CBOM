"""Does a value conform to the CycloneDX version the CBOM declares? (RQ3 sub-questions; not a full validation.)

The normaliser reads leniently (any version, the IBM draft, fields of later versions), so every value it takes is
checked here against the official JSON schema of the declared `specVersion` (vendored in analysis/data/cyclonedx/).
Issue codes, `<kind>:<component-relative path>[=<value>]`:
- `field`       the declared version does not define the field (1.7 `algorithmFamily` in a 1.6 CBOM)
- `enum`        the value is not one the declared version allows (`padding: PSS`, `assetType: key`)
- `type`        wrong JSON type (a string where an integer is required)
- `deprecated`  the declared version deprecates the field (1.6 `curve` in a 1.7 CBOM)
Document level: `dialect:<bomFormat>/<specVersion>` (not CycloneDX, e.g. IBM's `1.4-cbom-1.0`) and
`version:<specVersion>` (a CycloneDX version without crypto fields or without a vendored schema).
Full validation of whole documents (RQ3, analysis/schema.py) is a separate step that uses the same schema files.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "data" / "cyclonedx"
SUPPORTED = ("1.6", "1.7")  # crypto fields exist in CycloneDX only since 1.6


def document_issues(bom_format, spec_version) -> list[str]:
    fmt = str(bom_format or "")
    ver = str(spec_version or "")
    if fmt != "CycloneDX":
        return [f"dialect:{fmt or '?'}/{ver or '?'}"]
    if ver not in SUPPORTED:
        return [f"version:{ver or '?'}"]
    return []


class SchemaIndex:
    """Lookups into one official bom-<version>.schema.json for component-relative paths."""

    def __init__(self, version: str, schema_dir: Path = SCHEMA_DIR):
        self.version = version
        self._dir = schema_dir
        self._docs: dict[str, dict] = {}
        self._root = self._doc(f"bom-{version}.schema.json")
        self._cache: dict[tuple[str, ...], dict | None] = {}

    def _doc(self, name: str) -> dict:
        if name not in self._docs:
            self._docs[name] = json.loads((self._dir / name).read_text(encoding="utf-8"))
        return self._docs[name]

    def _resolve(self, node: dict, doc: str) -> tuple[dict, str]:
        seen = 0
        while isinstance(node, dict) and "$ref" in node and seen < 20:
            ref = node["$ref"]
            target_doc, _, pointer = ref.partition("#")
            doc = target_doc or doc
            node = self._doc(doc)
            for part in [p for p in pointer.split("/") if p]:
                node = node.get(part, {})
            seen += 1
        return node, doc

    def node(self, path: tuple[str, ...]) -> dict | None:
        """Schema node of a field inside a component, e.g. ("cryptoProperties", "algorithmProperties", "mode")."""
        if path in self._cache:
            return self._cache[path]
        doc = f"bom-{self.version}.schema.json"
        node, doc = self._resolve(self._root["definitions"]["component"], doc)
        for key in path:
            node, doc = self._resolve(node, doc)
            if node.get("type") == "array" and "items" in node:
                node, doc = self._resolve(node["items"], doc)
            props = node.get("properties") or {}
            if key not in props:
                self._cache[path] = None
                return None
            node = props[key]
        node, doc = self._resolve(node, doc)
        self._cache[path] = node
        return node

    def check(self, path: tuple[str, ...], value) -> list[str]:
        node = self.node(path)
        dotted = ".".join(path)
        if node is None:
            return [f"field:{dotted}"]
        issues = []
        if node.get("deprecated"):
            issues.append(f"deprecated:{dotted}")
        values = value if node.get("type") == "array" and isinstance(value, list) else [value]
        item = node
        if node.get("type") == "array":
            if not isinstance(value, list):
                return issues + [f"type:{dotted}"]
            item, _ = self._resolve(node.get("items") or {}, f"bom-{self.version}.schema.json")
        for v in values:
            if not _type_ok(item.get("type"), v):
                issues.append(f"type:{dotted}")
            elif item.get("enum") is not None and v not in item["enum"]:
                issues.append(f"enum:{dotted}={v}")
        return issues


def _type_ok(expected, value) -> bool:
    if expected is None:
        return True
    types = expected if isinstance(expected, list) else [expected]
    for t in types:
        if t == "string" and isinstance(value, str):
            return True
        if t == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if t == "number" and isinstance(value, int | float) and not isinstance(value, bool):
            return True
        if t == "boolean" and isinstance(value, bool):
            return True
        if t in ("object", "array") and isinstance(value, dict if t == "object" else list):
            return True
    return False


@lru_cache(maxsize=4)
def schema_index(version: str) -> SchemaIndex | None:
    return SchemaIndex(version) if version in SUPPORTED else None
