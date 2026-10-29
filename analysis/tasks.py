"""The units of work the analysis report hands to worker processes (misc/cli/parallel.py:pmap), one per CBOM or per
repository. Importable without typer or rich: a spawned worker imports this module, loads the vocabulary and the
adapter specs itself (their lru_caches) and returns plain dataclasses (NormalizedCbom, MatchGroup), which pickle.
The same functions run in-process when the pool is off, so the results do not depend on the worker count.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import perf_counter

from analysis import levels as lv, schema as sch
from analysis.matchers import raqun as rq
from analysis.matchers.base import MatchGroup
from analysis.matchers.compare import compare
from analysis.matchers.exact import ExactMatcher
from analysis.normalize import normalize_cbom
from analysis.normalize.record import NormalizedCbom, NormRecord
from common.tools_registry import VoterMap

# (meta, cbom path, tool, language, the snapshot's files or None, write run<i>.norm.json)
NormalizeItem = tuple[dict, Path, str, str | None, frozenset[str] | None, bool]
# (meta, cbom path, language, cbom_by)
SchemaItem = tuple[dict, Path, str | None, str | None]
# ((snapshot, variant), its records, the levels, window, closure, voter map, RaQuN too)
MatchItem = tuple[tuple[str, str], list[NormRecord], tuple[str, ...], int, bool, VoterMap, bool]
# ({level: exact groups}, {level: RaQuN groups} or None, {level: compare row}, {matcher: {level: seconds}})
MatchResult = tuple[
    dict[str, list[MatchGroup]], dict[str, list[MatchGroup]] | None, dict[str, dict], dict[str, dict[str, float]]
]


def write_json(path: Path, data, *, sort_keys: bool = False) -> None:
    """Write JSON through a temporary file, so a reader never sees a half-written file. `sort_keys` for a file whose
    key order would otherwise follow a set's iteration order, which differs from process to process (normalize.json)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=sort_keys) + "\n", encoding="utf-8")
    tmp.replace(path)


def norm_path(cbom_path: Path) -> Path:
    """`run<i>.json` -> `run<i>.norm.json` (the normalised CBOM sits next to the original)."""
    return cbom_path.with_name(cbom_path.name[: -len(".json")] + ".norm.json")


def normalize_one(item: NormalizeItem) -> tuple[dict, Path, NormalizedCbom] | None:
    """Normalise one stored CBOM with its tool's adapter spec; None when the file is not JSON (the caller reports it).
    With the flag set, the normalised CBOM is also written next to the original."""
    meta, cbom_path, tool, language, repo_files, write_norm = item
    body = cbom_path.read_bytes()
    try:
        cbom = json.loads(body)
    except json.JSONDecodeError:
        return None
    source = {
        "file": cbom_path.name,
        "sha256": hashlib.sha256(body).hexdigest(),
        "snapshot": cbom_path.parents[2].name,
        "variant": cbom_path.parents[1].name,
        "run": meta.get("run_index"),
        "tool_version": meta.get("tool_version"),
    }
    n = normalize_cbom(cbom, tool, language=language, repo_files=repo_files, source=source)
    if write_norm:
        write_json(norm_path(cbom_path), n.to_dict())
    return meta, cbom_path, n


def schema_one(item: SchemaItem) -> dict | None:
    """RQ3: one CBOM validated as a whole document against the schema it declares (analysis/schema.py:schema_row);
    None when the file is not JSON."""
    meta, cbom_path, language, cbom_by = item
    try:
        cbom = json.loads(cbom_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return sch.schema_row(meta, cbom_path, cbom, language=language, cbom_by=cbom_by)


def match_snapshot(item: MatchItem) -> MatchResult:
    """Both matchers on one repository's records at every level: the exact matcher's groups per level and, when asked,
    RaQuN's groups per level with the comparison of the two partitions (analysis/matchers/compare.py), plus each
    matcher's wall time per level, measured around its `match` call alone: the matcher is built (the vocabulary
    load) and PyQuN imported before the clock starts, so a worker's first repository costs what the others cost.
    One result per repository keeps the records shared between the levels' groups shared in the pickle too."""
    key, records, levels, window, closure, voter_of, raqun = item
    seconds: dict[str, dict[str, float]] = {"exact": {}, "raqun": {}}
    exact: dict[str, list[MatchGroup]] = {}
    for level in levels:
        matcher = ExactMatcher(level=level, window=window, closure=closure, voter_of=voter_of)
        start = perf_counter()
        exact[level] = matcher.match(records)
        seconds["exact"][level] = perf_counter() - start
    if not raqun:
        return exact, None, {}, seconds
    rq.available()  # PyQuN's imports, once per process
    groups: dict[str, list[MatchGroup]] = {}
    for level in levels:
        raqun_matcher = rq.RaqunMatcher(level=level, voter_of=voter_of)
        start = perf_counter()
        groups[level] = raqun_matcher.match(records)
        seconds["raqun"][level] = perf_counter() - start
    compared = {level: compare(exact[level], groups[level], "exact", "raqun") for level in levels}
    return exact, groups, compared, seconds


LEVELS = lv.LEVELS  # the levels a MatchItem names, re-exported for the caller
