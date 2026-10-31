"""The sections of _SUMMARY.md that answer the research questions (analysis/rqs.py): RQ1 volume to RQ5 detection,
then run-to-run stability and LLM usage, rendered from the CSVs `analysis report` writes. summary.py puts them in
order, the blocks they are built from are in summary_blocks.py.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable

from .rqs import RQ1, RQ2, RQ3, RQ4, RQ5, STABILITY, STABILITY_NOTE
from .summary_blocks import (
    CAVEAT,
    LEVEL_ORDER,
    LIST,
    _background,
    _bullets,
    _check,
    _columns,
    _cores,
    _details,
    _example,
    _false,
    _float,
    _gib,
    _int,
    _lang_key,
    _link,
    _n,
    _num,
    _pct,
    _text,
    _true,
    md_table,
)
from .tables import LOCATION_KINDS, RUNTIME_BOOTSTRAP, RUNTIME_MIN_REPOS

RESOLUTION_SHOWN = ["mapped", "ambiguous", "api", "non-crypto", "undefined"]  # analysis/normalize/engine.RESOLUTIONS


COVERAGE_SHOWN = ["key_size", "mode", "padding", "curve", "digest"]


# ----- RQ1 volume -----


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


# ----- RQ2 conformity -----


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


# ----- RQ3 normalisation -----


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


# ----- RQ4 agreement -----


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


# ----- RQ5 detection -----


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


# ----- stability and LLM usage -----


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
