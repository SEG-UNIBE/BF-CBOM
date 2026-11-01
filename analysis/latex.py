r"""LaTeX tables for the paper, rendered only from what `analysis report` writes (the CSV tables and the record), like
`_SUMMARY.md`: `_ANALYSIS/latex/tab_<part>_<what>.tex`, one file per table, each written when its data is there. The
part is where the paper uses it: `design` (the bar exam, the corpus), then `rq1` to `rq5`.

A file holds the table's content only: a `tabular*` (numbers) or `tabularx` (with a column of text) of exactly
`\textwidth`, booktabs rules, no vertical lines. The `table` environment, its caption and its label stay in the paper,
as in its `assets/tab_*.tex`. The layouts are made for Springer's svjour3 in its single-column `smallextended` format
at `\small`, which the paper's table environments set: 338 pt of text width. `Table.width` estimates a table's natural
width from glyph widths measured in that class, and a test keeps every layout inside it; nothing is ever scaled.
Tools appear under their names (`name` in tools.csv) in one order, alphabetical, the same in every table. The paper's
preamble needs booktabs, tabularx and makecell.

    python -m analysis.latex <exp>/_ANALYSIS [run]    # rebuild from an exported folder
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from common.experiment import spec_path

from .summary import PAPER_LANGUAGES, SMALL_SOURCE_SHARE, _source_share
from .summary_blocks import LEVEL_ORDER, _float, _int, _true, read_csv
from .summary_rqs import GAP_TEXT, MATCH_DIMS
from .tables import LOCATION_KINDS

LATEX_DIR = "latex"

# svjour3 `smallextended` at \small, measured with pdflatex and mathptmx (2026-11-01): the text width, LaTeX's column
# padding, and what an X column needs at least (a few words per line).
TEXTWIDTH_PT = 338.0
TABCOLSEP_PT = 6.0
X_MIN_PT = 60.0

STYLES = {"direct": "dir.", "wrapper": "wrap.", "indirect": "ind."}  # RQ5's insertion styles, as column heads


# Glyph widths in pt of Times (mathptmx, T1) at the paper's \small in svjour3 smallextended, measured with pdflatex
# (2026-11-01); kerning only narrows a word, and a glyph not listed counts as wide as a capital A.
_GLYPH_PT = {
    1.6: "|", 2.0: " ,.", 2.22: "/:;ijlt", 2.66: "!'()-I[]`fr", 3.11: "Js", 3.26: '"', 3.55: "?acez",
    4.0: "*0123456789bdghknopquvxy–", 4.45: "FPS", 4.51: "+<=>", 4.89: "ELTZ", 5.34: "BCR",
    5.78: "ADGHKNOQUVXYw", 6.22: "m", 6.66: "%", 7.11: "M", 7.37: "@", 7.55: "W",
}  # fmt: skip
GLYPH_MARGIN = 1.02  # the estimate rounds up: the 31 tables of four exports measured up to 1 % wider than summed
_GLYPHS = {ch: pt for pt, chars in _GLYPH_PT.items() for ch in chars}


def _glyphs(text: str) -> float:
    """The width of `text` in pt at the paper's \\small."""
    return sum(_GLYPHS.get(ch, 5.78) for ch in text)


class Tex(str):
    """A cell already in LaTeX; `plain` is what it prints (a newline starts a line), `pt` its measured width where
    it is set in math, which the glyph table does not cover."""

    plain: str
    pt: float | None

    def __new__(cls, tex: str, plain: str | None = None, pt: float | None = None) -> Tex:
        cell = super().__new__(cls, tex)
        cell.plain, cell.pt = (tex if plain is None else plain), pt
        return cell


@dataclass
class Sub:
    """A row across the whole table, the heading of the rows below it."""

    text: str


DASH = Tex("--", "–")  # nothing to show: not run, not applicable, or no value
MIDRULE = Tex(r"\midrule")
SPACE = Tex(r"\addlinespace")
_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
            "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}  # fmt: skip


def _tex(cell) -> str:
    return str(cell) if isinstance(cell, Tex) else "".join(_SPECIAL.get(ch, ch) for ch in str(cell))


def _plain(cell) -> str:
    return cell.plain if isinstance(cell, Tex) else str(cell)


def _width(cell) -> float:
    if isinstance(cell, Tex) and cell.pt is not None:
        return cell.pt
    return max(_glyphs(line) for line in _plain(cell).split("\n"))


