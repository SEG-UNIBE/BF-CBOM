"""The paper's LaTeX tables (analysis/latex.py): rendered from the CSVs only, booktabs, and every layout inside the
single-column text width of svjour3 smallextended at \\small (338 pt), checked with the measured glyph widths."""

from pathlib import Path

import yaml

from analysis import latex
from analysis.tables import TOOLS_COLUMNS, write_csv
from common.tools_registry import load_tools

LONGEST = max((t.get("name") or k for k, t in load_tools().items()), key=latex._glyphs)  # the widest tool name


def _names(n: int = 11) -> dict[str, str]:
    """`n` tools, every one as wide as the widest registered name, so a layout is checked at its worst."""
    return {f"t{i:02d}": LONGEST for i in range(n)}


def _fits(table: latex.Table) -> None:
    assert table.width() <= latex.TEXTWIDTH_PT, (table.name, round(table.width(), 1))
    assert not latex.too_wide([table])


def test_glyph_widths_are_the_measured_ones():
    assert latex._glyphs("0.38") == 14.0 and latex._glyphs("1,210") == 18.0  # measured in svjour3 at \small
    assert 54.8 <= latex._glyphs("CryptobomForge") <= 56.0  # measured 54.85 pt, kerning only narrows
    assert latex._glyphs("%") > latex._glyphs("a")


def test_a_table_renders_booktabs_at_textwidth_with_groups_and_escapes():
    table = latex.Table(
        "rq9_demo",
        ["demo-run1.csv"],
        "lrr",
        ["Tool", "a_b", latex.Tex(r"50\,\%", "50 %")],
        [["x&y", latex._count(1210), latex.DASH], latex.MIDRULE, latex.Sub("group"), ["z", "1", "2"]],
        [(2, 3, "Both")],
    )
    text = table.render("exp")
    lines = text.splitlines()
    assert lines[0].startswith("% tab_rq9_demo.tex from exp (demo-run1.csv)") and "do not edit" in lines[0]
    assert lines[2] == r"\begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}lrr@{}}" and lines[-1] == r"\end{tabular*}"
    assert lines[3] == r"\toprule" and lines[-2] == r"\bottomrule" and "|" not in lines[2]  # no vertical lines
    assert r" & \multicolumn{2}{c}{Both} \\" in lines and r"\cmidrule(l){2-3}" in lines  # trimmed inside only
    assert r"Tool & a\_b & 50\,\% \\" in lines and r"x\&y & 1{,}210 & -- \\" in lines
    assert r"\multicolumn{3}{@{}l}{\emph{group}} \\" in lines and lines.count(r"\midrule") == 2
    too = latex.Table("x", [], "l", ["a"], [["w" * 99]])
    assert latex.too_wide([too]) and "WIDER THAN THE TEXT" in too.render("e")
    wide = latex.Table("x", [], "lX", ["a", "b"], [["1", "text " * 99]])
    assert r"\begin{tabularx}{\textwidth}{@{}l>{\raggedright\arraybackslash}X@{}}" in wide.render("e")
    assert wide.width() < 100  # an X column wraps: it counts with its minimum


def test_a_group_label_wider_than_its_columns_widens_the_table():
    narrow = latex.Table("x", [], "lrr", ["a", "1", "2"], [])
    grouped = latex.Table("x", [], "lrr", ["a", "1", "2"], [], [(2, 3, "A rather long group label")])
    assert grouped.width() > narrow.width() + 50


def test_the_bar_table_gives_the_verdicts_eligible_tools_first():
    def r(tool, lang, verdict, lenient=0, repo=0, decoys="", tailored=""):
        return {"tool": tool, "language": lang, "verdict": verdict, "lenient": lenient, "repo": repo,
                "decoys_hit": decoys, "tailored": tailored}  # fmt: skip

    micro = [
        r("cdxgen", "java", "fail", 0, 6), r("cdxgen", "go", "pass", 9, 13), r("cdxgen", "python", "unsupported"),
        r("qscan", "java", "fail", 5, 5), r("qscan", "python", "fail", 5, 5), r("qscan", "go", "fail", 5, 5),
        r("ciphersift", "java", "pass", 14, 14, "comment;log-message", "True"),
    ]  # fmt: skip
    names = {"cdxgen": "cdxgen", "qscan": "qScan", "ciphersift": "CipherSift"}
    table = latex.bar(micro, names)
    lines = table.render("bar-micro").splitlines()
    assert r"Tool & Java & Python & Go & Verdict & Decoys reported on \\" in lines  # the paper's languages in order
    assert r"CipherSift$\ast$ & 14 (14) & -- & -- & eligible & comment, log message \\" in lines
    assert r"cdxgen & 0 (6) & -- & 9 (13) & eligible in Go & none \\" in lines
    assert r"qScan & 5 (5) & 5 (5) & 5 (5) & not eligible & none \\" in lines
    body = lines[lines.index(r"\midrule") + 1 :]
    assert body.index(r"\midrule") == 2 and body[3].startswith("qScan")  # eligible first, then the others
    assert latex.bar([], names) is None


