import json
from collections import Counter
from typing import Any

# Common crypto asset types used across tools/tests
COMMON_CRYPTO_ASSET_TYPES = [
    "algorithm",
    "key",
    "certificate",
    "digest",
    "related-crypto-material",
]


def get_crypto_asset_types() -> list[str]:
    """Return a copy of common crypto asset types."""
    return list(COMMON_CRYPTO_ASSET_TYPES)


def _safe_json_loads(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_components(obj: Any) -> list[dict]:
    """
    Best-effort extraction of components from diverse CBOM shapes.
    Supports:
    - { components: [...] }
    - { bom: { components: [...] } }
    - [ { bom: { components: [...] } }, ... ]
    - Fallback: returns []
    """
    if isinstance(obj, dict):
        if isinstance(obj.get("components"), list):
            return obj.get("components", [])
        bom = obj.get("bom")
        if isinstance(bom, dict) and isinstance(bom.get("components"), list):
            return bom.get("components", [])
        return []
    if isinstance(obj, list) and obj:
        first = obj[0]
        if isinstance(first, dict):
            if isinstance(first.get("components"), list):
                return first.get("components", [])
            bom = first.get("bom")
            if isinstance(bom, dict) and isinstance(bom.get("components"), list):
                return bom.get("components", [])
    return []


def _component_type(comp: dict) -> str:
    """Return the component's declared type, defaulting to ``"unknown"``."""
    if not isinstance(comp, dict):
        return "unknown"
    ctype = comp.get("type")
    if isinstance(ctype, str) and ctype.strip():
        return ctype
    return str(ctype or "unknown")


def _asset_type_label(comp: dict) -> str:
    """Return a formatted crypto asset type (optionally suffixed with primitive)."""
    if not isinstance(comp, dict):
        return ""
    crypto = comp.get("cryptoProperties")
    if not isinstance(crypto, dict):
        return ""
    asset_type = crypto.get("assetType")
    if isinstance(asset_type, str) and asset_type.strip():
        label = asset_type
    elif asset_type is not None:
        label = str(asset_type)
    else:
        label = ""
    if not label:
        return ""
    algo_props = crypto.get("algorithmProperties")
    if isinstance(algo_props, dict):
        primitive = algo_props.get("primitive")
        if isinstance(primitive, str) and primitive.strip():
            return f"{label} ({primitive})"
    return label


def analyze_cbom_json(raw_json: str, _tool: str) -> tuple[int, Counter, Counter, Counter]:
    """
    Parse a CBOM JSON string and return
    ``(total_components, type_counter, combo_counter, detail_counter)``.
    ``type_counter`` aggregates by component ``type``. ``combo_counter`` aggregates
    by ``(component type, asset type with optional primitive)``. ``detail_counter``
    aggregates by ``(component type, asset label, component name)`` to enable name-level
    tabulations in the UI.
    This function is non-throwing; invalid input yields (0, empty Counter).
    """
    obj = _safe_json_loads(raw_json or "")
    if obj is None:
        return 0, Counter(), Counter(), Counter()
    comps = _extract_components(obj)
    type_counter: Counter = Counter()
    combo_counter: Counter = Counter()
    detail_counter: Counter = Counter()
    for c in comps:
        comp_type = _component_type(c)
        type_counter[comp_type] += 1
        asset_label = _asset_type_label(c)
        combo_counter[(comp_type, asset_label)] += 1
        comp_name = ""
        if isinstance(c, dict):
            name_val = c.get("name")
            if isinstance(name_val, str) and name_val.strip():
                before, sep, _ = name_val.partition("@")
                comp_name = before if sep else name_val
            elif name_val is not None:
                comp_name = str(name_val)
        detail_counter[(comp_type, asset_label, comp_name)] += 1
    return len(comps), type_counter, combo_counter, detail_counter
