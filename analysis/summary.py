"""_SUMMARY.md: Markdown tables of one experiment for a first look, e.g. in an exported zip (CLAUDE.md, Phase 3).

Rendered only from what `analysis report` writes into `<exp>/_ANALYSIS/` (the CSV tables and `tables-run<i>.json`)
plus the stored spec and run log (`_experiment.yaml`, `_run-log.json`), so it can be rebuilt from an export
(`python -m analysis.summary <exp>/_ANALYSIS`) and never shows anything those files don't hold. The CSVs are the data
for plots; this file is for reading.

One layout everywhere (2026-10-31, CLAUDE.md "Summary layout"): a subsection is its heading,
one to three sentences, the table, its visible checks (`_check`: ✅ ⛔ ⚠️), then collapsibles in a fixed order:
`📋 Columns` (`_columns`, one bullet per column), `💡 Example` (`_example`), `📖 <topic>` (`_background`) and
`🗂️ <list> (N)` (`_run_list`). The Markdown has one sentence per line and no semicolons in its prose;
`style_issues` checks both.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from common.corpus import CorpusParams, bin_text
from common.experiment import load_run_log, spec_path

from .groundtruth import BAR_FAILED, BAR_PASSED
from .rqs import BAR_EXAM, BAR_EXAM_NOTE, BAR_EXAM_TABLES, RQ1, RQ2, RQ3, RQ4, RQ5, RQS, STABILITY, STABILITY_NOTE
from .tables import RUNTIME_BOOTSTRAP, RUNTIME_MIN_REPOS

SUMMARY_FILE = "_SUMMARY.md"
LANGUAGE_ORDER = ["java", "python", "go", "javascript", "typescript", "c", "cpp"]
LEVEL_ORDER = ("L0", "L1", "L2", "L3", "L4")
RESOLUTION_SHOWN = ["mapped", "ambiguous", "api", "non-crypto", "undefined"]  # analysis/normalize/engine.RESOLUTIONS
COVERAGE_SHOWN = ["key_size", "mode", "padding", "curve", "digest"]
FILES = {
    "tools.csv": "one row per tool that ran: vendor, project page, how it is shipped, what it is built on, the "
    "version the paper reports and the page that shows it (common/tools.yaml)",
    "runs.csv": "one row per stored run, all repetitions (status, duration, tool version, raw CBOM counts by asset "
    "type, LLM tokens and costs)",
    "runtime.csv": "per tool and language (and all): repositories, median seconds with a bootstrap CI over "
    "repositories, p90, max, spread across repositories and between reruns, timeouts, hours, seconds per 1k "
    "source files and Spearman against size, peak memory and CPU cores with the same statistics, and the memory "
    "limit, the runs at it, the OOM kills and the runs that may have failed for memory",
    "records-run{i}.csv": "one row per normalised record: every dimension with its raw value and source",
    "micro-run{i}.csv": "one row per (micro project, tool): status, verdict, recall at call sites and repo level",
    "micro-items-run{i}.csv": "one row per (micro project, tool, expected item): found at call site (strict, "
    "lenient), repo",
    "items-run{i}.csv": "one row per (repo, tool, item) with whether its family is the CycloneDX registry's and the "
    "voter groups matching it at each level L0-L4, without locations",
    "registry-run{i}.csv": "the delta to the CycloneDX registry: one row per family added to it (gap kind, "
    "primitive, items resting on it, repos, tools, reason), per unit no vocabulary names, and per table of the "
    "identifier layer with its size",
    "levels-run{i}.csv": "share of each tool's items another voter group confirms, per level L0-L4",
    "units-run{i}.csv": "per tool: algorithm assets, records, items and how they relate (records per asset, assets "
    "per item), the kinds of place and the records outside code files of the repository's language",
    "units-repo-run{i}.csv": "per (tool, repo): algorithm assets, records, items",
    "coverage-run{i}.csv": "per tool and dimension: algorithm assets with a value (reported vs derived), and what "
    "their names are (resolution)",
    "conformance-run{i}.csv": "spec issues per tool and kind (assets: field, enum, type, deprecated, and documents)",
    "sources-run{i}.csv": "per tool, dimension and kind of source (standard field, property, name, occurrence, API "
    "context, ref, dependency, derived): algorithm assets carrying the value from there, and their share",
    "schema-run{i}.csv": "one row per CBOM: declared format and version, schema validity, error count and kinds",
    "meaningful-run{i}.csv": "per tool, asset type and dimension, assets with a value from standard fields / any",
    "traceability-run{i}.csv": "per (snapshot, tool, asset type), records with a file, with a file and line, "
    "existing at the commit, in range, strong",
    "vocab.csv": "what every raw value mapped to, per tool and source (the reviewable mapping table)",
    "groups-exact-run{i}.csv": "exact matcher: one row per group (repo scope per item, site scope per call site, "
    "and `member_voter_groups` = 0 for only attached records, no item)",
    "members-exact-run{i}.csv": "exact matcher: one row per (group, record), `matched` = joined through a lower "
    "level, `implied` with --closure",
    "matchlevels-run{i}.csv": "per matcher and level, items by number of voter groups, the agreement per "
    "dimension inside the groups and the matcher's seconds",
    "agreement-exact-run{i}.csv": "exact matcher: one row per (group, dimension): voters reporting, agreeing, majority",
    "groups-raqun-run{i}.csv": "RaQuN: one row per group, with its attribute-set similarity",
    **{
        f"raqun-{lv}-run{{i}}.csv": f"RaQuN at {lv}, to read: one row per group, per tool the names it reported, the "
        "attributes matched and the records behind them"
        for lv in LEVEL_ORDER
    },
    "members-raqun-run{i}.csv": "RaQuN: one row per (group, record)",
    "agreement-raqun-run{i}.csv": "RaQuN: one row per (group, dimension)",
    "matchers-run{i}.csv": "per repository and level, exact vs RaQuN: common records, groups, co-membership pair "
    "Jaccard, each matcher's seconds",
    "score-run{i}.csv": "per tool, dimension and level: items, confirmation (CI), exclusivity, coverage, rank, "
    "Dawid-Skene",
    "loo-run{i}.csv": "the order without each voter group and its Kendall tau against the full order",
    "grouping-run{i}.csv": "the order with every tool its own voter group and with the LLM workers merged, and its "
    "Kendall tau against the declared voter groups",
    "stability.csv": "per (tool, snapshot) with repeated runs: Jaccard across runs, record-count CV, flux (the share "
    "of items not in every run), consensus set",
    "mutants.csv": "one row per mutant (mutants.json): the planted call or decoy, its file, lines, host and patch",
    "mutation.csv": "per (mutant, tool): found at the site / span / file / repo, by group, parameters, "
    "`false_positive` on a decoy, extra and lost units and sites",
    "mutation-rates.csv": "per tool, language, style and dimension: detection rate (mean of per-repository rates, CI)",
    "mutation-scores.csv": "per tool, language and style: recall on the planted calls (site, group), precision over "
    "calls and decoys, F1, CIs over repositories, the changes off the edit (mutants with any, share, medians) and the "
    "noise floor from --baseline",
    "bridge-run{i}.csv": "the bridge to RQ4: per language, each tool's confirmation rate at L4 against its F1, "
    "Spearman over the cells and Kendall tau between the rankings",
}


# ----- helpers -----

COLUMNS, EXAMPLE, BACKGROUND, LIST = "📋", "💡", "📖", "🗂️"  # the collapsibles' captions start with these
OK, FAIL, CAVEAT = "✅", "⛔", "⚠️"  # visible checks under a table
RUN_LIST_MAX = 25


def _text(*sentences: str) -> list[str]:
    """A paragraph: one sentence per line (the Markdown joins them), then a blank line; empty sentences are dropped."""
    out = [s for s in sentences if s]
    return [*out, ""] if out else []


def _bullet(label: str, *sentences: str, code: bool = True) -> list[str]:
    """One bullet, `label` in backticks (a column, a file) or bold (a term), its first sentence on the bullet's line
    and every further one on a line of its own, indented by two spaces, so it still renders inside the bullet; a
    sentence starting with `- ` is a bullet nested in it."""
    head = f"`{label}`" if code else f"**{label}**"
    first, *rest = [s for s in sentences if s] or [""]
    return [f"- {head}: {first}".rstrip(), *(f"  {s}" for s in rest)]


def _bullets(items: Iterable[tuple], *, code: bool = True) -> list[str]:
    """Bullets from `(label, sentence, ...)` tuples (`_bullet`), then a blank line."""
    out = [line for label, *sentences in items for line in _bullet(label, *sentences, code=code)]
    return [*out, ""] if out else []


def _details(caption: str, body: list[str]) -> list[str]:
    """A collapsible: GitHub renders the Markdown inside only with a blank line after `<summary>` and before
    `</details>`."""
    body = [line for line in body]
    while body and body[-1] == "":
        body.pop()
    if not body:
        return []
    return ["<details>", f"<summary>{caption}</summary>", "", *body, "", "</details>", ""]


def _columns(items: Iterable[tuple]) -> list[str]:
    """`📋 Columns`: one bullet per column (`(name, sentence, ...)`), a homogeneous series in one bullet."""
    return _details(f"{COLUMNS} Columns", _bullets(items))


def _example(*sentences: str) -> list[str]:
    """`💡 Example`: the reading of one cell of the table above, with its numbers."""
    return _details(f"{EXAMPLE} Example", _text(*sentences))


def _background(topic: str, body: list[str]) -> list[str]:
    """`📖 <topic>`: method, definitions, conceptual examples."""
    return _details(f"{BACKGROUND} {topic}", body)


def _check(mark: str, *sentences: str) -> list[str]:
    """A visible check under a table: ✅ / ⛔ a verdict, ⚠️ a caveat needed to read the table correctly."""
    first, *rest = [s for s in sentences if s]
    return [f"{mark} {first}", *rest, ""]


def _run_list(title: str, items: list[str]) -> list[str]:
    """`🗂️ <title> (N)`: one bullet per item, at most RUN_LIST_MAX and a line for the rest (runs.csv has them all)."""
    if not items:
        return []
    shown = [f"- {x}" for x in items[:RUN_LIST_MAX]]
    if len(items) > RUN_LIST_MAX:
        shown.append(f"- … and {_n(len(items) - RUN_LIST_MAX)} more (`runs.csv`)")
    return _details(f"{LIST} {title} ({_n(len(items))})", shown)


def _link(title: str) -> str:
    """A link to a section of this summary by its heading."""
    return f"[{title}](#{_anchor(title)})"


def _run_ref(r: dict) -> str:
    """`tool` on `repo` (variant, run i): how a list names one run."""
    return f"`{r['tool']}` on `{r['repo']}` ({r.get('variant') or 'original'}, run {r.get('run') or 1})"


CAPTION_MARKS = (COLUMNS, EXAMPLE, BACKGROUND, LIST)
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_SENTENCE_BREAK = re.compile(r"[.!?] +[A-Z0-9C(\[*]")


def style_issues(markdown: str) -> list[str]:
    """Where the summary breaks its own layout rules (CLAUDE.md "Summary layout"): two
    sentences on one line, a semicolon or a double quote in prose, a semicolon in a table cell outside code, an
    unbalanced `<details>`, a `<summary>` without its emoji. Code spans count as one word, fenced code is skipped."""
    issues, fenced, depth = [], False, 0
    for n, line in enumerate(markdown.splitlines(), 1):
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        depth += line.count("<details>") - line.count("</details>")
        if line.startswith("<summary>") and not line[len("<summary>") :].startswith(CAPTION_MARKS):
            issues.append(f"{n}: a collapsible without its emoji: {line}")
        if line.startswith(("<", "#")) or re.match(r"\s*- \[[^\]]*\]\(#[^)]*\)$", line):
            continue  # HTML, a heading, an entry of the contents: titles, no prose
        prose = _CODE_SPAN.sub("C", line)
        prose = re.sub(r"\]\([^)]*\)", "]", prose)  # a link's target is no prose
        if line.startswith("|"):
            if ";" in prose:
                issues.append(f"{n}: a semicolon in a table cell: {line[:120]}")
            continue
        if ";" in prose:
            issues.append(f"{n}: a semicolon: {line[:120]}")
        if '"' in prose:
            issues.append(f"{n}: a double quote: {line[:120]}")
        if _SENTENCE_BREAK.search(prose.lstrip("-* ").removeprefix("#")):
            issues.append(f"{n}: two sentences on one line: {line[:120]}")
    if depth:
        issues.append(f"unbalanced <details>: {depth}")
    return issues


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
    """A share as a fraction with two decimals (0.43, 1.00): every share in the summary, so columns line up."""
    f = _float(v)
    return "" if f is None else f"{f:.2f}"


def _num(v) -> str:
    """A measured number with one decimal (2 -> 2.0, 1.25 -> 1.2), so a column lines up."""
    f = _float(v)
    return "" if f is None else _n(f"{f:.1f}")


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


def _cores(v) -> str:
    f = _float(v)
    return "" if f is None else f"{f:.1f}"


def _by_tool(r: dict) -> tuple:
    """A run's place in a list of runs: by tool, repository, variant and run."""
    return (r.get("tool") or "", r.get("repo") or "", r.get("variant") or "", _int(r.get("run")))


def _true(v) -> bool:
    """CSV booleans: write_csv lowercases them."""
    return str(v).strip().lower() == "true"


def _false(v) -> bool:
    return str(v).strip().lower() == "false"


VERDICT_MARK = {"pass": "✅", "fail": "⛔"}  # a micro project's verdict in the bar exam's table
BAR_MARK = {BAR_PASSED: "✅", BAR_FAILED: "⛔"}  # the tool's verdict over its micro projects


def _lang_key(lang: str) -> tuple[int, str]:
    return (LANGUAGE_ORDER.index(lang) if lang in LANGUAGE_ORDER else len(LANGUAGE_ORDER), lang)


# ----- sections -----


def _header(spec: dict, info: dict, run: int) -> list[str]:
    """The title, the spec's description as a subtitle and one line on what the summary was rendered from."""
    name = spec.get("name") or "?"
    lines = [f"# Analysis Summary of Experiment `{name}`", ""]
    description = str(spec.get("description") or "").strip()
    if description:
        subtitle = description[0].upper() + description[1:]
        lines += [f"*{subtitle}{'' if subtitle[-1] in '.!?' else '.'}*", ""]

    return lines


def _reading() -> list[str]:
    """The terms every table uses, defined once, so that the summary reads without the code or the READMEs; then how
    a section is laid out and what the cells mean."""
    lines = ["### How to read this summary", ""]
    lines += _text("The terms the tables below use, then how every section is laid out.")
    lines += _bullets(
        [
            ("Run", "one tool on one repository once.",
             "With several repetitions each pair runs several times, and a table reads run 1 unless it says "
             "otherwise."),
            ("Variant", "the repository as pinned (`original`) or a mutant of it with one planted crypto call or "
             "decoy (RQ5).", "A table reads the original variants unless it says otherwise."),
            ("Asset", "one component of a tool's CBOM, as the tool wrote it."),
            ("Record", "one place an asset lists, so an asset listed at three places gives three records.",
             "How a tool packages its findings decides these counts: one component per algorithm with all its places, "
             "or one component per finding."),
            ("Item", "one algorithm in one repository, named by its normalised identity, the *L4 identity* (`AES`, "
             "`SHA-256`, `ECDSA`).", "A tool reports an item when one of its records maps to it.",
             "Items do not depend on packaging, so every comparison across tools counts items."),
            ("Levels L0-L4", "when two reports count as the same algorithm.",
             "A match found at one level holds at every higher one.",
             "- `L0`: the identical name.", "- `L1`: case and separators ignored (`sha256` = `SHA-256`).",
             "- `L2`: the standard vocabulary (`DESede` = `3DES`).",
             "- `L3`: composite names split (`AES-256-GCM` is `AES`).",
             "- `L4`: also each tool's own spellings and labels."),
            ("Voter group", "tools that share their detection logic count as one voter, so that the same logic never "
             f"confirms itself ({_link('Voter groups')})."),
            ("Confirmed", "a tool's item is confirmed when a tool of another voter group reports it too."),
            ("k", "the number of voter groups that report an item.", "`k=1`: one group only, unconfirmed."),
        ],
        code=False,
    )  # fmt: skip
    lines += _text(
        "Every section shows its table first, then its checks: ✅ and ⛔ a verdict, ⚠️ a caveat needed to read the "
        "table.",
        "Below them, collapsed: 📋 the columns, 💡 an example read from the table, 📖 the background and 🗂️ the runs "
        "a check counts.",
        "The cells use these marks:",
    )
    lines += _bullets(
        [
            ("–", "the repository's language is outside the tool's languages, so the tool was not run."),
            ("×", "the run failed or timed out."),
            ("1'234", "a number with ' between thousands, so 1234."),
            ("0.43", "a share as a fraction with two decimals, so 43 %, and `1.00` means all."),
            ("a / b", "a cell that combines numbers, such as `x (y)` and `rate [low, high]`, explained under its table "
             "with an example."),
        ]
    )  # fmt: skip
    return lines