def _head(*lines: str) -> Tex:
    """A header cell on several lines, bottom-aligned (makecell)."""
    return Tex(r"\makecell[b]{" + r"\\".join(_tex(x) for x in lines) + "}", "\n".join(lines))


def _count(v) -> Tex:
    """A whole number with the paper's thousands separator, 1{,}210."""
    text = f"{_int(v):,}"
    return Tex(text.replace(",", "{,}"), text)


def _number(v, digits: int = 1) -> Tex:
    f = _float(v)
    if f is None:
        return DASH
    text = f"{f:,.{digits}f}"
    return Tex(text.replace(",", "{,}"), text)


def _seconds(column: list) -> list[Tex]:
    """A column of seconds with one decimal while every value stays below 100, else whole (106, 3{,}600), the same
    decimals down the column so it lines up: a tenth says nothing about a long scan."""
    largest = max((f for v in column if (f := _float(v)) is not None), default=0.0)
    return [_number(v, 1 if largest < 100 else 0) for v in column]


def _share(v) -> Tex | str:
    """A share as a fraction with two decimals, as everywhere in the summary."""
    f = _float(v)
    return DASH if f is None else f"{f:.2f}"


def _ratio(part, whole) -> Tex | str:
    return _share(_int(part) / _int(whole)) if _int(whole) else DASH


def _interval(v, low, high) -> Tex | str:
    """`0.78 [0.74, 0.82]`, the value alone without an interval."""
    f, lo, hi = _float(v), _float(low), _float(high)
    if f is None:
        return DASH
    return f"{f:.2f} [{lo:.2f}, {hi:.2f}]" if lo is not None and hi is not None else f"{f:.2f}"


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


_LANGUAGES = {lang.lower(): lang for lang in PAPER_LANGUAGES}


def _language(lang: str) -> str:
    """A language as the paper writes it (the micro projects' `java` is Java)."""
    return _LANGUAGES.get((lang or "").lower(), lang or "")


def _paper_languages(values) -> list[str]:
    """The paper's languages among `values`, in its order (Java, Python, Go); no other language is analysed."""
    present = {_language(v) for v in values}
    return [lang for lang in PAPER_LANGUAGES if lang in present]


@dataclass
class Table:
    """One table: its name (the file is `tab_<name>.tex`), the files it reads, one letter per column (`l`, `r`, `X`),
    the header row, the rows (lists of cells, `MIDRULE`, `SPACE` or a `Sub`) and the column groups above the header,
    each `(first, last, label)` counted from 1."""

    name: str
    sources: list[str]
    columns: str
    header: list
    rows: list
    groups: list[tuple[int, int, str]] = field(default_factory=list)

    def width(self) -> float:
        """The natural width in pt: the widest cell of each column, `X_MIN_PT` for an X column (its text wraps), the
        padding between the columns (none at the outer edges, `@{}`) and what a group's label adds above its
        columns."""
        widths = [0.0] * len(self.columns)
        for row in [self.header, *self.rows]:
            if isinstance(row, list):
                for i, cell in enumerate(row):
                    widths[i] = max(widths[i], _width(cell))
        widths = [X_MIN_PT if c == "X" else w for c, w in zip(self.columns, widths, strict=True)]  # X wraps
        total = sum(widths) + 2 * TABCOLSEP_PT * (len(widths) - 1)
        for first, last, label in self.groups:  # a group's label wider than its columns widens them
            under = sum(widths[first - 1 : last]) + 2 * TABCOLSEP_PT * (last - first)
            total += max(_glyphs(label) - under, 0.0)
        return total * GLYPH_MARGIN

    def render(self, experiment: str) -> str:
        n = len(self.columns)
        if "X" in self.columns:
            spec = "".join(r">{\raggedright\arraybackslash}X" if c == "X" else c for c in self.columns)
            lines = [r"\begin{tabularx}{\textwidth}{@{}" + spec + "@{}}"]
            end = r"\end{tabularx}"
        else:
            lines = [r"\begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}" + self.columns + "@{}}"]
            end = r"\end{tabular*}"
        lines.append(r"\toprule")
        if self.groups:
            starts = {g[0]: g for g in self.groups}
            cells, rules, i = [], [], 1
            while i <= n:
                if i in starts:
                    first, last, label = starts[i]
                    cells.append(rf"\multicolumn{{{last - first + 1}}}{{c}}{{{_tex(label)}}}")
                    trim = ("" if first == 1 else "l") + ("" if last == n else "r")
                    rules.append(rf"\cmidrule({trim}){{{first}-{last}}}")
                    i = last + 1
                else:
                    cells.append("")
                    i += 1
            lines += [" & ".join(cells) + r" \\", "".join(rules)]
        lines += [" & ".join(_tex(c) for c in self.header) + r" \\", r"\midrule"]
        for row in self.rows:
            if isinstance(row, Sub):
                lines.append(rf"\multicolumn{{{n}}}{{@{{}}l}}{{\emph{{{_tex(row.text)}}}}} \\")
            elif isinstance(row, Tex):
                lines.append(str(row))
            else:
                lines.append(" & ".join(_tex(c) for c in row) + r" \\")
        lines += [r"\bottomrule", end]
        width = self.width()
        if width > TEXTWIDTH_PT:
            fit = f"WIDER THAN THE TEXT: about {width:.0f} pt of {TEXTWIDTH_PT:.0f} pt."
        elif "X" in self.columns:
            fit = "It fills the text width, its text column wraps."
        else:
            fit = f"About {width:.0f} pt wide, spread to the {TEXTWIDTH_PT:.0f} pt text width."
        comment = [
            f"% tab_{self.name}.tex from {experiment} ({', '.join(self.sources)}), written by `analysis report`: "
            "do not edit, rerun it.",
            "% Table content only: the table environment, its caption and its label sit in the paper (svjour3 "
            f"smallextended, \\small). {fit}",
        ]
        return "\n".join(comment + lines) + "\n"


