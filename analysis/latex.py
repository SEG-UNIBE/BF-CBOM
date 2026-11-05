r"""LaTeX tables for the paper, rendered only from what `analysis report` writes (the CSV tables and the record), like
`_SUMMARY.md`: `_ANALYSIS/latex/tab_<part>_<what>.tex`, one file per table, each written when its data is there. The
part is where the paper uses it: the three tables of the subject selection carry the paper's own file names
(`candidate_tools`, `decoy_types`, `bar_exam_results`), then `design_corpus` and `rq1` to `rq5` (`rq1_assets`
first: what the tools wrote, as written, from runs.csv).

A file holds the table's content only, written as the paper's `assets/tab_*.tex` are, so it is copied there as it
is: a `tabular*` (numbers) or `tabularx` (with a column of text) of exactly `\linewidth`, booktabs rules, no vertical
lines, the rows indented by a tab with their columns aligned, every tool name in the paper's `\tool{}` badge (with the
looser `\arraystretch` the badges need, scoped to the table). The `table` environment, its caption and its label stay
in the paper. The layouts are made for Springer's svjour3 in its single-column `smallextended` format at `\small`,
which the paper's table environments set: 338 pt of text width. `Table.width` estimates a table's natural width from
glyph widths measured in that class, and a test keeps every layout inside it; nothing is ever scaled. Tools appear
under their names (`name` in tools.csv) in one order, alphabetical, the same in every table. The paper's preamble
needs booktabs, tabularx, makecell, siunitx (`\num{}` for every number of four digits or more, so the thousands
separator is set once there) and the `\tool`, `\yes` and `\no` macros.

    python -m analysis.latex <exp>/_ANALYSIS [run]    # rebuild from an exported folder
"""

from __future__ import annotations

import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from common.experiment import spec_path
from mutation.catalog import load_catalog

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
BADGE_PT = 3.7  # what the paper's \tool{} badge adds around a name: inner xsep, line and outer sep, both sides
STRETCH = "1.12"  # the paper's \arraystretch for rows of badges, so they do not touch
CITE_PT = 18.0  # a numeric citation after a name, ` [12]`

STYLES = {"direct": "dir.", "wrapper": "wrap.", "indirect": "ind."}  # RQ5's insertion styles, as column heads


# Glyph widths in pt of Times (mathptmx, T1) at the paper's \small in svjour3 smallextended, measured with pdflatex
# (2026-11-01); kerning only narrows a word, and a glyph not listed counts as wide as a capital A.
_GLYPH_PT = {
    1.6: "|", 2.0: " ,.", 2.22: "/:;ijlt", 2.66: "!'()-I[]`fr", 3.11: "Js", 3.26: '"', 3.55: "?acez",
    4.0: "*0123456789bdghknopquvxy–", 4.45: "FPS", 4.51: "+<=>", 4.89: "ELTZ", 5.34: "BCR",
    5.78: "ADGHKNOQUVXYw", 6.22: "m", 6.66: "%", 7.11: "M", 7.37: "@", 7.55: "W",
}  # fmt: skip
GLYPH_MARGIN = 1.035  # the estimate rounds up: measured tables came out up to 3 % wider than summed (bold headers)
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


def _badge(name: str, mark: str = "") -> Tex:
    """A tool's name in the paper's `\\tool{}` badge, `mark` set after it in math (`*`: tailored)."""
    tex = r"\tool{" + _tex(name) + "}" + (r"$\ast$" if mark == "*" else "")
    return Tex(tex, name + mark, _glyphs(name + mark) + BADGE_PT)


YES = Tex(r"\yes", "✓", 7.0)  # the paper's macros: a green check, a red cross (pifont)
NO = Tex(r"\no", "✗", 7.0)


def _bold(cell) -> str:
    """A header cell in bold, unless it is empty or sets its own font."""
    tex = _tex(cell)
    return tex if not tex or r"\textbf" in tex else r"\textbf{" + tex + "}"


def _head(*lines: str) -> Tex:
    """A header cell on several lines, bottom-aligned (makecell)."""
    return Tex(r"\makecell[b]{" + r"\\".join(_tex(x) for x in lines) + "}", "\n".join(lines))


