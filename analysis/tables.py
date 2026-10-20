"""Flat, long-format CSV tables of one experiment, for pandas/R (CLAUDE.md, Phase 3).

Three layers, so that later steps (the normaliser, further n-way matchers) only add files:
- runs:    one row per stored run (`run<k>.meta.json`), all repetitions;
- records: one row per normalised record (`analysis/normalize/`) with a stable `record_id`
           (`<tool>:<component>:<occurrence>`; `site_id` = `<tool>:<file>:<line>` for located ones);
- matches: per matcher (`analysis/matchers/`) a groups table, a long membership table (group_id <-> record_id) and
           an agreement table (one row per group and dimension).

Join keys: records on (snapshot, variant, run, record_id), groups on (snapshot, variant, run, group_id);
group ids restart per (snapshot, variant). Lists inside a cell are joined with ";".
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common.experiment import iter_results, repo_full_name
from common.tools_registry import votes

from .costs import cost_usd, load_prices
from .groundtruth import TruthProject, TruthScore
from .levels import UNIT_LEVEL, identity
from .matchers import MatchGroup, agreement
from .normalize.record import RECORD_COLUMNS, NormalizedCbom, NormRecord
from .normalize.report import assets_of

LIST_SEP = ";"
ASSET_COMPONENT_TYPES = ("cryptographic-asset", "crypto-asset")  # the latter: IBM draft 1.4-cbom-1.0

RUNS_COLUMNS = [
    "repo", "snapshot", "commit", "variant", "variant_commit", "language", "expect_crypto", "tool", "run", "tailored",
    "status", "duration_sec", "size_bytes", "error", "workaround", "peak_memory_mb", "memory_limit_mb", "oom_kills",
    "enqueued_at", "started_at", "finished_at", "queue_sec", "received_at",
    "tool_version", "image_ref", "bfcbom_commit", "bom_format", "spec_version",
    "components", "crypto_assets", "algorithm_assets", "certificate_assets", "protocol_assets", "material_assets",
    "other_assets", "located_file_assets", "located_line_assets",
    "requests", "requests_failed", "chunks_total", "chunks_sent", "chunks_skipped", "input_tokens",
    "cached_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens", "models_answered", "cost_usd",
    "priced_model", "price_period", "price_table_version",
]  # fmt: skip
# JobResult.usage (common/llm_cbom.py:GenerationStats.usage) -> runs.csv columns
USAGE_COLUMNS = (
    "requests", "requests_failed", "chunks_total", "chunks_sent", "chunks_skipped", "input_tokens",
    "cached_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens",
)  # fmt: skip
RECORDS_COLUMNS = ["repo", "snapshot", "commit", "variant", "language", "tool", "run", "location_kind"] + RECORD_COLUMNS
GROUP_KEY_COLUMNS = ["matcher", "repo", "snapshot", "commit", "variant", "run", "group_id", "n_tools", "tools"]
GROUP_COLUMNS = GROUP_KEY_COLUMNS + [
    "language",
    "level",
    "scope",
    "key",
    "voters",
    "voter_groups",
    "n_records",
    "n_sites",
    "units",
    "n_units",
    "n_attached",
    "located_voters",
    "colocated_voters",
    "file",
    "line_min",
    "line_max",
    "parent",
    "n_elements",
    "similarity",
]  # fmt: skip  (exact: sites/located/parent columns; raqun: n_elements, similarity)
EXACT_GROUP_COLUMNS = GROUP_COLUMNS
MEMBERS_COLUMNS = [
    "matcher", "snapshot", "variant", "run", "group_id", "record_id", "tool", "unit", "implied", "matched", "attached",
]  # fmt: skip
MATCHLEVELS_COLUMNS = [
    "matcher", "level", "run", "repos", "items", "records", "k1", "k2", "k3", "k4plus", "share_k2plus",
    *[f"{d}_groups" for d in ("key_size", "mode", "padding", "curve", "digest", "location")],
    *[f"{d}_agree" for d in ("key_size", "mode", "padding", "curve", "digest", "location")],
]  # fmt: skip
AGREEMENT_COLUMNS = [
    "matcher", "repo", "snapshot", "variant", "run", "group_id", "scope", "key", "dimension", "reporting",
    "agreeing", "majority", "values",
]  # fmt: skip
LOCATION_KINDS = ("line", "window", "import", "file", "none", "unread")  # analysis/schema.py:location_kind
UNIT_COLUMNS = [
    "tool", "role", "cboms", "assets", "records", "items", "records_per_asset_median", "records_per_asset_max",
    "multi_occurrence_share", "assets_per_item", "unlocated_share", *[f"loc_{k}" for k in LOCATION_KINDS],
]  # fmt: skip
MICRO_COLUMNS = [
    "project", "language", "tool", "run", "tailored", "status", "verdict", "n_items",
    "strict", "lenient", "repo", "recall_strict", "recall_lenient", "recall_repo",
    "sites_in_file", "sites_correct", "precision", "passes_bar", "missed", "repo_missed", "repo_extra",
]  # fmt: skip
MICRO_ITEM_COLUMNS = [
    "project", "language", "tool", "run", "item", "families", "insertion_style",
    "callsite_strict", "callsite_lenient", "repo",
]  # fmt: skip

_ERROR_MAX = 300


def _timeout_text(meta: dict) -> str | None:
    if meta.get("status") != "timeout":
        return None
    d = meta.get("duration_sec")
    return f"timed out after {d:.0f} s" if isinstance(d, int | float) else "timed out"


WORKAROUND_PREFIX = "workaround: "


def workaround_line(notes: str | None) -> str | None:
    """The recorded setup workarounds of a run (worker note lines starting with `workaround: `: CipherRadar's
    `.cradarignore` of the files it crashes on), joined; None when there is none."""
    found = [
        ln.strip()[len(WORKAROUND_PREFIX) :]
        for ln in (notes or "").splitlines()
        if ln.strip().startswith(WORKAROUND_PREFIX)
    ]
    return LIST_SEP.join(found) or None


def error_line(text: str | None) -> str | None:
    """The most telling line of a run's error text, which usually starts with the echoed tool command: the
    exception line of the last Python traceback (the first unindented line after it), else a timeout line, else the
    first line that is not an echoed command (None if there is none)."""
    lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    starts = [i for i, ln in enumerate(lines) if ln.startswith("Traceback (most recent call last)")]
    if starts:
        for ln in lines[starts[-1] + 1 :]:
            if not ln[0].isspace():
                return ln[:_ERROR_MAX]
    for ln in lines:
        if "timeout" in ln.lower() and " cmd: " not in ln:
            return ln.strip()[:_ERROR_MAX]
    rest = [ln for ln in lines if " cmd: " not in ln]
    return rest[0][:_ERROR_MAX] if rest else None


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


full_name = repo_full_name  # `<owner>__<repo>@<sha12>` -> `<owner>/<repo>`


# ----- runs -----


ASSET_TYPE_COLUMNS = {  # cryptoProperties.assetType as declared -> runs.csv column; anything else is `other`
    "algorithm": "algorithm_assets",
    "certificate": "certificate_assets",
    "protocol": "protocol_assets",
    "related-crypto-material": "material_assets",
}


def _located(component: dict) -> tuple[bool, bool]:
    """(names a file, names a file and a line) as the tool wrote it: CycloneDX `evidence.occurrences[].location`
    (+ `line`), or `cryptoProperties.detectionContext[].filePath` (+ `lineNumbers`) in IBM's draft. A file alone is
    a weaker claim than a file and a line, so the two are counted apart (RQ2 `located`, RQ1 traceability)."""
    occurrences = [o for o in (component.get("evidence") or {}).get("occurrences") or [] if isinstance(o, dict)]
    contexts = [
        c for c in (component.get("cryptoProperties") or {}).get("detectionContext") or [] if isinstance(c, dict)
    ]
    file = any(o.get("location") for o in occurrences) or any(c.get("filePath") for c in contexts)
    line = any(o.get("location") and o.get("line") is not None for o in occurrences) or any(
        c.get("filePath") and c.get("lineNumbers") for c in contexts
    )
    return file, line


def _cbom_counts(cbom: dict) -> dict:
    """What the CBOM holds as written, before normalisation: components, crypto assets by declared asset type, and
    the crypto assets with a location (the raw RQ2 columns of runs.csv)."""
    comps = [c for c in cbom.get("components") or [] if isinstance(c, dict)]
    crypto = [c for c in comps if c.get("type") in ASSET_COMPONENT_TYPES]
    out = {
        "bom_format": cbom.get("bomFormat"),
        "spec_version": cbom.get("specVersion"),
        "components": len(comps),
        "crypto_assets": len(crypto),
        "located_file_assets": sum(_located(c)[0] for c in crypto),
        "located_line_assets": sum(_located(c)[1] for c in crypto),
        **dict.fromkeys(ASSET_TYPE_COLUMNS.values(), 0),
        "other_assets": 0,
    }
    for c in crypto:
        kind = (c.get("cryptoProperties") or {}).get("assetType")
        out[ASSET_TYPE_COLUMNS.get(kind, "other_assets")] += 1
    return out


def _usage_columns(usage: dict | None, started_at: str | None = None) -> dict:
    """Token columns from JobResult.usage plus the cost from the price table (analysis/costs.py)."""
    if not isinstance(usage, dict):
        return {}
    out = {col: usage.get(col) for col in USAGE_COLUMNS}
    out["models_answered"] = LIST_SEP.join(sorted(usage.get("models") or {})) or None
    cost = cost_usd(usage, started_at=started_at)
    out.update(
        cost_usd=cost["cost_usd"],
        priced_model=cost["priced_model"],
        price_period=cost["price_period"],
        price_table_version=load_prices()["version"] if cost["cost_usd"] is not None else None,
    )
    return out


def _seconds_between(start: str | None, end: str | None) -> float | None:
    try:
        t0 = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return round((t1 - t0).total_seconds(), 1)


def run_rows(
    exp_dir: Path,
    languages: dict[str, str | None] | None = None,
    tailored: dict[str, frozenset[str]] | None = None,
    expects: dict[str, str | None] | None = None,
) -> list[dict]:
    """One row per stored meta file; CBOM counts only where the CBOM exists and parses. `tailored`: {tool: repos it
    was tuned on} (refinement 13) for the `tailored` column; `expects`: {repo: expect_crypto} from the spec."""
    languages = languages or {}
    tailored = tailored or {}
    expects = expects or {}
    rows = []
    for meta, cbom_path in iter_results(exp_dir):
        snapshot = cbom_path.parents[2].name
        repo = full_name(snapshot)
        tool = meta.get("worker") or cbom_path.parent.name
        row = {
            "repo": repo,
            "snapshot": snapshot,
            "commit": meta.get("base_commit") or (meta.get("repo_info") or {}).get("commit"),
            "variant": meta.get("variant") or cbom_path.parents[1].name,
            "variant_commit": meta.get("variant_commit"),
            "language": meta.get("language") or languages.get(repo),
            "expect_crypto": expects.get(repo),
            "tool": tool,
            "run": meta.get("run_index"),
            "tailored": repo in tailored.get(tool, ()),
            "status": meta.get("status"),
            "duration_sec": meta.get("duration_sec"),
            "size_bytes": meta.get("size_bytes"),
            "error": error_line(meta.get("error")) or _timeout_text(meta),
            "workaround": workaround_line(meta.get("notes")),
            "peak_memory_mb": meta.get("peak_memory_mb"),
            "memory_limit_mb": meta.get("memory_limit_mb"),
            "oom_kills": meta.get("oom_kills"),
            "enqueued_at": meta.get("enqueued_at"),
            "started_at": meta.get("started_at"),
            "finished_at": meta.get("finished_at"),
            "queue_sec": _seconds_between(meta.get("enqueued_at"), meta.get("started_at")),
            "received_at": meta.get("received_at"),
            "tool_version": meta.get("tool_version"),
            "image_ref": meta.get("image_ref"),
            "bfcbom_commit": meta.get("bfcbom_commit"),
            **_usage_columns(meta.get("usage"), meta.get("started_at")),
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


# ----- records (normalised) -----


def record_rows(
    records: list[NormRecord],
    *,
    snapshot: str,
    variant: str,
    run: int,
    commit: str | None,
    language: str | None,
    location_kinds: dict[str, str] | None = None,
) -> list[dict]:
    """Normalised records (analysis/normalize/) of one CBOM; `location_kinds`: record id -> analysis/schema.py
    location_kind."""
    kinds = location_kinds or {}
    base = {
        "repo": full_name(snapshot),
        "snapshot": snapshot,
        "commit": commit,
        "variant": variant,
        "language": language,
        "run": run,
    }
    return [{**base, "tool": r.tool, "location_kind": kinds.get(r.record_id), **r.csv_row()} for r in records]


# ----- matches (generic over matchers) -----


def group_rows(
    matcher: str,
    groups: list[MatchGroup],
    *,
    snapshot: str,
    variant: str,
    run: int,
    commit: str | None,
    language: str | None = None,
    voter_of: dict[str, str] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Groups, membership and agreement rows of any matcher's groups for one (snapshot, variant)."""
    base = {"matcher": matcher, "snapshot": snapshot, "variant": variant, "run": run}
    g_rows, m_rows, a_rows = [], [], []
    for g in groups:
        voters = g.voters(voter_of)
        g_rows.append(
            {
                **base,
                "repo": full_name(snapshot),
                "commit": commit,
                "language": language,
                "group_id": g.group_id,
                "n_tools": g.k,
                "tools": g.tools,
                "scope": g.scope,
                "key": g.key,
                "voters": voters,
                "voter_groups": len(voters),
                **g.extra,
            }
        )
        for r in g.members:
            member = {"group_id": g.group_id, "record_id": r.record_id, "tool": r.tool, "unit": identity(r, UNIT_LEVEL)}
            m_rows.append(
                {
                    **base,
                    **member,
                    "implied": r.record_id in g.implied,
                    "matched": r.record_id in g.matched,
                    "attached": r.record_id in g.attached,
                }
            )
        a_rows += [
            {**base, "repo": full_name(snapshot), "group_id": g.group_id, "scope": g.scope, "key": g.key, **row}
            for row in agreement(g, voter_of=voter_of)
        ]
    return g_rows, m_rows, a_rows


