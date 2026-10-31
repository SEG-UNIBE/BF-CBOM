"""_SUMMARY.md: Markdown tables of one experiment for a first look, e.g. in an exported zip (CLAUDE.md, Phase 3).

Rendered only from what `analysis report` writes into `<exp>/_ANALYSIS/` (the CSV tables and `tables-run<i>.json`)
plus the stored spec and run log (`_experiment.yaml`, `_run-log.json`), so it can be rebuilt from an export
(`python -m analysis.summary <exp>/_ANALYSIS`) and never shows anything those files don't hold. The CSVs are the data
for plots; this file is for reading.

This module renders the overview, the preamble and the bar exam and puts every section in order (`render`); the
sections of the research questions are in summary_rqs.py, the blocks all of them are built from (the layout, the marks,
`style_issues`) in summary_blocks.py.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import yaml

from common.corpus import CorpusParams, bin_text
from common.experiment import load_run_log, spec_path

from .groundtruth import BAR_FAILED, BAR_PASSED
from .rqs import BAR_EXAM, BAR_EXAM_NOTE, BAR_EXAM_TABLES, RQS, STABILITY, STABILITY_NOTE
from .summary_blocks import (
    BACKGROUND,
    CAVEAT,
    COLUMNS,
    EXAMPLE,
    FAIL,
    LEVEL_ORDER,
    LIST,
    OK,
    _anchor,
    _background,
    _bullets,
    _by_tool,
    _check,
    _columns,
    _details,
    _example,
    _float,
    _gib,
    _int,
    _lang_key,
    _link,
    _n,
    _num,
    _pct,
    _run_list,
    _run_ref,
    _span,
    _text,
    _true,
    _ts,
    _when,
    md_table,
    read_csv,
)
from .summary_rqs import _agreement, _conformity, _llm, _mutation, _normalisation, _reported, _rq1, _stability

# ----- the overview and the preamble -----


SUMMARY_FILE = "_SUMMARY.md"


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
        f"Every section shows its table first, then its checks: {OK} and {FAIL} a verdict, {CAVEAT} a caveat needed to "
        "read the table.",
        f"Below them, collapsed: {COLUMNS} the columns, {EXAMPLE} an example read from the table, {BACKGROUND} the "
        f"background and {LIST} the runs a check counts.",
        "The cells use these marks:",
    )
    lines += _bullets(
        [
            ("–", "not run: the tool does not support the repository's language.",
             "The supported languages are the ones the tool declares (`languages` in `common/tools.yaml`), narrowed "
             "by a spec's `tool_languages`."),
            ("×", "the run failed or timed out."),
            ("1'234", "a number with ' between thousands, so 1234."),
            ("0.43", "a share as a fraction with two decimals, so 43 %, and `1.00` means all."),
            ("a / b", "a cell that combines numbers, such as `x (y)` and `rate [low, high]`, explained under its table "
             "with an example."),
        ]
    )  # fmt: skip
    return lines


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
    their runs) and the runner's (the run log), and `FAIL` when the workers ran another build than the runner, whose
    code then differs from the code that planned and stored the runs."""
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
        text += f" {FAIL} the workers ran another build than the runner"
        text += " (`make up-prod` rebuilds them, the board shows each)"
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
            versions = "not run: no repository in a language it supports"
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
            ("unsupported", "not run: the tool does not support the repository's language."),
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


# ----- the bar exam -----


VERDICT_MARK = {"pass": OK, "fail": FAIL}  # a micro project's verdict in the bar exam's table


BAR_MARK = {BAR_PASSED: OK, BAR_FAILED: FAIL}  # the tool's verdict over its micro projects


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
             "`–`: not run, a language the tool does not support, `error`: the run failed or timed out."),
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
             "on, out of the project's decoys.", "`–`: not run, a language the tool does not support."),
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


# ----- entry points -----


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
