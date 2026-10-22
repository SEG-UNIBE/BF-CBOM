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
# `tailored_on` entries are paths relative to the repository root (corpus YAMLs, fixture directories).
REPO_ROOT = TOOLS_YAML.resolve().parent.parent
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


class VoterMap(dict):
    """tool -> voter group, plus the set of observers (`role: observer`): tools whose records are matched and confirmed
    like everyone's but never confirm another tool, count in no k and are never ranked (decided 2026-10-19). A plain
    dict works wherever a VoterMap is expected: everyone votes."""

    def __init__(self, groups: dict[str, str] | None = None, observers: frozenset[str] | set[str] = frozenset()):
        super().__init__(groups or {})
        self.observers = frozenset(observers)


def votes(voter_of: dict[str, str] | None, tool: str) -> bool:
    """Does `tool` vote, i.e. can its records confirm another tool's? False only for an observer of a VoterMap."""
    return tool not in getattr(voter_of, "observers", ())


def role(voter_of: dict[str, str] | None, tool: str) -> str:
    return "voter" if votes(voter_of, tool) else "observer"


def voter_groups(
    tools: dict[str, dict] | None = None,
    *,
    merge_llm: bool = False,
    observers_vote: bool = False,
    engine_family: bool = False,
) -> VoterMap:
    """tool -> voter group (`voter_group`, else the tool itself). Tools of one group share an engine or rule set and
    count as one voter (RQ4). With `merge_llm`, tools that declare a `merge_group` (the LLM workers: shared prompts
    and file selection) are merged into it, the sensitivity analysis of CLAUDE.md Phase 2; with `engine_family`, tools
    sharing a pattern engine family are merged (the `engine-merged` ranking). The observers ride along
    (`VoterMap.observers`); `observers_vote` makes them voters (the sensitivity check of the observer role)."""
    tools = tools if tools is not None else load_tools()
    out = {}
    for tool, info in tools.items():
        group = str(info.get("voter_group") or tool)
        if merge_llm and info.get("merge_group"):
            group = str(info["merge_group"])
        if engine_family and info.get("engine_family"):
            group = str(info["engine_family"])
        out[tool] = group
    return VoterMap(out, frozenset() if observers_vote else observers(tools))


def observers(tools: dict[str, dict] | None = None) -> frozenset[str]:
    """Tools with `role: observer` (decided 2026-10-19): analysed like every tool and confirmed by the voters, but
    they confirm nobody, count in no k and are never ranked. The default role is `voter`."""
    tools = tools if tools is not None else load_tools()
    return frozenset(t for t, info in tools.items() if str(info.get("role") or "voter") == "observer")


def tailored_repos(tool: str, tools: dict[str, dict] | None = None, root: Path = REPO_ROOT) -> frozenset[str]:
    """Repositories a tool was tuned on (`tailored_on`, refinement 13), as `owner/name` full names: a corpus YAML
    contributes its `repos`, a fixture directory such as experiments/corpus/micro one `bfcbom/<dir>-<child>`
    per sub-directory (the naming of the micro projects in experiments/stability-pilot.yaml).
    Unknown entries are ignored."""
    entry = (tools if tools is not None else load_tools()).get(tool) or {}
    names: set[str] = set()
    for item in entry.get("tailored_on") or []:
        path = root / str(item)
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            names.update(str(r.get("full_name")) for r in data.get("repos") or [] if r.get("full_name"))
        elif path.is_dir():
            names.update(f"bfcbom/{path.name}-{child.name}" for child in path.iterdir() if child.is_dir())
    return frozenset(names)


def tailored_map(tools: dict[str, dict] | None = None, root: Path = REPO_ROOT) -> dict[str, frozenset[str]]:
    """{tool: tailored repos} for every tool with a non-empty `tailored_on`."""
    tools = tools if tools is not None else load_tools()
    out = {t: tailored_repos(t, tools, root) for t in tools}
    return {t: r for t, r in out.items() if r}


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