def matchlevel_row(
    matcher: str, level: str, run: int, groups_by_snapshot: dict, voter_of: dict[str, str] | None = None
) -> dict:
    """One row of matchlevels-run<k>.csv (RQ3, matcher x level): over the repo groups of every snapshot, how many
    items (algorithms, by their L4 identity: `extra.units` of the exact matcher's groups, else the group key) have
    1 / 2 / 3 / 4+ voter groups in their largest group at that level,
    and per dimension the share of the groups with at least two
    voters reporting a value (admitted at the level) in which every reporting voter carries the majority value;
    `location`: of the groups with two located voters, the share with two voters at one call site."""
    best: dict[tuple, int] = {}  # (snapshot, item) -> voter groups of its largest cluster
    records = 0
    reporting: Counter = Counter()
    agreeing: Counter = Counter()
    for snapshot, groups in groups_by_snapshot.items():
        for g in groups:
            if g.scope != "repo":
                continue
            voting = [r for r in g.members if votes(voter_of, r.tool)]  # observers are in no k and no count
            if not voting:
                continue
            for unit in g.extra.get("units") or [g.key]:  # the exact matcher's units are the voters' already
                best[(snapshot, unit)] = max(best.get((snapshot, unit), 0), len(g.voters(voter_of)))
            records += len(voting)
            for row in agreement(g, voter_of=voter_of, level=level):
                if row["reporting"] >= 2:
                    reporting[row["dimension"]] += 1
                    agreeing[row["dimension"]] += row["agreeing"] == row["reporting"]
    k = Counter(min(v, 4) for v in best.values())
    items = len(best)
    out = {
        "matcher": matcher,
        "level": level,
        "run": run,
        "repos": len(groups_by_snapshot),
        "items": items,
        "records": records,
        "k1": k[1],
        "k2": k[2],
        "k3": k[3],
        "k4plus": k[4],
        "share_k2plus": round((items - k[1]) / items, 3) if items else None,
    }
    for dim in ("key_size", "mode", "padding", "curve", "digest", "location"):
        out[f"{dim}_groups"] = reporting[dim]
        out[f"{dim}_agree"] = round(agreeing[dim] / reporting[dim], 3) if reporting[dim] else None
    return out


