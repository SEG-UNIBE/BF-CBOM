"""_SUMMARY.md: Markdown tables of one experiment for a first look, e.g. in an exported zip (CLAUDE.md, Phase 3).

Rendered only from what `analysis report` writes into `<exp>/_ANALYSIS/` (the CSV tables and `tables-run<k>.json`)
plus the stored spec and run log (`_experiment.yaml`, `_run-log.json`), so it can be rebuilt from an export
(`python -m analysis.summary <exp>/_ANALYSIS`) and never shows anything those files don't hold. The CSVs are the data
for plots; this file is for reading.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import yaml

from common.experiment import load_run_log, spec_path

SUMMARY_FILE = "_SUMMARY.md"
LANGUAGE_ORDER = ["java", "python", "go", "javascript", "typescript", "c", "cpp"]
LEVEL_ORDER = ("L0", "L1", "L2", "L3", "L4")
RESOLUTION_SHOWN = ["mapped", "ambiguous", "api", "non-crypto", "undefined"]  # analysis/normalize/engine.RESOLUTIONS
COVERAGE_SHOWN = ["key_size", "mode", "padding", "curve", "digest"]
FILES = {
    "runs.csv": "one row per stored run, all repetitions (status, duration, tool version, raw CBOM counts by asset "
    "type, LLM tokens and costs)",
    "records-run{k}.csv": "one row per normalised record: every dimension with its raw value and source",
    "micro-run{k}.csv": "one row per (micro project, tool): status, verdict, recall at call sites and repo level",
    "micro-items-run{k}.csv": "one row per (micro project, tool, expected item): found at call site (strict, "
    "lenient), repo",
    "items-run{k}.csv": "one row per (repo, tool, item) with the voter groups matching it at each level L0-L4; no "
    "locations",
    "levels-run{k}.csv": "share of each tool's items another voter group confirms, per level L0-L4",
    "units-run{k}.csv": "per tool: assets, records, items and how they relate (records per asset, assets per item)",
    "coverage-run{k}.csv": "per tool and dimension: algorithm assets with a value (reported vs derived), and what "
    "their names are (resolution)",
    "conformance-run{k}.csv": "spec issues per tool and kind (assets: field, enum, type, deprecated; documents)",
    "schema-run{k}.csv": "RQ1: one row per CBOM: declared format and version, schema validity, error count and kinds",
    "meaningful-run{k}.csv": "RQ1: per tool, asset type and dimension, assets with a value from standard fields / any",
    "traceability-run{k}.csv": "RQ1: per (snapshot, tool, asset type), records with a file, with a file and line, "
    "existing at the commit, in range, strong",
    "vocab.csv": "what every raw value mapped to, per tool and source (the reviewable mapping table)",
    "groups-exact-run{k}.csv": "exact matcher: one row per group (repo scope per item, site scope per call site)",
    "members-exact-run{k}.csv": "exact matcher: one row per (group, record); `matched` = joined through a lower "
    "level, `implied` with --closure",
    "matchlevels-run{k}.csv": "RQ3: per matcher and level, items by number of voter groups and the agreement per "
    "dimension inside the groups",
    "agreement-exact-run{k}.csv": "exact matcher: one row per (group, dimension): voters reporting, agreeing, majority",
    "groups-raqun-run{k}.csv": "RaQuN (matcher (c)): one row per group, with its attribute-set similarity",
    "members-raqun-run{k}.csv": "RaQuN: one row per (group, record)",
    "agreement-raqun-run{k}.csv": "RaQuN: one row per (group, dimension)",
    "matchers-run{k}.csv": "RQ3: per repository, exact vs RaQuN: common records, groups, co-membership pair Jaccard",
    "score-run{k}.csv": "RQ4: per tool, dimension and level: items, confirmation (CI), exclusivity, rank, Dawid-Skene",
    "loo-run{k}.csv": "RQ4: ranking without each voter group and its Kendall tau against the full ranking",
    "grouping-run{k}.csv": "RQ4: ranking with every tool its own voter group and with the LLM workers merged, and "
    "its Kendall tau against the declared voter groups",
    "stability.csv": "per (tool, snapshot) with repeated runs: Jaccard across runs, record-count CV, consensus set",
}


# ----- helpers -----


def md_table(header: list[str], rows: list[list], align: str | None = None) -> list[str]:
    """GitHub Markdown table; `align` has one letter per column (l or r), default: first left, rest right. A cell
    that is a number gets thousands separators (`_n`)."""
    align = align or "l" + "r" * (len(header) - 1)

    def cell(v) -> str:
        return "" if v is None else _n(v).replace("|", "\\|").replace("\n", " ")

    sep = ["---:" if a == "r" else "---" for a in align]
    out = ["| " + " | ".join(cell(h) for h in header) + " |", "| " + " | ".join(sep) + " |"]
    out += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return out


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(v) -> str:
    f = _float(v)
    return "" if f is None else f"{f:.0%}"


def _num(v) -> str:
    """A count or ratio without trailing zeros (2.0 -> 2, 1.25 -> 1.2)."""
    f = _float(v)
    return "" if f is None else _n(f"{f:.1f}".removesuffix(".0"))


NUMBER = re.compile(r"-?\d+(\.\d+)?")


def _n(v) -> str:
    """A number with ' between thousands (13043 -> 13'043, 1234.5 -> 1'234.5); anything else as it is. Table cells
    get it in `md_table`, counts in the text explicitly; the CSVs keep plain numbers."""
    s = "" if v is None else str(v)
    if not NUMBER.fullmatch(s):
        return s
    head, dot, tail = s.partition(".")
    return f"{int(head):,}".replace(",", "'") + dot + tail


def _ts(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _when(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _span(seconds: float) -> str:
    m = round(seconds / 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def _gib(mb) -> str:
    f = _float(mb)
    return "" if f is None else f"{f / 1024:.1f}"


def _true(v) -> bool:
    """CSV booleans: write_csv lowercases them."""
    return str(v).strip().lower() == "true"


def _false(v) -> bool:
    return str(v).strip().lower() == "false"


OBSERVER_NOTE = (
    "`°` = observer (tools.yaml `role`): confirmed by the voters like every tool, but it confirms nobody, is in no "
    "pooled row and no k, and gets no rank (`--observers-vote` makes it a voter)."
)


def _tool(r: dict) -> str:
    """The tool's name, marked when it is an observer in this report."""
    return f"{r['tool']} °" if r.get("role") == "observer" else r["tool"]


def _lang_key(lang: str) -> tuple[int, str]:
    return (LANGUAGE_ORDER.index(lang) if lang in LANGUAGE_ORDER else len(LANGUAGE_ORDER), lang)


# ----- sections -----


def _header(spec: dict, info: dict, run: int) -> list[str]:
    name = spec.get("name") or "?"
    vocab = info.get("vocabulary") or {}
    lines = [f"# {name}: analysis summary (run {run})", ""]
    if spec.get("description"):
        lines += [str(spec["description"]).strip(), ""]
    repos = spec.get("repos") or []
    tools = spec.get("tools") or []
    when = info.get("generated_at") or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(
        f"Generated {when} by `analysis report`: {_n(len(repos))} repos, {len(tools)} tools, "
        f"{spec.get('repetitions', 1)} repetition(s), as listed with their commits in `_experiment.yaml`. "
        f"Vocabulary {vocab.get('algorithms', '?')}, "
        f"CycloneDX registry {vocab.get('registry', '?')}."
    )
    lines += ["The data behind every table are the CSV files listed at the end (`;` joins lists inside a cell).", ""]
    return lines