def test_every_layout_fits_the_text_width_at_its_worst():
    """Eleven tools named like the widest, and the largest values a cell of the paper's experiments can hold: records
    up to 999'999 (corpus-main's largest is 347'678), a median scan up to 999 s (its largest 31.6 s), a p90 up to the
    hour, a timeout on every repository."""
    names = _names()
    tools = list(names)
    langs = ["Java", "Python", "Go"]
    micro = [{"tool": t, "language": lg.lower(), "verdict": "pass", "lenient": 14, "repo": 14,
              "decoys_hit": "comment;log-message;crc32;base64", "tailored": "True"}
             for t in tools for lg in langs]  # fmt: skip
    _fits(latex.bar(micro, names))
    repos = [{"full_name": f"o/r{i}", "language": lg, "stars": 250_000, "checkout_bytes": 1536 * 1024**2,
              "source_bytes": 999 * 1024**2} for i, lg in enumerate(langs * 99)]  # fmt: skip
    _fits(latex.corpus({"repos": repos}))
    units = [{"tool": t, "packaging": "per algorithm", "records": 999_999, "items": 99_999, "loc_line": 1,
              "loc_none": 0, "outside_code": 1} for t in tools]  # fmt: skip
    unit_repos = [{"tool": t, "items": 999} for t in tools]
    trace = [{"tool": t, "asset_type": "algorithm", "records": 3, "located_line": 1, "strong": 1, "source": "git"}
             for t in tools]  # fmt: skip
    _fits(latex.volume(units, unit_repos, trace, names))
    runtime = [{"tool": t, "language": lg, "median_sec": 999.0, "p90_sec": 3600.0, "timeouts": 162,
                "peak_median_mb": 24 * 1024, "peak_max_mb": 99 * 1024, "cores_median": 64.0}
               for t in tools for lg in ["all", *langs]]  # fmt: skip
    _fits(latex.cost(runtime, names))
    schema = [{"tool": t, "bom_format": "CycloneDX", "spec_version": "1.4-cbom-1.0", "schema": "1.7", "valid": "False",
               "error_kinds": "enum:a.relatedCryptoMaterialProperties.type=99999"} for t in tools]  # fmt: skip
    _fits(latex.conformance(schema, names))
    levels = [{"dimension": "algorithm", "tool": t, "level": lv, "items": 99_999, "share": 1.0}
              for t in [*tools, "all"] for lv in latex.LEVEL_ORDER]  # fmt: skip
    _fits(latex.ladder(levels, names))
    registry = [{"kind": "addition", "gap": "missing-family", "name": "Hybrid-KEM", "count": 99_999, "repos": 9_999,
                 "tools": ";".join(tools), "note": "a `reason` " * 30}]  # fmt: skip
    _fits(latex.registry(registry))
    match = [{"matcher": m, "level": lv, "items": 999_999, "k1": 1, "k2": 1, "k3": 1, "k4plus": 1,
              **{f"{d}_groups": 99_999 for d in latex.MATCH_DIMS}, **{f"{d}_agree": 1.0 for d in latex.MATCH_DIMS}}
             for m in ("exact", "raqun") for lv in latex.LEVEL_ORDER]  # fmt: skip
    _fits(latex.kdist(match))
    _fits(latex.dimensions(match))
    scores = [{"dimension": "key_size", "tool": t, "repos": 9_999, "items": 99_999, "rate": 0.5, "ci_low": 0.25,
               "ci_high": 0.75, "exclusivity": 0.5, "coverage": 0.5} for t in tools]  # fmt: skip
    for table in latex.rates(scores, names):
        _fits(table)
    mscores = [{"tool": t, "language": lg, "style": st, "calls": 9_999, "decoys": 9_999, "recall_site": 0.5,
                "recall_low": 0.25, "recall_high": 0.75, "precision": 0.5, "f1": 0.5, "f1_low": 0.25, "f1_high": 0.75,
                "offedit_mutants": 9_999, "offedit_share": 0.5, "extra_units_median": 999.5,
                "extra_sites_median": 999.5}
               for t in tools for lg in langs for st in ("all", *latex.STYLES)]  # fmt: skip
    for build in (latex.detection, latex.styles, latex.offedit):
        _fits(build(mscores, names))


def test_write_names_each_table_by_its_part_and_replaces_old_ones(tmp_path: Path):
    exp = tmp_path / "corpus-x"
    out = exp / "_ANALYSIS"
    out.mkdir(parents=True)
    repo = {"full_name": "o/a", "language": "Go", "stars": 120, "checkout_bytes": 4 * 1024**2, "source_bytes": 1024**2}
    (exp / "_experiment.yaml").write_text(yaml.safe_dump({"name": "corpus-x", "repos": [repo]}))
    write_csv(out / "tools.csv", [{"tool": "cbomkit", "name": "CBOMkit"}], TOOLS_COLUMNS)
    runtime = [{"tool": "cbomkit", "language": lg, "median_sec": 12.5, "p90_sec": 20} for lg in ("all", "Go")]
    write_csv(out / "runtime.csv", runtime, ["tool", "language", "median_sec", "p90_sec"])
    (out / "latex").mkdir()
    (out / "latex" / "tab_rq9_gone.tex").write_text("old")
    written = latex.write(out)
    folder = out / latex.LATEX_DIR
    assert sorted(p.name for p in folder.iterdir()) == ["tab_design_corpus.tex", "tab_rq1_cost.tex"]
    assert [t.name for t in written] == ["design_corpus", "rq1_cost"] and not latex.too_wide(written)
    cost = (folder / "tab_rq1_cost.tex").read_text()
    assert "from corpus-x (runtime.csv)" in cost and r"CBOMkit & 12.5 & 20.0 & 0 & -- & -- & -- \\" in cost
    assert "pt wide, spread to the 338 pt text width." in cost
    corpus = (folder / "tab_design_corpus.tex").read_text()
    assert r"Go & 1 & 120 & 4.0 & 1.0 & 0.25 & 0 \\" in corpus and r"\cmidrule(lr){4-5}\cmidrule(l){6-7}" in corpus


def test_every_registered_tool_has_a_name_for_the_tables():
    assert all(t.get("name") for t in load_tools().values())
