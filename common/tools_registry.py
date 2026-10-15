"""Read access to `common/tools.yaml`, the tool registry (engine family, voter group, language scope,
status, `memory_gb`) plus `plan_waves`, the memory budget shared by the batch runner and the
Streamlit coordinator: `memory_gb` mirrors the worker's compose memory limit (tests/test_experiment.py
checks both agree) and the budget caps the summed limits of the tools fed at the same time."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

TOOLS_YAML = Path(os.getenv("TOOLS_YAML", str(Path(__file__).with_name("tools.yaml"))))
# For tools missing from the registry (dev workers such as `testing`): a small, explicit placeholder.
DEFAULT_MEMORY_GB = 1.0


@lru_cache(maxsize=4)
def load_tools(path: str | Path = TOOLS_YAML) -> dict[str, dict]:
    """`tools:` mapping of the registry (tool name -> attributes). Missing file -> empty mapping."""
    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    tools = data.get("tools") or {}
    return {str(k): (v or {}) for k, v in tools.items()}


def tool_memory_gb(tool: str, tools: dict[str, dict] | None = None, default: float = DEFAULT_MEMORY_GB) -> float:
    """Container memory limit of a tool's worker in GiB (the `memory_gb` field), `default` when unknown."""
    entry = (tools if tools is not None else load_tools()).get(tool) or {}
    value = entry.get("memory_gb")
    try:
        return float(value) if value is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def plan_waves(
    tools: list[str],
    *,
    budget_gb: float,
    memory_gb: dict[str, float],
    first: list[str] | None = None,
) -> list[list[str]]:
    """Group tools into waves whose summed worker memory limits stay within `budget_gb`.

    Every worker handles one job at a time, so the memory a batch can take is the sum of the limits of
    the workers that have jobs queued; the runner feeds one wave, waits for it, then feeds the next.
    `budget_gb <= 0` means no limit (one wave, spec order). Packing is first-fit decreasing; a tool
    whose limit alone exceeds the budget gets a wave of its own (the caller warns). `first` names
    tools that must be in the first wave (jobs already in the queues from an earlier run).
    """
    ordered = list(dict.fromkeys(tools))
    if budget_gb <= 0 or not ordered:
        return [ordered] if ordered else []
    pinned = [t for t in dict.fromkeys(first or []) if t in ordered]
    rest = sorted((t for t in ordered if t not in pinned), key=lambda t: (-memory_gb.get(t, 0.0), ordered.index(t)))
    waves: list[list[str]] = [pinned] if pinned else []
    loads: list[float] = [sum(memory_gb.get(t, 0.0) for t in pinned)] if pinned else []
    for tool in rest:
        need = memory_gb.get(tool, 0.0)
        for i, load in enumerate(loads):
            if load + need <= budget_gb:
                waves[i].append(tool)
                loads[i] = load + need
                break
        else:
            waves.append([tool])
            loads.append(need)
    return [[t for t in ordered if t in wave] for wave in waves]
