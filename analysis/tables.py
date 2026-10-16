"""Flat, long-format CSV tables of one experiment, for pandas/R (CLAUDE.md, Phase 3).

Three layers, so that later steps (the normaliser, further n-way matchers) only add files:
- runs:    one row per stored run (`run<k>.meta.json`), all repetitions;
- records: one row per extracted item with a stable `record_id`; today the call sites
           (`analysis/sites.py`), later also the normalised records;
- matches: per matcher a groups table and a long membership table (group_id <-> record_id).

Join keys: records on (snapshot, variant, run, record_id), groups on (snapshot, variant, run, group_id);
group ids restart per (snapshot, variant). Lists inside a cell are joined with ";".
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path

from common.experiment import iter_results

from .groundtruth import TruthScore
from .match import Cluster
from .sites import ASSET_COMPONENT_TYPES, Site

LIST_SEP = ";"

RUNS_COLUMNS = [
    "repo", "snapshot", "commit", "variant", "variant_commit", "language", "tool", "run",
    "status", "duration_sec", "size_bytes", "error",
    "tool_version", "image_ref", "received_at",
    "components", "crypto_assets", "algorithm_assets",
    "requests", "prompt_tokens", "completion_tokens",
]  # fmt: skip
SITES_COLUMNS = [
    "repo", "snapshot", "commit", "variant", "language", "tool", "run",
    "record_id", "file", "line", "families", "raw", "asset_types", "in_scope",
]  # fmt: skip
GROUP_KEY_COLUMNS = ["matcher", "repo", "snapshot", "commit", "variant", "run", "group_id", "k", "tools"]
CALLSITE_GROUP_COLUMNS = GROUP_KEY_COLUMNS + ["file", "line_min", "line_max", "n_sites", "families"]
MEMBERS_COLUMNS = ["matcher", "snapshot", "variant", "run", "group_id", "record_id", "tool"]
MICRO_COLUMNS = [
    "project", "language", "tool", "run", "n_assets", "strict", "lenient", "recall_strict", "recall_lenient",
    "sites_in_file", "sites_correct", "precision", "passes_bar", "missed",
]  # fmt: skip

_LLM_PROPS = {
    "requests": "bf-cbom:requests",
    "prompt_tokens": "bf-cbom:prompt-tokens",
    "completion_tokens": "bf-cbom:completion-tokens",
}
_ERROR_MAX = 300


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple | set | frozenset):
        return LIST_SEP.join(str(v) for v in value)
    return str(value)


def write_csv(path: Path, rows: Iterable[dict], columns: list[str]) -> int:
    """Write `rows` with exactly `columns` (header even when empty); atomic via a .tmp file. Returns the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    n = 0
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(columns)
        for row in rows:
            w.writerow([_cell(row.get(c)) for c in columns])
            n += 1
    tmp.replace(path)
    return n


def full_name(snapshot: str) -> str:
    """`<owner>__<repo>@<sha12>` -> `<owner>/<repo>`."""
    return snapshot.split("@", 1)[0].replace("__", "/", 1)


def site_record_id(site: Site) -> str:
    return f"{site.tool}:{site.file}:{site.line}"


# ----- runs -----


def _cbom_counts(cbom: dict) -> dict:
    comps = [c for c in cbom.get("components") or [] if isinstance(c, dict)]
    crypto = [c for c in comps if c.get("type") in ASSET_COMPONENT_TYPES]
    algos = [c for c in crypto if (c.get("cryptoProperties") or {}).get("assetType") == "algorithm"]
    props = {
        str(p.get("name")): p.get("value")
        for p in (cbom.get("metadata") or {}).get("properties") or []
        if isinstance(p, dict)
    }
    out = {"components": len(comps), "crypto_assets": len(crypto), "algorithm_assets": len(algos)}
    out.update({col: props.get(name) for col, name in _LLM_PROPS.items()})
    return out