def _anchor(heading: str) -> str:
    """A heading's GitHub anchor: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[^\w\- ]", "", heading.replace("`", "").lower())
    return text.strip().replace(" ", "-")


def _toc(lines: list[str]) -> list[str]:
    """A table of contents from the `##` and `###` headings of the rendered body, with anchor links."""
    out, seen = ["## Contents", ""], Counter()
    for line in lines:
        if not line.startswith("## ") and not line.startswith("### "):
            continue
        depth, title = line.split(" ", 1)
        anchor = _anchor(title)
        if seen[anchor]:
            anchor = f"{anchor}-{seen[anchor]}"  # GitHub numbers repeated headings
        seen[_anchor(title)] += 1
        out.append(f"{'  ' if depth == '###' else ''}- [{title}](#{anchor})")
    return out + [""]


def _questions() -> list[str]:
    """The research questions (analysis/rqs.py), so the summary carries its own frame."""
    lines = ["### Research questions", ""]
    lines += _text("The sections below answer them in this order.")
    lines += _bullets(
        [(f"{rq.id}, {rq.title.lower()}", rq.question,
          "Tables: " + ", ".join(f"`{t.replace('{i}', '<i>')}`" for t in rq.tables) + ".") for rq in RQS],
        code=False,
    )  # fmt: skip
    return lines + _text(*BAR_EXAM_NOTE, *STABILITY_NOTE)


def _experiment(
    rows: list[dict], sessions: list[dict], *, spec: dict | None = None, info: dict | None = None, run: int = 1
) -> list[str]:
    """The overview table: what the spec covers (repos, tools, repetitions), when it ran, for how long, on how much
    RAM, the peaks and the code that ran (the record, the runner's run log and the runs' meta), and what this summary
    was rendered with."""
    ran = [r for r in rows if r.get("status") != "unsupported"]
    spec, info = spec or {}, info or {}
    lines: list[str] = []
    table: list[list] = []
    if spec:
        repos, tools, reps = spec.get("repos") or [], spec.get("tools") or [], _int(spec.get("repetitions", 1)) or 1
        table.append(["repositories", f"{_n(len(repos))}, pinned with their commits in `_experiment.yaml`"])
        table.append(["tools", f"{len(tools)}: {', '.join(f'`{x}`' for x in tools)}"])
        table.append(["repetitions", f"{reps}" + (f" (the tables show run {run} unless they say otherwise)"
                                                   if reps > 1 or run > 1 else "")])  # fmt: skip
    if sessions:
        s_start = [t for e in sessions if (t := _ts(e.get("started_at")))]
        s_end = [t for e in sessions if (t := _ts(e.get("finished_at") or e.get("updated_at")))]
        active = sum(
            (e_end - e_start).total_seconds()
            for e in sessions
            if (e_start := _ts(e.get("started_at"))) and (e_end := _ts(e.get("finished_at") or e.get("updated_at")))
        )
        n = len(sessions)
        statuses = Counter(str(e.get("status") or "?") for e in sessions)
        outcomes = ", ".join(f"{c} {k}" for k, c in sorted(statuses.items()))
        if s_start and s_end:
            table.append(["ran", f"{_when(min(s_start))} to {_when(max(s_end))}"])
            table.append(["duration", f"{_span(active)} in {n} runner session{'' if n == 1 else 's'} ({outcomes})"])
        else:
            table.append(["sessions", f"{n} ({outcomes})"])
    tool_time = sum(_float(r.get("duration_sec")) or 0 for r in ran)
    if ran:
        table.append(["tool time", f"{_span(tool_time)} summed over {_n(len(ran))} runs (excluding unsupported)"])
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
    settings = [e.get("replicas") or {} for e in sessions]  # `make up-prod REPLICAS=...`, counted per session
    if any(settings):

        def shown(s: dict) -> str:
            return ", ".join(f"{t} x{n}" for t, n in sorted(s.items())) or "none"

        text = (shown(settings[-1]) if all(s == settings[-1] for s in settings)
                else ", ".join(f"session {i}: {shown(s)}" for i, s in enumerate(settings, 1)))  # fmt: skip
        table.append(["replicas", f"{text} (containers of one tool share its queue, and run times include their "
                                  "contention)"])  # fmt: skip
    peaks = [p for e in sessions if (p := _float(e.get("ram_peak_used_mb"))) is not None]
    if peaks:
        total = _float(host.get("ram_total_mb"))
        share = f" of {_gib(total)} GiB ({_pct(max(peaks) / total)})" if total else ""
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
        table.append(["BF-CBOM version", f"{', '.join(images)} (the image tag, the commit below names the build)"])
    table += _commits(ran, sessions)
    image_ids = {t: i for e in sessions for t, i in (e.get("images") or {}).items()}
    if image_ids:
        table.append(["worker image ids", f"{len(image_ids)} recorded in `_run-log.json` (by content, per session)"])
    if not table:
        return []
    vocab = info.get("vocabulary") or {}
    if vocab:
        registry = str(vocab.get("registry") or "?")
        table.append(["vocabulary", f"{vocab.get('algorithms', '?')} (`analysis/data/algorithms.yaml`), CycloneDX "
                                    f"registry {registry[:10] if 'T' in registry else registry}"])  # fmt: skip
    table.append(["summary generated", info.get("generated_at") or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")])
    table.append(["data", "rendered by `analysis report` from the CSV files listed under [Files](#files), which "
                          "join a list inside a cell with `;`"])  # fmt: skip
    lines += md_table(["", ""], table, "ll")
    return lines + [""]


def _commits(ran: list[dict], sessions: list[dict]) -> list[list]:
    """The overview's commit row: the commits the workers' images were built from (runs.csv `bfcbom_commit`, with
    their runs) and the runner's (the run log), and ⛔ when the workers ran another build than the runner, whose code
    then differs from the code that planned and stored the runs."""
    workers = Counter(r["bfcbom_commit"] for r in ran if r.get("bfcbom_commit"))
    runner = sorted({e["runner_commit"] for e in sessions if e.get("runner_commit")})
    if not workers and not runner:
        return []
    every = set(workers) | set(runner)
    dirty = " (`-dirty`: built from uncommitted changes)" if any(c.endswith("-dirty") for c in every) else ""
    if len(every) == 1 and workers and runner:
        (c,) = every
        return [["BF-CBOM commit", f"`{c}` (workers and runner){dirty}"]]
    parts = ["workers " + ", ".join(f"`{c}` ({_n(n)} runs)" for c, n in sorted(workers.items()))] if workers else []
    parts += ["runner " + ", ".join(f"`{c}`" for c in runner)] if runner else []
    text = ", ".join(parts)
    if runner and set(workers) - set(runner):
        text += " ⛔ the workers ran another build than the runner (`make up-prod` rebuilds them, the board shows each)"
    return [["BF-CBOM commit", text + dirty]]


PAPER_LANGUAGES = ("Java", "Python", "Go")  # the folded table's column order (CLAUDE.md "Paper languages")


def _repo_cell(repo: dict) -> str:
    """A repository as the record names it: a GitHub link that opens the pinned commit, or the plain name for a
    fixture (a local directory published to the git server, `path:`) and for any other host."""
    name, commit = repo.get("full_name") or "", repo.get("commit") or ""
    if repo.get("path"):
        return f"{name} (fixture)"
    url = (repo.get("git_url") or f"https://github.com/{name}").removesuffix(".git").rstrip("/")
    if not url.startswith("https://github.com/"):
        return name
    return f"[{name}]({url}/tree/{commit})" if commit else f"[{name}]({url})"


def _repositories(spec: dict) -> list[str]:
    """The repositories the record names, each at its pinned commit: flat with their sizes when the runner counted
    them into the record (`files`, `source_files`, `checkout_bytes`, `source_bytes`), flat with GitHub's size when
    only the corpus builder recorded one (`size_kb`), else folded into one column per language so a corpus of
    hundreds stays readable."""
    repos = [r for r in spec.get("repos") or [] if r.get("full_name")]
    if not repos:
        return []
    order = {lang: i for i, lang in enumerate(PAPER_LANGUAGES)}

    def rank(repo: dict) -> tuple:
        lang = repo.get("language") or ""
        return (order.get(lang, len(order)), lang, repo["full_name"].lower())

    repos.sort(key=rank)
    lines = ["### Repositories", ""]
    lines += _text(
        f"The {_n(len(repos))} repositories the stored runs cover, each at its pinned commit, counted by language and "
        "stratum.",
        "Mutants are variants of these repositories and are not listed.",
    )
    lines += _size_strata(repos)
    caption = f"{LIST} Repositories ({_n(len(repos))})"
    links = (
        "A link opens the repository at its pinned commit, a fixture is a local directory published to the git server."
    )
    if any(r.get("files") is not None for r in repos):

        def count(v) -> str:
            return _n(_int(v)) if v not in (None, "") else ""

        def mib(b) -> str:
            return f"{_int(b) / 1024**2:.1f}" if b not in (None, "") else ""

        table = [
            [_repo_cell(r), r.get("language") or "", count(r.get("files")), count(r.get("source_files")),
             mib(r.get("checkout_bytes")), mib(r.get("source_bytes"))]
            for r in repos
        ]  # fmt: skip
        header = ["repository", "language", "files", "source files", "MiB", "source MiB"]
        body = _text(links) + md_table(header, table, "llrrrr") + [""]
        body += _bullets(
            [
                ("files", "the files at the pinned commit."),
                ("source files", "those of the repository's language (`.java .kt .scala .groovy`, `.py`, `.go`).",
                 "The run-time table scales by them."),
                ("MiB", "the bytes of the checkout at that commit (the blobs, no history)."),
                ("source MiB", "the bytes of its source files."),
            ]
        )  # fmt: skip
        return lines + _details(caption, body)
    if any(r.get("size_kb") for r in repos):
        table = [
            [_repo_cell(r), r.get("language") or "", f"{_int(r['size_kb']) / 1024:.1f}" if r.get("size_kb") else ""]
            for r in repos
        ]
        body = _text(links, "`size MB` is GitHub's size of the repository, as the corpus builder recorded it.")
        return lines + _details(caption, body + md_table(["repository", "language", "size MB"], table, "llr"))
    by_language: dict[str, list[str]] = defaultdict(list)
    for r in repos:
        by_language[r.get("language") or "unknown"].append(_repo_cell(r))
    columns = list(by_language.values())  # in rank order: the paper languages first, then the rest by name
    header = [f"{lang} ({_n(len(cells))})" for lang, cells in by_language.items()]
    table = [[col[i] if i < len(col) else "" for col in columns] for i in range(max(map(len, columns)))]
    return lines + _details(caption, _text(links) + md_table(header, table, "l" * len(header)))


def _size_strata(repos: list[dict]) -> list[str]:
    """Repositories counted by language (rows) and stratum (columns): first the corpus builder's default size bins
    (`CorpusParams.size_bins_kb`, `make corpus --size-bins`, GitHub's repository size in KB), then, after an empty
    column, its default star bins (`CorpusParams.star_bins`, `--star-bins`), each with a column below the first bin's
    floor for the micro projects and other small repositories and an `unknown` column where a value is missing. The
    size is GitHub's `size_kb` when every repository has one (a corpus-built spec), else the checkout at the pinned
    commit (`checkout_bytes`), and the text says which; the stars are the corpus builder's `stars`, absent from a
    hand-picked corpus."""
    params = CorpusParams()
    size_bins, star_bins = params.size_bins_kb, params.star_bins
    github = all(r.get("size_kb") for r in repos)

    def kb(r: dict) -> float | None:
        if github:
            return float(_int(r["size_kb"]))
        b = r.get("checkout_bytes")
        return _int(b) / 1024 if b not in (None, "") else None

    def stars(r: dict) -> float | None:
        return float(_int(r["stars"])) if r.get("stars") not in (None, "") else None

    sized = [(r, kb(r), stars(r)) for r in repos]
    if not any(z is not None or s is not None for _, z, s in sized):
        return []

    def mb(v: int) -> str:
        return f"{v / 1000:g}"

    def strata(bins: list, label, values: list[float | None]) -> tuple[list[str], list[int]]:
        """The labels of the floor column, the bins and `unknown` (when a value is missing), and each value's
        column."""
        cols = [(0, bins[0][0]), *bins]
        labels = [label(None, bins[0][0])] + [label(lo, hi) for lo, hi in bins]
        unknown = any(v is None for v in values)
        labels += [label(None, None)] * unknown
        index = [len(cols) if v is None else next(i for i, (lo, hi) in enumerate(cols)
                                                  if v >= lo and (hi is None or v < hi)) for v in values]  # fmt: skip
        return labels, index

    def size_label(lo, hi) -> str:
        if lo is None:
            return f"< {mb(hi)} MB" if hi else "size unknown"
        return f"{mb(lo)}-{mb(hi)} MB" if hi else f">= {mb(lo)} MB"

    def star_label(lo, hi) -> str:
        if lo is None:
            return f"< {hi} stars" if hi else "stars unknown"
        return f"{lo}-{hi} stars" if hi else f">= {lo} stars"

    size_labels, size_col = strata(size_bins, size_label, [z for _, z, _ in sized])
    star_labels, star_col = strata(star_bins, star_label, [s for _, _, s in sized])
    order = {lang: i for i, lang in enumerate(PAPER_LANGUAGES)}
    by_size: dict[str, Counter] = defaultdict(Counter)
    by_stars: dict[str, Counter] = defaultdict(Counter)
    for (r, _, _), z, s in zip(sized, size_col, star_col, strict=True):
        lang = r.get("language") or "unknown"
        by_size[lang][z] += 1
        by_stars[lang][s] += 1
    langs = sorted(by_size, key=lambda lang: (order.get(lang, len(order)), lang))

    def row(name: str, size: Counter, star: Counter, n: int) -> list:
        return [name, *[size[i] for i in range(len(size_labels))], n, "", *[star[i] for i in range(len(star_labels))]]

    table = [row(lang, by_size[lang], by_stars[lang], sum(by_size[lang].values())) for lang in langs]
    table.append(row("all", sum(by_size.values(), Counter()), sum(by_stars.values(), Counter()), len(repos)))
    measure = (
        "GitHub's repository size as the corpus builder recorded it (`size_kb`)."
        if github
        else "the checkout at the pinned commit (`checkout_bytes`: the blobs, no history, so smaller than GitHub's "
        "size of the same repository)."
    )
    header = ["language", *size_labels, "all", "", *star_labels]
    size_arg, star_arg = (",".join(bin_text(lo, hi) for lo, hi in bins) for bins in (size_bins, star_bins))
    lines = md_table(header, table, "l" + "r" * (len(header) - 1)) + [""]
    lines += _columns(
        [
            ("language", "one row per language, `all` the sum."),
            (f"{size_labels[0]} … {size_labels[len(size_bins)]}",
             f"the corpus builder's default size strata (`make corpus`, `--size-bins {size_arg}` in KB), with a "
             "column below the sampling floor for the micro projects and other small repositories.",
             f"The size is {measure}"),
            ("all", "the repositories of the language."),
            (f"{star_labels[0]} … {star_labels[len(star_bins)]}",
             f"after the empty column, the corpus builder's default star strata (`--star-bins {star_arg}`), with a "
             "column below the floor too.",
             "The stars are those the corpus builder recorded when it drew the repository (`stars`)."),
            *([("size unknown", "repositories without a recorded size.")] if "size unknown" in size_labels else []),
            *([("stars unknown", "repositories without a recorded star count, such as a hand-picked corpus or the "
                                 "micro projects.")] if "stars unknown" in star_labels else []),
        ]
    )  # fmt: skip
    return lines


VERSION_RULE = (
    "Images built since 2026-10-30 follow one rule (`docs/tool-versions.md` lists the pins).",
    "Each tool is at the latest stable release published by 30 October 2026, pre-releases and nightly builds excluded.",
    "A tool without any stable release is at its default branch's last commit on that date.",
    "An engine a tool's own build bundles stays at the version the tool pins.",
    "Runs of older images show older versions here.",
)


def _tools(rows: list[dict]) -> list[str]:
    """Who publishes each tool, how it is shipped and what it is built on (tools.csv, from common/tools.yaml)."""
    if not rows:
        return []
    table = [[f"[{r['tool']}]({r['url']})" if r.get("url") else r["tool"], r.get("vendor") or "",
              r.get("shipped") or "", r.get("approach") or ""] for r in rows]  # fmt: skip
    lines = ["### Tools", ""]
    lines += _text("The tools that ran, as `common/tools.yaml` records them, one row per tool.")
    lines += md_table(["tool", "vendor", "shipped as", "engine and approach"], table, "llll") + [""]
    lines += _columns(
        [
            ("tool", "the tool, linked to its project page."),
            ("vendor", "who publishes the tool."),
            ("shipped as", "how it is delivered: a CLI, a server with an API, a GitHub Action or an HTTP API."),
            ("engine and approach", "what it is built on, a third-party engine named first, else `own engine`, and how "
             "it detects."),
        ]
    )  # fmt: skip
    return lines


def ran_version(recorded: str, version: str) -> bool:
    """Whether a run's `tool_version` string names `version`: a release as a whole token (`2.3.0`, `v2.3.0`; not
    `12.3.0`), a commit's first characters as the start of the recorded SHA."""
    if re.fullmatch(r"[0-9a-f]{7,40}", version):
        return re.search(rf"(?<![0-9a-f]){version}", recorded) is not None
    return re.search(rf"(?<![\w.])v?{re.escape(version)}(?![\w.])", recorded) is not None


def _versions(rows: list[dict], tools: list[dict] | None = None) -> list[str]:
    """What ran, per tool: the version the paper reports, linked to the page that shows it (tools.csv `version`,
    `version_url`, from common/tools.yaml), then as every stored run recorded it (`tool_version`, `image_ref`,
    `bfcbom_commit` in runs.csv): one row per tool, several values joined when the runs disagree, which a check then
    flags, as it flags runs that did not record the version tools.yaml names."""
    ran = [r for r in rows if r.get("tool_version") or r.get("image_ref")]
    if not ran:
        return []
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for r in ran:
        by_tool[r["tool"]].append(r)
    named = {t["tool"]: t for t in tools or [] if t.get("version")}
    table, mixed, other = [], [], []
    for tool in sorted(by_tool):
        rs = by_tool[tool]
        cells = [sorted({r.get(k) or "" for r in rs} - {""}) for k in ("tool_version", "image_ref", "bfcbom_commit")]
        if any(len(c) > 1 for c in cells):
            mixed.append(tool)
        entry = named.get(tool) or {}
        version = entry.get("version") or ""
        if version and not all(ran_version(r.get("tool_version") or "", version) for r in rs):
            other.append(tool)
        shown = f"[{version}]({entry['version_url']})" if version and entry.get("version_url") else version
        table.append([tool, shown, *[", ".join(f"`{v}`" for v in c) for c in cells], len(rs)])
    lines = ["### Tool versions", ""]
    lines += _text("What ran, one row per tool: the version the paper reports, then what every stored run recorded.")
    lines += md_table(["tool", "version", "recorded as", "image", "BF-CBOM commit", "runs"], table, "lllllr") + [""]
    if named.keys() & by_tool.keys():
        if other:
            lines += _check(FAIL, f"Runs of {', '.join(f'`{t}`' for t in other)} did not record the `version` "
                                  "`common/tools.yaml` names.", "Their image is older, or a pin moved without "
                                  "`common/tools.yaml`.")  # fmt: skip
        else:
            lines += _check(OK, "Every run recorded the `version` `common/tools.yaml` names.")
    if mixed:
        lines += _check(FAIL, f"Runs of {', '.join(f'`{t}`' for t in mixed)} disagree on what ran, because a worker "
                              "was rebuilt between sessions.", "Their numbers mix versions, so rerun the experiment "
                              "whole (`--overwrite-old`).")  # fmt: skip
    lines += _columns(
        [
            ("version", "the version the paper reports, the tool's own: its release, or the first characters of the "
             "pinned commit when it has none (`common/tools.yaml`, `tools.csv`).",
             "It links to the page that shows it, the paper's citation.",
             "Engines are listed in `docs/tool-versions.md`."),
            ("recorded as", "the worker's version string, as every run recorded it."),
            ("image", "the worker image."),
            ("BF-CBOM commit", "the commit the image was built from (`-dirty`: built with uncommitted changes)."),
            ("runs", "the runs that recorded these values, all variants and repetitions.",
             "Unsupported runs record none."),
        ]
    )  # fmt: skip
    lines += _background("The version rule", _text(*VERSION_RULE))
    return lines


