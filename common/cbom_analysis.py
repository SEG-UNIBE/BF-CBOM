import json
from collections import Counter, defaultdict
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


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def summarize_component_types(
    comp_rows: list[dict],
    workers: list[str] | tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    """Aggregate component type/name counts per repo for display tables."""

    workers_list = list(workers or [])
    repo_summary: dict[str, dict[tuple[str, str, str], dict[str, int]]] = {}

    for row in comp_rows:
        repo_name = row.get("repo")
        worker_name = row.get("worker")
        if not repo_name or not worker_name:
            continue

        combo_counts = row.get("type_asset_counts") or {}
        detail_counts = row.get("type_asset_name_counts") or {}
        type_counts = row.get("types") or {}

        repo_map = repo_summary.setdefault(repo_name, {})
        if detail_counts:
            source_iter = detail_counts.items()
        elif combo_counts:
            source_iter = [
                ((str(comp_type) or "(unknown)", str(asset_label or ""), ""), count)
                for (comp_type, asset_label), count in combo_counts.items()
            ]
        else:
            source_iter = [((str(comp_type) or "(unknown)", "", ""), count) for comp_type, count in type_counts.items()]

        for key, count in source_iter:
            if isinstance(key, tuple) and len(key) == 3:
                base_type, asset_label, name = key
            elif isinstance(key, tuple) and len(key) == 2:
                base_type, asset_label = key
                name = ""
            else:
                base_type, asset_label, name = key, "", ""

            comp_type = str(base_type or "(unknown)")
            asset_type = str(asset_label or "")
            name_val = str(name or "")

            type_map = repo_map.setdefault((comp_type, asset_type, name_val), {})
            type_map[worker_name] = _coerce_int(count)

    result: dict[str, list[dict[str, Any]]] = {}

    for repo_name, type_map in repo_summary.items():
        grouped: dict[tuple[str, str], list[tuple[str, dict[str, int]]]] = defaultdict(list)
        for (comp_type, asset_type, name), counts in type_map.items():
            grouped[(comp_type, asset_type)].append((name, counts or {}))

        rows: list[dict[str, Any]] = []
        for (comp_type, asset_type), items in sorted(grouped.items()):
            base_map: dict[str, dict[str, Any]] = {}
            for raw_name, counts in items:
                name_str = raw_name.strip() if isinstance(raw_name, str) else str(raw_name)
                if name_str and "-" in name_str:
                    base, rest = name_str.split("-", 1)
                    suffix = f"-{rest}" if rest else ""
                else:
                    base = name_str
                    suffix = ""
                base = base or ""
                data = base_map.setdefault(
                    base,
                    {
                        "base_counts": defaultdict(int),
                        "suffix_counts": {},
                    },
                )
                if suffix:
                    suff_counts = data["suffix_counts"].setdefault(suffix, defaultdict(int))
                    for worker, val in (counts or {}).items():
                        suff_counts[worker] = suff_counts.get(worker, 0) + _coerce_int(val)
                else:
                    base_counts = data["base_counts"]
                    for worker, val in (counts or {}).items():
                        base_counts[worker] = base_counts.get(worker, 0) + _coerce_int(val)

            for base_name, data in sorted(base_map.items(), key=lambda kv: (kv[0] or "")):
                suffix_counts: dict[str, defaultdict] = data["suffix_counts"]
                suffix_list = sorted(suffix_counts.keys())
                base_counts = data["base_counts"]
                base_has_counts = any(base_counts.values())

                if not suffix_list:
                    display_name = base_name
                elif len(suffix_list) == 1 and not base_has_counts:
                    suffix = suffix_list[0]
                    display_name = f"{base_name}{suffix}" if base_name else suffix
                else:
                    if base_name:
                        display_name = base_name
                        display_name += f" ({', '.join(suffix_list)})"
                    else:
                        display_name = ", ".join(suffix_list)

                entry: dict[str, Any] = {
                    "component.type": comp_type,
                    "asset.type": asset_type,
                    "name": display_name,
                }
                for worker in workers_list:
                    total = _coerce_int(base_counts.get(worker, 0))
                    for suffix in suffix_list:
                        total += _coerce_int(suffix_counts.get(suffix, {}).get(worker, 0))
                    entry[worker] = total
                rows.append(entry)

        result[repo_name] = rows

    return result
