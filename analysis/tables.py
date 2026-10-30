"""Flat, long-format CSV tables of one experiment, for pandas/R (CLAUDE.md, Phase 3).

Three layers, so that later steps (the normaliser, further n-way matchers) only add files:
- runs:    one row per stored run (`run<i>.meta.json`), all repetitions;
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
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common.experiment import iter_results, repo_full_name, stored_status
from common.tools_registry import tool_packaging, votes

from .costs import cost_usd, load_prices
from .groundtruth import TruthProject, TruthScore, entry_verdict
from .levels import UNIT_LEVEL, identity
from .matchers import MatchGroup, agreement
from .normalize.record import RECORD_COLUMNS, NormalizedCbom, NormRecord
from .normalize.report import assets_of

LIST_SEP = ";"
ASSET_COMPONENT_TYPES = ("cryptographic-asset", "crypto-asset")  # the latter: IBM draft 1.4-cbom-1.0

RUNS_COLUMNS = [
    "repo", "snapshot", "commit", "variant", "variant_commit", "language", "expect_crypto", "repo_files",
    "repo_source_files", "repo_bytes", "repo_source_bytes", "tool", "run", "tailored",
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
    "member_voter_groups",
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
    "seconds",  # the matcher's wall time at the level, summed over the repositories (the report fills it)
]  # fmt: skip
AGREEMENT_COLUMNS = [
    "matcher", "repo", "snapshot", "variant", "run", "group_id", "scope", "key", "dimension", "reporting",
    "agreeing", "majority", "values",
]  # fmt: skip
LOCATION_KINDS = ("line", "window", "import", "file", "none", "unread")  # analysis/schema.py:location_kind
UNIT_COLUMNS = [
    "tool", "packaging", "role", "cboms", "assets", "records", "items", "records_per_asset_median",
    "records_per_asset_max", "multi_occurrence_share", "assets_per_item", "unlocated_share",
    *[f"loc_{k}" for k in LOCATION_KINDS], "outside_code",
]  # fmt: skip
UNIT_REPO_COLUMNS = ["tool", "role", "repo", "language", "assets", "records", "items"]
MICRO_COLUMNS = [
    "project", "language", "tool", "run", "tailored", "status", "verdict", "n_items",
    "strict", "lenient", "repo", "recall_strict", "recall_lenient", "recall_repo",
    "sites_in_file", "sites_correct", "precision", "f1", "bar_items", "passes_bar", "entry_bar", "missed",
    "repo_missed", "repo_extra", "decoys", "decoys_hit",
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
    a weaker claim than a file and a line, so the two are counted apart (RQ2: `located` and traceability)."""
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
    sizes: dict[str, dict[str, int]] | None = None,
) -> list[dict]:
    """One row per stored meta file; CBOM counts only where the CBOM exists and parses. `tailored`: {tool: repos it
    was tuned on} (refinement 13) for the `tailored` column; `expects`: {repo: expect_crypto} from the spec; `sizes`:
    {snapshot: {files, source_files, checkout_bytes, source_bytes}} at the pinned commit, from the record or the
    snapshot store (missing keys stay empty)."""
    languages = languages or {}
    tailored = tailored or {}
    expects = expects or {}
    sizes = sizes or {}
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
            "repo_files": (sizes.get(snapshot) or {}).get("files"),
            "repo_source_files": (sizes.get(snapshot) or {}).get("source_files"),
            "repo_bytes": (sizes.get(snapshot) or {}).get("checkout_bytes"),
            "repo_source_bytes": (sizes.get(snapshot) or {}).get("source_bytes"),
            "tool": tool,
            "run": meta.get("run_index"),
            "tailored": repo in tailored.get(tool, ()),
            "status": stored_status(meta),
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


# ----- run time (RQ2: what a scan costs) -----

RUNTIME_COLUMNS = [
    "tool", "language", "repos", "runs", "timeouts", "timeout_share", "median_sec", "median_ci_low", "median_ci_high",
    "p90_sec", "max_sec", "spread_cv", "rerun_cv", "rerun_pairs", "hours", "sec_per_1k_source_files", "size_rho",
]  # fmt: skip
RUNTIME_MIN_REPOS = 3  # a CI and a size correlation only over at least this many repositories (score.py's MIN_REPOS)
RUNTIME_BOOTSTRAP = 1000