def _experiment(rows: list[dict], sessions: list[dict]) -> list[str]:
    """When it ran, for how long, on how much RAM, the peaks and the code that ran (the runner's run log plus the
    runs' meta)."""
    ran = [r for r in rows if r.get("status") != "unsupported"]
    lines = ["## Experiment", ""]
    table: list[list] = []
    if sessions:
        s_start = [t for e in sessions if (t := _ts(e.get("started_at")))]
        s_end = [t for e in sessions if (t := _ts(e.get("finished_at") or e.get("updated_at")))]
        active = sum(
            (e_end - e_start).total_seconds()
            for e in sessions
            if (e_start := _ts(e.get("started_at"))) and (e_end := _ts(e.get("finished_at") or e.get("updated_at")))
        )
        if s_start and s_end:
            table.append(["ran", f"{_when(min(s_start))} to {_when(max(s_end))}"])
            table.append(["duration", f"{_span(active)} in {len(sessions)} runner session(s)"])
        outcomes = Counter(str(e.get("status") or "?") for e in sessions)
        table.append(["sessions", ", ".join(f"{n} {k}" for k, n in sorted(outcomes.items()))])
    tool_time = sum(_float(r.get("duration_sec")) or 0 for r in ran)
    if ran:
        table.append(["tool time", f"{_span(tool_time)} summed over {_n(len(ran))} runs (excluding unsupported)"])
    queue = [q for r in ran if (q := _float(r.get("queue_sec"))) is not None]
    if queue:
        table.append(
            [
                "queue wait",
                f"median {_n(round(statistics.median(queue)))} s, max {_n(round(max(queue)))} s before a job started",
            ]
        )
    host = next((e.get("host") for e in reversed(sessions) if e.get("host")), None) or {}
    if host.get("ram_total_mb"):
        table.append(["host", f"{_gib(host['ram_total_mb'])} GiB RAM, {host.get('cpus') or '?'} CPUs (Docker VM)"])
    last = sessions[-1] if sessions else {}
    if "memory_budget_gb" in last:
        budget, waves = _float(last.get("memory_budget_gb")) or 0, last.get("waves") or []
        if budget:
            table.append(["memory budget", f"{budget:g} GiB -> {len(waves)} wave(s) (last session)"])
        else:
            table.append(["memory budget", "none (all tools at once)"])
    peaks = [p for e in sessions if (p := _float(e.get("ram_peak_used_mb"))) is not None]
    if peaks:
        total = _float(host.get("ram_total_mb"))
        share = f" of {_gib(total)} GiB ({max(peaks) / total:.0%})" if total else ""
        table.append(["peak RAM in use", f"{_gib(max(peaks))} GiB{share} on the VM, sampled every 2 s"])
    per_job = [(p, r) for r in ran if (p := _float(r.get("peak_memory_mb"))) is not None]
    if per_job:
        p, r = max(per_job, key=lambda x: x[0])
        table.append(["peak RAM of a job", f"{_gib(p)} GiB: `{r['tool']}` on `{r['repo']}` (container working set)"])
    ooms = sum(_int(r.get("oom_kills")) for r in ran)
    if per_job:
        table.append(["OOM kills", str(ooms)])
    images = sorted({r["image_ref"].rsplit(":", 1)[-1] for r in ran if r.get("image_ref") and ":" in r["image_ref"]})
    if images:
        table.append(["BF-CBOM images", ", ".join(images)])
    commits = sorted({r["bfcbom_commit"] for r in ran if r.get("bfcbom_commit")})
    commits += sorted({e["runner_commit"] for e in sessions if e.get("runner_commit")} - set(commits))
    if commits:
        dirty = " (`-dirty`: built from uncommitted changes)" if any(c.endswith("-dirty") for c in commits) else ""
        table.append(["BF-CBOM commit", ", ".join(f"`{c}`" for c in commits) + dirty])
    image_ids = {t: i for e in sessions for t, i in (e.get("images") or {}).items()}
    if image_ids:
        table.append(["worker image ids", f"{len(image_ids)} recorded in `_run-log.json` (by content, per session)"])
    if not table:
        return []
    lines += md_table(["", ""], table, "ll")
    return lines + [""]


