"""The layout rules of _SUMMARY.md (analysis/summary_blocks.py `style_issues`): one sentence per line, no semicolons or
double quotes in prose, collapsibles balanced and captioned with their emoji, and the blocks in their order. The
rendered summaries themselves are checked in test_analysis.py and test_mutation_analysis.py."""

from pathlib import Path

from analysis import summary_blocks as sb


def test_style_issues_finds_each_broken_rule():
    assert sb.style_issues("One sentence.\nAnother one, with `code; inside`.\n| a | `x; y` |") == []
    assert sb.style_issues('```json\n{ "a": 1; }\n```') == []  # fenced code is data
    assert any("two sentences" in i for i in sb.style_issues("First. Second."))
    assert any("two sentences" in i for i in sb.style_issues("- `col`: first. `code` second."))
    assert any("semicolon:" in i for i in sb.style_issues("one; two"))
    assert any("double quote" in i for i in sb.style_issues('see "Runs"'))
    assert any("table cell" in i for i in sb.style_issues("| a | b; c |"))
    assert any("emoji" in i for i in sb.style_issues("<details>\n<summary>Columns</summary>\n\nx\n\n</details>"))
    assert any("unbalanced" in i for i in sb.style_issues(f"<details>\n<summary>{sb.COLUMNS} Columns</summary>"))
    assert sb.style_issues("[Units per tool](#units-per-tool) counts. Items") != []  # a link is no sentence end
    assert sb.style_issues("A link to [Runs](#runs).") == []


def test_blocks_render_in_their_layout():
    cols = sb._columns([("a", "the first.", "A second sentence."), ("b", "one more.")])
    assert cols[:3] == ["<details>", f"<summary>{sb.COLUMNS} Columns</summary>", ""]
    assert cols[3:6] == ["- `a`: the first.", "  A second sentence.", "- `b`: one more."]
    assert cols[-2:] == ["</details>", ""] and sb._columns([]) == []
    assert sb._bullet("Level", "the term.", "- `L0`: a nested bullet.", code=False) == [
        "- **Level**: the term.", "  - `L0`: a nested bullet."]  # fmt: skip
    assert sb._example("One.", "Two.")[1] == f"<summary>{sb.EXAMPLE} Example</summary>"
    assert sb._background("Why", ["x"])[1] == f"<summary>{sb.BACKGROUND} Why</summary>"
    assert sb._check(sb.FAIL, "Bad.", "Why.") == [f"{sb.FAIL} Bad.", "Why.", ""]
    items = [f"run {i}" for i in range(sb.RUN_LIST_MAX + 2)]
    listed = sb._run_list("Runs without a result", items)
    assert listed[1] == f"<summary>{sb.LIST} Runs without a result ({sb.RUN_LIST_MAX + 2})</summary>"
    assert "- … and 2 more (`runs.csv`)" in listed and sb._run_list("x", []) == []
    assert sb._link("Units per tool") == "[Units per tool](#units-per-tool)"
    assert sb._text("A.", "", "B.") == ["A.", "B.", ""] and sb._text() == []


def test_marks_are_set_in_one_place():
    """Every mark is written once in the analysis code, in summary_blocks.py, so changing it there changes the
    whole summary, the bar exam's verdicts and "How to read this summary" included."""
    marks = (sb.COLUMNS, sb.EXAMPLE, sb.BACKGROUND, sb.LIST, sb.OK, sb.FAIL, sb.CAVEAT)
    assert len(set(marks)) == len(marks)
    code = "".join(f.read_text(encoding="utf-8") for f in sorted(Path(sb.__file__).parent.rglob("*.py")))
    assert {m: code.count(m) for m in marks} == dict.fromkeys(marks, 1)