def _runs(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_tool[r["tool"]].append(r)
    statuses = ["ok", "error", "timeout", "oom", "unsupported"]
    table, others = [], []
    for tool in sorted(by_tool):
        rs = by_tool[tool]
        c = Counter(r["status"] for r in rs)
        other = sum(n for s, n in c.items() if s not in statuses)
        durations = [d for r in rs if r["status"] == "ok" and (d := _float(r.get("duration_sec"))) is not None]
        median = _num(statistics.median(durations)) if durations else ""
        versions = ", ".join(f"`{v}`" for v in sorted({r["tool_version"] for r in rs if r.get("tool_version")}))
        if not versions and c.get("unsupported") == len(rs):
            versions = "not run: no repo in its languages"
        row = [tool] + [c.get(s, 0) or "" for s in statuses] + [other or "", median]
        others.append(other)
        table.append(row + [versions])
    header = ["tool", *statuses, "other", "median s"]
    if not any(others):  # statuses outside the four are rare; keep the column only when there are some
        i = header.index("other")
        header.pop(i)
        for row in table:
            row.pop(i)
    lines = ["### Runs", ""]
    lines += _text("Every stored run, all repetitions and variants, one row per tool.")
    lines += md_table([*header, "tool version"], table, "l" + "r" * (len(header) - 1) + "l") + [""]
    failed = sorted((r for r in rows if r["status"] not in ("ok", "oom", "unsupported")), key=_by_tool)
    workarounds = sorted((r for r in rows if r.get("workaround")), key=_by_tool)
    warned = sorted((r for r in rows if r.get("tool_warning")), key=_by_tool)
    if failed:
        lines += _check(FAIL, f"{_n(len(failed))} run{'' if len(failed) == 1 else 's'} without a result.")
    else:
        lines += _check(OK, "Every run that ran has a result.")
    if workarounds:
        lines += _check(CAVEAT, f"{_n(len(workarounds))} run{'' if len(workarounds) == 1 else 's'} with a recorded "
                                "setup workaround.", "The tool is unchanged: the input it crashes on is excluded "
                                "through its own mechanism, and named.")  # fmt: skip
    if warned:
        lines += _check(CAVEAT, f"{_n(len(warned))} run{'' if len(warned) == 1 else 's'} on which the tool said it "
                                "limited its own analysis.", "The run is ok, but the tool analysed less than it "
                                "can.")  # fmt: skip
    lines += _columns(
        [
            ("ok", "the tool ran and wrote its CBOM."),
            ("error", "the run failed."),
            ("timeout", "the run hit the time limit."),
            ("oom", "the tool wrote output, but its container was OOM-killed meanwhile, so the CBOM may be partial.",
             "That is our memory cap, not the tool's result.",
             "Such a run is left out of every table below, and `experiment run --retry-failed` redoes it."),
            ("unsupported", "the repository's language is outside the tool's languages, so the tool was not run."),
            *([("other", "runs with any other status.")] if "other" in header else []),
            ("median s", "the median duration of the ok runs, in seconds."),
            ("tool version", "as the worker recorded it."),
        ]
    )  # fmt: skip
    lines += _run_list("Runs without a result", [f"{_run_ref(r)}: {r['status']}" + (f", `{r['error']}`" if r.get(
        "error") else "") for r in failed])  # fmt: skip
    lines += _run_list("Runs with a setup workaround", [f"{_run_ref(r)}: `{r['workaround']}`" for r in workarounds])
    lines += _run_list("Runs on which the tool limited its analysis (`tool_warning`)",
                       [f"{_run_ref(r)}: `{r['tool_warning']}`" for r in warned])  # fmt: skip
    return lines


def _memory_limits(runs: list[dict], runtime: list[dict]) -> list[str]:
    """Whether our memory limits shaped a result (runtime.csv, runs.csv): per tool the limit next to its peaks, the
    runs that reached it, the OOM kills and the failed runs that may have failed for memory, each of those listed.
    A check of the setup, not a finding: the paper reports the peaks, never the limits."""
    every = [r for r in runtime if r["language"] == "all" and r.get("peak_max_mb") not in (None, "")]
    if not every:
        return []

    def share(r: dict) -> float | None:
        peak, limit = _float(r.get("peak_max_mb")), _float(r.get("limit_mb"))
        return peak / limit if peak is not None and limit else None

    every.sort(key=lambda r: (-(share(r) or 0), r["tool"]))
    recorded = any(r.get("at_limit") not in (None, "") for r in every)
    table = [
        [r["tool"], _gib(r.get("limit_mb")), _gib(r.get("peak_max_mb")), _pct(share(r)), _gib(r.get("peak_p90_mb")),
         r.get("at_limit") or "", r.get("oom") or "0", r.get("memory_failures") or "0"]
        for r in every
    ]  # fmt: skip
    lines = ["### Memory limits", ""]
    lines += _text(
        "Whether our memory limits shaped any result, one row per tool over every run it made, closest to its limit "
        "first.",
        f"The limits are ours, not the tools': the peaks are reported under {_link('Run time, memory and CPU')}, the "
        "limits only here.",
    )
    header = ["tool", "limit GiB", "max peak GiB", "of limit", "p90 peak GiB", "at limit", "OOM kills",
              "failed for memory"]  # fmt: skip
    lines += md_table(header, table) + [""]
    capped = sorted((r for r in runs if _int(r.get("limit_hits")) > 0 and not r.get("memory_failure")), key=_by_tool)
    failed = sorted((r for r in runs if r.get("memory_failure")), key=_by_tool)
    if capped:
        lines += _check(CAVEAT, f"{_n(len(capped))} run{'' if len(capped) == 1 else 's'} reached the limit and still "
                                "finished.",
                        "Their peak is the limit's, not what the tool would have taken.")  # fmt: skip
    if failed:
        lines += _check(FAIL, f"{_n(len(failed))} run{'' if len(failed) == 1 else 's'} may have failed for memory.",
                        "An `oom` run is stored with the tool's output but left out of the tables.",
                        "`experiment run --retry-failed` redoes them all, after raising the limit.")  # fmt: skip
    else:
        lines += _check(OK, "No run failed for memory.")
    lines += _columns(
        [
            ("limit GiB", "the container's memory limit."),
            ("max peak GiB", "the largest working set of a single run."),
            ("of limit", "that peak as a share of the limit."),
            ("p90 peak GiB", "90 % of the repositories' peaks stay under it."),
            ("at limit", "runs whose usage reached the limit at least once.",
             "The kernel then reclaims memory, file cache first, which can slow the job, and the run's peak is the "
             "limit, not what the tool would have taken.",
             "" if recorded else "Empty: the workers of this experiment did not record it yet."),
            ("OOM kills", "runs during which the kernel killed a process of the container for memory."),
            ("failed for memory", "runs that failed (error, timeout, oom) with an OOM kill, with an error saying the "
             "tool ran out of memory, or at the limit (`memory_failure` in `runs.csv`).",
             "A Java tool ends at its heap, below the container's limit."),
        ]
    )  # fmt: skip
    lines += _run_list(
        "Runs at the limit that finished",
        [f"{_run_ref(r)}: {r['status']}, peak {_gib(r.get('peak_memory_mb'))} of {_gib(r.get('memory_limit_mb'))} "
         f"GiB, {_n(_int(r.get('limit_hits')))} limit hits" for r in capped],
    )  # fmt: skip
    lines += _run_list(
        "Runs that may have failed for memory",
        [f"{_run_ref(r)}: {r['status']}, {r['memory_failure']}, peak {_gib(r.get('peak_memory_mb'))} of "
         f"{_gib(r.get('memory_limit_mb'))} GiB" + (f", `{r['error']}`" if r.get("error") else "") for r in failed],
    )  # fmt: skip
    return lines


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
    lines = ["#### Low-expectation repositories", ""]
    lines += _text(
        f"The spec marks {len(repos)} repositories as containing little or no first-party cryptography: "
        f"{', '.join(f'`{r}`' for r in repos)}.",
        "What a tool reports there hints at false positives, a weak prior rather than ground truth (ok runs of this "
        "repetition).",
    )
    lines += md_table(["tool", "repos ok", "crypto assets", "algorithm assets", "empty runs", "max per repo"], table)
    lines += [""] + _columns(
        [
            ("repos ok", "the low-expectation repositories with an ok run."),
            ("crypto assets", "summed over them."),
            ("algorithm assets", "summed over them."),
            ("empty runs", "runs that reported nothing."),
            ("max per repo", "the most `crypto assets` in one of them."),
        ]
    )  # fmt: skip
    return lines


ASSET_TYPES = ("algorithm", "certificate", "protocol", "related-crypto-material")  # CycloneDX assetType, its order
ASSET_KINDS = [  # runs.csv column -> table header, in the order of the RQ1 table
    ("algorithm_assets", "algorithm"),
    ("certificate_assets", "certificate"),
    ("protocol_assets", "protocol"),
    ("material_assets", "related-crypto-material"),
    ("other_assets", "other"),
]


def _asset_types(values: Iterable[str]) -> list[str]:
    """The asset types among `values`: CycloneDX's four in the standard's order, then any other value."""
    present = set(values)
    return [t for t in ASSET_TYPES if t in present] + sorted(present - set(ASSET_TYPES))


def _asset_type_heading(at: str) -> list[str]:
    note = "" if at in ASSET_TYPES else " (none of CycloneDX's four)"
    return [f"#### Asset type `{at}`{note}", ""]


def _reported(rows: list[dict], run: int) -> list[str]:
    """RQ1 before any normalisation: what each tool wrote into its CBOMs (one repetition, original variants), and the
    crypto asset count of every (repo, tool) pair."""
    rows = [r for r in rows if _int(r.get("run")) == run and (r.get("variant") or "original") == "original"]
    if not rows:
        return []
    ok = [r for r in rows if r["status"] == "ok" and r.get("crypto_assets") not in (None, "")]
    tools = sorted({r["tool"] for r in rows})
    table = []
    for tool in tools:
        rs = [r for r in ok if r["tool"] == tool]
        failed = sum(r["tool"] == tool and r["status"] not in ("ok", "unsupported") for r in rows)
        if not rs:
            table.append([tool, 0, "", failed] + [""] * (len(ASSET_KINDS) + 5))
            continue
        crypto = sum(_int(r["crypto_assets"]) for r in rs)
        with_file = sum(_int(r.get("located_file_assets")) for r in rs)
        with_line = sum(_int(r.get("located_line_assets")) for r in rs)
        counts = [_int(r["crypto_assets"]) for r in rs]
        table.append(
            [tool, len(rs), sum(c == 0 for c in counts), failed, sum(_int(r.get("components")) for r in rs), crypto]
            + [sum(_int(r.get(col)) for r in rs) for col, _ in ASSET_KINDS]
            + [f"{statistics.median(counts):.0f}"]
            + [_pct(with_file / crypto) if crypto else "", _pct(with_line / crypto) if crypto else ""]
        )
    lines = ["### What the tools report, as written", ""]
    lines += _text(
        "What each tool wrote into its CBOMs, counted as written, before any normalisation (original variants, ok "
        "runs only).",
        "One row per tool, summed over the repositories.",
        f"Asset counts follow each tool's packaging, so they compare output formats, not findings, which "
        f"{_link('Units per tool')} counts in items.",
    )
    header = ["tool", "repos ok", "empty", "failed", "components", "crypto assets", *[h for _, h in ASSET_KINDS],
              "median", "file", "file+line"]  # fmt: skip
    lines += md_table(header, table, "l" + "r" * (len(header) - 1)) + [""]
    lines += _columns(
        [
            ("repos ok", "repositories with an ok run."),
            ("empty", "the ok runs among them with 0 `crypto assets`."),
            ("failed", "repositories whose run failed (error, timeout, oom), listed under "
             f"{_link('Runs')}.", "So `repos ok` + `failed` are the repositories the tool ran on."),
            ("components", "every component of the CBOMs, the dependency listings of inventory tools included."),
            ("crypto assets", "the components typed as cryptographic assets."),
            ("algorithm … other", "the `crypto assets` by the CycloneDX `assetType` they declare (`algorithm`, "
             "`certificate`, `protocol`, `related-crypto-material`), `other` a value outside these four.",
             f"The `algorithm` column is what {_link('Units per tool')} counts as `algorithm assets`."),
            ("median", "`crypto assets` per repository, over the ok runs."),
            ("file", "the share of the `crypto assets` that name a file."),
            ("file+line", "the share that name a file and a line (`evidence.occurrences`, or `detectionContext` in "
             "IBM's draft)."),
        ]
    )  # fmt: skip
    lines += _background("Why three units: the same findings as CycloneDX JSON, packaged three ways", UNITS_EXAMPLE)

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
    body = _text(
        "`crypto assets` per repository (rows) and tool (columns), as written, from the ok runs.",
        "`all` sums each column, `–` is an unsupported language and `×` a failed or timed-out run.",
        "`expect` is `expect_crypto` from the corpus: `low` marks a negative case where little crypto is expected, so "
        "reports there are a false-positive proxy."
        if expect
        else "",
    )
    body += md_table(["repo", "language"] + (["expect"] if expect else []) + tools, matrix,
                     "ll" + ("l" if expect else "") + "r" * len(tools))  # fmt: skip
    return lines + _details(f"{LIST} Crypto assets per repository ({_n(len(repos))})", body)


def _micro(rows: list[dict], expected: list[dict]) -> list[str]:
    if not rows:
        return []
    langs = sorted({r["language"] for r in rows}, key=_lang_key)
    cells = {(r["tool"], r["language"]): r for r in rows}
    n_items = max((_int(r.get("n_items")) for r in rows), default=11)
    bar = (n_items + 1) // 2

    tool_verdict = {r["tool"]: r.get("bar_verdict") or "" for r in rows}

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
            return f"{VERDICT_MARK[r['verdict']]} {r['lenient']}{mark}"
        return "–" if r["verdict"] == "unsupported" else r["verdict"] + mark

    def recall_cell(r: dict | None) -> str:
        if r is not None and r["verdict"] in ("pass", "fail"):
            return f"{r['strict']} / {r['lenient']} · {r['repo']}"
        return "–" if r is not None and r["verdict"] == "unsupported" else ""

    scored = [(t, lg, cells[(t, lg)]) for t in tools for lg in langs if (t, lg) in cells
              and cells[(t, lg)]["verdict"] in ("pass", "fail")]  # fmt: skip
    lines = [f"## {BAR_EXAM}", ""]
    lines += _text(
        "A preparatory step before the research questions: which of the integrated generators meet the minimum "
        "requirements of this study?",
        "The tool search (the candidates, where and when they were looked for, the requirements and every exclusion "
        "with its reason) is documented in `docs/tool-selection.md`, and this section is the bar exam on the "
        "generated micro projects.",
        "The verdicts are the one filter on the tools: a person reads them and writes the tools and their languages "
        "into the later specs by hand (`tools`, `tool_languages:`), and no code reads them.",
    )
    lines += _background("The micro projects", _text(
        f"Each micro project (`experiments/corpus/micro`, one per language) is a small program generated from the "
        f"snippet catalog (`mutation/micro.py`): {n_items} known crypto calls, each in a function of its own, plus "
        "decoys, code that names or resembles cryptography without using it.",
        "That is the ground truth (`experiments/corpus/micro/ground_truth.yaml`).",
    ))  # fmt: skip
    lines += ["### The bar", ""]
    lines += _text(
        f"**The bar: {bar} of {n_items} calls** (half, lenient call-site recall) in at least one language.",
        "One row per tool, one column per language, then the tool's verdict.",
    )
    lines += md_table(["tool", *langs, "bar exam"],
                      [[t] + [verdict_cell(cells.get((t, lg))) for lg in langs] + [_bar_cell(tool_verdict[t])]
                       for t in tools],
                      "l" + "r" * len(langs) + "l") + [""]  # fmt: skip
    if tailored:
        lines += _check(CAVEAT, "`*`: the tool was developed with this project at hand (`tailored_on` in "
                                "`common/tools.yaml`), so its number here says little about other code.",
                        "The pair is left out of every agreement table below.")  # fmt: skip
    lines += _columns(
        [
            (" … ".join(langs) if len(langs) > 1 else langs[0],
             f"`{VERDICT_MARK['pass']} n` (pass) or `{VERDICT_MARK['fail']} n` (fail), with n the calls the tool "
             "found in their functions.",
             "`–`: outside the tool's languages, `error`: the run failed or timed out."),
            ("bar exam", "the tool's verdict.",
             "`bar passed`: it clears the bar in at least one language.",
             "`bar failed`: it was scored and clears it nowhere, a candidate to leave out of the later specs.",
             "`not judged`: without any scored run."),
        ]
    )  # fmt: skip
    if scored:
        t, lg, r = scored[0]
        shown = verdict_cell(r).replace("\\*", "*")
        lines += _example(
            f"`{shown}` for `{t}` in {lg}: it reported {r['lenient']} of the {n_items} calls of micro-{lg} in their "
            f"functions, so it {'passes' if r['verdict'] == 'pass' else 'fails'} the bar of {bar}."
        )
    lines += _handover(rows, tools, langs)
    lines += _decoys(rows, langs, tools)
    lines += ["### Recall at two levels", ""]
    lines += _text(
        f"The same projects in detail, each cell out of {n_items}, written `strict / lenient · repo`.",
        "A call counts as found by the *family* of its algorithm (the ground truth's `group`: RSA, EC, AES, SHA-2, "
        "HMAC, …), not by its exact name, so a record `AES` counts for an AES-256-GCM call.",
    )
    lines += md_table(["tool", *langs], [[t] + [recall_cell(cells.get((t, lg))) for lg in langs] for t in tools])
    lines += [""] + _columns(
        [
            ("strict", "the tool has a record of that family on the call's own lines, the body of its function.",
             "A reported span counts at every line it covers, and for a key generation a key record of the family "
             "counts too."),
            ("lenient", "`strict`, or a record on those lines whose name fits no family, such as a code identifier "
             "(`Cipher.getInstance`) or a label nobody can read.",
             "The tool points at the right place, but not clearly at the right algorithm.",
             "The bar uses this number."),
            ("repo", "the family is reported somewhere in the project, location ignored.",
             "Calls sharing a family (two RSA, three AES) are found or missed together.",
             "It can be lower than `lenient`: a record without a family counts nowhere here."),
        ]
    )  # fmt: skip
    if scored:
        # the cell with three different numbers reads best
        t, lg, r = max(scored, key=lambda x: len({x[2]["strict"], x[2]["lenient"], x[2]["repo"]}))
        s, le, rp = _int(r["strict"]), _int(r["lenient"]), _int(r["repo"])
        more = (
            f"On {le - s} more it reports something there whose name fits no family, so {le} lenient."
            if le > s
            else "Nothing more counts leniently."
        )
        lines += _example(
            f"`{recall_cell(r)}` for `{t}` in {lg}: on the lines of {s} calls it reports the right family.",
            more,
            f"The family of {rp} calls is reported somewhere in the project.",
        )
    lines += _quality(cells, langs, tools)
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
        lines += ["### Who finds each expected call", ""]
        lines += _text(
            "One row per call of the ground truth, one column per language.",
            "A low count marks a call most tools miss.",
        )
        lines += md_table(["expected item", *langs], table) + [""]
        lines += _columns(
            [
                ("expected item", "the call, with its style in brackets when the algorithm is not named in the call "
                 "itself."),
                (" … ".join(langs) if len(langs) > 1 else langs[0],
                 "`found / scored`: how many of the tools that ran on that project found it at its lines "
                 "(lenient).", "Empty: the call is not in that language's catalog."),
            ]
        )  # fmt: skip
        first = next(((row[0], lg, cell) for row in table for lg, cell in zip(langs, row[1:], strict=True) if cell),
                     None)  # fmt: skip
        if first:
            item, lg, cell = first
            f, n = cell.split("/")
            lines += _example(f"`{cell}` for `{item}` in {lg}: {f} of the {n} tools scored on micro-{lg} found it.")
    return lines


def _bar_cell(verdict: str) -> str:
    """The tool's bar verdict with its mark (`bar passed`, `bar failed`; `not judged` has none)."""
    mark = BAR_MARK.get(verdict)
    return f"{mark} {verdict}" if mark else verdict


def _handover(rows: list[dict], tools: list[str], langs: list[str]) -> list[str]:
    """The verdicts as a person writes them into the later specs: the tools that passed, and for a passed tool that
    failed the bar in a language it was scored on, the languages it passed in (`tool_languages:`). No code reads
    this; it is the hand-over."""
    cells = {(r["tool"], r["language"]): r for r in rows}
    tool_verdict = {r["tool"]: r.get("bar_verdict") or "" for r in rows}
    passed = sorted(t for t in tools if tool_verdict[t] == BAR_PASSED)
    out = sorted(t for t in tools if tool_verdict[t] == BAR_FAILED)
    unjudged = sorted(t for t in tools if tool_verdict[t] not in (BAR_PASSED, BAR_FAILED))
    if not passed and not out:
        return []
    narrowed: dict[str, list[str]] = {}
    failed_runs: list[str] = []
    for t in passed:
        verdicts = {lg: cells[(t, lg)]["verdict"] for lg in langs if (t, lg) in cells}
        if "fail" in verdicts.values():
            narrowed[t] = [lg for lg, v in verdicts.items() if v == "pass"]
        broken = [lg for lg, v in verdicts.items() if v not in ("pass", "fail", "unsupported")]
        if broken:
            failed_runs.append(f"{t} in {', '.join(broken)}")
    head = f"{len(passed)} of {len(tools)} tools pass the bar"
    if out:
        head += f", {len(out)} failed it ({', '.join(out)})"
    if unjudged:
        head += f", {len(unjudged)} not judged ({', '.join(unjudged)})"
    sentences = [head + "."]
    if narrowed:
        sentences += [
            f"{len(narrowed)} passed tool{'s' if len(narrowed) > 1 else ''} failed the bar in a language it was scored "
            "on, so a `tool_languages:` entry narrows it to the languages it passed in.",
            "That also drops its declared languages outside the micro projects, which the paper does not analyse.",
        ]
    else:
        sentences.append("Every passed tool passes in every language it was scored on, so no `tool_languages:` entry "
                         "follows.")  # fmt: skip
    if failed_runs:
        sentences.append(f"No verdict where the run failed, a decision by hand: {', '.join(failed_runs)}.")
    sentences.append("As a spec writes it, by hand (no code reads the verdicts):")
    block = ["```yaml", f"tools: [{', '.join(passed)}]"]
    if narrowed:
        block.append("tool_languages:")
        block += [f"  {t}: [{', '.join(lgs)}]" for t, lgs in narrowed.items()]
    block.append("```")
    return ["### Verdicts", "", *_text(*sentences), *block, ""]


def _quality(cells: dict, langs: list[str], tools: list[str]) -> list[str]:
    """Recall, precision and F1 of every scored (tool, micro project) (micro-run<i>.csv)."""

    def cell(r: dict | None) -> str:
        if r is None or r["verdict"] not in ("pass", "fail"):
            return "–" if r is not None and r["verdict"] == "unsupported" else ""
        return f"{_pct(r['recall_lenient'])} / {_pct(r.get('precision')) or '–'} / {_pct(r.get('f1'))}"

    table = [[t] + [cell(cells.get((t, lg))) for lg in langs] for t in tools]
    lines = ["### Recall, precision and F1", ""]
    lines += _text("One row per tool, one column per language, each cell `recall / precision / F1`.")
    lines += md_table(["tool", *langs], table) + [""]
    lines += _columns(
        [
            ("recall", "the calls found (lenient) out of all calls."),
            ("precision", "the correct places out of all places the tool reported in the project's source file.",
             "A place is correct on a call's lines with its family or with a name that fits no family, or on a line "
             "the ground truth accepts (a sub-use such as a digest, an import of the call's API).",
             "So a report on a decoy, on unrelated code or with the wrong family is a false one.",
             "`–`: the tool reported no place."),
            ("F1", "the harmonic mean of the two, 0 when the tool found no call."),
        ]
    )  # fmt: skip
    eg = next(((t, lg, cells[(t, lg)]) for t in tools for lg in langs if (t, lg) in cells
               and cells[(t, lg)]["verdict"] in ("pass", "fail") and cells[(t, lg)].get("precision") not in (None, "")
               and _float(cells[(t, lg)]["precision"]) < 1), None)  # fmt: skip
    if eg:
        t, lg, r = eg
        lines += _example(
            f"`{cell(r)}` for `{t}` in {lg}: it found a share of {_pct(r['recall_lenient'])} of the calls, and "
            f"{_pct(r['precision'])} of the places it reported are correct.",
            f"The two combine to an F1 of {_pct(r['f1'])}.",
        )
    return lines


def _decoys(rows: list[dict], langs: list[str], tools: list[str]) -> list[str]:
    """False positives with ground truth: the micro projects' decoys a tool reported crypto on (micro-run<i>.csv)."""
    if not any(_int(r.get("decoys")) for r in rows):
        return []
    cells = {(r["tool"], r["language"]): r for r in rows}

    def hits(r: dict | None) -> list[str] | None:
        """The decoy kinds the tool reported crypto on; None where it was not scored."""
        if r is None or r["verdict"] not in ("pass", "fail"):
            return None
        return [d for d in (r.get("decoys_hit") or "").split(";") if d]

    def cell(r: dict | None) -> str:
        hit = hits(r)
        if hit is None:
            return "–" if r is not None and r["verdict"] == "unsupported" else ""
        return f"{len(hit)}/{r['decoys']}"

    def kinds(tool: str) -> str:
        """The kinds hit, each with the languages when not hit in every language the tool was scored on."""
        scored = {lg: hits(cells.get((tool, lg))) for lg in langs}
        scored = {lg: h for lg, h in scored.items() if h is not None}
        by_kind: dict[str, list[str]] = {}
        for lg, h in scored.items():
            for d in h:
                by_kind.setdefault(d, []).append(lg)
        return ", ".join(d + ("" if len(lgs) == len(scored) else f" ({', '.join(lgs)})") for d, lgs in by_kind.items())

    table = [[t] + [cell(cells.get((t, lg))) for lg in langs] + [kinds(t)] for t in tools]
    lines = ["### Decoys", ""]
    lines += _text(
        "False positives with ground truth: none of the decoys uses cryptography, so any crypto record a tool "
        "reports on their lines is a false positive.",
        "One row per tool, one column per language.",
    )
    lines += md_table(["tool", *langs, "decoys hit"], table, "l" + "r" * len(langs) + "l") + [""]
    lines += _columns(
        [
            (" … ".join(langs) if len(langs) > 1 else langs[0], "`hit/decoys`: the decoys the tool reported crypto "
             "on, out of the project's decoys.", "`–`: the language is outside the tool's languages, so it did not "
             "run there."),
            ("decoys hit", "the kinds it reported crypto on, with the languages in brackets when not in every "
             "language it was scored on."),
        ]
    )  # fmt: skip
    eg = next(((t, lg, c, k) for t, *cs, k in table for lg, c in zip(langs, cs, strict=True)
               if "/" in c and not c.startswith("0/")), None)  # fmt: skip
    if eg:
        t, lg, c, k = eg
        lines += _example(f"`{c}` for `{t}` in {lg}: it reported crypto on {c.split('/')[0]} of the "
                          f"{c.split('/')[1]} decoys of micro-{lg}, and `decoys hit` names which ({k}).")  # fmt: skip
    lines += _background("The decoy kinds", _text(
        "Each project holds decoys in functions of their own: a comment and a log message naming algorithms, a CRC32 "
        "checksum, a Base64 encoding, a variable named after an algorithm, a hex constant that looks like a key, a "
        "URL ending in `.sha256` and a path naming a certificate file.",
        "The kinds are listed in `experiments/corpus/README.md`.",
    ))  # fmt: skip
    return lines


LOCATION_KINDS = ("line", "window", "import", "file", "none", "unread")  # analysis/schema.py:location_kind


def _places(units: list[dict], *, snapshots: bool = True) -> list[str]:
    """What kind of place each tool's algorithm records name (units-run<i>.csv `loc_<kind>`); `snapshots`: whether the
    report could read the snapshot files (traceability-run<i>.csv has `file_exists`), which says why records are
    `unread`."""
    if not units or f"loc_{LOCATION_KINDS[0]}" not in units[0]:
        return []
    table = []
    for u in units:
        total = sum(_int(u.get(f"loc_{k}")) for k in LOCATION_KINDS)
        shares = [_pct(_int(u.get(f"loc_{k}")) / total if total else None) for k in LOCATION_KINDS]
        located = total - _int(u.get("loc_none"))
        outside = _pct(_int(u.get("outside_code")) / located) if located and "outside_code" in u else ""
        table.append([u["tool"], total, *shares, outside])
    unread = sum(_int(u.get("loc_unread")) for u in units)
    lines = ["#### Places per tool", ""]
    lines += _text(
        "Where a tool's algorithm records point, as shares of its `records`, one row per tool.",
        "A tool that points at the import or a window places the same use elsewhere than a tool that points at the "
        "call, and the call-site matching window (±2 lines) absorbs only part of that.",
    )
    lines += md_table(["tool", "records", *LOCATION_KINDS, "outside code"], table) + [""]
    if unread and snapshots:
        lines += _check(CAVEAT, f"{_n(unread)} records are `unread`: their file is not in the snapshot or has no such "
                                "line (`file_exists`, `line_in_range` in `traceability-run<i>.csv`).")  # fmt: skip
    elif unread:
        lines += _check(
            CAVEAT,
            f"{_n(unread)} records are `unread`: the snapshot files are on the VM's git mirror.",
            "Run `analysis report` there, or with `--fetch` for a copied export.",
        )
    lines += _columns(
        [
            ("line", "one line of code that is not an import, in practice the call or the declaration."),
            ("window", "a span of several lines, such as CryptobomForge's 5-9-line windows."),
            ("import", "one line holding an import statement."),
            ("file", "a file without a line."),
            ("none", "no location."),
            ("unread", "one line the snapshot does not show, so its kind is unknown.",
             "Its file is not in the snapshot (a place inside an archive, `x.jar!/...`), the line lies past the "
             "file's end, or the snapshot files are not available here."),
            ("outside code", "of the records with a file, the share whose file is not a source file of the "
             "repository's language (`.java`, `.kt`, `.scala`, `.groovy`, `.py`, `.go`).",
             "These are fixtures, test vectors, configuration, documentation and other languages.",
             "The agreement analyses count these places like any other, since the tools are benchmarked on the "
             "repositories as they are.", "The share says how much of a tool's places the discussion must read in "
             "that light."),
        ]
    )  # fmt: skip
    return lines


def _units_per_repo(unit_repos: list[dict]) -> list[str]:
    """Each tool's assets / records / items per repository (units-repo-run<i>.csv), with the sums of the units table."""
    if not unit_repos:
        return []
    tools = sorted({r["tool"] for r in unit_repos})
    repos = sorted({(r["repo"], (r.get("language") or "").lower()) for r in unit_repos},
                   key=lambda x: (_lang_key(x[1] or "?"), x[0]))  # fmt: skip
    counts = ("assets", "records", "items")
    by_pair = {(r["repo"], r["tool"]): r for r in unit_repos}

    def cell(r: dict | None) -> str:
        return " / ".join(_n(_int(r[k])) for k in counts) if r else ""

    matrix = [[repo, language] + [cell(by_pair.get((repo, t))) for t in tools] for repo, language in repos]
    totals = {t: {k: sum(_int(r[k]) for r in unit_repos if r["tool"] == t) for k in counts} for t in tools}
    matrix.append(["all", ""] + [cell(totals[t]) for t in tools])
    body = _text(
        "`algorithm assets / records / items` of each tool's CBOM per repository (`units-repo-run<i>.csv`), one row "
        "per repository, one column per tool.",
        f"Empty: no ok run, which {_link('What the tools report, as written')} explains, and `all` sums each column.",
        "The matchers, the ladder and the scores compare the items one repository at a time.",
    )
    body += md_table(["repo", "language", *tools], matrix, "ll" + "r" * len(tools)) + [""]
    eg = next(((repo, t, by_pair[(repo, t)]) for repo, _ in repos for t in tools
               if (repo, t) in by_pair and _int(by_pair[(repo, t)]["records"]) > _int(by_pair[(repo, t)]["items"])),
              None)  # fmt: skip
    if eg:
        repo, t, r = eg
        body += _text(
            f"For example, `{cell(r)}` for `{t}` on `{repo}`: its CBOM has {_n(_int(r['assets']))} algorithm "
            f"components, listed at {_n(_int(r['records']))} places in all, which name {_n(_int(r['items']))} "
            "distinct algorithms."
        )
    return _details(f"{LIST} Units per repository ({_n(len(repos))})", body)


# RQ1's first background, under "What the tools report": the same findings as CycloneDX JSON, packaged three ways.
UNITS_EXAMPLE = [
    "A CycloneDX CBOM is a document with a `components` list, and every entry of that list is a **component**.",
    "The ones typed `cryptographic-asset` are the **crypto assets**.",
    "A tool that also inventories libraries writes `library` components next to them, counted under `components` only.",
    "Each asset says what it is in `cryptoProperties` (`assetType`, then `algorithmProperties` and the like) and "
    "where it was seen in `evidence.occurrences`, one entry per place.",
    "A **record** is one asset at one occurrence, and an asset without occurrences is one record without a location.",
    "An **item** is one algorithm in one repository after normalisation, however many assets and records carry it.",
    "",
    "Say a repository uses AES-256-GCM at two lines of `src/Crypto.java` and SHA-256 at a third.",
    "Tool A lists each algorithm once with its occurrences, CBOMkit's packaging:",
    "",
    "```json",
    '{ "bomFormat": "CycloneDX", "specVersion": "1.6",',
    '  "components": [',
    '    { "type": "cryptographic-asset", "name": "AES-256-GCM",',
    '      "cryptoProperties": { "assetType": "algorithm",',
    '        "algorithmProperties": { "primitive": "ae", "mode": "gcm", "parameterSetIdentifier": "256" } },',
    '      "evidence": { "occurrences": [ { "location": "src/Crypto.java", "line": 42 },',
    '                                     { "location": "src/Crypto.java", "line": 87 } ] } },',
    '    { "type": "cryptographic-asset", "name": "SHA-256",',
    '      "cryptoProperties": { "assetType": "algorithm", "algorithmProperties": { "primitive": "hash" } },',
    '      "evidence": { "occurrences": [ { "location": "src/Crypto.java", "line": 120 } ] } } ] }',
    "```",
    "",
    "Tool B writes one component per finding, CipherRadar's packaging: three `cryptographic-asset` components, "
    "`AES-256-GCM` twice with one occurrence each and `SHA-256` once.",
    "Tool C writes the two algorithms without any `evidence`, as a location-free tool does.",
    "The three documents describe the same code:",
    "",
    "| tool | components | crypto assets | records | items | file+line |",
    "|---|---:|---:|---:|---:|---:|",
    "| A, one component per algorithm | 2 | 2 | 3 | 2 | 1.00 |",
    "| B, one component per finding | 3 | 3 | 3 | 2 | 1.00 |",
    "| C, no occurrences | 2 | 2 | 2 | 2 | 0.00 |",
    "",
    "Asset and record counts differ by packaging alone, so they describe the output format, as this table does.",
    "Items are the same for all three, so every comparison of what the tools found counts items: (item, value) pairs "
    "for parameters and (item, file:line) pairs for call sites.",
]


# The packaging terms (common/tools.yaml `packaging`, hand labels; `common/tools_registry.py:PACKAGINGS`), finest to
# coarsest, with the signature each leaves in the "Units per tool" table.
PACKAGING_TERMS = [
    ("per hit", "one place", "`records` = `algorithm assets`, `several places` 0.00, `assets per item` far above 1"),
    ("per rule", "one rule match on one API, with its places",
     "places aggregated (`several places` well above 0), yet several components per algorithm"),
    ("per algorithm", "one algorithm as the tool spells it, parameters included, with all its places",
     "`assets per item` a little above 1 (1.2-2: variants such as AES-128-CBC count apart), `several places` "
     "about 0.5"),
    ("per family", "one coarse family, the specific algorithm only at the place",
     "`assets per item` below 1, many places per component"),
    ("no places", "an algorithm without any evidence", "`no location` 1.00"),
    ("unknown", "nobody has read the tool's output yet", ""),
]  # fmt: skip

# RQ1's second background, under "Units per tool": real components, trimmed, from the Java micro project's CBOMs
# (bar-micro), one per packaging, and how the columns of that table count each.
PACKAGING_EXAMPLE = [
    "Real components from the Java micro project's CBOMs, trimmed to the keys that matter here.",
    "The same source file, four ways to package it, and what each row of the table makes of them.",
    "",
    "**per algorithm**, one component per algorithm with every place as an occurrence (CBOMkit, also xgrep, SCANOSS "
    "and Q-DARPAN):",
    "",
    "```json",
    '{ "type": "cryptographic-asset", "name": "SHA-256",',
    '  "cryptoProperties": { "assetType": "algorithm", "oid": "2.16.840.1.101.3.4.2.1",',
    '    "algorithmProperties": { "primitive": "hash", "parameterSetIdentifier": "256",',
    '                             "cryptoFunctions": ["digest"] } },',
    '  "evidence": { "occurrences": [',
    '    { "location": "src/main/java/app/CryptoDemo.java", "line": 45,',
    '      "additionalContext": "java.security.Signature#getInstance(...)" },',
    '    { "location": "src/main/java/app/CryptoDemo.java", "line": 108,',
    '      "additionalContext": "javax.crypto.Cipher#getInstance(...)" },',
    '    "... 4 more" ] } }',
    "```",
    "",
    "One `algorithm` asset, six `records`, one item.",
    "So `records per asset` is 6, the asset counts under `several places`, and `assets per item` is about 1.",
    "",
    "**per hit**, one component per finding with its one occurrence (CipherRadar, also pqcheck and securin-xbom):",
    "",
    "```json",
    '{ "type": "cryptographic-asset", "name": "RSA-2048", "bom-ref": "FIND-1",',
    '  "cryptoProperties": { "assetType": "algorithm",',
    '    "algorithmProperties": { "algorithmFamily": "RSASSA-PKCS1", "primitive": "pke",',
    '                             "parameterSetIdentifier": "2048" } },',
    '  "evidence": { "occurrences": [ { "location": "src/main/java/app/CryptoDemo.java", "line": 21 } ] },',
    '  "properties": [ { "name": "ruleId", "value": "cbom-java-jca-keypairgen-rsa" } ] }',
    "```",
    "",
    "The CBOM holds six such components for RSA (`RSA` four times, `RSA-2048` twice, at four lines).",
    "That is six assets, six `records` and one item, since their `algorithmFamily` field names the same algorithm.",
    "So `records per asset` is 1 / 1, `several places` 0.00 and `assets per item` far above 1: the asset count counts "
    "rule hits, not algorithms.",
    "",
    "**per family**, one component per coarse family with the detail at the place (CryptoScan):",
    "",
    "```json",
    '{ "type": "cryptographic-asset", "name": "ECC", "bom-ref": "crypto-ecc-3",',
    '  "cryptoProperties": { "assetType": "algorithm", "algorithmProperties": { "primitive": "other" } },',
    '  "evidence": { "occurrences": [',
    '    { "location": "src/main/java/app/CryptoDemo.java", "line": 28, "symbol": "secp256r1" },',
    '    { "location": "src/main/java/app/CryptoDemo.java", "line": 44, "symbol": "secp256r1" },',
    '    { "location": "src/main/java/app/CryptoDemo.java", "line": 52, "symbol": "Ed25519" },',
    '    { "location": "src/main/java/app/CryptoDemo.java", "line": 53, "symbol": "Ed25519" } ] } }',
    "```",
    "",
    "One asset, four `records`, two items (EC P-256 and Ed25519).",
    "The `symbol` at the place outranks the component's name, so `assets per item` falls below 1.",
    "",
    "**no places** (Cryptarium):",
    "",
    "```json",
    '{ "type": "cryptographic-asset", "name": "UNKNOWN", "bom-ref": "crypto/algorithm/8a22896e949a3c61",',
    '  "cryptoProperties": { "assetType": "algorithm",',
    '    "algorithmProperties": { "primitive": "other", "cryptoFunctions": ["keygen"] } } }',
    "```",
    "",
    "One asset, one record without a location, counted under `no location`.",
    "It is no item either, since `UNKNOWN` names no algorithm (`resolution`: undefined).",
]


def _rq1(
    reported: list[str],
    units: list[dict],
    cov: list[dict],
    unit_repos: list[dict],
    trace: list[dict],
    runtime: list[dict] | None = None,
) -> list[str]:
    """RQ1: what the tools wrote (as written), how each packages its findings (units-run<i>.csv, per repository
    units-repo-run<i>.csv), what its assets' names are and which values they carry (coverage-run<i>.csv), and whether
    its locations lead to real code (traceability-run<i>.csv); one run of the original variants throughout. Last,
    what a scan costs (runtime.csv: every run of every variant)."""
    if not reported and not units and not cov and not trace and not runtime:
        return []
    lines = [RQ1.heading(), ""]
    lines += _text(
        RQ1.question,
        "First what the tools wrote, as written, then the units after normalisation, the names and values their "
        "assets carry, whether their locations hold and what a scan costs.",
    )
    lines += reported
    if units:
        table = [
            [
                u["tool"],
                u.get("packaging") or "unknown",
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
        lines += ["### Units per tool", ""]
        lines += _text(
            "How each tool packages its findings, one row per tool (`units-run<i>.csv`), summed over the repositories.",
        )
        header = ["tool", "packaging", "CBOMs", "algorithm assets", "records", "items",
                  "records per asset (median / max)", "several places", "assets per item", "no location"]  # fmt: skip
        lines += md_table(header, table, "ll" + "r" * (len(header) - 2)) + [""]
        lines += _columns(
            [
                ("packaging", "what one component of the tool is, a hand label read from its CBOMs "
                 "(`common/tools.yaml`), never derived from the numbers.",
                 "The numbers are its signature, as the background below shows."),
                ("CBOMs", "the ok runs read."),
                ("algorithm assets", "its components of asset type `algorithm`, the `algorithm` column of "
                 f"{_link('What the tools report, as written')}.",
                 "Certificates, protocols and key material are no algorithms and never become items, so this number "
                 "is below `crypto assets` wherever a tool reports those."),
                ("records", "their places, one per place a component lists.",
                 "A place listed under several components counts once for each."),
                ("items", "the distinct algorithms its records name in each repository, summed over the "
                 "repositories."),
                ("records per asset (median / max)", "the median and the maximum places per component."),
                ("several places", "the share of components listed at more than one place."),
                ("assets per item", "components per algorithm.",
                 "About 1: the tool lists each algorithm once with all its places.",
                 "Much more: it writes one component per finding, so its asset count says little about what it "
                 "found.",
                 "Below 1: a component whose places carry different algorithms counts as an item for each, since the "
                 "evidence at a place outranks the component's name."),
                ("no location", "the share of components without a file."),
            ]
        )  # fmt: skip
        used = {u.get("packaging") or "unknown" for u in units}
        terms = md_table(
            ["packaging", "one component is", "signature in this table"],
            [[t, what, sign] for t, what, sign in PACKAGING_TERMS if t in used],
            "lll",
        )
        lines += _background(
            "The packagings and their signatures",
            _text("The labels, finest to coarsest, and the signature each leaves in this table.") + terms,
        )
        lines += _background("Four packagings as the tools write them (trimmed components from the Java micro "
                             "project)", PACKAGING_EXAMPLE)  # fmt: skip
        lines += _units_per_repo(unit_repos)
        lines += _places(units, snapshots=any(r.get("file_exists") not in (None, "") for r in trace))
    if cov:
        by_tool: dict[str, dict[str, dict]] = defaultdict(dict)
        for r in cov:
            by_tool[r["tool"]][r["dimension"]] = r
        table = []
        for tool in sorted(by_tool):
            dims = by_tool[tool]
            first = next(iter(dims.values()))
            assets = _int(first["assets"])
            table.append(
                [tool, assets]
                + [_pct(_int(first[k]) / assets if assets else None) for k in RESOLUTION_SHOWN]
                + [_pct(dims.get(d, {}).get("reported_share")) for d in COVERAGE_SHOWN]
            )
        lines += ["### Names and values", ""]
        lines += _text(
            "What the tools' algorithm assets are named and which values they carry, one row per tool "
            "(`coverage-run<i>.csv`).",
            "Every share is of its `algorithm assets`, as a reader of its CBOM finds them, so a tool that writes one "
            "component per finding weighs an often-found algorithm more.",
            "The ladder and the scores compare per item instead.",
        )
        header = ["tool", "algorithm assets", *RESOLUTION_SHOWN, *[d.replace("_", " ") for d in COVERAGE_SHOWN]]
        lines += md_table(header, table, "lr" + "r" * (len(RESOLUTION_SHOWN) + len(COVERAGE_SHOWN))) + [""]
        lines += _columns(
            [
                ("algorithm assets", "the tool's components of asset type `algorithm`."),
                ("mapped", "the share whose name is an algorithm.", "Only these become items."),
                ("ambiguous", "the name is a list of candidates."),
                ("api", "the name is a code identifier, such as `Cipher.getInstance`."),
                ("non-crypto", "the name is a rule outside cryptography."),
                ("undefined", "a name nobody understands, never guessed."),
                ("key size … digest", "the share with that value given by the tool, in any field, property, name or "
                 "code context.", "Values we derive are not counted."),
            ]
        )  # fmt: skip
    return lines + _traceability(trace) + _runtime(runtime or [])


def _runtime(rows: list[dict]) -> list[str]:
    """What a scan costs (runtime.csv): time, memory and CPU per tool in one table, slowest first (the order is the
    finding), then the remaining time columns and the median per language. The limits are not reported here: they
    are ours, not the tools' (`_memory_limits`)."""
    every = sorted((r for r in rows if r["language"] == "all" and r.get("median_sec")),
                   key=lambda r: (-(_float(r["median_sec"]) or 0), r["tool"]))  # fmt: skip
    if not every:
        return []

    def ci(r: dict, low: str, high: str, fmt) -> str:
        lo, hi = _float(r.get(low)), _float(r.get(high))
        return "" if lo is None or hi is None else f"{fmt(lo)} - {fmt(hi)}"

    # the memory and CPU columns always show; `–` where runs.csv has no value (an image from before the measurement)
    header = ["tool", "repos", "median s", "95 % CI s", "p90 s", "timeouts", "median GiB", "95 % CI GiB", "p90 GiB",
              "max GiB", "median CPU s", "95 % CI CPU s", "median cores", "max cores"]  # fmt: skip
    table = []
    for r in every:
        row = [r["tool"], r["repos"], _num(r["median_sec"]), ci(r, "median_ci_low", "median_ci_high", _num),
               _num(r.get("p90_sec")), r.get("timeouts") or "0"]  # fmt: skip
        if r.get("peak_max_mb") not in (None, ""):
            row += [_gib(r.get("peak_median_mb")), ci(r, "peak_ci_low", "peak_ci_high", _gib),
                    _gib(r.get("peak_p90_mb")), _gib(r.get("peak_max_mb"))]  # fmt: skip
        else:
            row += ["–"] * 4
        if r.get("cpu_sec_median") not in (None, ""):
            row += [_num(r.get("cpu_sec_median")), ci(r, "cpu_sec_ci_low", "cpu_sec_ci_high", _num),
                    _cores(r.get("cores_median")), _cores(r.get("cores_max"))]  # fmt: skip
        else:
            row += ["–"] * 4
        table.append(row)
    unmeasured = [r["tool"] for r in every if r.get("cpu_sec_median") in (None, "")]
    lines = ["### Run time, memory and CPU", ""]
    lines += _text(
        "What a scan costs, one row per tool over every run it made, all variants and repetitions, slowest first "
        "(`runtime.csv`).",
        "The unit is the repository, so a mutated or repeated repository counts once: its time and its CPU load are "
        "the median of its ok runs, its peak memory the largest of its runs.",
    )
    lines += md_table(header, table) + [""]
    if unmeasured:
        who = "any tool" if len(unmeasured) == len(every) else ", ".join(f"`{t}`" for t in unmeasured)
        lines += _check(CAVEAT, f"CPU is not recorded for {who} here (`–`): the worker images predate that "
                                "measurement.",
                        "Rebuild the workers (`make up-prod`) and rerun to have it.")  # fmt: skip
    lines += _columns(
        [
            ("repos", "repositories with an ok run."),
            ("median s", "the median over them of the worker's wall time for the job, the clone from the local git "
             "server included."),
            ("95 % CI s", f"a bootstrap interval of that median over the repositories, resampled {RUNTIME_BOOTSTRAP} "
             f"times, only with {RUNTIME_MIN_REPOS} or more."),
            ("p90 s", "the value 90 % of the repositories stay under."),
            ("timeouts", f"the runs that hit the time limit, as in {_link('Runs')}.",
             "They have no time of their own, so the median, its CI and p90 leave them out and understate the tool "
             "on the repositories it timed out on.", "`hours` below counts them at about the limit."),
            ("median GiB … max GiB", "the peak memory with the same statistics, `max` a single run."),
            ("median CPU s … max cores", "the CPU seconds and the cores with the same statistics."),
            ("–", "not recorded in `runs.csv` (`peak_memory_mb`, `cpu_seconds`): the worker's image predates that "
             "measurement."),
        ]
    )  # fmt: skip
    lines += _background(
        "How to read memory and CPU",
        _bullets(
            [
                ("Memory (GiB)", "the peak working set of the tool's container during one job, memory in use less "
                 "inactive file cache, as `docker stats` shows it, sampled every second.",
                 "It covers the tool, any server it starts (CBOMkit's backend and Postgres) and our worker.",
                 "`runs.csv` stores it per run in MiB (`peak_memory_mb`), and a repository's peak is the largest of "
                 "its runs."),
                ("Provisioning", "read it as the RAM to provision: the median for a typical repository, p90 for nine "
                 "in ten, max for the worst seen.", "A spike shorter than a second can be missed.",
                 "A Java tool fills the heap it is given, so its peak partly reflects the heap configured for it "
                 "(`JAVA_OPTS` in `docker-compose.yml`).",
                 f"Whether a limit came near is checked in {_link('Memory limits')}."),
                ("Wall time and CPU", "`s` is wall time, the job's duration on the clock.",
                 "`CPU s` is the container's CPU time during the job, summed over its cores: the work done, waiting "
                 "not counted.", "`cores` is CPU s over wall time, the mean number of cores busy: 600 s at 2.5 "
                 "cores is 1'500 CPU s."),
                ("Cores", "about 1: effectively single-threaded, so more cores do not help but replicas do.",
                 "Clearly above 1: it parallelises.", "Below 1: it waits for the clone, a server's start or I/O."),
                ("Shared host", "no CPU limits are set and the tools run side by side, so cores are what a tool got "
                 "on a shared host, not what it could use alone.",
                 "CPU seconds, the work done, are less affected and compare tools better."),
                ("More in runtime.csv", "the spread across repositories (`spread_cv`, `peak_spread_cv`: sd / mean, "
                 "around 0 the tool needs about as much everywhere, above 1 the repository decides), the p90 and max "
                 "of CPU s, the CI and p90 of cores."),
            ],
            code=False,
        ),
    )  # fmt: skip

    more = [
        [r["tool"], _num(r.get("max_sec")), _pct(r.get("rerun_cv")), _num(r.get("hours")),
         _num(r.get("sec_per_1k_source_files")), _pct(r.get("size_rho"))]
        for r in every
    ]  # fmt: skip
    sized = any(r.get("size_rho") or r.get("sec_per_1k_source_files") for r in every)
    lines += ["#### More on time", ""]
    lines += _text("The slowest run, the run-to-run noise, the hours and how the time scales with size, per tool.")
    lines += md_table(["tool", "max s", "rerun", "hours", "s / 1k files", "ρ size"], more) + [""]
    if not sized:
        lines += _check(CAVEAT, f"The size columns are empty: they need {RUNTIME_MIN_REPOS} or more repositories "
                                "whose sizes are known and differ.", "Sizes come from the record, else from the "
                                "snapshot files (`--fetch`).")  # fmt: skip
    lines += _columns(
        [
            ("max s", "the slowest single ok run."),
            ("rerun", "the median coefficient of variation between repeated runs of one snapshot, the run-to-run "
             "noise.", "Empty without repetitions."),
            ("hours", "the summed wall time of every run, failed ones included."),
            ("s / 1k files", "the median over repositories of seconds per thousand source files of the repository's "
             f"language, only with {RUNTIME_MIN_REPOS} or more repositories whose sizes differ."),
            ("ρ size", "Spearman between a repository's time and its source files.",
             "Near 1: the time grows with the code.",
             "Near 0: something else, such as start-up or dependency resolution, dominates."),
        ]
    )  # fmt: skip
    languages = sorted({r["language"] for r in rows if r["language"] != "all"})
    if len(languages) > 1:
        by = {(r["tool"], r["language"]): r for r in rows}
        table = [
            [r["tool"]] + [
                f"{_num(c['median_sec'])} ({c['repos']})" if (c := by.get((r["tool"], lang))) and c.get("median_sec")
                else ""
                for lang in languages
            ]
            for r in every
        ]  # fmt: skip
        lines += ["#### Per language", ""]
        lines += _text("The median seconds per tool and language, with the repositories in brackets.")
        lines += md_table(["tool", *languages], table) + [""]
    return lines


def _spec_issues(conf: list[dict], tools: list[str]) -> list[str]:
    """Standard-field values the declared CycloneDX version does not allow (conformance-run<i>.csv); `none` for a
    tool without any is a finding too, so the table always shows every tool."""
    if not tools and not conf:
        return []
    issues: dict[str, list[str]] = defaultdict(list)
    for r in conf:
        unit = " CBOMs" if r["scope"] == "document" else ""
        issues[r["tool"]].append(f"{r['kind']} {_n(r['count'])}{unit}")
    lines = ["### Spec issues", ""]
    lines += _text(
        "A finer view than whole-document validity: the standard-field values that the CycloneDX version a CBOM "
        "declares does not allow, one row per tool.",
    )
    table = [[tool, ", ".join(issues.get(tool, [])) or "none"] for tool in sorted(set(tools) | set(issues))]
    lines += md_table(["tool", "spec issues"], table, "ll") + [""]
    lines += _columns(
        [
            ("spec issues", "the issues by kind, `none` when every standard-field value is allowed.",
             "`field`, `enum`, `type` and `deprecated` count assets, `dialect` and `version` count CBOMs."),
        ]
    )  # fmt: skip
    return lines


def _conformity(schema: list[dict], meaningful: list[dict], conf: list[dict], sources: list[dict]) -> list[str]:
    """RQ2: conformance, spec issues and meaningfulness per tool (analysis/schema.py), then where the information
    lives (sources-run<i>.csv)."""
    if not schema and not meaningful and not sources:
        return []
    lines = [RQ2.heading(), ""]
    lines += _text(
        RQ2.question,
        "First whether the CBOMs follow the standard (conformance), whether their standard-field values are ones the "
        "declared version allows (spec issues) and whether they fill the standard's fields (meaningful), then where "
        "the information lives.",
        f"Whether their locations lead to real code is RQ1's {_link('Traceable')}.",
    )
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
            top = ", ".join(f"{k} ({_n(n)})" for k, n in kinds.most_common(3))
            versions = ", ".join(sorted({f"{r.get('bom_format')} {r.get('spec_version')}" for r in rs}))
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
        lines += ["### Conformance", ""]
        lines += _text(
            "Every CBOM validated as a whole document against the official CycloneDX JSON schema of the version it "
            "declares (1.6 and 1.7 are vendored), one row per tool.",
        )
        header = ["tool", "CBOM by", "CBOMs", "declares", "checked", "valid", "invalid", "no schema", "top error kinds"]
        lines += md_table(header, table, "lllrrrrrl") + [""]
        lines += _columns(
            [
                ("CBOM by", "`tool`: the tool writes the document.",
                 "`BF-CBOM`: our worker assembles it (the LLM workers), so validity there measures BF-CBOM, not the "
                 "tool."),
                ("CBOMs", "its stored CBOMs."),
                ("declares", "the format and versions they declare."),
                ("checked", "the CBOMs validated."),
                ("valid", "how many passed."),
                ("invalid", "how many failed."),
                ("no schema", "another dialect or version, not validated (IBM's draft, 1.5 and older)."),
                ("top error kinds", "the most frequent schema errors, as `rule:JSON path (count)`."),
            ]
        )  # fmt: skip
        lines += _spec_issues(conf, sorted(by_tool))
    if meaningful:
        lines += ["### Meaningful", ""]
        lines += _text(
            "CycloneDX defines fields of its own for each asset type, such as `primitive`, `parameterSetIdentifier`, "
            "`mode` and `padding` for an algorithm.",
            "How often does a tool fill them, and how often does it give the same information somewhere else?",
            "One table per asset type, one row per tool, one column per field.",
        )
        example = ""
        for at in _asset_types(r["asset_type"] for r in meaningful):
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
                        if not example:
                            standard, anywhere = _pct(r["standard_share"]), _pct(r["any_share"])
                            if standard != anywhere:
                                gap = (_float(r["any_share"]) or 0) - (_float(r["standard_share"]) or 0)
                                example = (
                                    f"`{cells[-1]}` for `{t}` under `{d.replace('_', ' ')}` ({at}): a share of "
                                    f"{standard} of its {_n(r['assets'])} {at} assets carry the value in the "
                                    f"standard's field, and {anywhere} carry it somewhere.",
                                    f"So {_pct(gap)} give it only outside the field.",
                                )
                table.append([t, by[(t, dims[0])]["assets"], *cells])
            header = ["tool", "assets", *[d.replace("_", " ") for d in dims]]
            lines += _asset_type_heading(at) + md_table(header, table) + [""]
        lines += _columns(
            [
                ("assets", "the tool's `crypto assets` of that type."),
                ("A / B", "A is the share of those assets with the value in the standard's own field, B the share with "
                 "the value anywhere.", "Anywhere means that field, or the name (also what it implies: AES is a block "
                 "cipher), a property or a code symbol the normaliser reads it from.",
                 "B − A is what the tool reports only outside the standard's field."),
                ("a single number", "the normaliser reads that value nowhere else, so only the standard's field "
                 "counts."),
                ("parameter", "`parameterSetIdentifier` or a curve field: the key length of AES or RSA, the output "
                 "length of SHA-256, a curve or a named parameter set."),
            ]
        )  # fmt: skip
        if example:
            lines += _example(*example)
    lines += _sources(sources)
    return lines


SOURCE_DIMS = (
    "family",
    "primitive",
    "mode",
    "padding",
    "curve",
    "key_size",
)  # the dimensions the standard has a field for
SOURCE_KIND_TEXT = {
    "field": "a standard CycloneDX field",
    "property": "a property the tool defines",
    "name": "the component's name, parsed",
    "occurrence": "an occurrence's evidence",
    "context": "an API identifier",
    "ref": "a referenced component",
    "dependency": "a dependency",
    "derived": "implied by another value",
}


def _sources(rows: list[dict]) -> list[str]:
    """RQ2: where each tool puts each kind of information (sources-run<i>.csv): the share of its algorithm assets
    that carry the dimension in a standard field, and the other kind of place when that carries more."""
    if not rows:
        return []
    by: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    assets: dict[str, int] = {}
    for r in rows:
        by[(r["tool"], r["dimension"])][r["kind"]] = r
        assets[r["tool"]] = _int(r["assets"])
    tools = sorted(assets)
    table, example = [], ""
    for tool in tools:
        cells = []
        for d in SOURCE_DIMS:
            kinds = by.get((tool, d), {})
            if not kinds:
                cells.append("")
                continue
            field = _float(kinds["field"]["share"]) if "field" in kinds else 0.0
            others = [(k, _float(r["share"]) or 0.0) for k, r in kinds.items() if k != "field"]
            top = max(others, key=lambda kv: kv[1], default=None)
            cell = _pct(field)
            if top and top[1] > field:
                cell += f" ({top[0]} {_pct(top[1])})"
                if not example:
                    example = (
                        f"`{cell}` for `{tool}` under `{d.replace('_', ' ')}`: a share of {_pct(field)} of its "
                        f"{_n(assets[tool])} algorithm assets carry the {d.replace('_', ' ')} in a standard field.",
                        f"A share of {_pct(top[1])} carry it in {SOURCE_KIND_TEXT.get(top[0], top[0])}, which no "
                        "consumer of the standard reads as that value.",
                    )
            cells.append(cell)
        table.append([tool, assets[tool], *cells])
    lines = ["### Where the information lives", ""]
    lines += _text(
        "The same information can sit in the standard's own field, in a property the tool defines, in the "
        "component's name or only in a code identifier the normaliser recognises.",
        "One row per tool, one column per dimension (`sources-run<i>.csv`).",
        "A value in a property or a name is information the tool has but a consumer of the standard does not read, "
        "and it is what RQ3's normalisation has to recover.",
    )
    lines += md_table(["tool", "assets", *[d.replace("_", " ") for d in SOURCE_DIMS]], table) + [""]
    lines += _columns(
        [
            ("assets", "the tool's algorithm assets."),
            ("family … key size", "the share of those assets that carry the value in a standard field.",
             "When another kind of place carries it for a larger share, that kind and its share follow in brackets.",
             "Only dimensions with a field of their own have a column: the algorithm's name is the component's name "
             "by design, and the standard has no field for the digest of a composite (both are in "
             "`sources-run<i>.csv`).",
             "`family` is `algorithmFamily`, which exists from CycloneDX 1.7 on, so a 1.6 document cannot carry it."),
            *((k, v + ".") for k, v in SOURCE_KIND_TEXT.items()),
        ]
    )  # fmt: skip
    if example:
        lines += _example(*example)
    return lines


def _traceability(trace: list[dict]) -> list[str]:
    """RQ1: can a reader follow a record to the code it describes? (traceability-run<i>.csv)"""
    if not trace:
        return []
    lines: list[str] = []
    with_source = [r for r in trace if r.get("source")]
    types = _asset_types(r.get("asset_type") or "unknown" for r in trace)
    lines += ["### Traceable", ""]
    lines += _text(
        "Can a reader follow a record to the code it describes?",
        "One table per CycloneDX asset type, one row per tool, with keys and secrets in configuration files "
        "(`related-crypto-material`) apart so they do not dilute the algorithms, and library records left out.",
    )
    for at in types:
        rs_type = [r for r in trace if (r.get("asset_type") or "unknown") == at]
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
        header = ["tool", "records", "file", "file+line", "weak", "strong"]
        lines += _asset_type_heading(at) + md_table(header, table) + [""]
    if not with_source:
        lines += _check(CAVEAT, "No snapshot files were available, so `weak` and `strong` are empty.",
                        "Run the report on the VM, or `analysis report --fetch` on the laptop to fetch each pinned "
                        "commit into `~/.cache/bf-cbom/snapshots`.")  # fmt: skip
    lines += _columns(
        [
            ("records", "the tool's records of that type."),
            ("file", "the share that names a file."),
            ("file+line", "the share that names a file and a line."),
            ("weak", "of the records with a file and a line, the share whose file exists at the pinned commit and "
             "has that line.", "Empty where the snapshot files were not available."),
            ("strong", "of the same records, the share where the line or one of the two lines above or below it "
             "holds a cryptographic word: an algorithm, family, curve, identifier or API spelling of the "
             "vocabulary."),
        ]
    )  # fmt: skip
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
    lines = [f"#### {a} vs {b}, per level", ""]
    lines += _text("Do the two matchers put the same records together?",
                   "One row per level, over the records both matchers grouped.")  # fmt: skip
    header = ["level", "repos", "records", f"groups {a}", f"groups {b}", "pair Jaccard", f"{a} in one {b}",
              f"{b} in one {a}"]  # fmt: skip
    lines += md_table(header, table, "lrrrrrrr") + [""]
    lines += _columns(
        [
            ("repos", "the repositories compared."),
            ("records", "the records compared."),
            (f"groups {a}", f"how many groups {a} made of them, `groups {b}` the same for {b}."),
            ("pair Jaccard", "of all record pairs that at least one matcher puts in one group, the share both do.",
             "1.00 is the same grouping."),
            (f"{a} in one {b}", f"the share of {a}'s groups whose records all sit in a single group of {b}, the mean "
             f"over the repositories, and `{b} in one {a}` the reverse.",
             "Below 1.00: the second matcher splits those groups.",
             f"{b} holds at most one value set per tool in a group, so it splits a tool's reports of one algorithm "
             "with different values."),
        ]
    )  # fmt: skip
    return lines


MATCH_DIMS = ("key_size", "mode", "padding", "curve", "digest", "location")


def _matching(levels: list[dict], groups: list[dict], matchers: list[dict]) -> list[str]:
    """RQ4 in one section: matcher x level (items by number of voter groups, agreement per dimension), the per-language
    distribution at the scoring level, and the matchers compared per level."""
    if not levels and not groups:
        return []
    lines = ["### Agreement at L4 (matching)", ""]
    lines += _text(
        "A matcher puts the records of different tools that describe the same algorithm in one repository into one "
        "group, and how many voter groups end up together says how much the tools agree.",
        "The instrument is `exact`, the ladder's relation, and the call sites, the agreement per dimension and the "
        "per-tool rates are built on its groups.",
        "`raqun` is the cross-check in the background.",
    )
    if levels:
        lines += ["#### Matcher x level", ""]
        lines += _text("Items by the number of voter groups that agree, and the agreement per dimension, one row per "
                       "matcher and level.")  # fmt: skip
        header = ["matcher", "level", "items", "k=1", "k=2", "k=3", "k≥4", "key size", "mode", "padding", "curve",
                  "digest", "location"]  # fmt: skip
        table = []
        for r in levels:
            items = _int(r["items"])
            shares = [
                f"{_n(_int(r[c]))} ({_pct(_int(r[c]) / items)})" if items else "" for c in ("k1", "k2", "k3", "k4plus")
            ]
            cells = [r["matcher"], r["level"], items, *shares]
            for d in MATCH_DIMS:
                n = _int(r.get(f"{d}_groups"))
                cells.append(f"{_pct(r.get(f'{d}_agree'))} ({_n(n)})" if n else "")
            table.append(cells)
        lines += md_table(header, table, "ll" + "r" * (len(header) - 2)) + [""]
        lines += _headline(levels)
        lines += _check(CAVEAT, "Pairs a tool was tuned on are left out, as in the ladder and the scores.")
        lines += _columns(
            [
                ("items", "the algorithms per repository that some voter reports, the same items for both matchers "
                 "and at every level."),
                ("k=1 … k≥4", "the items by the number of voter groups in their largest group, as a count and a "
                 "share of `items`.", "`k=1`: one voter group only, unconfirmed.", "`k≥4`: four or more agree."),
                ("key size … digest", "among the groups where at least two voter groups give that parameter, the "
                 "share where all of them give the same value, with the number of such groups in brackets.",
                 "Parameters count from L2 on."),
                ("location", "among the groups where at least two voter groups give a location, the share where two "
                 "of them point at the same call site (the same file, lines within ±2).",
                 "Empty for `raqun`, which has no call sites."),
            ]
        )  # fmt: skip
        eg = next((c for c in table if c[0] == "exact" and c[1] == LEVEL_ORDER[-1]), table[-1] if table else None)
        if eg and eg[2]:
            agree = next(((d, c) for d, c in zip(MATCH_DIMS, eg[7:], strict=True) if c and d != "location"), None)
            sentences = [
                f"`{eg[3]}` under `k=1` for {eg[0]} at {eg[1]}: {eg[3].split(' ')[0]} of the {_n(eg[2])} items are "
                "reported by a single voter group.",
                f"`{eg[6]}` under `k≥4` says four or more voter groups agree on {eg[6].split(' ')[0]}.",
            ]
            if agree:
                share, n_groups = agree[1].split(" (")
                sentences.append(
                    f"`{agree[1]}` under `{agree[0].replace('_', ' ')}`: in {n_groups.rstrip(')')} groups at least two "
                    f"voter groups give a {agree[0].replace('_', ' ')}, and in {share} of them all give the same."
                )
            lines += _example(*sentences)
        lines += _background("The two matchers", _text(
            "`exact` groups the records whose names count as the same algorithm at the level, the ladder's relation.",
            "`raqun` is RaQuN, a published n-way matcher under its paper's configuration: it groups records whose sets "
            "of values are similar enough (Jaccard >= 0.75).",
            "It holds at most one value set per tool in a group, so a terse report stands alone next to a detailed "
            "one.",
            "Only its algorithm identity varies with the level, the other values are the full normalised ones "
            "throughout, and each level is matched on its own, so its counts need not grow with the level.",
        ))  # fmt: skip
        lines += _timing(levels)
    lines += _overlap(groups)
    lines += _matchers(matchers)
    return lines


def _timing(levels: list[dict]) -> list[str]:
    """The matchers' wall time per level, summed over the repositories (`seconds` in matchlevels-run<i>.csv)."""
    by = {(r["matcher"], r["level"]): _float(r.get("seconds")) for r in levels}
    names = {"exact": "exact matcher", "raqun": "RaQuN"}
    matchers = [m for m in names if any(by.get((m, lv)) is not None for lv in LEVEL_ORDER)]
    if not matchers:
        return []
    table = [
        [names[m], *(f"{by[(m, lv)]:.2f}" if by.get((m, lv)) is not None else "" for lv in LEVEL_ORDER)]
        for m in matchers
    ]
    lines = ["#### Matching time", ""]
    lines += _text(
        f"Seconds per matcher and level, summed over the {_n(_int(levels[0]['repos']))} repositories.",
        "A level is one clustering for the exact matcher and one n-way matching for RaQuN.",
    )
    lines += md_table(["matcher", *LEVEL_ORDER], table, "l" + "r" * len(LEVEL_ORDER)) + [""]
    lines += _background("How the time is measured", _text(
        "The report measures around each matcher's call alone.",
        "The vocabulary load and PyQuN's imports are outside the clock, and the process pool does not change the sum.",
    ))  # fmt: skip
    return lines


def _headline(levels: list[dict]) -> list[str]:
    """RQ4's headline: the share of items at least two voter groups agree on at the top level, the exact matcher on
    the algorithm; RaQuN's share on whole findings follows as the cross-check (matchlevels-run<i>.csv)."""
    top = {r["matcher"]: r for r in levels if r["level"] == LEVEL_ORDER[-1] and _int(r["items"])}
    if "exact" not in top:
        return []

    def confirmed(r: dict) -> float:
        return round(1 - _int(r["k1"]) / _int(r["items"]), 2)

    ex, n = confirmed(top["exact"]), _n(_int(top["exact"]["items"]))
    sentences = [
        f"**Headline at {LEVEL_ORDER[-1]}**: of the {n} items, another voter group reports the same algorithm for a "
        f"share of {_pct(ex)} (the exact matcher)."
    ]
    if "raqun" not in top:
        return _text(*sentences, "RaQuN was not run (`--no-raqun`).")
    rq = confirmed(top["raqun"])
    sentences.append(f"Cross-check: when a group must be the same finding with the same details, RaQuN confirms "
                     f"{_pct(rq)}.")  # fmt: skip
    if ex > rq:
        sentences.append(f"The {_pct(ex - rq)} in between is agreement that breaks on the details.")
    return _text(*sentences)


def _scores(rows: list[dict], loo: list[dict], grouping: list[dict] | None = None) -> list[str]:
    """RQ4: the per-tool rates per dimension (analysis/score.py); the orders are sensitivity rows."""
    if not rows:
        return []
    level = rows[0].get("level") or "?"
    lines = ["### Per-tool rates", ""]
    lines += _text(
        "Without a reference CBOM, a tool is scored by how much of what it reports other, independent tools report "
        f"too (the exact matcher's groups at {level}).",
        "One table per dimension (`algorithm`, then the parameters and `location`), one row per tool, in the order of "
        "its rate.",
        "The checks after the tables (leave one voter out, other voter groupings) ask whether the order itself is "
        "stable.",
    )
    example: tuple = ()  # one worked reading, from the first table with an interval
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
                r["tool"],
                r["voter_group"],
                r.get("repos", ""),
                r["items"],
                f"{_pct(r['rate'])}{ci}",
                _pct(r["rate_pooled"]),
                _pct(r["exclusivity"]),
                _pct(r["coverage"]) if r.get("coverage") not in (None, "") else "",
            ]
            if dim == "algorithm":
                row += [_pct(r["ds_support"]), f"{_pct(r['ds_sensitivity'])} / {_pct(r['ds_specificity'])}"]
            table.append(row)
            if not example and ci and _pct(r["ci_low"]) != _pct(r["ci_high"]):  # an interval with a width
                example = (
                    f"`{row[5]}` for `{r['tool']}` ({dim}): in the average repository a share of {_pct(r['rate'])} of "
                    "its items are reported by another voter group too.",
                    f"Resampling the repositories puts that average between {_pct(r['ci_low'])} and "
                    f"{_pct(r['ci_high'])}.",
                    f"Over all its {_n(r['items'])} items at once the share is {_pct(r['rate_pooled'])}, and no other "
                    f"voter group reports {_pct(r['exclusivity'])} of them at all.",
                )
        header = ["rank", "tool", "voter group", "repos", "items", "rate [95 % CI]", "pooled", "exclusivity",
                  "coverage"]  # fmt: skip
        if dim == "algorithm":
            header += ["DS support", "DS sens. / spec."]
        lines += [f"#### {dim}", ""] + md_table(header, table, "rllrrrrrr" + "rr" * (dim == "algorithm")) + [""]
    lines += _check(CAVEAT, "The order is no ranking claim: a tool that reports little and only the obvious has a "
                            "high rate, so `coverage` stands next to it.", "The ranking by F1 is RQ5's.")  # fmt: skip
    lines += _check(CAVEAT, "Pairs a tool was tuned on are left out, and tools of one voter group never confirm each "
                            "other.")  # fmt: skip
    lines += _columns(
        [
            ("rank", "the position in that order, only for tools with items in at least 3 repositories and at least "
             "10 items in total.", "`–`: too little, and then no interval either."),
            ("repos", "where the tool reports."),
            ("items", "what it reports.",
             "For `algorithm` an item is an algorithm in a repository, for a parameter (algorithm, value) such as "
             "(AES, key size 256), for `location` (algorithm, call site)."),
            ("rate [95 % CI]", "the share of the tool's items that another voter group reports too, for a parameter "
             "the same value, for `location` the same call site.",
             "It is computed per repository and then averaged over the repositories, so one large repository does "
             "not decide it.",
             "The brackets are a 95 % bootstrap confidence interval: the repositories resampled 200 times, the range "
             "that holds the middle 95 % of the averages.", "A wide range means few repositories or very different "
             "ones."),
            ("pooled", "the same share over all the tool's items at once, so large repositories weigh more."),
            ("exclusivity", "the share of its items no other voter group reports at all (pooled).",
             "Agreement cannot tell whether these are findings the others miss or false positives, RQ5 can."),
            ("coverage", "of the repository's majority items (those at least half of the voter groups that ran there "
             "report), the share the tool reports, averaged over the repositories.",
             "A recall proxy next to `rate`'s precision proxy.",
             "Empty for `location`, where call sites are compared by window, not by equality."),
            ("DS support", "Dawid-Skene's likelihood that an item is real, averaged over the tool's items "
             "(algorithm only)."),
            ("DS sens. / spec.", "Dawid-Skene's estimate of how often the tool reports an item that is there "
             "(sensitivity) and stays silent on one that is not (specificity)."),
        ]
    )  # fmt: skip
    if example:
        lines += _example(*example)
    lines += _background("Dawid-Skene", _text(
        "Dawid-Skene is a second model of the same agreement.",
        "From the pattern of who reports what, it estimates for each voter how often it reports an item that is there "
        "and stays silent on one that is not, and for each item how likely it is real.",
    ))  # fmt: skip
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
            lines += ["#### Leave one voter out", ""]
            lines += _text(
                "Does the ranking depend on one voter?",
                "Each voter group is left out in turn and the tools ranked again, one row per dimension.",
            )
            lines += md_table(["dimension", "lowest tau", "mean tau"], table, "lll") + [""]
            lines += _columns(
                [
                    ("lowest tau", "Kendall's tau between that ranking and the full one, with the voter group whose "
                     "removal changes the order most.", "1 is the same order, 0 unrelated, -1 reversed."),
                    ("mean tau", "the mean over all removals."),
                ]
            )  # fmt: skip
    return lines + _groupings(grouping or [])


def _groupings(rows: list[dict]) -> list[str]:
    """The ranking under other voter groupings (grouping-run<i>.csv): the sensitivity check of the voter groups."""
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
    lines = ["#### Other voter groupings", ""]
    lines += _text(
        "Does the ranking depend on how tools are grouped into voters?",
        "One row per dimension, one column per alternative grouping (`grouping-run<i>.csv`).",
        "The matcher's groups stay the same, so a change is the grouping's effect alone.",
    )
    lines += md_table(["dimension", *names], table, "l" + "l" * len(names)) + [""]
    if algo and algo.get("ranking"):
        lines += _text(f"Algorithm ranking with every tool its own voter: {algo['ranking'].replace(';', ', ')}.")
    labels = {
        "per-tool": "every tool its own voter, so tools sharing their detection logic then confirm each other.",
        "llm-merged": "the LLM workers as one voter.",
        "engine-merged": "the tools of one pattern engine family as one voter (semgrep and its fork opengrep), "
                         "whatever their rules.",
    }  # fmt: skip
    series = f"{names[0]} … {names[-1]}" if len(names) > 1 else names[0]
    lines += _columns(
        [(series, "Kendall's tau between the ranking under the declared voter groups and under that grouping (1 is "
                  "the same order), with the number of voter groups it has."),
         *((g, labels[g]) for g in names if g in labels)]
    )  # fmt: skip
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
                _pct(statistics.mean(_float(r.get("flux")) or 0 for r in rs)),
                _pct(sum(1 for r in rs if (_float(r.get("flux")) or 0) > 0) / len(rs)),
            ]
        )
    lines = [f"## {STABILITY}", ""]
    lines += _text(
        *STABILITY_NOTE,
        "Does a tool report the same when it runs again on the same code?",
        "One row per tool, over the repositories it ran on more than once (`stability.csv`).",
    )
    lines += md_table(
        ["tool", "repos", "Jaccard mean", "Jaccard min", "records CV", "unstable items", "flux", "repos changed"], table
    )
    lines += [""] + _check(CAVEAT, "Where runs were repeated, the scores use each tool's consensus: the items present "
                                   "in more than half of its runs.")  # fmt: skip
    lines += _columns(
        [
            ("Jaccard mean", "for each pair of runs on a repository, the items both report divided by the items either "
             "reports (1.00 = identical), averaged per repository, then over the repositories."),
            ("Jaccard min", "the lowest pair of runs anywhere."),
            ("records CV", "how much the number of records varies between runs (standard deviation / mean, 0 = always "
             "the same count)."),
            ("unstable items", "items that appear in some runs but not in most."),
            ("flux", "the mean share of a tool's items on a repository that are not in every run (0.00 = every run "
             "reports the same items).",
             "With `repos changed` it is the noise floor RQ5 reads its changes off the edit against (`report "
             "--baseline <this experiment>`)."),
            ("repos changed", "the share of its repositories where the runs differ at all."),
        ]
    )  # fmt: skip
    return lines