def _num(text: str) -> Tex:
    """A number as the paper writes it: four digits or more in siunitx's `\\num{}`, which sets the thousands
    separator once in the preamble (`\\num{1210}` prints 1,210), fewer digits as they are."""
    sign, digits = ("-", text[1:]) if text.startswith("-") else ("", text)
    whole, _, fraction = digits.partition(".")
    plain = sign + f"{int(whole):,}" + ("." + fraction if fraction else "")
    return Tex(r"\num{" + text + "}" if len(whole) >= 4 else text, plain)


def _count(v) -> Tex:
    """A whole number, `\\num{1210}` from four digits (prints 1,210)."""
    return _num(str(_int(v)))


def _number(v, digits: int = 1) -> Tex:
    f = _float(v)
    return DASH if f is None else _num(f"{f:.{digits}f}")


def _seconds(column: list) -> list[Tex]:
    """A column of seconds with one decimal while every value stays below 100, else whole (106, \\num{3600}), the same
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


def _aligned(body: list) -> list[str]:
    """The rows (lists of LaTeX cells) joined with `&` and ended with `\\\\`, every column padded to its widest cell,
    as the paper's hand-kept tables are aligned; the strings between them stay as they are."""
    rows = [row for row in body if isinstance(row, list)]
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))] if rows else []

    def line(row) -> str:
        if not isinstance(row, list):
            return row
        return " & ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) + r" \\"

    return [line(row) for row in body]


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
    specs: dict[int, str] = field(default_factory=dict)  # a column's full spec where the letter is not enough (0-based)

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
            default = {"X": r">{\raggedright\arraybackslash}X"}
            spec = "".join(self.specs.get(i, default.get(c, c)) for i, c in enumerate(self.columns))
            begin, end = r"\begin{tabularx}{\linewidth}{@{}" + spec + "@{}}", r"\end{tabularx}"
        else:
            begin = r"\begin{tabular*}{\linewidth}{@{\extracolsep{\fill}}" + self.columns + "@{}}"
            end = r"\end{tabular*}"
        body: list = [r"\toprule"]  # a list is a row of cells, aligned below; a string a line as it is
        if self.groups:
            starts = {g[0]: g for g in self.groups}
            cells, rules, i = [], [], 1
            while i <= n:
                if i in starts:
                    first, last, label = starts[i]
                    cells.append(rf"\multicolumn{{{last - first + 1}}}{{c}}{{{_bold(label)}}}")
                    trim = ("" if first == 1 else "l") + ("" if last == n else "r")
                    rules.append(rf"\cmidrule({trim}){{{first}-{last}}}")
                    i = last + 1
                else:
                    cells.append("")
                    i += 1
            body += [" & ".join(cells) + r" \\", "".join(rules)]
        body += [[_bold(c) for c in self.header], r"\midrule"]
        for row in self.rows:
            if isinstance(row, Sub):
                body.append(rf"\multicolumn{{{n}}}{{@{{}}l}}{{\emph{{{_tex(row.text)}}}}} \\")
            elif isinstance(row, Tex):
                body.append(str(row))
            else:
                body.append([_tex(c) for c in row])
        body.append(r"\bottomrule")
        lines = [begin, *("\t" + line for line in _aligned(body)), end]
        if any(r"\tool{" in line for line in lines):
            lines = [
                "% Rows a little looser than the default, so the tool badges do not touch (scoped to the table).",
                r"\renewcommand{\arraystretch}{" + STRETCH + "}",
                *lines,
            ]
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


# ----- the subject selection: the candidates, the decoys, the bar exam; then the corpus -----


