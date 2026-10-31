"""The layout of _SUMMARY.md: the blocks every section is built from, the marks, the number formatters and
`style_issues`. summary.py renders the summary, summary_rqs.py the sections of the research questions, both from these
blocks, and this module imports neither.

One layout everywhere (2026-10-31, CLAUDE.md "Summary layout"): a subsection is its heading,
one to three sentences, the table, its visible checks (`_check` with `OK`, `FAIL` or `CAVEAT`), then collapsibles in
a fixed order: Columns (`_columns`, one bullet per column), Example (`_example`), a background topic (`_background`)
and a list (`_run_list`), each caption starting with its mark (`COLUMNS`, `EXAMPLE`, `BACKGROUND`, `LIST`). The marks
are set once, below, and nothing else in `analysis/` writes one. The Markdown has one sentence per line and no
semicolons in its prose; `style_issues` checks both.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

LANGUAGE_ORDER = ["java", "python", "go", "javascript", "typescript", "c", "cpp"]


LEVEL_ORDER = ("L0", "L1", "L2", "L3", "L4")


# The summary's marks, set here only: every block, the bar exam's verdicts and "How to read this summary" take them
# from here (a test keeps each in this file once).
COLUMNS, EXAMPLE, BACKGROUND, LIST = "📋", "💡", "📖", "🗂️"  # the captions of the collapsibles


OK, FAIL, CAVEAT = "✅", "⛔", "⚠️"  # the visible checks under a table: a verdict, a caveat


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
    """Columns: one bullet per column (`(name, sentence, ...)`), a homogeneous series in one bullet."""
    return _details(f"{COLUMNS} Columns", _bullets(items))


def _example(*sentences: str) -> list[str]:
    """Example: the reading of one cell of the table above, with its numbers."""
    return _details(f"{EXAMPLE} Example", _text(*sentences))


def _background(topic: str, body: list[str]) -> list[str]:
    """A background topic: method, definitions, conceptual examples."""
    return _details(f"{BACKGROUND} {topic}", body)


def _check(mark: str, *sentences: str) -> list[str]:
    """A visible check under a table: `OK` / `FAIL` a verdict, `CAVEAT` a caveat needed to read the table correctly."""
    first, *rest = [s for s in sentences if s]
    return [f"{mark} {first}", *rest, ""]


def _run_list(title: str, items: list[str]) -> list[str]:
    """A list `<title> (N)`: one bullet per item, at most RUN_LIST_MAX and a line for the rest (runs.csv has them
    all)."""
    if not items:
        return []
    shown = [f"- {x}" for x in items[:RUN_LIST_MAX]]
    if len(items) > RUN_LIST_MAX:
        shown.append(f"- … and {_n(len(items) - RUN_LIST_MAX)} more (`runs.csv`)")
    return _details(f"{LIST} {title} ({_n(len(items))})", shown)


def _anchor(heading: str) -> str:
    """A heading's GitHub anchor: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[^\w\- ]", "", heading.replace("`", "").lower())
    return text.strip().replace(" ", "-")


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


def _lang_key(lang: str) -> tuple[int, str]:
    return (LANGUAGE_ORDER.index(lang) if lang in LANGUAGE_ORDER else len(LANGUAGE_ORDER), lang)
