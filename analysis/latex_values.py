r"""The paper's key numbers as LaTeX macros, rendered only from what `analysis report` writes, like the tables:
`_ANALYSIS/latex/val_<experiment>.tex` (`val_corpus_main.tex`), one block per number.

A macro is named `\<Kind><What>`, the kind being the first part of the experiment's name (`Bar`, `Corpus`,
`Mutation`), so the files of several experiments load side by side, and a name defined twice stops LaTeX. Each number
has two macros. `\CorpusScans` prints it as the paper prints numbers, through siunitx's `\num`, so the thousands
separator is the preamble's everywhere, and ends in `\xspace` like the paper's other text macros. `\CorpusScansRaw` is
the bare value, digits and a decimal point, for arithmetic. A whole number below 100 has two more, the count spelled
out: `\BarPassedWord` (eleven) and `\BarPassedWordCap` (Eleven, at the start of a sentence), written as plain text,
so the paper's source names what it prints without a command around a macro. The paper's preamble needs siunitx and
xspace.

A block is a comment naming the table the number was read from and what it counts, then one macro per line, and an
empty line parts it from the next, so a value is looked up by eye. A number is written only when its data is there.
Few on purpose: the counts of the design and the headline of each question, no value of a single tool and no tool
name. A macro replaces a number, never a judgment: a sentence that interprets one ("the largest step") still has to be
reread when the value changes.

    python -m analysis.latex <exp>/_ANALYSIS [run]    # rebuilds the tables and this file from an exported folder
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml

from common.experiment import spec_path

from .summary import PAPER_LANGUAGES
from .summary_blocks import LEVEL_ORDER, _float, _int, _true, read_csv

FILE_PREFIX = "val_"
_LANGUAGES = {lang.lower(): lang for lang in PAPER_LANGUAGES}


@dataclass(frozen=True)
class Value:
    """One macro: `what` after the kind (letters only), the raw `value`, the table it was read from and what it
    counts (the line's comment)."""

    what: str
    value: str
    source: str
    note: str


def kind(experiment: str) -> str:
    """The macros' prefix: the first part of the experiment's name, letters only (`corpus-main`: `Corpus`)."""
    first = next((part for part in re.split(r"[^A-Za-z]+", experiment) if part), "")
    return first.capitalize() or "Exp"


def _lang(row: dict) -> str:
    """The row's language as the paper writes it, empty for a language the paper does not analyze."""
    return _LANGUAGES.get((row.get("language") or "").lower(), "")


def _share(part: float, whole: float) -> str:
    """A share as a fraction with two decimals, as in the tables."""
    return f"{part / whole:.2f}"


def _from(source: str):
    """A constructor for the values read from one table."""

    def value(what: str, number, note: str) -> Value:
        return Value(what, str(number), source, note)

    return value


def design(runs: list[dict]) -> list[Value]:
    """The design's counts (runs.csv): repositories, tools, the tool and language pairs, the scans and how they
    ended. A scan is a stored run of a tool on a variant it supports, in any repetition."""
    if not runs:
        return []
    v = _from("runs.csv")
    repos = {r["repo"]: _lang(r) for r in runs}
    per = Counter(lang for lang in repos.values() if lang)
    out = [v("Repos", len(repos), "repositories")]
    out += [v(f"Repos{lang}", per[lang], f"repositories in {lang}") for lang in PAPER_LANGUAGES if per[lang]]
    if len(per) > 1 and len(set(per.values())) == 1:
        out.append(v("ReposPerLanguage", next(iter(per.values())), "repositories in every language"))
    ran = [r for r in runs if r["status"] != "unsupported"]
    scanned = {lang: {r["tool"] for r in ran if _lang(r) == lang} for lang in PAPER_LANGUAGES}
    out.append(v("Tools", len({r["tool"] for r in runs}), "tools"))
    for lang in PAPER_LANGUAGES:
        if scanned[lang]:
            out.append(v(f"Tools{lang}", len(scanned[lang]), f"tools that scan {lang}"))
    ok = sum(r["status"] == "ok" for r in ran)
    out += [
        v("Pairs", sum(len(tools) for tools in scanned.values()), "tool and language pairs"),
        v("Scans", len(ran), "scans: runs of a tool on a variant it supports"),
        v("ScansOk", ok, "scans that gave a CBOM"),
        v("ScansFailed", len(ran) - ok, "scans that failed, timed out or ran out of memory"),
    ]
    return out


def bar(micro: list[dict]) -> list[Value]:
    """The bar exam (micro-run<i>.csv): how many tools passed, the known calls and decoys of a micro project, the
    bar, and how close the cut is (the lowest passing and the highest failing score of a tool in a language)."""
    scored = [r for r in micro if r.get("verdict") in ("pass", "fail")]
    if not scored:
        return []
    v = _from("micro-run<i>.csv")
    verdicts: dict[str, list[str]] = defaultdict(list)
    for r in scored:
        verdicts[r["tool"]].append(r["verdict"])
    passed = [t for t, vs in verdicts.items() if "pass" in vs]
    out = [
        v("Passed", len(passed), "tools that passed the bar in a language"),
        v("PassedEverywhere", sum("fail" not in verdicts[t] for t in passed), "of them, in every language scored"),
        v("Failed", len(verdicts) - len(passed), "tools below the bar in every language"),
        v("KnownCalls", max(_int(r["n_items"]) for r in scored), "known calls of a micro project"),
        v("Threshold", max(_int(r["bar_items"]) for r in scored), "the bar: calls to find in one language"),
        v("KnownDecoys", max(_int(r.get("decoys")) for r in scored), "decoys of a micro project"),
    ]
    lowest = [_int(r["lenient"]) for r in scored if r["verdict"] == "pass"]
    highest = [_int(r["lenient"]) for r in scored if r["verdict"] == "fail"]
    if lowest:
        out.append(v("LowestPass", min(lowest), "the lowest passing score of a tool in a language"))
    if highest:
        out.append(v("HighestFail", max(highest), "the highest failing score of a tool in a language"))
    return out


def findings(levels: list[dict], items: list[dict], schema: list[dict]) -> list[Value]:
    """Content to Agreement in a handful of numbers: the algorithm items in all and per tool (levels-run<i>.csv, the
    top level), those resting on a vocabulary addition (items-run<i>.csv), the documents that validate
    (schema-run<i>.csv), and the two ends of the ladder: the share of items another voter group also reports before
    normalization (raw names) and after it."""
    out: list[Value] = []
    algorithm = [r for r in levels if r.get("dimension") == "algorithm"]
    present = [lv for lv in LEVEL_ORDER if any(r["level"] == lv for r in algorithm)]
    whole = {r["level"]: r for r in algorithm if r["tool"] == "all"}
    ends = (present[0], present[-1]) if present and present[-1] in whole else None
    v = _from("levels-run<i>.csv")
    if ends:
        per_tool = [_int(r["items"]) for r in algorithm if r["level"] == ends[1] and r["tool"] != "all"]
        out.append(v("Items", _int(whole[ends[1]]["items"]), "algorithm items of all tools"))
        if per_tool:
            out += [v("ItemsMin", min(per_tool), "items of the tool with the fewest"),
                    v("ItemsMax", max(per_tool), "items of the tool with the most")]  # fmt: skip
    if items:
        off = sum(not _true(r.get("registry")) for r in items)
        of_items = _from("items-run<i>.csv")
        out += [of_items("ItemsOffRegistry", off, "items whose family is a vocabulary addition"),
                of_items("OffRegistryShare", _share(off, len(items)), "their share of all items")]  # fmt: skip
    checked = [r for r in schema if (r.get("valid") or "").lower() in ("true", "false")]
    if checked:
        valid = sum(_true(r["valid"]) for r in checked)
        of_schema = _from("schema-run<i>.csv")
        out += [of_schema("Documents", len(checked), "CBOMs checked against their schema"),
                of_schema("DocumentsValid", valid, "of them, valid")]  # fmt: skip
    ladder_ends = (("ConfirmedBefore", "before normalization"), ("ConfirmedAfter", "after it"))
    for (what, note), level in zip(ladder_ends, ends or (), strict=False):
        row = whole.get(level)
        if row and _int(row["items"]):
            share = _share(_int(row["confirmed"]), _int(row["items"]))
            out.append(v(what, share, f"share of items another voter group also reports, {note} ({level})"))
    return out


def detection(mutants: list[dict], scores: list[dict]) -> list[Value]:
    """Detection (mutants.csv, mutation-scores.csv): the mutants, their injected calls and decoys, the variants of
    a repository when every repository has as many, and the range of F1 over tools and languages (all styles)."""
    if not mutants:
        return []
    v = _from("mutants.csv")
    per_repo = Counter(r["repo"] for r in mutants)
    out = [
        v("Mutants", len(mutants), "mutants"),
        v("InjectedCalls", sum(r.get("kind") == "call" for r in mutants), "mutants with an injected call"),
        v("InjectedDecoys", sum(r.get("kind") == "decoy" for r in mutants), "mutants with an injected decoy"),
    ]
    if len(set(per_repo.values())) == 1:
        variants = next(iter(per_repo.values())) + 1
        out.append(v("VariantsPerRepo", variants, "variants of a repository: the original and its mutants"))
    f1 = [f for s in scores if s.get("style") == "all" and _lang(s) and (f := _float(s.get("f1"))) is not None]
    if f1:
        v = _from("mutation-scores.csv")
        out += [v("FscoreMin", f"{min(f1):.2f}", "lowest F1 of a tool in a language"),
                v("FscoreMax", f"{max(f1):.2f}", "highest F1 of a tool in a language")]  # fmt: skip
    return out


def build(analysis_dir: Path, *, run: int = 1) -> list[Value]:
    """Every value the folder's CSVs have data for, in the paper's order."""
    analysis_dir = Path(analysis_dir)

    def rows(name: str) -> list[dict]:
        return read_csv(analysis_dir / name.format(i=run))

    values = [
        *design(rows("runs.csv")),
        *bar(rows("micro-run{i}.csv")),
        *findings(rows("levels-run{i}.csv"), rows("items-run{i}.csv"), rows("schema-run{i}.csv")),
        *detection(rows("mutants.csv"), rows("mutation-scores.csv")),
    ]
    return [Value(v.what, v.value, v.source.replace("<i>", str(run)), v.note) for v in values]


RAW = "Raw"  # the suffix of a number's bare twin: \CorpusScansRaw
WORD, WORD_CAP = "Word", "WordCap"  # a count spelled out: \BarPassedWord (eleven), \BarPassedWordCap (Eleven)
_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()  # fmt: skip
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


def spelled(value: str) -> str | None:
    """A whole number below 100 in words (`21`: twenty-one), None for anything else: a larger number or a share is
    never spelled out in the paper."""
    if not re.fullmatch(r"\d{1,2}", value):
        return None
    n = int(value)
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens - 2] + (f"-{_ONES[ones]}" if ones else "")