MUTANT_PATCHES = 12  # patches shown in the summary (all are in mutants.csv and mutants.json)


def _mutation(
    mutants: list[dict], rows: list[dict], rates: list[dict], scores: list[dict] | None = None,
    bridge: list[dict] | None = None,
) -> list[str]:  # fmt: skip
    if not mutants:
        return []
    calls = [m for m in mutants if m.get("kind") != "decoy"]
    decoys = [m for m in mutants if m.get("kind") == "decoy"]
    rows = [r for r in rows if r.get("kind") != "decoy"]  # the detection table judges the planted calls
    styles = list(dict.fromkeys(m["style"] for m in calls))
    by_lang: dict[str, int] = defaultdict(int)
    for m in mutants:
        by_lang[m["language"]] += 1
    per_lang = ", ".join(
        f"{lang} {_n(n)}" for lang, n in sorted(by_lang.items(), key=lambda kv: _lang_key(kv[0] or ""))
    )
    lines = [RQ5.heading(), ""]
    lines += _text(
        RQ5.question,
        "A mutant is a copy of a repository with one known piece of code inserted, so here there is ground truth.",
        f"{_n(len(mutants))} mutants in {_n(len({m['repo'] for m in mutants}))} repositories ({per_lang}): "
        f"{_n(len(calls))} planted calls and {_n(len(decoys))} planted decoys (`mutants.csv`).",
    )
    lines += _background("What is planted", _bullets(
        [
            ("Planted call", "one snippet of the catalog (`mutation/catalog/`), inserted into a first-party function "
             "that has a caller, in one style.",
             "`direct` names the algorithm in the call, `indirect` passes its name through a variable, `wrapper` "
             "puts the call into a helper."),
            ("Planted decoy", "code that names or resembles cryptography without using it.",
             "A comment or a log message naming algorithms, a CRC32 checksum, a Base64 encoding, a variable named "
             "after an algorithm, a hex constant that looks like a key, a URL ending in `.sha256` or a path naming a "
             "certificate file."),
            ("New records only", "only records that the tool's run on the original does not have count."),
        ],
        code=False,
    ))  # fmt: skip
    lines += ["### Planted calls found", ""]
    lines += _text("Does the tool report the inserted call?", "One row per language and tool.")
    cell = {(r["tool"], r["language"], r["style"], r["dimension"]): r for r in rates}
    pairs = sorted({(r["tool"], r["language"]) for r in rows}, key=lambda tl: (_lang_key(tl[1] or ""), tl[0]))
    table = []
    for tool, lang in pairs:

        def show(style: str, dim: str, tool: str = tool, lang: str = lang) -> str:
            r = cell.get((tool, lang, style, dim))
            return f"{_pct(r['rate'])} ({_n(r['detected'])}/{_n(r['mutants'])})" if r else ""

        ok = [r for r in rows if (r["tool"], r["language"]) == (tool, lang) and r.get("status") == "ok"]
        masked = sum(_true(r.get("masked")) for r in ok)
        table.append(
            [lang, tool]
            + [show(st, "found_site") for st in styles]
            + [show("all", "found_span"), show("all", "found_repo"), masked, len(ok)]
        )
    header = ["language", "tool"] + [f"site, {st}" for st in styles]
    header += ["span, all", "repository, all", "masked", "judged"]
    lines += md_table(header, table, "ll" + "r" * (len(header) - 2)) + [""]
    failed = Counter(r.get("status") or "missing" for r in rows if r.get("status") != "ok")
    if failed:
        why = ", ".join(
            f"{_n(n)} {'unsupported (the language is outside the tool’s)' if st == 'unsupported' else st}"
            for st, n in sorted(failed.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        lines += _check(CAVEAT, f"Not judged: {_n(sum(failed.values()))} planted call × tool, by the status of the "
                                f"run on the mutant: {why}.", "`original <status>` is the original's, and `status` "
                                "in `mutation.csv` has each.")  # fmt: skip
    lines += _columns(
        [
            ("site, <style>", "mutants of that style where a new record of the expected algorithm appears at the "
             "line naming it (±2 lines)."),
            ("span, all", "the same anywhere on the inserted lines, imports included, all styles together."),
            ("repository, all", "the algorithm appears in the tool's results for the mutant and was not in them for "
             "the original, wherever it is.", "Empty when every mutant is `masked`."),
            ("masked", "mutants whose original already has the algorithm, so the repository level cannot tell."),
            ("judged", "mutants with an ok run on both the mutant and the original."),
            ("rate (detected/judged)", "a cell: the rate is the mean of the per-repository rates "
             "(`mutation-rates.csv`), so with several repositories it can differ from detected/judged.",
             "With repeated runs a mutant counts as detected when most runs detect it.",
             "Per mutant and tool: `mutation.csv`."),
        ]
    )  # fmt: skip
    eg = next(((row, st, c) for row in table for st, c in zip(styles, row[2:], strict=False) if c), None)
    if eg:
        row, st, c = eg
        detected, judged = c.split("(")[1].rstrip(")").split("/")
        lines += _example(
            f"`{c}` under `site, {st}` for `{row[1]}` in {row[0]}: `{row[1]}` reported the inserted algorithm at the "
            f"inserted call in {detected} of the {judged} judged {st} mutant(s)."
        )
    lines += _mutation_scores(scores or [])
    lines += _offedit(scores or [])
    lines += _bridge(bridge or [])
    patches: list[str] = []
    for m in mutants[:MUTANT_PATCHES]:
        patches += [f"`{m['variant']}` in `{m['repo']}`, `{m['file']}:{m['call_line']}` ({m['host']}):", ""]
        patches += ["```diff", m.get("patch") or "", "```", ""]
    shown = min(MUTANT_PATCHES, len(mutants))
    lines += _details(f"{LIST} Mutant patches (the first {shown} of {_n(len(mutants))})", patches)
    return lines


def _mutation_scores(scores: list[dict]) -> list[str]:
    """Recall, precision and F1 on the planted items (mutation-scores.csv), style `all`."""
    rows = [s for s in scores if s.get("style") == "all"]
    if not rows:
        return []
    lines = ["### Recall, precision and F1 on the planted items", ""]
    lines += _text(
        "The planted calls are the positives, the planted decoys the negatives, both known exactly, so recall, "
        "precision and F1 are real here.",
        "One row per language and tool, pooled over its judged mutants (`mutation-scores.csv`).",
    )
    no_decoys = not any(_int(s.get("decoys")) for s in rows)

    def ci(v, lo, hi) -> str:
        if v is None:
            return ""
        text = _pct(v)
        return f"{text} [{_pct(lo)}, {_pct(hi)}]" if lo is not None and hi is not None else text

    def f1(v, lo, hi) -> str:
        if v is None or v == "":
            return ""
        text = f"{_float(v):.2f}"
        return f"{text} [{_float(lo):.2f}, {_float(hi):.2f}]" if lo not in (None, "") and hi not in (None, "") else text

    table = []
    for s in sorted(rows, key=lambda s: (_lang_key(s["language"] or ""), s["tool"])):
        table.append(
            [
                s["language"],
                s["tool"],
                s["calls"],
                ci(_float(s["recall_site"]), _float(s.get("recall_low")), _float(s.get("recall_high"))),
                _pct(s["recall_group"]),
                s["decoys"],
                s["decoys_hit"],
                _pct(s["precision"]) if s.get("precision") not in (None, "") else "",
                f1(s.get("f1"), s.get("f1_low"), s.get("f1_high")),
            ]
        )
    header = ["language", "tool", "calls", "recall site [95 % CI]", "recall group", "decoys", "hit", "precision",
              "F1 [95 % CI]"]  # fmt: skip
    lines += md_table(header, table, "ll" + "r" * (len(header) - 2)) + [""]
    if no_decoys:
        lines += _check(CAVEAT, "No decoys were planted in this experiment (`decoys: 0`, or mutants from before "
                                "2026-10-22), so precision and F1 are empty: recall only.")  # fmt: skip
    lines += _check(CAVEAT, "The planted calls are plain uses of the catalog's APIs, and the negatives are the eight "
                            "decoy kinds.")  # fmt: skip
    lines += _columns(
        [
            ("calls", "judged planted calls, with an ok run on the mutant and on the original."),
            ("recall site [95 % CI]", "the share of the calls with a new record of the expected algorithm at the "
             "inserted call (±2 lines), all styles together, with the 95 % bootstrap interval over repositories.",
             "An interval only where the support rule of the scores holds (3 repositories, 10 calls)."),
            ("recall group", "the same leniently: a record of the algorithm's group."),
            ("decoys", "judged planted decoys."),
            ("hit", "decoys with any new crypto record on their inserted lines."),
            ("precision", "calls found / (calls found + decoys hit).", "Empty when no decoys were planted."),
            ("F1 [95 % CI]", "the harmonic mean of recall site and precision, with its interval.",
             "Empty when no decoys were planted."),
        ]
    )  # fmt: skip
    eg = next((row for row in table if row[8]), None) or next((row for row in table if row[3]), None)
    if eg:
        if eg[8]:
            lines += _example(
                f"F1 `{eg[8]}` for `{eg[1]}` in {eg[0]}: it found {eg[3].split(' ')[0]} of its {eg[2]} planted calls "
                f"at the call and reported {eg[6]} of the {eg[5]} planted decoys.",
                f"So its precision over the planted items is {eg[7] or 'undefined'}.",
            )
        else:
            lines += _example(f"`{eg[1]}` found {eg[3].split(' ')[0]} of its {eg[2]} planted calls in {eg[0]} at the "
                              "inserted call.")  # fmt: skip
    return lines


def _offedit(scores: list[dict]) -> list[str]:
    """RQ5's side finding: the changes a tool reports outside the inserted lines (mutation-scores.csv, style `all`),
    read against its run-to-run noise floor from a stability experiment (`--baseline`)."""
    rows = [s for s in scores if s.get("style") == "all" and s.get("offedit_share") not in (None, "")]
    if not rows:
        return []
    lines = ["### Changes off the edit", ""]
    lines += _text(
        "A mutant differs from its original by one inserted piece of code, so everything else a tool reports should "
        "stay the same.",
        "It does not always: one row per language and tool over its judged mutants, calls and decoys together "
        "(`mutation-scores.csv`).",
    )
    table = []
    for s in sorted(rows, key=lambda s: (_lang_key(s["language"] or ""), s["tool"])):
        judged = _int(s.get("calls")) + _int(s.get("decoys"))
        floor = ""
        if s.get("baseline_changed") not in (None, ""):
            floor = f"{_pct(s['baseline_changed'])} ({_pct(s.get('baseline_flux'))})"
        table.append(
            [
                s["language"],
                s["tool"],
                judged,
                s["offedit_mutants"],
                _pct(s["offedit_share"]),
                _num(s.get("extra_units_median")),
                _num(s.get("extra_sites_median")),
                floor,
            ]
        )
    header = ["language", "tool", "mutants", "with changes", "share", "extra units", "extra sites", "noise floor"]
    lines += md_table(header, table, "ll" + "r" * 6) + [""]
    lines += _check(CAVEAT, "This is a finding about the tools, never folded into recall, precision or F1.")
    lines += _columns(
        [
            ("mutants", "the judged mutants, calls and decoys."),
            ("with changes", "mutants where the tool's report of the mutant has a new algorithm (`extra_units`, "
             "besides the planted one and what it implies) or a new located finding outside the inserted lines "
             "(`extra_sites`).", "The original's findings moved by the insertion are not new.",
             "Lost findings are in `mutation.csv` (`lost_units`, `lost_sites`)."),
            ("share", "their share of the judged mutants."),
            ("extra units", "the median over the mutants with changes, so how much changes when something does.",
             "Empty without any."),
            ("extra sites", "the same for located findings.", "A tool without locations has no sites, so it counts "
             "units only."),
            ("noise floor", "the same tool on unchanged code in a stability experiment (`--baseline`): the share of "
             "repositories whose repeated runs differ at all.",
             "In brackets the mean share of its items that come and go between runs (`flux` in that experiment's "
             "`stability.csv`).", "Empty without a baseline."),
        ]
    )  # fmt: skip
    eg = next((row for row in table if _int(row[3])), None) or table[0]
    lines += _example(
        f"`{eg[4]}` for `{eg[1]}` in {eg[0]}: of its {_n(eg[2])} judged mutants, {_n(eg[3])} came back with something "
        "new outside the inserted lines.",
        f"On unchanged code a share of {eg[7].split(' ')[0]} of its repositories vary between runs." if eg[7] else "",
    )
    lines += _background("Reading it against the noise floor", _text(
        "A share of changes near the floor is the tool's own non-determinism.",
        "One far above it is an effect of the edit on untouched code: a changed analysis, a different ranking of "
        "files, a time limit hit elsewhere.",
    ))  # fmt: skip
    return lines


def _bridge(bridge: list[dict]) -> list[str]:
    """RQ5 -> RQ4 (bridge-run<i>.csv): confirmation rate against F1, per language and over all cells."""
    summary = [r for r in bridge if r.get("tool") == "all"]
    if not summary:
        return []
    lines = ["### Does agreement predict detection? (the bridge to RQ4)", ""]
    lines += _text(
        "Per language, each tool's confirmation rate on this experiment's original repositories is set against its F1 "
        "on the planted items (`bridge-run<i>.csv`, one cell per tool and language).",
        "The confirmation rate is RQ4's: the share of its algorithm items at L4 that another voter group confirms.",
    )

    def coef(v) -> str:
        return _num(v) if v not in (None, "") else ""

    table = [[r["language"], r["n"], coef(r.get("spearman")), coef(r.get("kendall_tau"))] for r in summary]
    lines += md_table(["language", "cells", "Spearman", "Kendall τ"], table, "lrrr") + [""]
    lines += _columns(
        [
            ("language", "`all` pools the cells of every language."),
            ("cells", "the tool and language cells compared."),
            ("Spearman", "the rank correlation over the cells."),
            ("Kendall τ", "between the two rankings, 1 the same order, 0 unrelated, −1 reversed.",
             "Empty with fewer than three cells."),
        ]
    )  # fmt: skip
    eg = next((row for row in table if row[2]), None)
    if eg:
        lines += _example(
            f"Spearman `{eg[2]}` for {eg[0]}: over its {eg[1]} tools, the order by confirmation rate and the order by "
            "F1 agree to that degree, and 1 would be the same ranking."
        )
    return lines


def _identity_of(mapped: str) -> str | None:
    """The L4 identity a vocab row maps to: its `algorithm=`, else its `family=` (none for a functions-only row)."""
    parts = dict(p.split("=", 1) for p in (mapped or "").split(";") if "=" in p)
    return parts.get("algorithm") or parts.get("family") or None


def _spellings(vocab: list[dict]) -> list[str]:
    """RQ3: how many distinct spellings each tool uses per identity (vocab.csv), and the identities spelled the most
    ways across tools: the representational heterogeneity in names that L1-L4 of the ladder fold."""
    by_tool: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for r in vocab:
        if r.get("status") != "mapped" or (r.get("asset_type") or "algorithm") != "algorithm":
            continue  # a certificate or key named after its file is a spelling of the file, not of the algorithm
        ident = _identity_of(r.get("mapped") or "")
        if ident:
            by_tool[r["tool"]][ident].add(r["raw"])
    if not by_tool:
        return []
    lines = ["#### Spellings per identity", ""]
    lines += _text(
        "The same algorithm written in different ways, one row per tool (`vocab.csv`: every raw value a tool wrote, "
        "with the identity it mapped to).",
        "Only algorithm assets count, or records without an asset type: a certificate or key named after its file "
        "(`CA_ecdsa.pem`) is a spelling of the file, not of ECDSA (`asset_type` in `vocab.csv`).",
    )
    table = []
    for tool in sorted(by_tool):
        idents = by_tool[tool]
        spellings = sum(len(s) for s in idents.values())
        ident, raws = max(idents.items(), key=lambda kv: (len(kv[1]), kv[0]))
        shown = ", ".join(f"`{r}`" for r in sorted(raws)[:4]) + ("…" if len(raws) > 4 else "")  # as the tool wrote
        table.append(
            [tool, len(idents), spellings, f"{spellings / len(idents):.2f}", f"{ident} ({len(raws)}: {shown})"]
        )
    header = ["tool", "identities", "spellings", "per identity", "most spelled identity (spellings)"]
    lines += md_table(header, table, "lrrrl") + [""]
    across: dict[str, set[str]] = defaultdict(set)
    tools_of: dict[str, set[str]] = defaultdict(set)
    for tool, idents in by_tool.items():
        for ident, raws in idents.items():
            across[ident] |= raws
            tools_of[ident].add(tool)
    top = sorted(across.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:5]
    most = ", ".join(f"`{i}` {len(s)} spellings by {len(tools_of[i])} tools" for i, s in top)
    lines += _text(f"Across tools, the identities spelled the most ways: {most}.")
    lines += _columns(
        [
            ("identities", "the distinct algorithms or families its mapped values name."),
            ("spellings", "the distinct raw values behind them."),
            ("per identity", "their ratio, 1.00 is one spelling per algorithm."),
            ("most spelled identity (spellings)", "the identity it spells the most ways, with the spellings."),
        ]
    )  # fmt: skip
    first = table[0]
    lines += _example(
        f"{first[4]} for `{first[0]}`: among its {first[1]} identities, `{first[4].split(' (')[0]}` is the one it "
        "writes in the most ways.",
        "Every such spelling is one name the ladder has to fold before two reports count as the same algorithm.",
    )
    return lines


def _normalisation(items: list[dict], registry: list[dict], levels: list[dict], vocab: list[dict]) -> list[str]:
    """RQ3: the registry and what it lacks (items-run<i>.csv `registry`, registry-run<i>.csv), the L0-L4 ladder
    (levels-run<i>.csv) and the spellings per identity (vocab.csv)."""
    body = _registry(items, registry) + _ladder(levels) + _spellings(vocab)
    if not body:
        return []
    intro = _text(
        RQ3.question,
        "First the CycloneDX registry as the base vocabulary and the families that had to be added to it.",
        "Then the deltas L0 -> L4 on fixed items, the ladder: the gap is representational heterogeneity, what remains "
        "at L4 is substantive.",
        "Then the spellings behind it.",
    )
    return [RQ3.heading(), "", *intro] + body


GAP_TEXT = {
    "missing-family": "a scheme the registry has no entry for",
    "scheme-less": "a key type or use without a scheme, which the registry names only as schemes",
    "generic-marker": "a name that covers several registry entries and attaches to them without merging them",
    "non-crypto": "a non-cryptographic thing tools report as crypto",
}


def _registry(items: list[dict], registry: list[dict]) -> list[str]:
    """RQ3: the share of items that rest on a family the CycloneDX registry lacks (`registry` in items-run<i>.csv) and
    the complete delta (registry-run<i>.csv): every addition with its gap kind and the items behind it, the units no
    vocabulary names, and the size of the identifier layer."""
    if not items and not registry:
        return []
    lines = ["### The registry and what it lacks", ""]
    lines += _text(
        "The vocabulary's canonical level is the CycloneDX 1.7 cryptography registry (`algorithmFamily`), and L2 of "
        "the ladder normalises to it alone.",
        "Real CBOMs name things the registry has no entry for, so `algorithms.yaml` adds those families (`additions:`, "
        "each with the kind of gap and its reason).",
        "Every item records whether its family is the registry's (`registry` in `items-run<i>.csv`), one row per tool.",
    )
    if items:
        by_tool: dict[str, list[dict]] = defaultdict(list)
        for r in items:
            by_tool[r["tool"]].append(r)
        table = []
        for tool in [*sorted(by_tool), "all"]:
            rs = items if tool == "all" else by_tool[tool]
            off = [r for r in rs if _false(r.get("registry"))]
            top = Counter(r["unit"] for r in off).most_common(3)
            table.append(
                [
                    tool,
                    len(rs),
                    len(off),
                    _pct(len(off) / len(rs)) if rs else "",
                    ", ".join(f"{u} ({_n(n)})" for u, n in top),
                ]
            )
        lines += md_table(["tool", "items", "on additions", "share", "most used additions (items)"], table, "lrrrl")
        lines += [""] + _columns(
            [
                ("items", "the tool's items."),
                ("on additions", "how many rest on a family `algorithms.yaml` adds."),
                ("share", "their share of the items."),
                ("most used additions (items)", "the additions they rest on the most."),
            ]
        )  # fmt: skip
        all_row = table[-1]
        lines += _example(
            f"`{all_row[3]}` for `all`: of the {_n(all_row[1])} items the tools report, {_n(all_row[2])} rest on a "
            "family the registry does not name.",
            "A normalisation on the registry alone would leave them without an identity.",
        )
    additions = [r for r in registry if r["kind"] == "addition"]
    if additions:
        lines += ["#### The delta", ""]
        lines += _text(
            "Every family added to the registry's (`registry-run<i>.csv`), grouped by the kind of gap, with the items, "
            "repositories and tools of this experiment that rest on it and the reason it is an addition.",
        )
        table = []
        for r in additions:
            reason = (r.get("note") or "").replace("; ", ", ")  # the vocabulary's own text, as one phrase
            table.append([r["gap"] or "", r["name"], r["primitive"] or "", _int(r["count"]), _int(r["repos"]),
                          (r.get("tools") or "").replace(";", ", "), reason])  # fmt: skip
        lines += md_table(["gap", "family", "primitive", "items", "repos", "tools", "reason"], table, "lllrrll")
        lines += [""] + _columns(
            [
                ("gap", "the kind of gap.",
                 *(f"- `{k}`: {v}." for k, v in GAP_TEXT.items() if any(r["gap"] == k for r in additions))),
                ("items", "the items of this experiment that rest on it.",
                 "0: needed by another experiment or by the micro projects."),
                ("repos", "the repositories behind them."),
                ("tools", "the tools behind them."),
                ("reason", "why it is an addition."),
            ]
        )  # fmt: skip
    unknown = [r for r in registry if r["kind"] == "unknown"]
    layers = [r for r in registry if r["kind"] == "layer"]
    beyond = []
    if unknown:
        shown = ", ".join(f"`{r['name']}` ({_n(_int(r['count']))}, {r['tools'].replace(';', ', ')})" for r in unknown)
        beyond += _text(
            f"Units under no family at all: {shown}.",
            "Each is a tool's field value taken as written, never checked or dropped, or an algorithm the vocabulary "
            "lists under no family.",
        )
    if layers:
        sizes = ", ".join(f"{r['name']} {_n(_int(r['count']))}" for r in layers)
        beyond += _text(
            "Below the families the registry has no identifier layer at all.",
            "The spellings, whole names (JOSE, SSH, JCA), API identifiers and OIDs that L2-L4 read come from the "
            f"vocabulary's own tables: {sizes} entries (`analysis/data/algorithms.yaml`).",
        )
    lines += _background("Beyond the families", beyond)
    return lines


def _agreement(info: dict, matchlevels: list[dict], groups: list[dict], matchers: list[dict], scores: list[dict],
               loo: list[dict], grouping: list[dict]) -> list[str]:  # fmt: skip
    """RQ4: the voter groups, the agreement at L4 (matching) and the per-tool rates."""
    body = _voters(info) + _matching(matchlevels, groups, matchers) + _scores(scores, loo, grouping)
    if not body:
        return []
    intro = _text(
        RQ4.question,
        "The agreement at L4 by the exact matcher, which also decomposes it per dimension and supplies the call sites "
        "and the per-tool rates.",
        "RaQuN, an external n-way matcher, cross-checks the headline in one sentence.",
        "How much of this agreement normalisation creates is RQ3's ladder, in "
        f"{_link('The L0-L4 deltas (the agreement ladder)')}.",
    )
    return [RQ4.heading(), "", *intro] + body


def _voters(info: dict) -> list[str]:
    """The voter groups the agreement numbers rest on (tables-run<i>.json `voters`, after --merge-llm)."""
    voters = info.get("voters") or {}
    if not voters:
        return []
    groups: dict[str, list[str]] = defaultdict(list)
    for tool, v in voters.items():
        groups[v.get("voter_group") or tool].append(tool)
    shared = {g: ts for g, ts in sorted(groups.items()) if len(ts) > 1}
    single = sorted(ts[0] for ts in groups.values() if len(ts) == 1)
    lines = ["### Voter groups", ""]
    lines += _text(
        "Who confirms whom in every table below: a tool's finding counts as confirmed only when a tool of *another* "
        "voter group reports it too.",
        "Tools that share their detection logic (the same engine packaged twice, or the same rules) form one group, so "
        "that the same logic never confirms itself, and every other tool is a voter group of its own.",
    )
    if shared:
        table = [
            [g, ", ".join(sorted(ts)), ", ".join(sorted({voters[t].get("engine") or "?" for t in ts}))]
            for g, ts in shared.items()
        ]
        lines += md_table(["voter group", "tools", "engine"], table, "lll") + [""]
    if single:
        lines += _text(f"One voter group each: {', '.join(f'`{t}`' for t in single)}.")
    if (info.get("settings") or {}).get("merge_llm"):
        lines += _check(CAVEAT, "`--merge-llm`: the LLM workers count as one voter (`merge_group: llm`), the "
                                "sensitivity check.")  # fmt: skip
    lines += _background("How the groups are decided", _text(
        "Tools that share only a pattern engine but run independently written rules count apart: their rule sets were "
        "checked and share no rule.",
        "The groups are BF-CBOM's decision, set in `common/tools.yaml` (`voter_group`) with the evidence in "
        "`analysis/README.md` (Voter groups).",
        "The scores also rank the tools under other groupings.",
    ))  # fmt: skip
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
            [t, first.get("voter_group", "")]
            + [_pct(by.get((t, lv), {}).get("share")) for lv in levels]
            + [first.get("items", "")]
        )
    lines = ["### The L0-L4 deltas (the agreement ladder)", ""]
    lines += _text(
        "How much of the tools' agreement is created by normalisation?",
        "One row per tool: of its `items` (algorithms per repository, location ignored), the share another voter group "
        f"also reports when names count as the same at that level (see {_link('How to read this summary')}).",
        "The L0 to L4 gap is the agreement normalisation creates.",
    )
    lines += md_table(["tool", "voter group", *levels, "items"], table, "ll" + "r" * (len(levels) + 1)) + [""]
    lines += _check(CAVEAT, "Pairs a tool was tuned on are left out.")
    alls = {r["level"]: r for r in rows if r["tool"] == "all"}
    lines += _columns(
        [
            ("voter group", "the tool's voter group."),
            (f"{levels[0]} … {levels[-1]}", "the share of its items another voter group also reports at that level.",
             "The items are the same at every level, so the shares are comparable: they only grow from left to right, "
             "and each step's gain is what one normalisation step adds.",
             "The tool specs behind L4 hold the tools' own spellings and labels, never our judgements."),
            ("items", "the tool's items, and for `all` the voters' items pooled."),
        ]
    )  # fmt: skip
    eg = next((row for row in table if row[0] != "all" and row[2] and row[2] != row[1 + len(levels)]), None)
    if eg:
        lines += _example(
            f"`{eg[0]}` reports {_n(eg[-1])} items.",
            f"Another voter group reports a share of {eg[2]} of them under the identical name ({levels[0]}) and "
            f"{eg[1 + len(levels)]} once every normalisation step counts ({levels[-1]}).",
        )
    if alls and any(r.get("multi_unit_clusters") not in (None, "") for r in alls.values()):
        strict = ", ".join(
            f"{lv} {_pct(_int(r.get('confirmed_strict')) / _int(r['items']))}" if _int(r.get("items")) else lv
            for lv, r in alls.items()
        )
        multi = ", ".join(f"{lv} {_n(_int(r.get('multi_unit_clusters')))}" for lv, r in alls.items())
        lines += _background("How much rests on vocabulary knowledge", _text(
            "A generic report (`EC`, `SHA-2`) confirms the specific algorithms it may mean.",
            f"Confirmed without such generic reports, all voters: {strict}.",
            f"Groups whose records name several algorithms (what a level declares the same), per level: {multi}.",
            "Records whose name is no algorithm (`undefined`, `api`, `non-crypto`, `ambiguous`) are no items.",
        ))  # fmt: skip
    params = [r for r in all_rows if r.get("dimension", "algorithm") != "algorithm" and r["tool"] == "all"]
    if params:
        dims = list(dict.fromkeys(r["dimension"] for r in params))
        plevels = list(dict.fromkeys(r["level"] for r in params))
        by_dim = {(r["dimension"], r["level"]): r for r in params}
        ptable = []
        for d in dims:
            cells = [_pct(by_dim[(d, lv)]["share"]) if (d, lv) in by_dim else "n/a" for lv in plevels]
            ptable.append([d, *cells, by_dim.get((d, plevels[-1]), {}).get("items", "")])
        lines += ["#### Parameters", ""]
        lines += _text(
            "The same ladder for the parameters, pooled over the voters, one row per parameter.",
            "A pair is an item with one value of it, such as AES with key size 256.",
        )
        lines += md_table(["parameter", *plevels, f"pairs {plevels[-1]}"], ptable) + [""]
        lines += _columns(
            [
                (f"{plevels[0]} … {plevels[-1]}", "the share of the voters' pairs for which another voter group "
                 "reports the same value for that item.",
                 "A value counts at L2 when it sits in a standard field, from L3 also when parsed from the name, at L4 "
                 "from any source (per-tool rows in `levels-run<i>.csv`).",
                 "`n/a`: the standard has no field for that value (CycloneDX carries no digest of a composite such as "
                 "HMAC-SHA256), so the row starts where a name can be parsed."),
                (f"pairs {plevels[-1]}", "the pairs counted."),
            ]
        )  # fmt: skip
    return lines


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
        # a group without a member record of its own is no item, however many generic records hang on it (the rule
        # of tables.matchlevel_row, so `all` equals the matcher x level table)
        rows = [g for g in rows if _int(g.get("member_voter_groups", g.get("voter_groups")))]
        for g in rows:
            for unit in (g.get("units") or g["key"]).split(";"):  # the L4 identities inside the group
                key = (g["snapshot"], g.get("variant"), unit)
                best[key] = max(best.get(key, 0), _int(g["voter_groups"]))
        c = Counter(bucket(v) for v in best.values())
        n = len(best)
        sites = sum(_int(g.get("n_sites")) for g in rows)
        coloc = sum(_int(g.get("colocated_voters")) >= 2 for g in rows)
        shares = [f"{_n(c.get(b, 0))} ({_pct(c.get(b, 0) / n)})" if n else "" for b in buckets]
        return [label, n, *shares, sites, coloc]

    table = [line("all", repo)]
    for lg in sorted({(g.get("language") or "?").lower() for g in repo}, key=_lang_key):
        table.append(line(lg, [g for g in repo if (g.get("language") or "?").lower() == lg]))
    lines = ["#### Per language", ""]
    lines += _text(f"The exact matcher at the scoring level ({level}), one row per language, `all` first.")
    lines += md_table(["language", "items", *[f"k={b}" if b != "4+" else "k≥4" for b in buckets], "sites",
                       "co-located"], table)  # fmt: skip
    lines += [""] + _columns(
        [
            ("items", "as in the matcher x level table."),
            ("k=1 … k≥4", "as in the matcher x level table."),
            ("sites", "the call sites inside those groups: records in the same file with lines within ±2 form one "
             "site."),
            ("co-located", "groups where at least two voter groups point at the same call site."),
        ]
    )  # fmt: skip
    return lines


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
                ", ".join(models),
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
                    ", ".join(sorted({r.get("priced_model") or "" for r in rs if r.get("priced_model")})),
                ]
            )
    lines = ["## LLM usage", ""]
    lines += _text("What the LLM providers reported, summed over all runs with requests (failed runs included), one "
                   "row per LLM worker.")  # fmt: skip
    header = [
        "tool", "runs", "requests", "failed", "chunks", "skipped", "input tokens", "cached", "output tokens",
        "reasoning", "median input", "median output", "models answered",
    ]  # fmt: skip
    lines += md_table(header, table, "l" + "r" * (len(header) - 2) + "l") + [""]
    lines += _columns(
        [
            ("requests", "the requests sent."),
            ("failed", "those that failed after the provider's retries."),
            ("chunks", "the pieces the selected source files were cut into, one request each."),
            ("skipped", "chunks over `LLM_MAX_CHUNKS` that were never sent, with the runs affected.",
             "The model saw only part of those repositories."),
            ("input tokens", "the input tokens billed."),
            ("cached", "input served from the provider's prompt cache, priced lower."),
            ("output tokens", "the output tokens billed."),
            ("reasoning", "the model's hidden thinking, part of the output."),
            ("median input", "input tokens per run."),
            ("median output", "output tokens per run."),
            ("models answered", "the model ids the provider answered with."),
        ]
    )  # fmt: skip
    if cost_table:
        lines += ["### Costs", ""]
        lines += _text(
            "The costs in USD, computed from the stored token counts with the price table "
            f"{', '.join(sorted(versions))} (`analysis/data/llm_prices.yaml`).",
        )
        header = ["tool", "priced runs", "total", "median per repo", "per item", "per confirmed item", "priced as"]
        lines += md_table(header, cost_table, "lrrrrrl") + [""]
        lines += _columns(
            [
                ("priced runs", "runs with a price."),
                ("total", "their sum."),
                ("median per repo", "the median of one run."),
                ("per item", "the total divided by the algorithms the tool reports (from the scores' algorithm "
                 "table)."),
                ("per confirmed item", "the total divided by those another voter group reports too."),
                ("priced as", "the price entry used."),
            ]
        )  # fmt: skip
    return lines