def _quantile(values: list[float], q: float) -> float:
    """The q-quantile with linear interpolation between the order statistics (values sorted)."""
    pos = q * (len(values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def _cv(values: list[float]) -> float | None:
    """Coefficient of variation (population sd / mean), None below two values or for a zero mean."""
    if len(values) < 2 or not statistics.mean(values):
        return None
    return statistics.pstdev(values) / statistics.mean(values)


def runtime_rows(runs: list[dict], *, seed: int = 20261023) -> list[dict]:
    """Per tool and language (and `all`), how long its scans take, from runs.csv. The unit is the repository: a
    repository's time is the median of its ok runs (all variants and repetitions), so a mutated repository or a
    repeated run counts once. `median_sec` is the median over the repositories with a bootstrap CI over them (95 %,
    percentile, only with RUNTIME_MIN_REPOS); `p90_sec` over the repositories too, `max_sec` the slowest single ok run;
    `spread_cv` the CV of the repositories' times (how much the repository decides); `rerun_cv` the median CV of the
    ok runs of one snapshot and variant with several runs (run-to-run noise; empty without repetitions); `hours` the
    wall time of every run that ran (ok, error, timeout, oom); `timeout_share` of those; `sec_per_1k_source_files`
    the median over repositories of time per thousand source files of the repository's language; `size_rho`
    Spearman between a repository's time and its source files; both only where `size_rho` exists (RUNTIME_MIN_REPOS
    repositories whose sizes differ). Unsupported runs never ran and are left out."""
    from .score import spearman

    ran = [r for r in runs if r.get("status") not in (None, "", "unsupported")]
    scopes: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in ran:
        scopes[(r["tool"], "all")].append(r)
        if r.get("language"):
            scopes[(r["tool"], r["language"])].append(r)
    out = []
    for (tool, language), rs in sorted(scopes.items(), key=lambda kv: (kv[0][0], kv[0][1] != "all", kv[0][1])):
        seconds = [(r, float(r["duration_sec"])) for r in rs if r.get("duration_sec") not in (None, "")]
        ok = [(r, d) for r, d in seconds if r["status"] == "ok"]
        per_repo: dict[str, list[float]] = defaultdict(list)
        per_cell: dict[tuple[str, str], list[float]] = defaultdict(list)
        source: dict[str, int] = {}
        for r, d in ok:
            per_repo[r["snapshot"]].append(d)
            per_cell[(r["snapshot"], r.get("variant") or "")].append(d)
            if r.get("repo_source_files") not in (None, ""):
                source[r["snapshot"]] = int(r["repo_source_files"])
        repo_sec = {s: statistics.median(v) for s, v in sorted(per_repo.items())}
        values = sorted(repo_sec.values())
        timeouts = sum(r["status"] == "timeout" for r in rs)
        row = {
            "tool": tool,
            "language": language,
            "repos": len(repo_sec),
            "runs": len(rs),
            "timeouts": timeouts,
            "timeout_share": round(timeouts / len(rs), 3),
            "hours": round(sum(d for _, d in seconds) / 3600, 2),
        }
        if values:
            row |= {
                "median_sec": round(statistics.median(values), 1),
                "p90_sec": round(_quantile(values, 0.9), 1),
                "max_sec": round(max(d for _, d in ok), 1),
            }
            if (cv := _cv(values)) is not None:
                row["spread_cv"] = round(cv, 2)
        if len(values) >= RUNTIME_MIN_REPOS:
            rng = random.Random(seed)
            medians = sorted(statistics.median(rng.choice(values) for _ in values) for _ in range(RUNTIME_BOOTSTRAP))
            row["median_ci_low"] = round(_quantile(medians, 0.025), 1)
            row["median_ci_high"] = round(_quantile(medians, 0.975), 1)
        reruns = [cv for v in per_cell.values() if (cv := _cv(v)) is not None]
        if reruns:
            row["rerun_cv"] = round(statistics.median(reruns), 2)
            row["rerun_pairs"] = len(reruns)
        sized = {s: float(n) for s, n in source.items()}
        if len(sized) >= RUNTIME_MIN_REPOS and (rho := spearman(repo_sec, sized)) is not None:
            row["size_rho"] = round(rho, 2)
            # only where sizes vary: over projects of one size (the micro projects: one file) it is start-up cost
            rates = [1000 * repo_sec[s] / n for s, n in sized.items() if n]
            row["sec_per_1k_source_files"] = round(statistics.median(rates), 1) if rates else None
        out.append(row)
    return out


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
        # the voter groups whose own records are in the group (not attached by vocabulary knowledge): a group with
        # none is no item of the voters (matchlevel_row skips it), however many generic records hang on it
        members = {(voter_of or {}).get(r.tool, r.tool) for r in g.members
                   if r.record_id not in g.attached and votes(voter_of, r.tool)}  # fmt: skip
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
                "member_voter_groups": len(members),
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


def raqun_inspection_rows(
    groups_by_snapshot: dict[tuple[str, str], list[MatchGroup]], *, level: str, run: int, tools: list[str]
) -> tuple[list[dict], list[str]]:
    """One readable match per row, with the actual element attributes and reported names side by side by tool."""
    from .matchers.raqun import attributes

    columns = ["repo", "snapshot", "variant", "run", "level", "group_id", "similarity", "n_tools", "n_records", *tools]
    rows = []
    for (snapshot, variant), groups in sorted(groups_by_snapshot.items()):
        for g in groups:
            row = {
                "repo": full_name(snapshot),
                "snapshot": snapshot,
                "variant": variant,
                "run": run,
                "level": level,
                "group_id": g.group_id,
                "similarity": g.extra.get("similarity"),
                "n_tools": g.k,
                "n_records": len(g.members),
            }
            by_tool: dict[str, list[NormRecord]] = defaultdict(list)
            for rec in g.members:
                by_tool[rec.tool].append(rec)
            for tool, records in sorted(by_tool.items()):
                names = json.dumps(sorted({r.name for r in records if r.name}), ensure_ascii=False)
                attrs = "; ".join(sorted(attributes(records[0], level)))
                row[tool] = f"{names} <{attrs}> [{len(records)} records]"
            rows.append(row)
    return rows, columns


def matchlevel_row(
    matcher: str, level: str, run: int, groups_by_snapshot: dict, voter_of: dict[str, str] | None = None
) -> dict:
    """One row of matchlevels-run<i>.csv (RQ4, matcher x level): over the repo groups of every snapshot, how many
    items (algorithms, by their L4 identity: `extra.units` of the matchers' groups, else the group key) have
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
            if not any(r.record_id not in g.attached for r in voting):
                continue  # only an observer reports it: the voters' generic records attached to it make no item
            for unit in g.extra.get("units") or [g.key]:  # the matchers' units are the voters' already
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
    the assets per item (above 1: one component per finding), the share of assets without a file, the
    algorithm records per location kind (`loc_<kind>`; `location_kinds`: one {record id: kind} per CBOM) and the
    records whose file is not a source file of the repository's language (`outside_code`: `NormRecord.in_scope`
    False; fixtures, test vectors, configuration, documentation, other languages)."""
    acc: dict[str, dict] = {}
    for i, n in enumerate(cboms):
        t = acc.setdefault(
            n.tool, {"cboms": 0, "per_asset": [], "items": 0, "unlocated": 0, "kinds": Counter(), "outside": 0}
        )
        t["cboms"] += 1
        assets, units = _packaging(n)
        kinds = location_kinds[i] if location_kinds else {}
        t["kinds"].update(kinds.get(r.record_id) or "unread" for rs in assets for r in rs)
        t["outside"] += sum(r.in_scope is False for rs in assets for r in rs)
        t["per_asset"] += [len(rs) for rs in assets]
        t["unlocated"] += sum(not any(r.file for r in rs) for rs in assets)
        t["items"] += len(units)
    rows = []
    for tool, t in sorted(acc.items()):
        per, n_assets = t["per_asset"], len(t["per_asset"])
        rows.append(
            {
                "tool": tool,
                "packaging": tool_packaging(tool),
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
                "outside_code": t["outside"],
            }
        )
    return rows


def unit_repo_rows(
    cboms: list[NormalizedCbom],
    repos: list[str],
    languages: dict[str, str] | None = None,
    voter_of: dict[str, str] | None = None,
) -> list[dict]:
    """unit_rows' assets, records and items per repository, one row per CBOM (`repos[i]` is the repository of
    `cboms[i]`): the items are what the matchers work on, one repository at a time."""
    rows = []
    for n, repo in zip(cboms, repos, strict=True):
        assets, units = _packaging(n)
        rows.append(
            {
                "tool": n.tool,
                "role": "voter" if votes(voter_of, n.tool) else "observer",
                "repo": repo,
                "language": (languages or {}).get(repo),
                "assets": len(assets),
                "records": sum(len(rs) for rs in assets),
                "items": len(units),
            }
        )
    return sorted(rows, key=lambda r: (r["tool"], r["repo"]))


def _packaging(n: NormalizedCbom) -> tuple[list[list[NormRecord]], set[str]]:
    """A CBOM's algorithm assets, each as its records, and its items (the L4 identities of its mapped records)."""
    assets = [rs for rs in assets_of(n) if rs[0].values.get("asset_type") == "algorithm"]
    units = {identity(r, UNIT_LEVEL) for rs in assets for r in rs if r.resolution in (None, "mapped")}
    return assets, units - {None}


def micro_verdict(status: str, score: TruthScore | None) -> str:
    """pass/fail (the entry bar: lenient call-site recall) for a scored run; otherwise why there is no grade:
    unsupported (outside the tool's languages), missing (not run), error (failed, timed out, cancelled, unreadable)."""
    if score is not None:
        return "pass" if score.passes_bar else "fail"
    return status if status in ("unsupported", "missing") else "error"


def micro_rows(entries: list[MicroEntry], *, run: int) -> list[dict]:
    """One row per (micro project, tool); `entry_bar` is the tool's verdict over all its projects (passed,
    disqualified, not judged: groundtruth.entry_verdict)."""
    by_tool: dict[str, list] = defaultdict(list)
    for e in entries:
        by_tool[e.tool].append(e.score)
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
            "decoys": len(e.project.decoys),
            "entry_bar": entry_verdict(by_tool[e.tool]),
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
                    "precision": None if s.precision is None else round(s.precision, 3),
                    "f1": round(s.f1, 3),
                    "bar_items": s.bar_items,
                    "passes_bar": s.passes_bar,
                    "missed": s.missed,
                    "repo_missed": s.repo_missed,
                    "repo_extra": s.repo_extra,
                    "decoys_hit": s.decoys_hit,
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