def _names(tools: list[dict]) -> dict[str, str]:
    return {r["tool"]: r.get("name") or r["tool"] for r in tools}


def _order(tools, names: dict[str, str]) -> list[str]:
    """The tools in the one order of every table: by name, alphabetical."""
    return sorted(set(tools), key=lambda t: (names.get(t, t).casefold(), t))


# ----- the study design: the bar exam, the corpus -----


def bar(micro: list[dict], names: dict[str, str]) -> Table | None:
    """The bar exam (micro-run<i>.csv): per tool and language the calls found in their functions, leniently, with in
    brackets the calls whose family is reported anywhere in the project; the verdict and the decoy kinds reported on.
    The eligible tools first, then the others, each by name."""
    if not micro:
        return None
    langs = _paper_languages(r["language"] for r in micro)
    cells = {(r["tool"], _language(r["language"])): r for r in micro}
    tailored = {r["tool"] for r in micro if _true(r.get("tailored"))}

    def verdict(tool: str) -> str:
        done = [lg for lg in langs if (r := cells.get((tool, lg))) and r["verdict"] in ("pass", "fail")]
        passed = [lg for lg in done if cells[(tool, lg)]["verdict"] == "pass"]
        if not done:
            return "not judged"
        if passed == done:
            return "eligible"
        return f"eligible in {', '.join(passed)}" if passed else "not eligible"

    def cell(r: dict | None):
        if r is None or r["verdict"] == "unsupported":
            return DASH
        if r["verdict"] in ("pass", "fail"):
            return f"{_int(r['lenient'])} ({_int(r['repo'])})"
        return "not run" if r["verdict"] == "missing" else r["verdict"]

    def decoys(tool: str) -> str:
        kinds = dict.fromkeys(k for lg in langs if (r := cells.get((tool, lg)))
                              for k in (r.get("decoys_hit") or "").split(";") if k)  # fmt: skip
        return ", ".join(k.replace("-", " ") for k in kinds) or "none"

    def row(tool: str) -> list:
        label = names.get(tool, tool)
        name = Tex(_tex(label) + r"$\ast$", label + "*") if tool in tailored else label
        return [name, *[cell(cells.get((tool, lg))) for lg in langs], verdict(tool), decoys(tool)]

    tools = _order({r["tool"] for r in micro}, names)
    eligible = [t for t in tools if verdict(t).startswith("eligible")]
    rest = [t for t in tools if t not in eligible]
    rows = [row(t) for t in eligible] + ([MIDRULE] if eligible and rest else []) + [row(t) for t in rest]
    header = ["Tool", *langs, "Verdict", "Decoys reported on"]
    return Table("design_bar", ["micro-run<i>.csv"], "l" + "r" * len(langs) + "lX", header, rows)