def _runs(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_tool[r["tool"]].append(r)
    statuses = ["ok", "error", "timeout", "unsupported"]
    memory = any(r.get("peak_memory_mb") for r in rows)
    table, others = [], []
    for tool in sorted(by_tool):
        rs = by_tool[tool]
        c = Counter(r["status"] for r in rs)
        other = sum(n for s, n in c.items() if s not in statuses)
        durations = [d for r in rs if r["status"] == "ok" and (d := _float(r.get("duration_sec"))) is not None]
        median = f"{statistics.median(durations):.0f}" if durations else ""
        versions = sorted({r["tool_version"] for r in rs if r.get("tool_version")})
        if not versions and c.get("unsupported") == len(rs):
            versions = ["not run: no repo in its languages"]
        row = [tool] + [c.get(s, 0) or "" for s in statuses] + [other or "", median]
        others.append(other)
        if memory:
            peaks = [p for r in rs if (p := _float(r.get("peak_memory_mb"))) is not None]
            limits = [p for r in rs if (p := _float(r.get("memory_limit_mb"))) is not None]
            row += [_gib(max(peaks)) if peaks else "", _gib(max(limits)) if limits else ""]
            row += [sum(_int(r.get("oom_kills")) for r in rs) or ""]
        table.append(row + ["; ".join(versions)])
    header = ["tool", *statuses, "other", "median s"]
    if not any(others):  # statuses outside the four are rare; keep the column only when there are some
        i = header.index("other")
        header.pop(i)
        for row in table:
            row.pop(i)
    if memory:
        header += ["peak GiB", "limit GiB", "OOM kills"]
    lines = ["## Runs", "", "All stored runs, every repetition."]
    if memory:
        lines[-1] += " Peak GiB: the largest container working set of any run (sampled every second)."
    lines += [""]
    lines += md_table([*header, "tool version"], table, "l" + "r" * (len(header) - 1) + "l")
    failed = [r for r in rows if r["status"] not in ("ok", "unsupported")]
    if failed:
        lines += ["", f"Runs without a result ({_n(len(failed))}):", ""]
        for r in failed[:25]:
            err = f": {r['error']}" if r.get("error") else ""
            lines.append(f"- `{r['tool']}` on `{r['repo']}` ({r['variant']}, run {r['run']}): {r['status']}{err}")
        if len(failed) > 25:
            lines.append(f"- ... and {_n(len(failed) - 25)} more (runs.csv)")
    workarounds = [r for r in rows if r.get("workaround")]
    if workarounds:
        lines += [
            "",
            f"Runs with a recorded setup workaround ({_n(len(workarounds))}; refinement 15: the tool is unchanged, the "
            "input it crashes on is excluded and named):",
            "",
        ]
        for r in workarounds[:25]:
            lines.append(f"- `{r['tool']}` on `{r['repo']}` ({r['variant']}, run {r['run']}): {r['workaround']}")
        if len(workarounds) > 25:
            lines.append(f"- ... and {_n(len(workarounds) - 25)} more (runs.csv)")
    oomed = [r for r in rows if r["status"] == "ok" and _int(r.get("oom_kills")) > 0]
    if oomed:
        lines += [
            "",
            f"Runs stored ok although the container was OOM-killed meanwhile ({_n(len(oomed))}; not clean results, "
            "`experiment run --retry-failed` redoes them):",
            "",
        ]
        for r in oomed[:25]:
            lines.append(
                f"- `{r['tool']}` on `{r['repo']}` ({r['variant']}, run {r['run']}): {r['oom_kills']} OOM kill(s) "
                f"at {_gib(r.get('memory_limit_mb'))} GiB"
            )
        if len(oomed) > 25:
            lines.append(f"- ... and {_n(len(oomed) - 25)} more (runs.csv)")
    return lines + [""]


def _negatives(rows: list[dict], run: int) -> list[str]:
    """Repositories the spec expects little or no cryptography in (`expect_crypto: low`): what each tool reports there
    is a proxy for false positives (no ground truth, but a weak prior)."""
    rows = [
        r
        for r in rows
        if _int(r.get("run")) == run
        and (r.get("variant") or "original") == "original"
        and (r.get("expect_crypto") or "").lower() == "low"
    ]
    if not rows:
        return []
    repos = sorted({r["repo"] for r in rows})
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["status"] == "ok" and r.get("crypto_assets") not in (None, ""):
            by_tool[r["tool"]].append(r)
    table = []
    for tool in sorted(by_tool):
        rs = by_tool[tool]
        counts = [_int(r["crypto_assets"]) for r in rs]
        algos = sum(_int(r.get("algorithm_assets")) for r in rs)
        table.append([tool, len(rs), sum(counts), algos, sum(c == 0 for c in counts), max(counts)])
    lines = [
        f"**Low-expectation repositories** ({len(repos)}: {', '.join(f'`{r}`' for r in repos)}): the spec marks them "
        "as containing little or no first-party cryptography, so what a tool reports there is a false-positive "
        "proxy (a weak prior, not ground truth; ok runs of this repetition).",
        "",
    ]
    lines += md_table(["tool", "repos ok", "crypto assets", "algorithm assets", "empty runs", "max per repo"], table)
    return lines + [""]


ASSET_KINDS = [  # runs.csv column -> table header, in the order of the RQ2 table
    ("algorithm_assets", "algorithm"),
    ("certificate_assets", "certificate"),
    ("protocol_assets", "protocol"),
    ("material_assets", "key material"),
    ("other_assets", "other"),
]


def _reported(rows: list[dict], run: int) -> list[str]:
    """RQ2 before any normalisation: what each tool wrote into its CBOMs (one repetition, original variants), and the
    crypto asset count of every (repo, tool) pair."""
    rows = [r for r in rows if _int(r.get("run")) == run and (r.get("variant") or "original") == "original"]
    if not rows:
        return []
    ok = [r for r in rows if r["status"] == "ok" and r.get("crypto_assets") not in (None, "")]
    tools = sorted({r["tool"] for r in rows})
    table = []
    for tool in tools:
        rs = [r for r in ok if r["tool"] == tool]
        if not rs:
            table.append([tool, 0] + [""] * (len(ASSET_KINDS) + 6))
            continue
        crypto = sum(_int(r["crypto_assets"]) for r in rs)
        with_file = sum(_int(r.get("located_file_assets")) for r in rs)
        with_line = sum(_int(r.get("located_line_assets")) for r in rs)
        counts = [_int(r["crypto_assets"]) for r in rs]
        table.append(
            [tool, len(rs), sum(_int(r.get("components")) for r in rs), crypto]
            + [sum(_int(r.get(col)) for r in rs) for col, _ in ASSET_KINDS]
            + [_pct(with_file / crypto) if crypto else "", _pct(with_line / crypto) if crypto else ""]
            + [sum(c == 0 for c in counts), f"{statistics.median(counts):.0f}"]
        )
    lines = ["## What the tools report (RQ2)", ""]
    lines += [
        f"Read from the CBOMs as the tools wrote them, before normalisation (run {run}, original variants, ok runs "
        "only). `components` is every component in the CBOM (inventory tools list dependencies here), `crypto assets` "
        "those typed as crypto assets, split by the declared `assetType`; `file` / `file+line` = share of crypto "
        "assets with a file, and with a file and a line, in the CBOM (`evidence.occurrences`, or `detectionContext` "
        "in IBM's draft: a line is an addition to the file, so the two are counted apart); `empty` = ok runs that "
        "reported no crypto asset; `median` = crypto assets per repo. Asset counts follow each tool's packaging (one "
        "component per algorithm, or one per finding), so they compare output formats, not findings: how many "
        'items each tool reports is in the normalisation section ("Units per tool").',
        "",
    ]
    header = ["tool", "repos ok", "components", "crypto assets", *[h for _, h in ASSET_KINDS], "file", "file+line",
              "empty", "median"]  # fmt: skip
    lines += md_table(header, table, "l" + "r" * (len(header) - 1))

    repos = sorted({(r["repo"], (r.get("language") or ""), r.get("expect_crypto") or "") for r in rows},
                   key=lambda x: (_lang_key(x[1].lower() or "?"), x[0]))  # fmt: skip
    expect = any(x[2] for x in repos)
    by_pair = {(r["repo"], r["tool"]): r for r in rows}

    def cell(repo: str, tool: str) -> str:
        r = by_pair.get((repo, tool))
        if r is None:
            return ""
        if r["status"] == "ok":
            return str(_int(r["crypto_assets"])) if r.get("crypto_assets") not in (None, "") else "?"
        return "–" if r["status"] == "unsupported" else "×"

    matrix = []
    for repo, language, exp in repos:
        matrix.append([repo, language.lower()] + ([exp] if expect else []) + [cell(repo, t) for t in tools])
    totals = ["all", ""] + ([""] if expect else [])
    for tool in tools:
        rs = [r for r in ok if r["tool"] == tool]
        totals.append(sum(_int(r["crypto_assets"]) for r in rs) if rs else "")
    matrix.append(totals)
    lines += [
        "",
        "Crypto assets per repository and tool, as written (ok runs; – unsupported language, × failed or timed out"
        + (
            ", `expect` = `expect_crypto` from the corpus: `low` marks a negative case where little crypto is "
            "expected, so reports there are a false-positive proxy"
            if expect
            else ""
        )
        + ").",
        "",
    ]
    lines += md_table(["repo", "language"] + (["expect"] if expect else []) + tools, matrix,
                      "ll" + ("l" if expect else "") + "r" * len(tools))  # fmt: skip
    return lines + [""]


def _micro(rows: list[dict], expected: list[dict]) -> list[str]:
    if not rows:
        return []
    langs = sorted({r["language"] for r in rows}, key=_lang_key)
    cells = {(r["tool"], r["language"]): r for r in rows}
    n_items = max((_int(r.get("n_items")) for r in rows), default=11)
    bar = (n_items + 1) // 2

    def rank(tool: str) -> tuple:
        rs = [cells[(tool, lg)] for lg in langs if (tool, lg) in cells]
        return (-sum(r["verdict"] == "pass" for r in rs), -sum(_int(r.get("lenient")) for r in rs), tool)

    tools = sorted({r["tool"] for r in rows}, key=rank)

    tailored = any(_true(r.get("tailored")) for r in rows)

    def verdict_cell(r: dict | None) -> str:
        if r is None or r["verdict"] == "missing":
            return "not run"
        mark = r"\*" if _true(r.get("tailored")) else ""
        if r["verdict"] in ("pass", "fail"):
            return f"{r['verdict']} {r['lenient']}{mark}"
        return "–" if r["verdict"] == "unsupported" else r["verdict"] + mark

    def recall_cell(r: dict | None) -> str:
        if r is not None and r["verdict"] in ("pass", "fail"):
            return f"{r['strict']} / {r['lenient']} · {r['repo']}"
        return "–" if r is not None and r["verdict"] == "unsupported" else ""

    lines = ["## Micro projects", ""]
    lines += [
        f"**Inclusion check** (`corpus/micro`, {n_items} expected items per project: a known crypto call at known "
        f"lines, the ground truth): lenient call-site recall, bar {bar}/{n_items}. `–` = outside the tool's "
        "languages, `error` = the run failed or timed out."
        + (
            " `*` = the tool was tuned on this project (`tailored_on` in tools.yaml, refinement 13)."
            if tailored
            else ""
        ),
        "",
    ]
    lines += md_table(["tool", *langs], [[t] + [verdict_cell(cells.get((t, lg))) for lg in langs] for t in tools])
    lines += [
        "",
        f"**Recall at two levels** (out of {n_items}): `strict / lenient · repo`. Call sites: a record at one of "
        "the item's ground-truth lines (a window counts at every line it spans) with a compatible family (strict; "
        "a family the item's name contains counts, SHA-2 for HMAC-SHA512) or an unknown one (lenient). Repo: the "
        "item's own family (HMAC for HMAC-SHA512) reported anywhere in the project, location ignored; items "
        "sharing a family (two RSA, two EC, two AES) are found together. Repo can be lower than call sites: it "
        "needs a known family, and the item's own one.",
        "",
    ]
    lines += md_table(["tool", *langs], [[t] + [recall_cell(cells.get((t, lg))) for lg in langs] for t in tools])
    if expected:
        found: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
        style: dict[str, str] = {}
        for a in expected:
            cnt = found[(a["item"], a["language"])]
            cnt[0] += _true(a["callsite_lenient"])
            cnt[1] += 1
            style[a["item"]] = a.get("insertion_style") or ""
        item_ids = list(dict.fromkeys(a["item"] for a in expected))
        table = []
        for aid in item_ids:
            row = [aid + (f" ({style[aid]})" if style.get(aid) not in ("", "direct") else "")]
            for lg in langs:
                f, n = found.get((aid, lg), (0, 0))
                row.append(f"{f}/{n}" if n else "")
            table.append(row)
        lines += ["", "**Who finds each expected item** (lenient call site): tools that found it / tools scored.", ""]
        lines += md_table(["expected item", *langs], table)
    return lines + [""]


LOCATION_KINDS = ("line", "window", "import", "file", "none", "unread")  # analysis/schema.py:location_kind


def _places(units: list[dict]) -> list[str]:
    """What kind of place each tool's algorithm records name (units-run<k>.csv `loc_<kind>`)."""
    if not units or f"loc_{LOCATION_KINDS[0]}" not in units[0]:
        return []
    table = []
    for u in units:
        total = sum(_int(u.get(f"loc_{k}")) for k in LOCATION_KINDS)
        shares = [_pct(_int(u.get(f"loc_{k}")) / total if total else None) for k in LOCATION_KINDS]
        table.append([_tool(u), total, *shares])
    unread = sum(_int(u.get("loc_unread")) for u in units)
    lines = [
        "**Places per tool**: the algorithm records by the kind of place they name. `line` = a single line of code "
        "that is not an import (in practice the call or the declaration), `window` = a span of several lines "
        "(CryptobomForge's 5-9-line windows), `import` = a single line holding an import statement, `file` = a file "
        "without a line, `none` = no location, `unread` = a single line whose file the snapshot does not have here. "
        "Tools that report import lines or windows place the same use elsewhere than tools that report the call: "
        "the call-site matching window (±2 lines) absorbs only part of that.",
    ]
    if unread:
        lines[0] += (
            f" {_n(unread)} records are `unread`: the snapshot files are on the VM's git mirror (`analysis report` "
            "there, or `--fetch` for a copied export)."
        )
    lines += [""] + md_table(["tool", "records", *LOCATION_KINDS], table) + [""]
    return lines


def _normalisation(units: list[dict], cov: list[dict], conf: list[dict]) -> list[str]:
    """How each tool packages its findings (units-run<k>.csv), then what its assets' names are and which values they
    carry (coverage-run<k>.csv, conformance-run<k>.csv); one run of the original variants throughout."""
    if not units and not cov:
        return []
    lines = ["## Normalisation", ""]
    if units:
        table = [
            [
                _tool(u),
                u["cboms"],
                u["assets"],
                u["records"],
                u["items"],
                f"{_num(u.get('records_per_asset_median'))} / {_n(u.get('records_per_asset_max'))}",
                _pct(u.get("multi_occurrence_share")),
                _num(u.get("assets_per_item")),
                _pct(u.get("unlocated_share")),
            ]
            for u in units
        ]
        lines += [
            "**Units per tool** (this run, original variants; `units-run<k>.csv`). How a tool splits what it finds "
            'into assets and records is its output format (`analysis/README.md`, "Units" and "Packaging per '
            'tool"): `assets` = its algorithm components, `records` = their occurrences, one per place in the code, '
            "`items` = the distinct algorithms (L4 identities) its mapped records name per repository, summed over "
            "the repositories. A tool that lists an algorithm once with all its places has several records per asset "
            "and about one asset per item; a tool that writes one component per finding has one record per asset and "
            "many assets per item, so its asset count says little about what it found. Compare tools by items. "
            "`several places` = assets listed at more than one place; `no location` = assets without a file.",
            "",
        ]
        header = ["tool", "CBOMs", "assets", "records", "items", "records per asset (median / max)", "several places",
                  "assets per item", "no location"]  # fmt: skip
        lines += md_table(header, table) + [""]
        lines += _places(units)
    if cov:
        by_tool: dict[str, dict[str, dict]] = defaultdict(dict)
        for r in cov:
            by_tool[r["tool"]][r["dimension"]] = r
        issues: dict[str, list[str]] = defaultdict(list)
        for r in conf:
            unit = " CBOMs" if r["scope"] == "document" else ""
            issues[r["tool"]].append(f"{r['kind']} {_n(r['count'])}{unit}")
        roles = {u["tool"]: u.get("role") for u in units}
        table = []
        for tool in sorted(by_tool):
            dims = by_tool[tool]
            first = next(iter(dims.values()))
            assets = _int(first["assets"])
            table.append(
                [_tool({"tool": tool, "role": roles.get(tool)}), assets]
                + [_pct(_int(first[k]) / assets if assets else None) for k in RESOLUTION_SHOWN]
                + [_pct(dims.get(d, {}).get("reported_share")) for d in COVERAGE_SHOWN]
                + [", ".join(issues.get(tool, [])) or "none"]
            )
        lines += [
            "**Names and values** (this run, original variants; `coverage-run<k>.csv`, `conformance-run<k>.csv`), as "
            "shares of the tool's algorithm assets, i.e. of its CBOM's components as a reader of that CBOM finds "
            "them: a document view, so the shares follow the tool's packaging (a tool that writes one component per "
            "finding weighs an often-found algorithm more). The packaging-free comparison of parameters across "
            "tools is per item: the ladder's parameter rows and the scores. What an asset's name is: `mapped` an "
            "algorithm identity (only these become items), `ambiguous` a list of candidates, `api` a code "
            "identifier, `non-crypto` a rule outside cryptography, `undefined` a name nobody understands. Then the "
            "share of assets with a value the tool gave, from any field, property, name or API context (derived "
            "values not counted), and the spec issues: assets with a standard-field value the declared version does "
            "not allow, by kind; `dialect` and `version` count CBOMs.",
            "",
        ]
        header = ["tool", "assets", *RESOLUTION_SHOWN, *[d.replace("_", " ") for d in COVERAGE_SHOWN], "spec issues"]
        lines += md_table(header, table, "lr" + "r" * (len(RESOLUTION_SHOWN) + len(COVERAGE_SHOWN)) + "l") + [""]
    if any(u.get("role") == "observer" for u in units):
        lines += [OBSERVER_NOTE, ""]
    return lines


def _rq1(schema: list[dict], meaningful: list[dict], trace: list[dict]) -> list[str]:
    """Conformance, meaningfulness and traceability per tool (analysis/schema.py)."""
    if not schema and not meaningful:
        return []
    lines = ["## RQ1: conformance, meaningful, traceable", ""]
    if schema:
        by_tool: dict[str, list[dict]] = defaultdict(list)
        for r in schema:
            by_tool[r["tool"]].append(r)
        table = []
        for tool in sorted(by_tool):
            rs = by_tool[tool]
            by = rs[0].get("cbom_by")
            checked = [r for r in rs if r.get("schema")]
            valid = sum(_true(r["valid"]) for r in checked)
            kinds: Counter = Counter()
            for r in checked:
                for item in (r.get("error_kinds") or "").split(";"):
                    if "=" in item:
                        k, n = item.rsplit("=", 1)
                        kinds[k] += _int(n)
            top = "; ".join(f"{k} ({_n(n)})" for k, n in kinds.most_common(3))
            versions = "; ".join(sorted({f"{r.get('bom_format')} {r.get('spec_version')}" for r in rs}))
            table.append(
                [
                    tool,
                    {"tool": "tool", "bf-cbom": "BF-CBOM"}.get(by or "", "?"),
                    len(rs),
                    versions,
                    len(checked) or "",
                    valid if checked else "",
                    (len(checked) - valid) or "" if checked else "",
                    len(rs) - len(checked) or "",
                    top,
                ]
            )
        lines += [
            "**Conformance:** every CBOM validated as a whole document against the official CycloneDX JSON schema of "
            "the version it declares (vendored 1.6 and 1.7). `CBOM by`: `tool` = the tool writes the document, "
            "`BF-CBOM` = our worker assembles it (the LLM workers), so validity there measures BF-CBOM, not the tool "
            "(refinement 3). `no schema` = another dialect or version "
            "(IBM's draft, 1.5 and older).",
            "",
        ]
        header = ["tool", "CBOM by", "CBOMs", "declares", "checked", "valid", "invalid", "no schema", "top error kinds"]
        lines += md_table(header, table, "lllrrrrrl") + [""]
    if meaningful:
        lines += [
            "**Meaningful:** share of crypto assets (components) of each CycloneDX asset type carrying the standard's "
            "own fields for that type, `standard fields / any source` (names, properties, symbols, derivations; "
            "shown where the normaliser reads such a value); the gap is what sits outside the standard. The asset "
            "types and their fields are CycloneDX's (`assetType` and the per-type property sets).",
            "",
        ]
        for at in dict.fromkeys(r["asset_type"] for r in meaningful):
            rs = [r for r in meaningful if r["asset_type"] == at]
            dims = list(dict.fromkeys(r["dimension"] for r in rs))
            by = {(r["tool"], r["dimension"]): r for r in rs}
            tools = sorted({r["tool"] for r in rs})
            table = []
            for t in tools:
                cells = []
                for d in dims:
                    r = by.get((t, d))
                    if r is None:
                        cells.append("")
                    elif r.get("any") in (None, ""):
                        cells.append(_pct(r["standard_share"]))
                    else:
                        cells.append(f"{_pct(r['standard_share'])} / {_pct(r['any_share'])}")
                table.append([t, by[(t, dims[0])]["assets"], *cells])
            lines += [f"*{at}*", ""] + md_table(["tool", "assets", *[d.replace("_", " ") for d in dims]], table) + [""]
    if trace:
        with_source = [r for r in trace if r.get("source")]
        types = list(dict.fromkeys(r.get("asset_type") or "?" for r in trace))
        lines += [
            "**Traceable:** per CycloneDX asset type: `file` = the record names a file, `file+line` = a file and a "
            "line; weak = the file exists at the commit and the line is inside it; strong = lines ±2 also hold a "
            "vocabulary token (an algorithm, family, curve, identifier or API spelling), refinement 4. Weak and strong "
            "are shares of the records with a file and a line. Library records excluded; key material is listed apart "
            "because secrets and keys in configuration files would otherwise dominate the picture."
            + (
                ""
                if with_source
                else " No snapshot files were available (run the report on the VM, or `analysis report --fetch` on "
                "the laptop to fetch each pinned commit into `~/.cache/bf-cbom/snapshots`)."
            ),
            "",
        ]
        for at in types:
            rs_type = [r for r in trace if (r.get("asset_type") or "?") == at]
            by_tool_t: dict[str, list[dict]] = defaultdict(list)
            for r in rs_type:
                by_tool_t[r["tool"]].append(r)
            table = []
            for tool in sorted(by_tool_t):
                rs = by_tool_t[tool]
                records = sum(_int(r["records"]) for r in rs)
                with_file = sum(_int(r["located_file"]) for r in rs)
                with_line = sum(_int(r["located_line"]) for r in rs)
                checked = [r for r in rs if r.get("source")]
                line_checked = sum(_int(r["located_line"]) for r in checked)
                weak = sum(_int(r["line_in_range"]) for r in checked)
                strong = sum(_int(r["strong"]) for r in checked)
                table.append(
                    [
                        tool,
                        records,
                        _pct(with_file / records) if records else "",
                        _pct(with_line / records) if records else "",
                        _pct(weak / line_checked) if line_checked else "",
                        _pct(strong / line_checked) if line_checked else "",
                    ]
                )
            lines += (
                [f"*{at}*", ""] + md_table(["tool", "records", "file", "file+line", "weak", "strong"], table) + [""]
            )
    return lines


def _matchers(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    levels = [lv for lv in LEVEL_ORDER if any(r.get("level") == lv for r in rows)] or [""]
    table = []
    for lv in levels:
        rs = [r for r in rows if (r.get("level") or "") == lv]
        common = sum(_int(r["records_common"]) for r in rs)
        pairs_a, pairs_b, both = (sum(_int(r[c]) for r in rs) for c in ("pairs_a", "pairs_b", "pairs_both"))
        union = pairs_a + pairs_b - both
        a_in = [v for r in rs if (v := _float(r["a_in_one_b"])) is not None]
        b_in = [v for r in rs if (v := _float(r["b_in_one_a"])) is not None]
        table.append(
            [
                lv,
                len(rs),
                common,
                sum(_int(r["groups_a"]) for r in rs),
                sum(_int(r["groups_b"]) for r in rs),
                _pct(both / union) if union else "",
                _pct(statistics.mean(a_in)) if a_in else "",
                _pct(statistics.mean(b_in)) if b_in else "",
            ]
        )
    a, b = rows[0]["matcher_a"], rows[0]["matcher_b"]
    lines = [
        f"**{a} vs {b}, per level.** Over the records both matchers grouped (repository level): co-membership "
        "pairs (two records in one group) shared by both, as a Jaccard; `a in one b` = share of the first "
        "matcher's groups whose members all sit in one group of the second (mean over repositories), and the "
        "reverse. RaQuN's matches hold at most one record per tool, so it splits what the exact matcher keeps "
        "together when a tool reports an item with several parameter sets.",
        "",
    ]
    header = ["level", "repos", "records", f"groups {a}", f"groups {b}", "pair Jaccard", f"{a} in one {b}",
              f"{b} in one {a}"]  # fmt: skip
    lines += md_table(header, table, "lrrrrrrr")
    return lines + [""]


MATCH_DIMS = ("key_size", "mode", "padding", "curve", "digest", "location")


def _matching(levels: list[dict], groups: list[dict], matchers: list[dict]) -> list[str]:
    """RQ3 in one section: matcher x level (items by number of voter groups, agreement per dimension), the per-language
    distribution at the scoring level, and the matchers compared per level."""
    if not levels and not groups:
        return []
    lines = ["## Matching (RQ3)", ""]
    if levels:
        lines += [
            "**Matcher x level.** Every item (an L4 identity some tool reports in a repository), by how many voter "
            "groups have a matching record at that level or below (tools sharing an engine count once). Then, per "
            "dimension, the share of the groups with at least two voters reporting a value admitted at that level in "
            "which every reporting voter carries the majority value (the number of such groups in brackets); "
            "`location`: of the groups with two located voters, the share with two voters at one call site. "
            "Parameters exist from L2 on. Pairs a tool was tuned on are left out, as in the ladder and the scores.",
            "",
        ]
        header = ["matcher", "level", "items", "k=1", "k=2", "k=3", "k≥4", "key size", "mode", "padding", "curve",
                  "digest", "location"]  # fmt: skip
        table = []
        for r in levels:
            items = _int(r["items"])
            shares = [
                f"{_n(_int(r[c]))} ({_int(r[c]) / items:.0%})" if items else "" for c in ("k1", "k2", "k3", "k4plus")
            ]
            cells = [r["matcher"], r["level"], items, *shares]
            for d in MATCH_DIMS:
                n = _int(r.get(f"{d}_groups"))
                cells.append(f"{_pct(r.get(f'{d}_agree'))} ({_n(n)})" if n else "")
            table.append(cells)
        lines += md_table(header, table, "ll" + "r" * (len(header) - 2)) + [""]
    lines += _overlap(groups)
    lines += _matchers(matchers)
    return lines


def _scores(rows: list[dict], loo: list[dict], grouping: list[dict] | None = None) -> list[str]:
    """RQ4: the ranking per dimension (analysis/score.py)."""
    if not rows:
        return []
    level = rows[0].get("level") or "?"
    lines = ["## Scores without ground truth (RQ4)", ""]
    lines += [
        f"Exact matcher at {level}. Per tool: items = what it reports per repository (by their L4 identity; for a "
        "parameter (item, value) pairs; for location (item, call site) pairs), "
        "rate = mean over repositories of the share another voter group reports too (same value / a shared call "
        "site), with a 95 % bootstrap CI over repositories; pooled = the same share over all items at once; "
        "exclusivity = share no other voter group reports at all (pooled). A rank and a CI are given only with items "
        "in at least 3 repositories and 10 items in total. Dawid-Skene (algorithm dimension): voter "
        "sensitivity/specificity estimated by EM without labels, support = mean posterior of the tool's items. "
        "Pairs a tool was tuned on are excluded; tools sharing an engine never confirm each other.",
        "",
    ]
    dims = list(dict.fromkeys(r["dimension"] for r in rows))
    for dim in dims:
        rs = sorted(
            (r for r in rows if r["dimension"] == dim),
            key=lambda r: (_int(r.get("rank")) or 999, -(_float(r.get("rate")) or 0.0), r["tool"]),
        )
        table = []
        for r in rs:
            ci = f" [{_pct(r['ci_low'])}, {_pct(r['ci_high'])}]" if r.get("ci_low") not in (None, "") else ""
            row = [
                r.get("rank") or "–",
                _tool(r),
                r["voter_group"],
                r.get("repos", ""),
                r["items"],
                f"{_pct(r['rate'])}{ci}",
                _pct(r["rate_pooled"]),
                _pct(r["exclusivity"]),
            ]
            if dim == "algorithm":
                row += [_pct(r["ds_support"]), f"{_pct(r['ds_sensitivity'])} / {_pct(r['ds_specificity'])}"]
            table.append(row)
        header = ["rank", "tool", "voter group", "repos", "items", "rate [95 % CI]", "pooled", "exclusivity"]
        if dim == "algorithm":
            header += ["DS support", "DS sens. / spec."]
        lines += [f"**{dim}**", ""] + md_table(header, table, "rllrrrrr" + "rr" * (dim == "algorithm")) + [""]
    if any(r.get("role") == "observer" for r in rows):
        lines += [OBSERVER_NOTE, ""]
    if loo:
        by_dim: dict[str, list[dict]] = defaultdict(list)
        for r in loo:
            by_dim[r["dimension"]].append(r)
        table = []
        for dim in dims:
            taus = [
                (_float(r["kendall_tau"]), r["left_out"])
                for r in by_dim.get(dim, [])
                if _float(r["kendall_tau"]) is not None
            ]
            if taus:
                lo = min(taus)
                table.append([dim, f"{lo[0]:.2f} (without {lo[1]})", f"{statistics.mean(t for t, _ in taus):.2f}"])
        if table:
            lines += [
                "**Leave one voter out:** Kendall's tau between the full ranking and the ranking without one voter "
                "group (1 = unchanged order); the lowest value names the voter whose removal changes the ranking most.",
                "",
            ]
            lines += md_table(["dimension", "lowest tau", "mean tau"], table, "lll") + [""]
    return lines + _groupings(grouping or [])


def _groupings(rows: list[dict]) -> list[str]:
    """The ranking under other voter groupings (grouping-run<k>.csv): the sensitivity check of the voter groups."""
    others = [r for r in rows if r["grouping"] != "declared"]
    if not others:
        return []
    names = list(dict.fromkeys(r["grouping"] for r in others))
    by = {(r["dimension"], r["grouping"]): r for r in rows}
    table = []
    for dim in dict.fromkeys(r["dimension"] for r in rows):
        cells = [dim]
        for g in names:
            r = by.get((dim, g))
            tau = _float(r.get("kendall_tau")) if r else None
            cells.append(f"{tau:.2f} ({r['voter_groups']} voters)" if tau is not None else "")
        table.append(cells)
    algo = by.get(("algorithm", "per-tool"))
    lines = [
        "**Other voter groupings:** Kendall's tau between the ranking under the declared voter groups and the ranking "
        "with every tool its own voter group (`per-tool`: tools sharing their detection logic then confirm each "
        "other), with the LLM workers as one voter (`llm-merged`) and with the tools of one pattern engine family "
        "as one voter (`engine-merged`: semgrep and its fork opengrep, whatever their rules); the matcher's groups "
        "are the same, so a change is the grouping's effect alone (1 = unchanged order; `grouping-run<k>.csv`).",
        "",
    ]
    lines += md_table(["dimension", *names], table, "l" + "l" * len(names)) + [""]
    if algo and algo.get("ranking"):
        lines += [f"Algorithm ranking with every tool its own voter: {algo['ranking'].replace(';', ', ')}.", ""]
    return lines


def _stability(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_tool[r["tool"]].append(r)
    table = []
    for tool in sorted(by_tool):
        rs = by_tool[tool]
        table.append(
            [
                tool,
                len(rs),
                f"{statistics.mean(_float(r['jaccard_mean']) or 0 for r in rs):.2f}",
                f"{min(_float(r['jaccard_min']) or 0 for r in rs):.2f}",
                f"{statistics.mean(_float(r['records_cv']) or 0 for r in rs):.2f}",
                sum(len((r.get("unstable") or "").split(";")) if r.get("unstable") else 0 for r in rs),
            ]
        )
    lines = ["## Run-to-run stability", ""]
    lines += [
        "Per tool over the repositories it ran on more than once: Jaccard similarity of the item sets between "
        "runs (mean of the per-repository means, and the lowest pair), the coefficient of variation of the record "
        "count, and how many items appeared in some runs but not in most (`stability.csv`). Scores use the "
        "consensus set (present in more than half of the runs) where runs were repeated.",
        "",
    ]
    lines += md_table(["tool", "repos", "Jaccard mean", "Jaccard min", "records CV", "unstable items"], table)
    return lines + [""]


MUTANT_PATCHES = 12  # patches shown in the summary (all are in mutants.csv and mutants.json)


def _mutation(mutants: list[dict], rows: list[dict], rates: list[dict]) -> list[str]:
    if not mutants:
        return []
    styles = list(dict.fromkeys(m["style"] for m in mutants))
    by_lang: dict[str, int] = defaultdict(int)
    for m in mutants:
        by_lang[m["language"]] += 1
    per_lang = ", ".join(
        f"{lang} {_n(n)}" for lang, n in sorted(by_lang.items(), key=lambda kv: _lang_key(kv[0] or ""))
    )
    lines = ["## E4: insertion mutants", ""]
    lines += [
        f"{_n(len(mutants))} mutants in {_n(len({m['repo'] for m in mutants}))} repositories ({per_lang}); "
        "each inserts one catalog snippet (mutation/catalog/) in one style into a first-party method with a caller "
        "(`mutants.csv`). A tool detects it when a new record of the expected unit appears at the line naming the "
        "algorithm ±2 (site) or anywhere on the inserted lines (span), in most of the mutant's runs; at the "
        "repository level when the unit is new to the repository (`masked` when the original already has it). "
        "Rate = mean of the per-repository rates (`mutation-rates.csv`), detected/judged mutants in brackets; "
        "per mutant and tool: `mutation.csv`.",
        "",
    ]
    cell = {(r["tool"], r["language"], r["style"], r["dimension"]): r for r in rates}
    role = {r["tool"]: r.get("role") for r in rows}
    pairs = sorted({(r["tool"], r["language"]) for r in rows}, key=lambda tl: (_lang_key(tl[1] or ""), tl[0]))
    table = []
    for tool, lang in pairs:

        def show(style: str, dim: str, tool: str = tool, lang: str = lang) -> str:
            r = cell.get((tool, lang, style, dim))
            return f"{_pct(r['rate'])} ({_n(r['detected'])}/{_n(r['mutants'])})" if r else ""

        ok = [r for r in rows if (r["tool"], r["language"]) == (tool, lang) and r.get("status") == "ok"]
        masked = sum(_true(r.get("masked")) for r in ok)
        table.append(
            [lang, _tool({"tool": tool, "role": role.get(tool)})]
            + [show(st, "found_site") for st in styles]
            + [show("all", "found_span"), show("all", "found_repo"), masked, len(ok)]
        )
    header = ["language", "tool"] + [f"site, {st}" for st in styles]
    header += ["span, all", "repository, all", "masked", "judged"]
    lines += md_table(header, table, "ll" + "r" * (len(header) - 2))
    if any(r.get("role") == "observer" for r in rows):
        lines += ["", OBSERVER_NOTE]
    failed = [r for r in rows if r.get("status") != "ok"]
    if failed:
        lines += ["", f"Not judged ({_n(len(failed))} mutant × tool): the run on the mutant or on the original is "
                  "missing or not ok (`status` in `mutation.csv`)."]  # fmt: skip
    lines += ["", f"### Mutant patches (first {min(MUTANT_PATCHES, len(mutants))} of {_n(len(mutants))})", ""]
    for m in mutants[:MUTANT_PATCHES]:
        lines += [f"`{m['variant']}` in `{m['repo']}`, `{m['file']}:{m['call_line']}` ({m['host']}):", ""]
        lines += ["```diff", m.get("patch") or "", "```", ""]
    return lines


def _voters(info: dict) -> list[str]:
    """The voter groups the agreement numbers rest on (tables-run<k>.json `voters`, after --merge-llm)."""
    voters = info.get("voters") or {}
    if not voters:
        return []
    groups: dict[str, list[str]] = defaultdict(list)
    for tool, v in voters.items():
        if v.get("role") != "observer":
            groups[v.get("voter_group") or tool].append(tool)
    shared = {g: ts for g, ts in sorted(groups.items()) if len(ts) > 1}
    single = sorted(ts[0] for ts in groups.values() if len(ts) == 1)
    observers = sorted(t for t, v in voters.items() if v.get("role") == "observer")
    lines = ["## Voter groups", ""]
    lines += [
        "A tool's finding counts as confirmed only when a tool of *another* voter group reports it too (the ladder, "
        "the matchers' k and the scores). Tools that share their detection logic (the same engine packaged twice, "
        "or the same rules) form one group, so that the same logic never confirms itself; every other tool is a "
        "voter group of its own. Tools that share only a pattern engine but run independently written rules count "
        "apart: their rule sets were checked and share no rule. The groups are BF-CBOM's decision, set in "
        '`common/tools.yaml` (`voter_group`) with the evidence in `analysis/README.md` ("Voter groups"); the '
        "scores also rank the tools under other groupings.",
        "",
    ]
    if shared:
        table = [
            [g, ", ".join(sorted(ts)), ", ".join(sorted({voters[t].get("engine") or "?" for t in ts}))]
            for g, ts in shared.items()
        ]
        lines += md_table(["voter group", "tools", "engine"], table, "lll") + [""]
    if single:
        lines += [f"One voter group each: {', '.join(single)}.", ""]
    if observers:
        lines += [
            f"Observers (°), with a group of their own in the tables below that confirms nobody (`--observers-vote` "
            f"makes them voters): {', '.join(observers)}.",
            "",
        ]
    if (info.get("settings") or {}).get("merge_llm"):
        lines += ["`--merge-llm`: the LLM workers count as one voter (`merge_group: llm`), the sensitivity check.", ""]
    return lines


def _ladder(all_rows: list[dict]) -> list[str]:
    rows = [r for r in all_rows if r.get("dimension", "algorithm") == "algorithm"]
    if not rows:
        return []
    levels = list(dict.fromkeys(r["level"] for r in rows))
    by = {(r["tool"], r["level"]): r for r in rows}
    tools = [t for t in dict.fromkeys(r["tool"] for r in rows) if t != "all"] + ["all"]
    table = []
    for t in tools:
        first = by.get((t, levels[0]), {})
        table.append(
            [_tool({"tool": t, "role": first.get("role")}), first.get("voter_group", "")]
            + [_pct(by.get((t, lv), {}).get("share")) for lv in levels]
            + [first.get("items", "")]
        )
    lines = ["## Agreement ladder", ""]
    lines += [
        "Share of a tool's items (per repository, location ignored; an item = an L4 identity, the same at every "
        "level, so every level's share is a fraction of the same items) for which a tool of another voter group "
        "(above) has a "
        "matching record at that level or below: at L0 a verbatim identical name, L1 case and separators ignored, "
        "L2 the standard vocabulary, L3 composite names parsed, L4 everything the tool specs know. Matches are never "
        "lost at a higher level, so the share can only grow and each step's gain is one normalisation step. The L0 "
        "to L4 gap is the agreement normalisation creates (refinement 14). The tool specs hold the tools' own "
        "spellings and labels, never our judgements. Pairs the tool was tuned on are excluded.",
        "",
    ]
    lines += md_table(["tool", "voter group", *levels, "items"], table, "ll" + "r" * (len(levels) + 1))
    if any(r.get("role") == "observer" for r in rows):
        lines += ["", OBSERVER_NOTE]
    alls = {r["level"]: r for r in rows if r["tool"] == "all"}
    if alls and any(r.get("multi_unit_clusters") not in (None, "") for r in alls.values()):
        strict = ", ".join(
            f"{lv} {_pct(_int(r.get('confirmed_strict')) / _int(r['items']))}" if _int(r.get("items")) else lv
            for lv, r in alls.items()
        )
        multi = ", ".join(f"{lv} {_n(_int(r.get('multi_unit_clusters')))}" for lv, r in alls.items())
        lines += [
            "",
            f"Confirmed through cluster members alone, all voters (no generic or implied attachment): {strict}. "
            f"Clusters whose members span several L4 identities (what the level declares the same): {multi}. "
            "Records without an L4 identity (`resolution` undefined, api, non-crypto, ambiguous) are not items.",
        ]
    params = [r for r in all_rows if r.get("dimension", "algorithm") != "algorithm" and r["tool"] == "all"]
    if params:
        dims = list(dict.fromkeys(r["dimension"] for r in params))
        plevels = list(dict.fromkeys(r["level"] for r in params))
        by_dim = {(r["dimension"], r["level"]): r for r in params}
        ptable = []
        for d in dims:
            cells = [_pct(by_dim[(d, lv)]["share"]) if (d, lv) in by_dim else "n/a" for lv in plevels]
            ptable.append([d, *cells, by_dim.get((d, plevels[-1]), {}).get("items", "")])
        lines += [
            "",
            "**Parameters**, pooled over the voters: share of (item, value) pairs, values from any source, for "
            "which another voter group reports the same value inside the item's group at that level; a value "
            "is admitted at L2 from a standard field, at L3 also parsed from the name, at L4 from any source "
            "(per-tool rows in `levels-run<k>.csv`). n/a: the standard has no field for the value (CycloneDX carries "
            "no digest of a composite such as HMAC-SHA256), so the row starts where a name can be parsed.",
            "",
        ]
        lines += md_table(["parameter", *plevels, f"pairs {plevels[-1]}"], ptable)
    return lines + [""]


def _overlap(groups: list[dict]) -> list[str]:
    repo = [g for g in groups if g.get("scope") == "repo"]
    if not repo:
        return []
    level = repo[0].get("level") or "?"
    buckets = ["1", "2", "3", "4+"]

    def bucket(k: int) -> str:
        return buckets[min(k, 4) - 1]

    def line(label: str, rows: list[dict]) -> list:
        best: dict[tuple, int] = {}  # (snapshot, variant, unit) -> voter groups of its largest group
        for g in rows:
            if not _int(g["voter_groups"]):
                continue  # only observers report it: no voter group, not counted
            for unit in (g.get("units") or g["key"]).split(";"):  # the L4 identities inside the group
                key = (g["snapshot"], g.get("variant"), unit)
                best[key] = max(best.get(key, 0), _int(g["voter_groups"]))
        c = Counter(bucket(v) for v in best.values())
        n = len(best)
        sites = sum(_int(g.get("n_sites")) for g in rows)
        coloc = sum(_int(g.get("colocated_voters")) >= 2 for g in rows)
        shares = [f"{_n(c.get(b, 0))} ({c.get(b, 0) / n:.0%})" if n else "" for b in buckets]
        return [label, n, *shares, sites, coloc]

    table = [line("all", repo)]
    for lg in sorted({(g.get("language") or "?").lower() for g in repo}, key=_lang_key):
        table.append(line(lg, [g for g in repo if (g.get("language") or "?").lower() == lg]))
    lines = [
        f"**Per language at the scoring level ({level}), exact matcher:** items by how many voter groups report "
        "them; `sites` = call-site groups inside those (same file, lines within the window), `co-located` = "
        "items where at least two voter groups share a call site.",
        "",
    ]
    lines += md_table(["language", "items", *[f"k={b}" for b in buckets], "sites", "co-located"], table)
    return lines + [""]


def _llm(rows: list[dict], scores: list[dict] | None = None) -> list[str]:
    usage: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("requests"):
            usage[r["tool"]].append(r)
    if not usage:
        return []
    found = {r["tool"]: r for r in scores or [] if r.get("dimension") == "algorithm"}
    table, cost_table = [], []
    versions = set()
    for tool in sorted(usage):
        rs = usage[tool]
        inputs = [_int(r.get("input_tokens")) for r in rs]
        outputs = [_int(r.get("output_tokens")) for r in rs]
        models = sorted({m for r in rs for m in (r.get("models_answered") or "").split(";") if m})
        chunks_total = sum(_int(r.get("chunks_total")) for r in rs)
        skipped = sum(_int(r.get("chunks_skipped")) for r in rs)
        capped = sum(1 for r in rs if _int(r.get("chunks_skipped")) > 0)
        table.append(
            [
                tool,
                len(rs),
                sum(_int(r.get("requests")) for r in rs),
                sum(_int(r.get("requests_failed")) for r in rs) or "",
                chunks_total,
                f"{_n(skipped)} ({_n(capped)} runs)" if skipped else "",
                sum(inputs),
                sum(_int(r.get("cached_input_tokens")) for r in rs),
                sum(outputs),
                sum(_int(r.get("reasoning_tokens")) for r in rs) or "",
                f"{statistics.median(inputs):.0f}",
                f"{statistics.median(outputs):.0f}",
                "; ".join(models),
            ]
        )
        costs = [c for r in rs if (c := _float(r.get("cost_usd"))) is not None]
        if costs:
            versions |= {r.get("price_table_version") for r in rs if r.get("price_table_version")}
            total = sum(costs)
            items = _int(found.get(tool, {}).get("items"))
            confirmed = _int(found.get(tool, {}).get("confirmed"))
            cost_table.append(
                [
                    tool,
                    len(costs),
                    f"{total:.2f}",
                    f"{statistics.median(costs):.3f}",
                    f"{total / items:.4f}" if items else "",
                    f"{total / confirmed:.4f}" if confirmed else "",
                    "; ".join(sorted({r.get("priced_model") or "" for r in rs if r.get("priced_model")})),
                ]
            )
    lines = [
        "## LLM usage",
        "",
        "What the providers reported, summed over all runs with requests (failed runs included). Chunks: what the "
        "selected files render to; skipped = chunks over LLM_MAX_CHUNKS that were never sent (the model saw only part "
        "of those repos). Cached: input tokens served from the provider's prompt cache (priced lower).",
        "",
    ]
    header = [
        "tool", "runs", "requests", "failed", "chunks", "skipped", "input tokens", "cached", "output tokens",
        "reasoning", "median input", "median output", "models answered",
    ]  # fmt: skip
    lines += md_table(header, table, "l" + "r" * (len(header) - 2) + "l")
    if cost_table:
        lines += [
            "",
            f"**Costs** (USD, price table {', '.join(sorted(versions))} in `analysis/data/llm_prices.yaml`, computed "
            "from the stored token counts; per item = per item the tool reports, per confirmed item = per item "
            "another voter group reports too, both from the scores' algorithm dimension).",
            "",
        ]
        header = [
            "tool",
            "priced runs",
            "total",
            "median per repo",
            "per item",
            "per confirmed item",
            "priced as",
        ]
        lines += md_table(header, cost_table, "lrrrrrl")
    return lines + [""]


def _files(info: dict, run: int) -> list[str]:
    counts = info.get("rows") or {}
    lines = ["## Files", ""]
    for pattern, meaning in FILES.items():
        name = pattern.format(k=run)
        if name in counts:
            lines.append(f"- `{name}` ({_n(counts[name])} rows): {meaning}")
    lines += [
        "- `normalize.json`: coverage per tool in full (sources, conflicts, top unmapped values)",
        "- `../_run-log.json`: one entry per runner session (times, host RAM and CPUs, budget, waves, peak RAM)",
        "- `<snapshot>/<variant>/<tool>/run<k>.norm.json`: the normalised CBOM next to each stored one",
    ]
    settings = info.get("settings") or {}
    if settings:
        lines += ["", "Settings: " + ", ".join(f"{k}={v}" for k, v in settings.items()) + "."]
    adapters = info.get("adapters") or {}
    if adapters:
        lines += ["", "Adapter specs: " + "; ".join(f"`{v}`" for _, v in sorted(adapters.items())) + "."]
    return lines + [""]


# ----- entry points -----


def render(analysis_dir: Path, *, run: int = 1) -> str:
    analysis_dir = Path(analysis_dir)
    spec_file = spec_path(analysis_dir.parent)
    spec = yaml.safe_load(spec_file.read_text(encoding="utf-8")) if spec_file.exists() else {}
    info_path = analysis_dir / f"tables-run{run}.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}

    def rows(name: str) -> list[dict]:
        return read_csv(analysis_dir / name.format(k=run))

    lines = _header(spec or {}, info, run)
    lines += _experiment(rows("runs.csv"), load_run_log(analysis_dir.parent))
    lines += _runs(rows("runs.csv"))
    lines += _reported(rows("runs.csv"), run)
    lines += _negatives(rows("runs.csv"), run)
    lines += _micro(rows("micro-run{k}.csv"), rows("micro-items-run{k}.csv"))
    lines += _normalisation(rows("units-run{k}.csv"), rows("coverage-run{k}.csv"), rows("conformance-run{k}.csv"))
    lines += _rq1(rows("schema-run{k}.csv"), rows("meaningful-run{k}.csv"), rows("traceability-run{k}.csv"))
    lines += _voters(info)
    lines += _ladder(rows("levels-run{k}.csv"))
    lines += _matching(rows("matchlevels-run{k}.csv"), rows("groups-exact-run{k}.csv"), rows("matchers-run{k}.csv"))
    lines += _scores(rows("score-run{k}.csv"), rows("loo-run{k}.csv"), rows("grouping-run{k}.csv"))
    lines += _stability(rows("stability.csv"))
    lines += _mutation(rows("mutants.csv"), rows("mutation.csv"), rows("mutation-rates.csv"))
    lines += _llm(rows("runs.csv"), rows("score-run{k}.csv"))
    lines += _files(info, run)
    return "\n".join(lines).rstrip() + "\n"


def write(analysis_dir: Path, *, run: int = 1) -> Path:
    path = Path(analysis_dir) / SUMMARY_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(render(analysis_dir, run=run), encoding="utf-8")
    tmp.replace(path)
    return path


if __name__ == "__main__":  # rebuild from an exported folder: python -m analysis.summary <exp>/_ANALYSIS [run]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("_ANALYSIS")
    print(write(target, run=int(sys.argv[2]) if len(sys.argv) > 2 else 1))