# ----- micro ground truth -----


@dataclass
class MicroEntry:
    """One (micro project, tool) cell of an experiment: the run's status and, for a readable ok run, its score."""

    project: TruthProject
    tool: str
    status: str  # status of the run (meta file); `missing` when the tool has no stored run on the project
    score: TruthScore | None = None
    tailored: bool = False  # the tool was tuned on this project (tools.yaml tailored_on, refinement 13)


def unit_rows(
    cboms: list[NormalizedCbom],
    voter_of: dict[str, str] | None = None,
    location_kinds: list[dict[str, str]] | None = None,
) -> list[dict]:
    """How each tool packages its findings (analysis/README.md "Units"), over the algorithm assets of `cboms` (one
    run's CBOMs of the original variants): per tool its assets (CBOM components), their records (occurrences) and its
    items (distinct L4 identities of its `mapped` records per CBOM, i.e. per repository, as the ladder counts them,
    tailored repositories included), with the records per asset, the share of assets listed at more than one place,
    the assets per item (above 1: one component per finding), the share of assets without a file, and the
    algorithm records per location kind (`loc_<kind>`; `location_kinds`: one {record id: kind} per CBOM)."""
    acc: dict[str, dict] = {}
    for i, n in enumerate(cboms):
        t = acc.setdefault(n.tool, {"cboms": 0, "per_asset": [], "items": 0, "unlocated": 0, "kinds": Counter()})
        t["cboms"] += 1
        assets = [rs for rs in assets_of(n) if rs[0].values.get("asset_type") == "algorithm"]
        kinds = location_kinds[i] if location_kinds else {}
        t["kinds"].update(kinds.get(r.record_id) or "unread" for rs in assets for r in rs)
        t["per_asset"] += [len(rs) for rs in assets]
        t["unlocated"] += sum(not any(r.file for r in rs) for rs in assets)
        units = {identity(r, UNIT_LEVEL) for rs in assets for r in rs if r.resolution in (None, "mapped")}
        t["items"] += len(units - {None})
    rows = []
    for tool, t in sorted(acc.items()):
        per, n_assets = t["per_asset"], len(t["per_asset"])
        rows.append(
            {
                "tool": tool,
                "role": "voter" if votes(voter_of, tool) else "observer",
                "cboms": t["cboms"],
                "assets": n_assets,
                "records": sum(per),
                "items": t["items"],
                "records_per_asset_median": statistics.median(per) if per else None,
                "records_per_asset_max": max(per, default=None),
                "multi_occurrence_share": round(sum(k > 1 for k in per) / n_assets, 3) if n_assets else None,
                "assets_per_item": round(n_assets / t["items"], 2) if t["items"] else None,
                "unlocated_share": round(t["unlocated"] / n_assets, 3) if n_assets else None,
                **{f"loc_{k}": t["kinds"][k] for k in LOCATION_KINDS},
            }
        )
    return rows