def corpus(spec: dict) -> Table | None:
    """The repositories per language (the record, `_experiment.yaml`): how many, their median stars, the median size of
    their checkout and of its source files at the pinned commit, the median source share and how many are mostly
    something else (below `SMALL_SOURCE_SHARE`)."""
    repos = [r for r in spec.get("repos") or [] if r.get("full_name")]
    if not repos:
        return None
    by_lang: dict[str, list[dict]] = defaultdict(list)
    for r in repos:
        by_lang[_language(r.get("language") or "unknown")].append(r)
    order = {lang: i for i, lang in enumerate(PAPER_LANGUAGES)}
    langs = sorted(by_lang, key=lambda lang: (order.get(lang, len(order)), lang))

    def row(label: str, group: list[dict]) -> list:
        stars = _median([_int(r["stars"]) for r in group if r.get("stars") not in (None, "")])
        sized = [(r, s) for r in group if (s := _source_share(r)) is not None]  # with both sizes, and the share
        shares = [s for _, s in sized]

        def mib(key: str):
            m = _median([_int(r[key]) for r, _ in sized])
            return DASH if m is None else _number(m / 1024**2)

        return [label, _count(len(group)), DASH if stars is None else _count(round(stars)),
                mib("checkout_bytes"), mib("source_bytes"), _share(_median(shares)),
                _count(sum(s < SMALL_SOURCE_SHARE for s in shares)) if sized else DASH]  # fmt: skip

    rows = [row(lang, by_lang[lang]) for lang in langs]
    if len(langs) > 1:
        rows += [MIDRULE, row("all", repos)]
    below = round(SMALL_SOURCE_SHARE * 100)
    header = ["Language", "Repos", _head("Median", "stars"), "checkout", "source", "median",
              Tex(rf"$<$\,{below}\,\%", f"< {below} %")]  # fmt: skip
    return Table("design_corpus", ["_experiment.yaml"], "lrrrrrr", header, rows,
                 [(4, 5, "Median MiB"), (6, 7, "Source share")])  # fmt: skip


# ----- RQ1 volume -----


def volume(units: list[dict], unit_repos: list[dict], trace: list[dict], names: dict[str, str]) -> Table | None:
    """What each tool reports (units-run<i>.csv, units-repo-run<i>.csv, traceability-run<i>.csv): its packaging, its
    algorithm records and items, the median items per repository with an ok run, the share of its algorithm records
    with a file and a line and, of those, the share whose lines hold a cryptographic word (strong), and the share of
    its located records outside the repository's source files."""
    if not units:
        return None
    per_repo: dict[str, list[int]] = defaultdict(list)
    for r in unit_repos:
        per_repo[r["tool"]].append(_int(r["items"]))
    traced: dict[str, list[dict]] = defaultdict(list)
    for r in trace:
        if (r.get("asset_type") or "") == "algorithm":
            traced[r["tool"]].append(r)
    by_tool = {u["tool"]: u for u in units}
    rows = []
    for tool in _order(by_tool, names):
        u, rs = by_tool[tool], traced.get(tool, [])
        located = sum(_int(u.get(f"loc_{k}")) for k in LOCATION_KINDS if k != "none")  # records with a place
        checked = [r for r in rs if r.get("source")]
        rows.append(
            [
                names.get(tool, tool),
                u.get("packaging") or "unknown",
                _count(u["records"]),
                _count(u["items"]),
                _number(_median(per_repo.get(tool, []))),
                _ratio(sum(_int(r["located_line"]) for r in rs), sum(_int(r["records"]) for r in rs)),
                _ratio(sum(_int(r["strong"]) for r in checked), sum(_int(r["located_line"]) for r in checked)),
                _ratio(u.get("outside_code"), located) if "outside_code" in u else DASH,
            ]
        )
    header = ["Tool", "Packaging", "Records", "Items", _head("Items", "per repo"), "file+line", "strong",
              _head("Outside", "code")]  # fmt: skip
    return Table("rq1_volume", ["units-run<i>.csv", "units-repo-run<i>.csv", "traceability-run<i>.csv"], "llrrrrrr",
                 header, rows, [(6, 7, "Located")])  # fmt: skip


