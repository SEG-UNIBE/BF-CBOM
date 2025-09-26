import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from typing import Any

from common.cbom_filters import (
    is_included_component_type,
)
from common.models import CbomJson, ComponentMatchJobInstruction
from common.utils import repo_dict_to_info

# Common crypto asset types used across tools/tests
COMMON_CRYPTO_ASSET_TYPES = [
    "algorithm",
    "key",
    "certificate",
    "digest",
    "related-crypto-material",
]

logger = logging.getLogger(__name__)


def _find_value_path(obj: Any, target_key_lc: str) -> tuple[list[str] | None, Any]:
    """Return (path, value) for the first occurrence of key (case-insensitive).

    Path is a list of dict keys leading to the key found. Lists are traversed,
    but not represented in the path; the first matching branch is returned.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() == target_key_lc:
                return [k], v
            sub_path, sub_val = _find_value_path(v, target_key_lc)
            if sub_path is not None:
                return [k] + sub_path, sub_val
    elif isinstance(obj, list):
        for item in obj:
            sub_path, sub_val = _find_value_path(item, target_key_lc)
            if sub_path is not None:
                return sub_path, sub_val
    return None, None


def _set_nested(target: dict, path: list[str], value: Any) -> None:
    cur = target
    for i, key in enumerate(path):
        if i == len(path) - 1:
            cur[key] = value
        else:
            nxt = cur.get(key)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[key] = nxt
            cur = nxt


def create_component_match_instruction(
    repo: dict,
    bench_id: str,
    cboms_by_worker: dict[str, str],
    exclude_types: bool = True,
) -> ComponentMatchJobInstruction | None:
    """Construct a ComponentMatchJobInstruction from repo snapshot and worker CBOMs.
    For each CBOM, it extracts and minimizes the components to include only 'type',
    'name', and 'cryptoProperties'.
    Returns None if fewer than two valid CBOMs with components are available.
    """
    entries: list[CbomJson] = []
    for worker, payload in cboms_by_worker.items():
        if not payload:
            continue
        try:
            cbom_data = json.loads(payload)
            # Find the components list using the helper
            components = find_components_list(cbom_data)
            if not isinstance(components, list):
                logger.warning("CBOM for worker '%s' has no valid 'components' list.", worker)
                continue

            minimized_components_dicts = []
            for component in components:
                if not isinstance(component, dict):
                    continue

                # When filtering is requested, include only cryptographic asset types
                if exclude_types and not is_included_component_type(component.get("type")):
                    continue

                min_comp = {
                    "type": component.get("type"),
                    "name": component.get("name"),
                }

                # Preserve assetType and primitive in their original nested locations
                at_path, at_val = _find_value_path(component, "assettype")
                if at_path is not None:
                    _set_nested(min_comp, at_path, at_val)
                prim_path, prim_val = _find_value_path(component, "primitive")
                if prim_path is not None:
                    _set_nested(min_comp, prim_path, prim_val)
                minimized_components_dicts.append(min_comp)

            if minimized_components_dicts:
                harmonized_list = harmonize_value(minimized_components_dicts)
                minimized_components = [json.dumps(comp) for comp in harmonized_list]
                entries.append(CbomJson(tool=worker, components_as_json=minimized_components, entire_json_raw=payload))

        except json.JSONDecodeError as e:
            logger.error("Invalid CBOM JSON for worker '%s': %s", worker, e)
            continue
        except (TypeError, ValueError, KeyError, AttributeError) as e:
            # Non-JSON errors can occur due to unexpected structures; log and continue.
            logger.error("Error processing CBOM for worker '%s': %s", worker, e)
            continue

    if len(entries) < 2:
        return None

    repo_info = repo_dict_to_info(repo)
    job_id = str(uuid.uuid4())
    return ComponentMatchJobInstruction(
        job_id=job_id,
        benchmark_id=bench_id,
        repo_info=repo_info,
        CbomJsons=entries,
    )


def get_crypto_asset_types() -> list[str]:
    """Return a copy of common crypto asset types."""
    return list(COMMON_CRYPTO_ASSET_TYPES)


def _safe_json_loads(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def find_components_list(data: Any) -> list[dict]:
    """
    Find the most likely 'components' list from a parsed CBOM JSON object.
    It handles several common CycloneDX structures.
    """
    if isinstance(data, dict):
        # Case 1: { "components": [...] }
        if isinstance(data.get("components"), list):
            return data["components"]
        # Case 2: { "bom": { "components": [...] } }
        bom = data.get("bom")
        if isinstance(bom, dict) and isinstance(bom.get("components"), list):
            return bom["components"]
    # Case 3: [ { "bom": { "components": [...] } }, ... ] (often from multi-doc streams)
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            bom = first.get("bom")
            if isinstance(bom, dict) and isinstance(bom.get("components"), list):
                return bom["components"]
    return []


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


def load_components(raw_json: str) -> list[dict]:
    """Return a best-effort list of component dicts from a CBOM JSON payload."""

    obj = _safe_json_loads(raw_json or "")
    if obj is None:
        return []

    components = _extract_components(obj)
    normalized: list[dict] = []

    for comp in components:
        if isinstance(comp, dict):
            normalized.append(comp)
        else:
            normalized.append({"value": comp})

    return normalized


def render_similarity_matches(
    *,
    matches: list,
    tools: list[str] | tuple[str, ...],
    cboms_by_tool: dict[str, str],
    renderer,
    safe_int_func,
) -> None:
    """Render component similarity matches using a provided Streamlit-like renderer."""

    if not matches:
        renderer.info("No component matches were returned.")
        return

    tools_list = [str(t) for t in (tools or [])]

    component_cache: dict[str, list[dict]] = {}

    def _components_for_tool(tool_name: str) -> list[dict]:
        if tool_name in component_cache:
            return component_cache[tool_name]
        if tool_name in cboms_by_tool:
            component_cache[tool_name] = load_components(cboms_by_tool[tool_name])
            return component_cache[tool_name]
        component_cache[tool_name] = []
        return []

    def _format_cost(value) -> str:
        if isinstance(value, int | float):
            return f"{value:.4f}"
        return str(value)

    def _extract_cost(match_block: list) -> float:
        costs = [entry.get("cost") for entry in match_block if isinstance(entry, dict)]
        numeric = [c for c in costs if isinstance(c, int | float)]
        if numeric:
            return float(numeric[0])
        return float("inf")

    def _render_component_column(col, tool_name: str | None, comp_idx: int) -> None:
        if not tool_name:
            col.caption("Unknown tool")
            col.warning("Unmapped file index.")
            return

        col.caption(f"{tool_name} component #{comp_idx if comp_idx >= 0 else '?'}")
        if tool_name not in cboms_by_tool:
            col.warning("No CBOM data available for this tool.")
            return

        comps = _components_for_tool(tool_name)
        if 0 <= comp_idx < len(comps):
            col.json(comps[comp_idx])
        else:
            col.warning("Component index out of range.")

    if matches and isinstance(matches[0], list):
        indexed_matches = [(i, block) for i, block in enumerate(matches, start=1)]
        indexed_matches.sort(key=lambda item: (_extract_cost(item[1]), item[0]))

        for display_idx, (original_idx, match_block) in enumerate(indexed_matches, start=1):
            cost_value = _extract_cost(match_block)
            cost_label = "n/a" if cost_value == float("inf") else _format_cost(cost_value)
            # Build badges for involved tools in this match block
            tool_names_seq: list[str] = []
            for entry in match_block:
                if not isinstance(entry, dict):
                    continue
                file_idx = safe_int_func(entry.get("file"), default=-1)
                if 0 <= file_idx < len(tools_list):
                    tool_names_seq.append(tools_list[file_idx])
            # De-duplicate preserving order
            seen = set()
            tool_badges_list = [t for t in tool_names_seq if not (t in seen or seen.add(t))]
            badges = " ".join(f"[{t}]" for t in tool_badges_list)
            header_label = f"Match {display_idx} · Cost: {cost_label}"
            if badges:
                header_label += f" · {badges}"

            with renderer.expander(header_label, expanded=(display_idx == 1)):
                if original_idx != display_idx:
                    renderer.caption(f"Original order: {original_idx}")
                if not match_block:
                    renderer.info("Match group is empty.")
                    continue

                columns = renderer.columns(len(match_block))
                for col, entry in zip(columns, match_block, strict=False):
                    if not isinstance(entry, dict):
                        col.json(entry)
                        continue

                    file_idx = safe_int_func(entry.get("file"), default=-1)
                    comp_idx = safe_int_func(entry.get("component"), default=-1)
                    tool_name = tools_list[file_idx] if 0 <= file_idx < len(tools_list) else None

                    _render_component_column(col, tool_name, comp_idx)

    elif matches and isinstance(matches[0], dict):
        for idx, match in enumerate(matches, start=1):
            # Derive tool names from indices or explicit fields
            q_file_idx = safe_int_func(match.get("query_file"), default=-1)
            t_file_idx = safe_int_func(match.get("target_file"), default=-1)
            q_name = tools_list[q_file_idx] if 0 <= q_file_idx < len(tools_list) else (match.get("query_tool") or "?")
            t_name = tools_list[t_file_idx] if 0 <= t_file_idx < len(tools_list) else (match.get("target_tool") or "?")
            query_tool = str(q_name)
            target_tool = str(t_name)
            query_idx = safe_int_func(match.get("query_comp"), default=-1)
            target_idx = safe_int_func(match.get("target_comp"), default=-1)
            cost = match.get("cost")

            # Build badges for involved tools
            tool_names_seq = []
            if isinstance(query_tool, str) and query_tool and query_tool != "?":
                tool_names_seq.append(query_tool)
            if isinstance(target_tool, str) and target_tool and target_tool != "?":
                tool_names_seq.append(target_tool)
            seen = set()
            tool_badges_list = [t for t in tool_names_seq if not (t in seen or seen.add(t))]
            badges = " ".join(f"[{t}]" for t in tool_badges_list)

            header = f"Match {idx}"
            if cost is not None:
                header += f" · Cost: {_format_cost(cost)}"
            if badges:
                header += f" · {badges}"

            with renderer.expander(header, expanded=(idx == 1)):
                col_query, col_target = renderer.columns(2)
                _render_component_column(col_query, query_tool, query_idx)
                _render_component_column(col_target, target_tool, target_idx)

    else:
        renderer.json(matches)


def component_counts_for_repo(
    comp_rows: list[dict],
    repo_name: str,
    workers: list[str] | tuple[str, ...],
    excluded_types: set[str] | tuple[str, ...] | list[str] | None = None,
) -> dict[str, int]:
    """Return per-worker component counts, optionally excluding selected types."""

    worker_set = set(workers or [])
    excludes = {str(t).lower() for t in (excluded_types or []) if str(t).strip()}
    counts: dict[str, int] = {}

    for row in comp_rows:
        if row.get("repo") != repo_name:
            continue
        worker = row.get("worker")
        if worker not in worker_set:
            continue

        total = _coerce_int(row.get("total_components"))
        if excludes and row.get("types"):
            type_counts = row.get("types") or {}
            filtered_total = 0
            for comp_type, value in type_counts.items():
                if str(comp_type).lower() in excludes:
                    continue
                filtered_total += _coerce_int(value)
            counts[worker] = filtered_total
        else:
            counts[worker] = total

    for worker in worker_set:
        counts.setdefault(worker, 0)

    return counts


def summarize_runtime_estimate(seconds: float) -> str:
    """Return a coarse runtime estimate string from a raw seconds value."""

    if seconds is None:
        return "unknown"

    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "unknown"

    if seconds <= 5:
        return "<5 seconds"
    if seconds < 60:
        rounded = int((seconds + 4) // 5 * 5)
        lower = max(5, rounded - 5)
        upper = rounded
        return f"{lower}-{upper} seconds"

    minutes = seconds / 60
    if minutes < 2:
        return "1-2 minutes"
    if minutes < 5:
        return "2-5 minutes"
    if minutes < 10:
        return "5-10 minutes"
    if minutes < 20:
        return "10-20 minutes"
    if minutes < 30:
        return "20-30 minutes"
    if minutes < 60:
        return "30-60 minutes"
    hours = minutes / 60
    if hours < 2:
        return "1-2 hours"
    if hours < 4:
        return "2-4 hours"
    return ">4 hours"


def harmonize_value(value: Any) -> Any:
    """Recursively harmonize a value for similarity matching."""
    if isinstance(value, str):
        # Harmonize string values
        harmonized = value.lower()
        # harmonized = re.sub(r"@.*", "", harmonized)
        harmonized = re.sub(r"[^a-z0-9]", "", harmonized)
        return harmonized
    if isinstance(value, dict):
        # Recursively harmonize dictionary values
        return {k: harmonize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        # Recursively harmonize list elements
        return [harmonize_value(v) for v in value]
    # Return other types as is (e.g., int, bool)
    return value