def candidates(tools: list[dict]) -> Table | None:
    """The candidate tools (tools.csv, from the registry): each with its citation (`cite`, the paper's bibliography
    key), how it ships, its version at the cutoff date (a bare commit prefixed `commit`) and whether it declares each
    paper language (`languages`), as the paper's macros `\\yes` and `\\no`."""
    named = [t for t in tools if t.get("name")]
    if not named:
        return None

    def version(v: str):
        if not v:
            return DASH
        return f"commit {v}" if re.fullmatch(r"[0-9a-f]{7,}", v) and not v.isdigit() else v

    def row(t: dict) -> list:
        name = _badge(t["name"])
        if cite := t.get("cite"):
            name = Tex(str(name) + rf" \citep{{{cite}}}", name.plain + " [0]", (name.pt or 0.0) + CITE_PT)
        langs = {x.strip().lower() for x in (t.get("languages") or "").split(";") if x.strip()}
        marks = [YES if lg.lower() in langs else NO for lg in PAPER_LANGUAGES]
        return [name, t.get("shipped") or "", version(t.get("version") or ""), *marks]

    rows = [row(t) for t in sorted(named, key=lambda t: (t["name"].casefold(), t["tool"]))]
    header = ["Tool", "Shipped as", "Version", *PAPER_LANGUAGES]
    return Table("candidate_tools", ["tools.csv"], "lXl" + "c" * len(PAPER_LANGUAGES), header, rows)


# The decoy kinds as the paper presents them, in its order: the catalog id, the label, what the decoy mimics and the
# Java example, the catalog's body shortened to its telling line. The catalog (`mutation/catalog/java.yaml`) stays the
# one definition; a kind it has and this list lacks is rendered from the catalog's own words.
DECOY_ROWS = [
    ("comment", "Comment", "algorithm name in prose", "// replace MD5 and DES with SHA-256 and AES"),
    ("log-message", "Log message", "algorithm name in a string", 'println("RSA and ECDSA keys are rotated nightly")'),
    ("identifier", "Variable name", "algorithm name in identifier", 'String sha256Tag = "checksum-" + n;'),
    ("url", "URL", "algorithm name as file suffix", '"https://x.org/app.tar.gz.sha256"'),
    ("crc32", "CRC32 checksum", "non-cryptographic hash API", "new CRC32().update(data)"),
    ("base64", "Base64 encoding", "encoding taken for encryption", "Base64.getEncoder().encodeToStr(data)"),  # cut
    ("hex-constant", "Hex constant", "key or digest value",
     Tex(r'"9f86d081884c7d65\ldots" (64 hex digits)', '"9f86d081884c7d65..." (64 hex digits)')),
    ("cert-path", "Certificate path", "key or certificate file", '"config/certs/ca_ecdsa.pem"'),
]  # fmt: skip


def decoys(catalog_decoys) -> Table | None:
    """The decoy kinds of the micro projects and the mutants (the Java catalog): what each mimics and a Java example,
    the example column in typewriter type. The rows follow `DECOY_ROWS`, limited to the kinds the catalog has."""
    by_id = {d.id: d for d in catalog_decoys}
    if not by_id:
        return None
    rows = [[label, mimics, example] for did, label, mimics, example in DECOY_ROWS if did in by_id]
    for did, d in by_id.items():  # a kind the paper's list does not know yet: the catalog's own words
        if did not in {r[0] for r in DECOY_ROWS}:
            lines = [ln.strip().replace("$", "") for ln in d.forms[0].templates["direct"].splitlines() if ln.strip()]
            rows.append([did, d.what, lines[0] if lines else ""])
    header = ["Decoy", "Mimics", Tex(r"\multicolumn{1}{l@{}}{\textbf{Java example}}", "Java example")]
    specs = {1: r">{\hsize=0.77\hsize\raggedright\arraybackslash}X",
             2: r">{\hsize=1.23\hsize\ttfamily\raggedright\arraybackslash}X"}  # fmt: skip
    return Table("decoy_types", ["mutation/catalog/java.yaml"], "lXX", header, rows, specs=specs)