def micro_verdict(status: str, score: TruthScore | None) -> str:
    """pass/fail (inclusion bar: lenient call-site recall) for a scored run; otherwise why there is no grade:
    unsupported (outside the tool's languages), missing (not run), error (failed, timed out, cancelled, unreadable)."""
    if score is not None:
        return "pass" if score.passes_bar else "fail"
    return status if status in ("unsupported", "missing") else "error"


def micro_rows(entries: list[MicroEntry], *, run: int) -> list[dict]:
    rows = []
    for e in entries:
        s = e.score
        row = {
            "project": e.project.name,
            "language": e.project.language,
            "tool": e.tool,
            "run": run,
            "tailored": e.tailored,
            "status": e.status,
            "verdict": micro_verdict(e.status, s),
            "n_items": len(e.project.assets),
        }
        if s is not None:
            recall_repo = s.recall_repo
            row.update(
                {
                    "strict": len(s.hits_strict),
                    "lenient": len(s.hits_lenient),
                    "repo": None if s.hits_repo is None else len(s.hits_repo),
                    "recall_strict": round(s.recall_strict, 3),
                    "recall_lenient": round(s.recall_lenient, 3),
                    "recall_repo": None if recall_repo is None else round(recall_repo, 3),
                    "sites_in_file": s.sites_in_file,
                    "sites_correct": s.sites_correct,
                    "precision": round(s.precision, 3),
                    "passes_bar": s.passes_bar,
                    "missed": s.missed,
                    "repo_missed": s.repo_missed,
                    "repo_extra": s.repo_extra,
                }
            )
        rows.append(row)
    return rows


def micro_item_rows(entries: list[MicroEntry], *, run: int) -> list[dict]:
    """Long format: one row per (project, tool, asset) of every scored run, which level found the asset."""
    rows = []
    for e in entries:
        s = e.score
        if s is None:
            continue
        for a in e.project.assets:
            rows.append(
                {
                    "project": e.project.name,
                    "language": e.project.language,
                    "tool": e.tool,
                    "run": run,
                    "item": a.id,
                    "families": sorted(a.families),
                    "insertion_style": a.insertion_style,
                    "callsite_strict": a.id in s.hits_strict,
                    "callsite_lenient": a.id in s.hits_lenient,
                    "repo": None if s.hits_repo is None else a.id in s.hits_repo,
                }
            )
    return rows