def cost(runtime: list[dict], names: dict[str, str]) -> Table | None:
    """What a scan costs (runtime.csv): the median wall time per language, then over every repository the p90 time,
    the timeouts, the median and largest peak memory and the median cores kept busy."""
    every = {r["tool"]: r for r in runtime if r["language"] == "all" and r.get("median_sec")}
    if not every:
        return None
    by = {(r["tool"], _language(r["language"])): r for r in runtime}
    langs = _paper_languages(r["language"] for r in runtime if r["language"] != "all")

    def gib(mb):
        f = _float(mb)
        return DASH if f is None else _number(f / 1024)

    tools = _order(every, names)
    cols = langs or ["all"]  # the median time per paper language, else over every repository
    times = [_seconds([(c.get("median_sec") if (c := by.get((t, lang))) else None) for t in tools]) for lang in cols]
    p90 = _seconds([every[t].get("p90_sec") for t in tools])
    rows = []
    for i, tool in enumerate(tools):
        r = every[tool]
        peaks = [gib(r.get("peak_median_mb")), gib(r.get("peak_max_mb"))]
        rows.append([names.get(tool, tool), *(col[i] for col in times), p90[i], _count(r.get("timeouts")), *peaks,
                     _number(r.get("cores_median"))])  # fmt: skip
    k = len(cols)
    header = ["Tool", *cols, "p90 (s)", "Timeouts", "median", "max", "Cores"]
    groups = [(2, 1 + k, "Median time (s)"), (4 + k, 5 + k, "Peak memory (GiB)")]
    return Table("rq1_cost", ["runtime.csv"], "l" + "r" * (len(header) - 1), header, rows, groups)


# ----- RQ2 conformity -----


def _code(text: str) -> str:
    """Code in typewriter that may break after a dot and inside a camelCase name, where a column is narrow."""
    out = []
    for i, ch in enumerate(text):
        if i and ch.isupper() and text[i - 1].islower():
            out.append(r"\allowbreak{}")
        out.append(_tex(ch))
        if ch == ".":
            out.append(r"\allowbreak{}")
    return r"\texttt{" + "".join(out) + "}"


def _prose(text: str) -> Tex:
    """Text whose backticked spans are code, as the vocabulary writes its reasons: those in typewriter."""
    parts = text.split("`")
    return Tex("".join(_code(x) if i % 2 else _tex(x) for i, x in enumerate(parts)), text.replace("`", ""))


def _error_kind(kind: str, n: int) -> Tex:
    """A schema error as its rule and the last two steps of its JSON path, `enum parent.field (n)`."""
    rule, _, path = kind.partition(":")
    short = ".".join(path.split(".")[-2:]) if path else ""
    tex = [_tex(rule), *([_code(short)] if short else []), f"({_count(n)})"]
    plain = [rule, *([short] if short else []), f"({n:,})"]
    return Tex(" ".join(tex), " ".join(plain))


def conformance(schema: list[dict], names: dict[str, str]) -> Table | None:
    """Whether the CBOMs follow the standard they declare (schema-run<i>.csv): the format and version, the CBOMs, the
    share valid against that version's official schema, and the two most frequent schema errors."""
    if not schema:
        return None
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for r in schema:
        by_tool[r["tool"]].append(r)
    rows = []
    for tool in _order(by_tool, names):
        rs = by_tool[tool]
        checked = [r for r in rs if r.get("schema")]
        kinds: dict[str, int] = defaultdict(int)
        for r in checked:
            for item in (r.get("error_kinds") or "").split(";"):
                if "=" in item:
                    k, n = item.rsplit("=", 1)
                    kinds[k] += _int(n)
        top = sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
        if not checked:
            errors = DASH
        elif not top:
            errors = "none"
        else:
            parts = [_error_kind(k, n) for k, n in top]
            errors = Tex(", ".join(parts), ", ".join(p.plain for p in parts))
        versions = ", ".join(sorted({f"{r.get('bom_format')} {r.get('spec_version')}" for r in rs}))
        rows.append([names.get(tool, tool), versions, _count(len(rs)),
                     _ratio(sum(_true(r["valid"]) for r in checked), len(checked)), errors])  # fmt: skip
    header = ["Tool", "Format", "CBOMs", "Valid", "Most frequent schema errors"]
    return Table("rq2_conformance", ["schema-run<i>.csv"], "llrrX", header, rows)


# ----- RQ3 normalisation -----


