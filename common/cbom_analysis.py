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


def _component_type(comp: dict, tool: str) -> str:
    """
    Infer a component "type" field, prioritizing cbomkit's cryptoProperties.assetType
    when present; otherwise use generic `type`.
    """
    if not isinstance(comp, dict):
        return "unknown"
    if tool.lower() in ("cbomkit", "testing"):
        crypto = comp.get("cryptoProperties")
        if isinstance(crypto, dict) and crypto.get("assetType"):
            return str(crypto.get("assetType"))
    return str(comp.get("type", "unknown"))


def analyze_cbom_json(raw_json: str, tool: str) -> tuple[int, Counter]:
    """
    Parse a CBOM JSON string and return (total_components, component_type_counter).
    This function is non-throwing; invalid input yields (0, empty Counter).
    """
    obj = _safe_json_loads(raw_json or "")
    if obj is None:
        return 0, Counter()
    comps = _extract_components(obj)
    counter: Counter = Counter()
    for c in comps:
        counter[_component_type(c, tool)] += 1
    return len(comps), counter