def run_rows(exp_dir: Path, languages: dict[str, str | None] | None = None) -> list[dict]:
    """One row per stored meta file; CBOM counts only where the CBOM exists and parses."""
    languages = languages or {}
    rows = []
    for meta, cbom_path in iter_results(exp_dir):
        snapshot = cbom_path.parents[2].name
        repo = full_name(snapshot)
        error = (meta.get("error") or "").strip().splitlines()
        row = {
            "repo": repo,
            "snapshot": snapshot,
            "commit": meta.get("base_commit") or (meta.get("repo_info") or {}).get("commit"),
            "variant": meta.get("variant") or cbom_path.parents[1].name,
            "variant_commit": meta.get("variant_commit"),
            "language": meta.get("language") or languages.get(repo),
            "tool": meta.get("worker") or cbom_path.parent.name,
            "run": meta.get("run_index"),
            "status": meta.get("status"),
            "duration_sec": meta.get("duration_sec"),
            "size_bytes": meta.get("size_bytes"),
            "error": error[0][:_ERROR_MAX] if error else None,
            "tool_version": meta.get("tool_version"),
            "image_ref": meta.get("image_ref"),
            "received_at": meta.get("received_at"),
        }
        if cbom_path.exists():
            try:
                cbom = json.loads(cbom_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cbom = None
            if isinstance(cbom, dict):
                row.update(_cbom_counts(cbom))
        rows.append(row)
    return rows


# ----- records (call sites) -----


def site_rows(
    sites: list[Site], *, snapshot: str, variant: str, run: int, commit: str | None, language: str | None
) -> list[dict]:
    base = {
        "repo": full_name(snapshot),
        "snapshot": snapshot,
        "commit": commit,
        "variant": variant,
        "language": language,
        "run": run,
    }
    return [
        {
            **base,
            "tool": s.tool,
            "record_id": site_record_id(s),
            "file": s.file,
            "line": s.line,
            "families": sorted(s.families),
            "raw": s.raw,
            "asset_types": s.asset_types,
            "in_scope": s.in_scope,
        }
        for s in sorted(sites, key=lambda s: (s.file, s.line, s.tool))
    ]


# ----- matches (generic over matchers) -----


def group_rows(
    matcher: str,
    groups: list[tuple[str, list[tuple[str, str]], dict]],
    *,
    snapshot: str,
    variant: str,
    run: int,
    commit: str | None,
) -> tuple[list[dict], list[dict]]:
    """Groups and membership rows of any matcher.

    `groups` holds (group_id, [(record_id, tool), ...], extra columns) per match group.
    """
    base = {"matcher": matcher, "snapshot": snapshot, "variant": variant, "run": run}
    g_rows, m_rows = [], []
    for group_id, members, extra in groups:
        tools = sorted({t for _, t in members})
        g_rows.append(
            {
                **base,
                "repo": full_name(snapshot),
                "commit": commit,
                "group_id": group_id,
                "k": len(tools),
                "tools": tools,
                **extra,
            }
        )
        m_rows += [{**base, "group_id": group_id, "record_id": rid, "tool": t} for rid, t in members]
    return g_rows, m_rows


def callsite_groups(clusters: list[Cluster]) -> list[tuple[str, list[tuple[str, str]], dict]]:
    """Adapter: call-site matcher (`analysis/match.py`) clusters -> generic match groups."""
    out = []
    for c in clusters:
        members = [(site_record_id(m), m.tool) for m in sorted(c.members, key=lambda s: (s.line, s.tool))]
        extra = {
            "file": c.file,
            "line_min": c.line_min,
            "line_max": c.line_max,
            "n_sites": len(c.members),
            "families": [f"{name}:{n}" for name, n in c.families.most_common()],
        }
        out.append((c.id, members, extra))
    return out


# ----- micro ground truth -----


def micro_rows(scores: list[TruthScore], *, run: int, languages: dict[str, str] | None = None) -> list[dict]:
    languages = languages or {}
    return [
        {
            "project": s.project,
            "language": languages.get(s.project),
            "tool": s.tool,
            "run": run,
            "n_assets": s.n_assets,
            "strict": len(s.hits_strict),
            "lenient": len(s.hits_lenient),
            "recall_strict": round(s.recall_strict, 3),
            "recall_lenient": round(s.recall_lenient, 3),
            "sites_in_file": s.sites_in_file,
            "sites_correct": s.sites_correct,
            "precision": round(s.precision, 3),
            "passes_bar": s.passes_bar,
            "missed": s.missed,
        }
        for s in scores
    ]