def _files(info: dict, run: int) -> list[str]:
    counts = info.get("rows") or {}
    lines = ["## Files", ""]
    lines += _text("The files in this folder behind the tables above, with their row counts, by the question they "
                   "answer.")  # fmt: skip
    groups = [(BAR_EXAM, BAR_EXAM_TABLES), *((f"{rq.id} {rq.title}", rq.tables) for rq in RQS),
              (STABILITY, ("stability.csv",))]  # fmt: skip
    listed: set[str] = set()
    for title, patterns in groups:
        names = [p.format(i=run) for p in patterns if p.format(i=run) in counts]
        if title == STABILITY and not any(_int(counts[n]) for n in names):  # one repetition: nothing to compare
            listed |= set(names)
            continue
        if not names:
            continue
        lines += [f"#### {title}", ""]
        lines += [f"- `{n}` ({_n(counts[n])} rows): {FILES.get(p, '')}." for p, n in zip(patterns, names, strict=False)
                  if n in counts]  # fmt: skip
        lines.append("")
        listed |= set(names)
    rest = [
        (p.format(i=run), m) for p, m in FILES.items() if p.format(i=run) in counts and p.format(i=run) not in listed
    ]
    lines += ["#### Other", ""] + [f"- `{n}` ({_n(counts[n])} rows): {m}." for n, m in rest]
    lines += _bullets(
        [
            ("normalize.json", "coverage per tool in full: sources, conflicts, top unmapped values."),
            ("../_run-log.json", "one entry per runner session: times, host RAM and CPUs, budget, waves, peak RAM."),
            ("<snapshot>/<variant>/<tool>/run<i>.norm.json", "the normalised CBOM next to each stored one."),
        ]
    )  # fmt: skip
    settings = info.get("settings") or {}
    adapters = info.get("adapters") or {}
    lines += _background(
        "Settings and adapter specs",
        _bullets([(f"{k}={v}", "a setting of this report.") for k, v in settings.items()])
        + _bullets([(v, f"the adapter spec of `{t}`, with what it inherits.") for t, v in sorted(adapters.items())]),
    )
    return lines