def bar(micro: list[dict], trace: list[dict], names: dict[str, str]) -> Table | None:
    """The bar exam (micro-run<i>.csv, traceability-run<i>.csv): per tool and language the calls found in their
    functions, leniently (the bar's count; the calls found anywhere in the project and the decoy hits stay in the
    summary), then what the tool reported on the micro projects, its algorithm records and the share of them with a
    file and a line (a tool without locations reports, but cannot pass), and, last, the bar exam's verdict (`passed`,
    `failed`, `passed in Go only`). Two column groups name the calls out of how many with the bar (`n_items`,
    `bar_items`) and the records. The tools that passed first, then the others, each by name."""
    if not micro:
        return None
    langs = _paper_languages(r["language"] for r in micro)
    cells = {(r["tool"], _language(r["language"])): r for r in micro}
    tailored = {r["tool"] for r in micro if _true(r.get("tailored"))}
    records: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # per tool: algorithm records, those with a line
    for r in trace:
        if (r.get("asset_type") or "") == "algorithm":
            records[r["tool"]][0] += _int(r["records"])
            records[r["tool"]][1] += _int(r["located_line"])

    def verdict(tool: str) -> str:
        done = [lg for lg in langs if (r := cells.get((tool, lg))) and r["verdict"] in ("pass", "fail")]
        passed = [lg for lg in done if cells[(tool, lg)]["verdict"] == "pass"]
        if not done:
            return "not judged"
        if passed == done:
            return "passed"
        return f"passed in {', '.join(passed)} only" if passed else "failed"

    def cell(r: dict | None):
        if r is None or r["verdict"] == "unsupported":
            return DASH
        if r["verdict"] in ("pass", "fail"):
            return _count(r["lenient"])
        return "not run" if r["verdict"] == "missing" else r["verdict"]

    def row(tool: str) -> list:
        name = _badge(names.get(tool, tool), "*" if tool in tailored else "")
        n, located = records.get(tool, [0, 0])
        return [name, *[cell(cells.get((tool, lg))) for lg in langs], _count(n), _ratio(located, n), verdict(tool)]

    tools = _order({r["tool"] for r in micro}, names)
    passed = [t for t in tools if verdict(t).startswith("passed")]
    rest = [t for t in tools if t not in passed]
    rows = [row(t) for t in passed] + ([MIDRULE] if passed and rest else []) + [row(t) for t in rest]
    header = ["Tool", *langs, "All", "Located", "Bar exam verdict"]
    scored = [r for r in micro if r["verdict"] in ("pass", "fail")]
    calls = max((_int(r["n_items"]) for r in scored), default=0)
    bar_items = max((_int(r["bar_items"]) for r in scored), default=0)
    groups = [(2, 1 + len(langs), f"Calls found of {calls}, bar {bar_items}"),
              (2 + len(langs), 3 + len(langs), "Algorithm records")]  # fmt: skip
    return Table("bar_exam_results", ["micro-run<i>.csv", "traceability-run<i>.csv"], "l" + "r" * len(langs) + "rrl",
                 header, rows, groups)  # fmt: skip


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