def ladder(levels: list[dict], names: dict[str, str]) -> Table | None:
    """The agreement ladder (levels-run<i>.csv, algorithm): per tool its items and the share another voter group also
    reports at each level, the gain from the first level to the last, and all voters pooled."""
    rows_in = [r for r in levels if r.get("dimension", "algorithm") == "algorithm"]
    if not rows_in:
        return None
    order = {lv: i for i, lv in enumerate(LEVEL_ORDER)}
    lvls = sorted({r["level"] for r in rows_in}, key=lambda lv: (order.get(lv, len(order)), lv))
    by = {(r["tool"], r["level"]): r for r in rows_in}

    def row(label: str, tool: str) -> list:
        shares = [_float(by.get((tool, lv), {}).get("share")) for lv in lvls]
        gain = f"{shares[-1] - shares[0]:+.2f}" if shares[0] is not None and shares[-1] is not None else DASH
        return [label, _count(by.get((tool, lvls[0]), {}).get("items")), *(_share(s) for s in shares), gain]

    tools = _order({r["tool"] for r in rows_in if r["tool"] != "all"}, names)
    rows = [row(names.get(t, t), t) for t in tools]
    if any(r["tool"] == "all" for r in rows_in):
        rows += [MIDRULE, row("all tools", "all")]
    gain = Tex(rf"{lvls[0]}$\to${lvls[-1]}", pt=_glyphs(lvls[0] + lvls[-1]) + 8.0)  # the arrow is 8 pt
    header = ["Tool", "Items", *lvls, gain]
    return Table("rq3_ladder", ["levels-run<i>.csv"], "l" + "r" * (len(header) - 1), header, rows)


GAP_LABELS = {"missing-family": "missing family", "generic-marker": "generic marker"}  # registry-run<i>.csv `gap`


def registry(rows_in: list[dict]) -> Table | None:
    """The families added to the CycloneDX registry (registry-run<i>.csv), grouped by the kind of gap, each with the
    items, repositories and tools of the experiment that rest on it and the reason it is an addition."""
    additions = [r for r in rows_in if r["kind"] == "addition"]
    if not additions:
        return None
    present = {r["gap"] or "" for r in additions}
    gaps = [g for g in GAP_TEXT if g in present] + sorted(present - set(GAP_TEXT))
    rows: list = []
    for gap in gaps:
        rows.append(Sub(GAP_LABELS.get(gap, gap) or "other"))
        for r in sorted(
            (r for r in additions if (r["gap"] or "") == gap), key=lambda r: (-_int(r["count"]), r["name"])
        ):
            tools = [t for t in (r.get("tools") or "").split(";") if t]
            rows.append([r["name"], _count(r["count"]), _count(r["repos"]), _count(len(tools)),
                         _prose((r.get("note") or "").replace("; ", ", "))])  # fmt: skip
    return Table("rq3_registry", ["registry-run<i>.csv"], "lrrrX", ["Family", "Items", "Repos", "Tools", "Reason"],
                 rows)  # fmt: skip


# ----- RQ4 agreement -----

MATCHERS = {"exact": "exact", "raqun": "RaQuN"}  # matchlevels-run<i>.csv `matcher`, as the paper names them


def kdist(match: list[dict]) -> Table | None:
    """Items by the number of voter groups that report them (matchlevels-run<i>.csv), per matcher and level."""
    matchers = [m for m in MATCHERS if any(r["matcher"] == m for r in match)]
    if not matchers:
        return None
    order = {lv: i for i, lv in enumerate(LEVEL_ORDER)}
    rows: list = []
    for matcher in matchers:
        if rows:
            rows.append(MIDRULE)
        rs = sorted((r for r in match if r["matcher"] == matcher), key=lambda r: order.get(r["level"], len(order)))
        for i, r in enumerate(rs):
            rows.append([MATCHERS[matcher] if i == 0 else "", r["level"], _count(r["items"]),
                         *(_ratio(r[c], r["items"]) for c in ("k1", "k2", "k3", "k4plus"))])  # fmt: skip
    # set in math, which the glyph table does not cover: $k\geq 4$ measures 17.5 pt
    header = ["Matcher", "Level", "Items", *(Tex(f"$k={i}$", pt=18.0) for i in (1, 2, 3)),
              Tex(r"$k\geq 4$", pt=18.0)]  # fmt: skip
    return Table("rq4_k", ["matchlevels-run<i>.csv"], "llrrrrr", header, rows)