# ----- entry points -----


def render(analysis_dir: Path, *, run: int = 1) -> str:
    analysis_dir = Path(analysis_dir)
    spec_file = spec_path(analysis_dir.parent)
    spec = yaml.safe_load(spec_file.read_text(encoding="utf-8")) if spec_file.exists() else {}
    info_path = analysis_dir / f"tables-run{run}.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}

    def rows(name: str) -> list[dict]:
        return read_csv(analysis_dir / name.format(i=run))

    lines = ["## Preamble", "", "The terms the tables use, the questions they answer, and every stored run.", ""]
    lines += _reading() + _questions()
    lines += _runs(rows("runs.csv"))
    lines += _memory_limits(rows("runs.csv"), rows("runtime.csv"))
    lines += _tools(rows("tools.csv"))
    lines += _versions(rows("runs.csv"), rows("tools.csv"))
    lines += _repositories(spec or {})
    lines += _negatives(rows("runs.csv"), run)
    lines += _micro(rows("micro-run{i}.csv"), rows("micro-items-run{i}.csv"))
    lines += _rq1(
        _reported(rows("runs.csv"), run),
        rows("units-run{i}.csv"),
        rows("coverage-run{i}.csv"),
        rows("units-repo-run{i}.csv"),
        rows("traceability-run{i}.csv"),
        rows("runtime.csv"),
    )
    lines += _conformity(
        rows("schema-run{i}.csv"), rows("meaningful-run{i}.csv"), rows("conformance-run{i}.csv"),
        rows("sources-run{i}.csv"),
    )  # fmt: skip
    lines += _normalisation(
        rows("items-run{i}.csv"), rows("registry-run{i}.csv"), rows("levels-run{i}.csv"), rows("vocab.csv")
    )
    lines += _agreement(
        info,
        rows("matchlevels-run{i}.csv"),
        rows("groups-exact-run{i}.csv"),
        rows("matchers-run{i}.csv"),
        rows("score-run{i}.csv"),
        rows("loo-run{i}.csv"),
        rows("grouping-run{i}.csv"),
    )
    lines += _mutation(rows("mutants.csv"), rows("mutation.csv"), rows("mutation-rates.csv"),
                       rows("mutation-scores.csv"), rows("bridge-run{i}.csv"))  # fmt: skip
    lines += _stability(rows("stability.csv"))
    lines += _llm(rows("runs.csv"), rows("score-run{i}.csv"))
    lines += _files(info, run)
    overview = _experiment(rows("runs.csv"), load_run_log(analysis_dir.parent), spec=spec or {}, info=info, run=run)
    lines = _header(spec or {}, info, run) + overview + _toc(lines) + lines
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip() + "\n"


def write(analysis_dir: Path, *, run: int = 1) -> Path:
    path = Path(analysis_dir) / SUMMARY_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(render(analysis_dir, run=run), encoding="utf-8")
    tmp.replace(path)
    return path


if __name__ == "__main__":  # rebuild from an exported folder: python -m analysis.summary <exp>/_ANALYSIS [run]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("_ANALYSIS")
    print(write(target, run=int(sys.argv[2]) if len(sys.argv) > 2 else 1))