def assets(runs: list[dict], names: dict[str, str], run: int = 1) -> Table | None:
    """What each tool wrote, as written (runs.csv, the original variants of one repetition): the repositories with an
    ok run, the ok runs with no cryptographic asset, the failed runs, and the cryptographic assets by the CycloneDX
    asset type they declare. Asset counts follow each tool's packaging, so they compare output formats; the volume
    table counts findings in records and items."""
    rows = [r for r in runs if _int(r.get("run")) == run and (r.get("variant") or "original") == "original"]
    if not any(r.get("status") == "ok" for r in rows):
        return None
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("status") != "unsupported":
            by_tool[r["tool"]].append(r)
    kinds = ["crypto_assets", "algorithm_assets", "certificate_assets", "protocol_assets", "material_assets"]
    table = []
    for tool in _order(by_tool, names):
        rs = by_tool[tool]
        ok = [r for r in rs if r["status"] == "ok"]
        table.append([_badge(names.get(tool, tool)), _count(len(ok)),
                      _count(sum(_int(r.get("crypto_assets")) == 0 for r in ok)), _count(len(rs) - len(ok)),
                      *(_count(sum(_int(r.get(k)) for r in ok)) for k in kinds)])  # fmt: skip
    header = ["Tool", "ok", "empty", "failed", "all", "algorithm", "cert.", "proto.", "material"]
    return Table("rq1_assets", ["runs.csv"], "lrrrrrrrr", header, table, [(2, 4, "Runs"), (5, 9, "Crypto assets")])


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
                _badge(names.get(tool, tool)),
                u.get("packaging") or "unknown",
                _count(u["records"]),
                _count(u["items"]),
                _number(_median(per_repo.get(tool, []))),
                _ratio(sum(_int(r["located_line"]) for r in rs), sum(_int(r["records"]) for r in rs)),
                _ratio(sum(_int(r["strong"]) for r in checked), sum(_int(r["located_line"]) for r in checked)),
                _ratio(u.get("outside_code"), located) if "outside_code" in u else DASH,
            ]
        )
    header = ["Tool", "Packaging", "Records", "Items", _head("Items", "per repo"), _head("file", "+line"), "strong",
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
        rows.append([_badge(names.get(tool, tool)), *(col[i] for col in times), p90[i], _count(r.get("timeouts")),
                     *peaks, _number(r.get("cores_median"))])  # fmt: skip
    k = len(cols)
    header = ["Tool", *cols, "p90 (s)", _head("Time-", "outs"), "median", "max", "Cores"]
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
        rows.append([_badge(names.get(tool, tool)), versions, _count(len(rs)),
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

    def row(label, tool: str) -> list:
        shares = [_float(by.get((tool, lv), {}).get("share")) for lv in lvls]
        gain = f"{shares[-1] - shares[0]:+.2f}" if shares[0] is not None and shares[-1] is not None else DASH
        return [label, _count(by.get((tool, lvls[0]), {}).get("items")), *(_share(s) for s in shares), gain]

    tools = _order({r["tool"] for r in rows_in if r["tool"] != "all"}, names)
    rows = [row(_badge(names.get(t, t)), t) for t in tools]
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
        rows = [[_badge(names.get(t, t)), _count(by[t].get("repos")), _count(by[t]["items"]),
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
        rows += [[_badge(names.get(tool, tool)) if i == 0 else "", lang, *cells(by[(tool, lang)])] for i, lang in
                 enumerate(mine)]  # fmt: skip
    return rows


def detection(scores: list[dict], names: dict[str, str]) -> Table | None:
    """Recall, precision and F1 on the planted items (mutation-scores.csv, all styles), per tool and language: the
    judged calls, recall on the inserted lines and F1 with their bootstrap intervals over repositories, precision over
    the planted calls and decoys."""
    rows_in = [s for s in scores if s.get("style") == "all"]
    if not rows_in:
        return None

    def cells(s: dict) -> list:
        return [_count(s.get("calls")), _interval(s.get("recall"), s.get("recall_low"), s.get("recall_high")),
                _share(s.get("precision")), _interval(s.get("f1"), s.get("f1_low"), s.get("f1_high"))]  # fmt: skip

    header = ["Tool", "Language", "Calls", Tex(r"Recall [95\,\% CI]", "Recall [95 % CI]"), "Precision",
              Tex(r"F1 [95\,\% CI]", "F1 [95 % CI]")]  # fmt: skip
    return Table("rq5_detection", ["mutation-scores.csv"], "llrlrl", header,
                 _by_tool_language(rows_in, names, cells))  # fmt: skip


def styles(scores: list[dict], names: dict[str, str]) -> Table | None:
    """Recall on the inserted lines per insertion style (mutation-scores.csv), per tool, the languages side by
    side."""
    rows_in = [s for s in scores if s.get("style") in STYLES]
    if not rows_in:
        return None
    langs = _paper_languages(s["language"] for s in rows_in)
    shown = [st for st in STYLES if any(s["style"] == st for s in rows_in)]
    by = {(s["tool"], _language(s["language"]), s["style"]): s for s in rows_in}
    rows = [[_badge(names.get(t, t)), *(_share(by[(t, lang, st)].get("recall")) if (t, lang, st) in by else DASH
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
        candidates(rows("tools.csv")),
        decoys(load_catalog("java").decoys),
        bar(rows("micro-run{i}.csv"), rows("traceability-run{i}.csv"), names),
        corpus(_spec(analysis_dir)),
        assets(rows("runs.csv"), names, run),
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