def dimensions(match: list[dict]) -> Table | None:
    """Agreement per dimension at the top level (matchlevels-run<i>.csv): the groups where at least two voter groups
    give the value, and the share where all of them give the same (for the location: two point at the same call
    site), per matcher."""
    top = LEVEL_ORDER[-1]
    by = {r["matcher"]: r for r in match if r["level"] == top}
    matchers = [m for m in MATCHERS if m in by]
    if not matchers:
        return None
    rows = []
    for d in MATCH_DIMS:
        cells: list = [d.replace("_", " ")]
        for m in matchers:
            groups = _int(by[m].get(f"{d}_groups"))
            cells += [_count(groups), _share(by[m].get(f"{d}_agree"))] if groups else [DASH, DASH]
        rows.append(cells)
    header = ["Dimension", *["Groups", "Agree"] * len(matchers)]
    groups = [(2 + 2 * i, 3 + 2 * i, MATCHERS[m]) for i, m in enumerate(matchers)]
    return Table("rq4_dimensions", ["matchlevels-run<i>.csv"], "l" + "rr" * len(matchers), header, rows, groups)


def rates(scores: list[dict], names: dict[str, str]) -> list[Table]:
    """The per-tool rates (score-run<i>.csv), one table per dimension: the repositories and items, the confirmation
    rate (the mean of the per-repository rates) with its bootstrap interval, exclusivity and majority coverage. In
    the one order of every table: the paper makes no ranking claim from them."""
    tables = []
    for dim in dict.fromkeys(r["dimension"] for r in scores):
        by = {r["tool"]: r for r in scores if r["dimension"] == dim}
        rows = [[names.get(t, t), _count(by[t].get("repos")), _count(by[t]["items"]),
                 _interval(by[t].get("rate"), by[t].get("ci_low"), by[t].get("ci_high")),
                 _share(by[t].get("exclusivity")), _share(by[t].get("coverage"))]
                for t in _order(by, names)]  # fmt: skip
        header = ["Tool", "Repos", "Items", Tex(r"Confirmation [95\,\% CI]", "Confirmation [95 % CI]"),
                  "Exclusivity", "Coverage"]  # fmt: skip
        tables.append(Table(f"rq4_rates_{dim}", ["score-run<i>.csv"], "lrrlrr", header, rows))
    return tables


# ----- RQ5 detection -----


def _by_tool_language(scores: list[dict], names: dict[str, str], cells) -> list:
    """Rows per tool and language, the tool named on its first row, a little space between the tools."""
    langs = _paper_languages(s["language"] for s in scores)
    by = {(s["tool"], _language(s["language"])): s for s in scores}
    rows: list = []
    for tool in _order({s["tool"] for s in scores}, names):
        mine = [lang for lang in langs if (tool, lang) in by]
        if rows and mine:
            rows.append(SPACE)
        rows += [[names.get(tool, tool) if i == 0 else "", lang, *cells(by[(tool, lang)])] for i, lang in
                 enumerate(mine)]  # fmt: skip
    return rows


def detection(scores: list[dict], names: dict[str, str]) -> Table | None:
    """Recall, precision and F1 on the planted items (mutation-scores.csv, all styles), per tool and language: the
    judged calls, recall at the call site and F1 with their bootstrap intervals over repositories, precision over the
    planted calls and decoys."""
    rows_in = [s for s in scores if s.get("style") == "all"]
    if not rows_in:
        return None

    def cells(s: dict) -> list:
        return [_count(s.get("calls")), _interval(s.get("recall_site"), s.get("recall_low"), s.get("recall_high")),
                _share(s.get("precision")), _interval(s.get("f1"), s.get("f1_low"), s.get("f1_high"))]  # fmt: skip

    header = ["Tool", "Language", "Calls", Tex(r"Recall [95\,\% CI]", "Recall [95 % CI]"), "Precision",
              Tex(r"F1 [95\,\% CI]", "F1 [95 % CI]")]  # fmt: skip
    return Table("rq5_detection", ["mutation-scores.csv"], "llrlrl", header,
                 _by_tool_language(rows_in, names, cells))  # fmt: skip


def styles(scores: list[dict], names: dict[str, str]) -> Table | None:
    """Recall at the call site per insertion style (mutation-scores.csv), per tool, the languages side by side."""
    rows_in = [s for s in scores if s.get("style") in STYLES]
    if not rows_in:
        return None
    langs = _paper_languages(s["language"] for s in rows_in)
    shown = [st for st in STYLES if any(s["style"] == st for s in rows_in)]
    by = {(s["tool"], _language(s["language"]), s["style"]): s for s in rows_in}
    rows = [[names.get(t, t), *(_share(by[(t, lang, st)].get("recall_site")) if (t, lang, st) in by else DASH
                                for lang in langs for st in shown)]
            for t in _order({s["tool"] for s in rows_in}, names)]  # fmt: skip
    header = ["Tool", *[STYLES[st] for _ in langs for st in shown]]
    groups = [(2 + i * len(shown), 1 + (i + 1) * len(shown), lang) for i, lang in enumerate(langs)]
    return Table("rq5_styles", ["mutation-scores.csv"], "l" + "r" * (len(header) - 1), header, rows, groups)


