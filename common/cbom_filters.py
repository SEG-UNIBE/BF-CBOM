import json
from typing import Any

# Allowed component.type labels for cryptographic assets (case-insensitive)
INCLUDE_COMPONENT_TYPE_ONLY: list[str] = [
    "cryptographic-asset",
    "cryptographic asset",
    "crypto-assets",
    "crypto asset",
    "crypto-asset",
    "crypto assets",
]


def is_included_component_type(type_value: Any) -> bool:
    """Return True if the provided component type matches allowed labels (case-insensitive)."""
    if type_value is None:
        return False
    val = str(type_value).strip().lower()
    return val in {s.lower() for s in INCLUDE_COMPONENT_TYPE_ONLY}


def _safe_json_loads(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _component_type(comp: dict) -> str:
    if not isinstance(comp, dict):
        return "unknown"
    ctype = comp.get("type")
    if isinstance(ctype, str) and ctype.strip():
        return ctype
    return str(ctype or "unknown")


def filter_cbom_components_include_only(
    raw_json: str,
    include_types: set[str] | tuple[str, ...] | list[str] | None = None,
) -> str:
    """Return JSON text keeping only components of the given types.

    When include_types is empty/None, returns raw_json unchanged.
    """

    includes_lc = {str(t).strip().lower() for t in (include_types or []) if str(t).strip()}
    if not includes_lc:
        return raw_json

    obj = _safe_json_loads(raw_json or "")
    if obj is None:
        return raw_json

    changed = False

    def _filter_list(items: list) -> list:
        nonlocal changed
        if not isinstance(items, list):
            return items
        filtered = []
        for comp in items:
            comp_type_lc = str(_component_type(comp)).strip().lower()
            if comp_type_lc in includes_lc:
                filtered.append(comp)
            else:
                changed = True
        return filtered

    def _apply(node: Any) -> None:
        if isinstance(node, dict):
            comps = node.get("components")
            if isinstance(comps, list):
                node["components"] = _filter_list(comps)
            bom = node.get("bom")
            if isinstance(bom, dict):
                _apply(bom)
        elif isinstance(node, list):
            for entry in node:
                _apply(entry)

    _apply(obj)

    if not changed:
        return raw_json

    try:
        return json.dumps(obj, ensure_ascii=False)
    except TypeError:
        return raw_json