def render(experiment: str, values: list[Value]) -> str:
    """The file: what the macros are and need, then one block per number: its source and meaning as a comment, its
    printed macro, its bare twin and the two word macros of a small count, one per line."""
    prefix = kind(experiment)
    names = [prefix + v.what for v in values]
    words = [spelled(v.value) for v in values]
    every = names + [n + RAW for n in names]
    every += [n + suffix for n, word in zip(names, words, strict=True) if word for suffix in (WORD, WORD_CAP)]
    bad = [n for n in names if not re.fullmatch(r"[A-Za-z]+", n)] + [n for n, c in Counter(every).items() if c > 1]
    if bad:
        raise ValueError(f"macro names must be letters only and unique: {sorted(set(bad))}")
    eg = names[0] if names else prefix
    small = next((n for n, word in zip(names, words, strict=True) if word), None)
    lines = [
        f"% {file_name(experiment)} from {experiment}, written by `analysis report`: do not edit, rerun it.",
        f"% The paper's key numbers as macros. \\{eg} prints a number as the paper prints numbers (siunitx's \\num,",
        f"% so the thousands separator is the preamble's), and \\{eg}{RAW} is the bare value, for arithmetic.",
    ]
    if small:  # the header names the word macros only when the file has some
        lines += [
            f"% A whole number below 100 also comes spelled out: \\{small}{WORD} and, at the start of a sentence,",
            f"% \\{small}{WORD_CAP}.",
        ]
    lines += [
        "% The preamble needs siunitx and xspace.",
        "% A macro replaces a number, never a judgment: reread a sentence when its value changes.",
    ]
    for name, word, v in zip(names, words, values, strict=True):
        lines += [
            "",
            f"% {v.source}: {v.note}",
            f"\\newcommand{{\\{name}}}{{\\num{{{v.value}}}\\xspace}}",
            f"\\newcommand{{\\{name}{RAW}}}{{{v.value}}}",
        ]
        if word:
            lines += [
                f"\\newcommand{{\\{name}{WORD}}}{{{word}\\xspace}}",
                f"\\newcommand{{\\{name}{WORD_CAP}}}{{{word.capitalize()}\\xspace}}",
            ]
    return "\n".join(lines) + "\n"


def file_name(experiment: str) -> str:
    return f"{FILE_PREFIX}{re.sub(r'[^A-Za-z0-9]+', '_', experiment).strip('_')}.tex"


def write(analysis_dir: Path, out_dir: Path, *, run: int = 1) -> Path | None:
    """Write the experiment's values into `out_dir`, replacing the file written before; None without any value."""
    analysis_dir = Path(analysis_dir)
    for old in Path(out_dir).glob(f"{FILE_PREFIX}*.tex"):
        old.unlink()
    values = build(analysis_dir, run=run)
    if not values:
        return None
    spec_file = spec_path(analysis_dir.parent)
    spec = (yaml.safe_load(spec_file.read_text(encoding="utf-8")) if spec_file.exists() else None) or {}
    experiment = spec.get("name") or analysis_dir.parent.name
    target = Path(out_dir) / file_name(experiment)
    target.write_text(render(experiment, values), encoding="utf-8")
    return target
