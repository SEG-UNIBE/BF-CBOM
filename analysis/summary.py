"""_SUMMARY.md: Markdown tables of one experiment for a first look, e.g. in an exported zip (CLAUDE.md, Phase 3).

Rendered only from what `analysis report` writes into `<exp>/_ANALYSIS/` (the CSV tables and `tables-run<k>.json`)
plus the stored spec and run log (`_experiment.yaml`, `_run-log.json`), so it can be rebuilt from an export
(`python -m analysis.summary <exp>/_ANALYSIS`) and never shows anything those files don't hold. The CSVs are the data
for plots; this file is for reading.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import yaml

from common.experiment import load_run_log, spec_path

SUMMARY_FILE = "_SUMMARY.md"
LANGUAGE_ORDER = ["java", "python", "go", "javascript", "typescript", "c", "cpp"]
COVERAGE_SHOWN = ["family", "key_size", "mode", "padding", "curve", "digest", "location"]
FILES = {
    "runs.csv": "one row per stored run, all repetitions (status, duration, tool version, counts, LLM tokens)",
    "records-run{k}.csv": "one row per normalised record: every dimension with its raw value and source",
    "micro-run{k}.csv": "one row per (micro project, tool): status, verdict, recall at call sites and repo level",
    "micro-assets-run{k}.csv": "one row per (micro project, tool, asset): found at call site (strict, lenient), repo",
    "algorithms-run{k}.csv": "one row per (repo, level L0-L4, tool, algorithm): what each tool reports, no locations",
    "levels-run{k}.csv": "share of each tool's algorithms another voter group confirms, per level L0-L4",
    "coverage.csv": "fill rate of the algorithm records per tool and dimension (reported vs derived)",
    "conformance.csv": "spec issues per tool and kind (records: field, enum, type, deprecated; documents)",
    "vocab.csv": "what every raw value mapped to, per tool and source (the reviewable mapping table)",
    "sites-run{k}.csv": "legacy call-site extraction (to be retired)",
    "groups-callsite-run{k}.csv": "legacy call-site matcher: one row per cluster",
    "members-callsite-run{k}.csv": "legacy call-site matcher: one row per (cluster, site)",
}


# ----- helpers -----


def md_table(header: list[str], rows: list[list], align: str | None = None) -> list[str]:
    """GitHub Markdown table; `align` has one letter per column (l or r), default: first left, rest right."""
    align = align or "l" + "r" * (len(header) - 1)

    def cell(v) -> str:
        return "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")

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
        f"Generated {when} by `analysis report`: {len(repos)} repos, {len(tools)} tools, "
        f"{spec.get('repetitions', 1)} repetition(s). Vocabulary {vocab.get('algorithms', '?')}, "
        f"CycloneDX registry {vocab.get('registry', '?')}."
    )
    lines += ["The data behind every table are the CSV files listed at the end (`;` joins lists inside a cell).", ""]
    return lines


def _experiment(rows: list[dict], sessions: list[dict]) -> list[str]:
    """When it ran, for how long, on how much RAM, and the peaks (run log of the runner plus the runs' meta)."""
    ran = [r for r in rows if r.get("status") != "unsupported"]
    ends = [t for r in ran if (t := _ts(r.get("received_at")))]
    starts = [t.timestamp() - (_float(r.get("duration_sec")) or 0) for r in ran if (t := _ts(r.get("received_at")))]
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
    elif ends and starts:
        first = datetime.fromtimestamp(min(starts), UTC)
        table.append(["ran", f"about {_when(first)} to {_when(max(ends))} (from the stored runs; no run log)"])
        table.append(["duration", f"about {_span((max(ends) - first).total_seconds())}"])
    tool_time = sum(_float(r.get("duration_sec")) or 0 for r in ran)
    if ran:
        table.append(["tool time", f"{_span(tool_time)} summed over {len(ran)} runs (excluding unsupported)"])
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
    if not table:
        return []
    lines += md_table(["", ""], table, "ll")
    if not sessions:
        lines += ["", "No `_run-log.json` (run before 2026-10-18): host RAM, budget and peak RAM were not recorded."]
    elif not per_job:
        lines += ["", "The runs carry no memory measurements (worker images built before 2026-10-18)."]
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
        lines += ["", f"Runs without a result ({len(failed)}):", ""]
        for r in failed[:25]:
            err = f": {r['error']}" if r.get("error") else ""
            lines.append(f"- `{r['tool']}` on `{r['repo']}` ({r['variant']}, run {r['run']}): {r['status']}{err}")
        if len(failed) > 25:
            lines.append(f"- ... and {len(failed) - 25} more (runs.csv)")
    return lines + [""]


def _micro(rows: list[dict], assets: list[dict]) -> list[str]:
    if not rows:
        return []
    langs = sorted({r["language"] for r in rows}, key=_lang_key)
    cells = {(r["tool"], r["language"]): r for r in rows}
    n_assets = max((_int(r.get("n_assets")) for r in rows), default=11)
    bar = (n_assets + 1) // 2

    def rank(tool: str) -> tuple:
        rs = [cells[(tool, lg)] for lg in langs if (tool, lg) in cells]
        return (-sum(r["verdict"] == "pass" for r in rs), -sum(_int(r.get("lenient")) for r in rs), tool)

    tools = sorted({r["tool"] for r in rows}, key=rank)

    def verdict_cell(r: dict | None) -> str:
        if r is None or r["verdict"] == "missing":
            return "not run"
        if r["verdict"] in ("pass", "fail"):
            return f"{r['verdict']} {r['lenient']}"
        return "–" if r["verdict"] == "unsupported" else r["verdict"]

    def recall_cell(r: dict | None) -> str:
        if r is not None and r["verdict"] in ("pass", "fail"):
            return f"{r['strict']} / {r['lenient']} · {r['repo']}"
        return "–" if r is not None and r["verdict"] == "unsupported" else ""

    lines = ["## Micro projects", ""]
    lines += [
        f"**Inclusion check** (`corpus/micro`, {n_assets} assets per project): lenient call-site recall, "
        f"bar {bar}/{n_assets}. `–` = outside the tool's languages, `error` = the run failed or timed out.",
        "",
    ]
    lines += md_table(["tool", *langs], [[t] + [verdict_cell(cells.get((t, lg))) for lg in langs] for t in tools])
    lines += [
        "",
        f"**Recall at two levels** (out of {n_assets}): `strict / lenient · repo`. Call sites: a report at one of "
        "the asset's ground-truth lines (a window counts at every line it spans) with a compatible family (strict; "
        "a family the asset's name contains counts, SHA-2 for HMAC-SHA512) or an unknown one (lenient). Repo: the "
        "asset's own family (HMAC for HMAC-SHA512) reported anywhere in the project, location ignored; assets "
        "sharing a family (two RSA, two EC, two AES) are found together. Repo can be lower than call sites: it "
        "needs a known family, and the asset's own one.",
        "",
    ]
    lines += md_table(["tool", *langs], [[t] + [recall_cell(cells.get((t, lg))) for lg in langs] for t in tools])
    if assets:
        found: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
        style: dict[str, str] = {}
        for a in assets:
            cnt = found[(a["asset"], a["language"])]
            cnt[0] += a["callsite_lenient"] == "true"
            cnt[1] += 1
            style[a["asset"]] = a.get("insertion_style") or ""
        asset_ids = list(dict.fromkeys(a["asset"] for a in assets))
        table = []
        for aid in asset_ids:
            row = [aid + (f" ({style[aid]})" if style.get(aid) not in ("", "direct") else "")]
            for lg in langs:
                f, n = found.get((aid, lg), (0, 0))
                row.append(f"{f}/{n}" if n else "")
            table.append(row)
        lines += ["", "**Who finds each asset** (lenient call site): tools that found it / tools scored.", ""]
        lines += md_table(["asset", *langs], table)
    return lines + [""]


def _coverage(cov: list[dict], conf: list[dict]) -> list[str]:
    if not cov:
        return []
    by_tool: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in cov:
        by_tool[r["tool"]][r["dimension"]] = r
    issues: dict[str, list[str]] = defaultdict(list)
    for r in conf:
        unit = " CBOMs" if r["scope"] == "document" else ""
        issues[r["tool"]].append(f"{r['kind']} {r['count']}{unit}")
    table = []
    for tool in sorted(by_tool):
        dims = by_tool[tool]
        first = next(iter(dims.values()))
        table.append(
            [tool, first["cboms"], first["algorithm_records"]]
            + [_pct(dims.get(d, {}).get("reported_share")) for d in COVERAGE_SHOWN]
            + [", ".join(issues.get(tool, [])) or "none"]
        )
    lines = ["## Normalisation", ""]
    lines += [
        "Share of the algorithm records (all repetitions) with a value the tool reported, per dimension, and the "
        "spec issues of the values read from standard fields (RQ1, a first check; full schema validation to do).",
        "",
    ]
    header = ["tool", "CBOMs", "algorithm records", *[d.replace("_", " ") for d in COVERAGE_SHOWN], "spec issues"]
    lines += md_table(header, table, "lrr" + "r" * len(COVERAGE_SHOWN) + "l")
    return lines + [""]


def _ladder(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    levels = list(dict.fromkeys(r["level"] for r in rows))
    by = {(r["tool"], r["level"]): r for r in rows}
    tools = [t for t in dict.fromkeys(r["tool"] for r in rows) if t != "all"] + ["all"]
    table = []
    for t in tools:
        first = by.get((t, levels[0]), {})
        last = by.get((t, levels[-1]), {})
        table.append(
            [t, first.get("voter_group", "")]
            + [_pct(by.get((t, lv), {}).get("share")) for lv in levels]
            + [first.get("algorithms", ""), last.get("algorithms", "")]
        )
    lines = ["## Agreement ladder", ""]
    lines += [
        "Share of a tool's algorithms (per repository, location ignored) that a tool of another voter group also "
        "reports, from raw names (L0) to full normalisation (L4). The L0 to L4 gap is the agreement normalisation "
        "creates (refinement 14).",
        "",
    ]
    lines += md_table(
        ["tool", "voter group", *levels, f"algorithms {levels[0]}", f"algorithms {levels[-1]}"],
        table,
        "ll" + "r" * (len(levels) + 2),
    )
    return lines + [""]


def _overlap(rows: list[dict], level: str = "L3") -> list[str]:
    rows = [r for r in rows if r["level"] == level]
    if not rows:
        return []
    groups: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    lang_of: dict[tuple[str, str, str], str] = {}
    for r in rows:
        key = (r["snapshot"], r["variant"], r["algorithm"])
        groups[key].add(r["voter_group"])
        lang_of[key] = (r.get("language") or "?").lower()
    buckets = ["1", "2", "3", "4+"]

    def bucket(k: int) -> str:
        return buckets[min(k, 4) - 1]

    def line(label: str, keys: list) -> list:
        c = Counter(bucket(len(groups[k])) for k in keys)
        n = len(keys)
        return [label, n] + [f"{c.get(b, 0)} ({c.get(b, 0) / n:.0%})" if n else "" for b in buckets]

    keys = list(groups)
    table = [line("all", keys)]
    for lg in sorted({lang_of[k] for k in keys}, key=_lang_key):
        table.append(line(lg, [k for k in keys if lang_of[k] == lg]))
    lines = ["## Tools per algorithm", ""]
    lines += [
        f"Per repository, every algorithm some tool reports ({level}: composite names parsed), by how many voter "
        "groups report it (tools sharing an engine count once, `voter_group` in common/tools.yaml).",
        "",
    ]
    lines += md_table(["language", "algorithms", *[f"k={b}" for b in buckets]], table)
    return lines + [""]


def _llm(rows: list[dict]) -> list[str]:
    usage: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["status"] == "ok" and (r.get("prompt_tokens") or r.get("requests")):
            usage[r["tool"]].append(r)
    if not usage:
        return []
    table = []
    for tool in sorted(usage):
        rs = usage[tool]
        prompt = [_int(r.get("prompt_tokens")) for r in rs]
        completion = [_int(r.get("completion_tokens")) for r in rs]
        table.append(
            [
                tool,
                len(rs),
                sum(_int(r.get("requests")) for r in rs),
                sum(prompt),
                sum(completion),
                f"{statistics.median(prompt):.0f}",
                f"{statistics.median(completion):.0f}",
            ]
        )
    lines = ["## LLM usage", "", "Token counts the providers reported (ok runs, all repetitions); costs not yet.", ""]
    lines += md_table(
        ["tool", "runs", "requests", "prompt tokens", "completion tokens", "median prompt", "median completion"], table
    )
    return lines + [""]


def _files(info: dict, run: int) -> list[str]:
    counts = info.get("rows") or {}
    lines = ["## Files", ""]
    for pattern, meaning in FILES.items():
        name = pattern.format(k=run)
        if name in counts:
            lines.append(f"- `{name}` ({counts[name]} rows): {meaning}")
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

    algorithms = rows("algorithms-run{k}.csv")
    lines = _header(spec or {}, info, run)
    lines += _experiment(rows("runs.csv"), load_run_log(analysis_dir.parent))
    lines += _runs(rows("runs.csv"))
    lines += _micro(rows("micro-run{k}.csv"), rows("micro-assets-run{k}.csv"))
    lines += _coverage(rows("coverage.csv"), rows("conformance.csv"))
    lines += _ladder(rows("levels-run{k}.csv"))
    lines += _overlap(algorithms)
    lines += _llm(rows("runs.csv"))
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