def offedit(scores: list[dict], names: dict[str, str]) -> Table | None:
    """Changes off the edit (mutation-scores.csv, all styles), per tool and language: the judged mutants, those whose
    report has something new outside the inserted lines and their share, and the median new units and sites among
    them."""
    rows_in = [s for s in scores if s.get("style") == "all" and s.get("offedit_share") not in (None, "")]
    if not rows_in:
        return None

    def cells(s: dict) -> list:
        return [_count(_int(s.get("calls")) + _int(s.get("decoys"))), _count(s.get("offedit_mutants")),
                _share(s.get("offedit_share")), _number(s.get("extra_units_median")),
                _number(s.get("extra_sites_median"))]  # fmt: skip

    header = ["Tool", "Language", "Mutants", _head("With", "changes"), "Share", _head("Extra", "units"),
              _head("Extra", "sites")]  # fmt: skip
    return Table("rq5_offedit", ["mutation-scores.csv"], "llrrrrr", header, _by_tool_language(rows_in, names, cells))


# ----- all of them -----


def _spec(analysis_dir: Path) -> dict:
    """The experiment's record next to the analysis folder (`_experiment.yaml`), empty without one."""
    spec_file = spec_path(analysis_dir.parent)
    return (yaml.safe_load(spec_file.read_text(encoding="utf-8")) if spec_file.exists() else None) or {}


def build(analysis_dir: Path, *, run: int = 1) -> list[Table]:
    """Every table the folder's CSVs and its experiment's record have data for, in the paper's order."""
    analysis_dir = Path(analysis_dir)

    def rows(name: str) -> list[dict]:
        return read_csv(analysis_dir / name.format(i=run))

    names = _names(rows("tools.csv"))
    match, mscores = rows("matchlevels-run{i}.csv"), rows("mutation-scores.csv")
    tables = [
        bar(rows("micro-run{i}.csv"), names),
        corpus(_spec(analysis_dir)),
        volume(rows("units-run{i}.csv"), rows("units-repo-run{i}.csv"), rows("traceability-run{i}.csv"), names),
        cost(rows("runtime.csv"), names),
        conformance(rows("schema-run{i}.csv"), names),
        ladder(rows("levels-run{i}.csv"), names),
        registry(rows("registry-run{i}.csv")),
        kdist(match),
        dimensions(match),
        *rates(rows("score-run{i}.csv"), names),
        detection(mscores, names),
        styles(mscores, names),
        offedit(mscores, names),
    ]
    for t in tables:
        if t is not None:
            t.sources = [s.replace("<i>", str(run)) for s in t.sources]
    return [t for t in tables if t is not None]


def write(analysis_dir: Path, *, run: int = 1) -> list[Table]:
    """Write every table into `<analysis_dir>/latex/`, replacing the tables written before; returns them, so a caller
    can warn about one wider than the text (`Table.width`)."""
    analysis_dir = Path(analysis_dir)
    out = analysis_dir / LATEX_DIR
    out.mkdir(exist_ok=True)
    for old in out.glob("tab_*.tex"):
        old.unlink()
    experiment = _spec(analysis_dir).get("name") or analysis_dir.parent.name
    tables = build(analysis_dir, run=run)
    for table in tables:
        (out / f"tab_{table.name}.tex").write_text(table.render(experiment), encoding="utf-8")
    return tables


def too_wide(tables: list[Table]) -> list[str]:
    """A warning for each table whose estimated width is above the text width."""
    return [f"tab_{t.name}.tex is about {w:.0f} pt wide, above the {TEXTWIDTH_PT:.0f} pt text width"
            for t in tables if (w := t.width()) > TEXTWIDTH_PT]  # fmt: skip


if __name__ == "__main__":  # rebuild from an exported folder: python -m analysis.latex <exp>/_ANALYSIS [run]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("_ANALYSIS")
    written = write(target, run=int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    print(f"{len(written)} tables in {target / LATEX_DIR}")
    for warning in too_wide(written):
        print(f"warning: {warning}", file=sys.stderr)
