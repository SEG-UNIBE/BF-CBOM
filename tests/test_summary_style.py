"""The layout rules of _SUMMARY.md (analysis/summary.py `style_issues`): one sentence per line, no semicolons or
double quotes in prose, collapsibles balanced and captioned with their emoji, and the blocks in their order. The
rendered summaries themselves are checked in test_analysis.py and test_mutation_analysis.py."""

from analysis import summary as sm


def test_style_issues_finds_each_broken_rule():
    assert sm.style_issues("One sentence.\nAnother one, with `code; inside`.\n| a | `x; y` |") == []
    assert sm.style_issues('```json\n{ "a": 1; }\n```') == []  # fenced code is data
    assert any("two sentences" in i for i in sm.style_issues("First. Second."))
    assert any("two sentences" in i for i in sm.style_issues("- `col`: first. `code` second."))
    assert any("semicolon:" in i for i in sm.style_issues("one; two"))
    assert any("double quote" in i for i in sm.style_issues('see "Runs"'))
    assert any("table cell" in i for i in sm.style_issues("| a | b; c |"))
    assert any("emoji" in i for i in sm.style_issues("<details>\n<summary>Columns</summary>\n\nx\n\n</details>"))
    assert any("unbalanced" in i for i in sm.style_issues("<details>\n<summary>📋 Columns</summary>"))
    assert sm.style_issues("[Units per tool](#units-per-tool) counts. Items") != []  # a link is no sentence end
    assert sm.style_issues("A link to [Runs](#runs).") == []


def test_blocks_render_in_their_layout():
    cols = sm._columns([("a", "the first.", "A second sentence."), ("b", "one more.")])
    assert cols[:3] == ["<details>", "<summary>📋 Columns</summary>", ""]
    assert cols[3:6] == ["- `a`: the first.", "  A second sentence.", "- `b`: one more."]
    assert cols[-2:] == ["</details>", ""] and sm._columns([]) == []
    assert sm._bullet("Level", "the term.", "- `L0`: a nested bullet.", code=False) == [
        "- **Level**: the term.", "  - `L0`: a nested bullet."]  # fmt: skip
    assert sm._example("One.", "Two.")[1] == "<summary>💡 Example</summary>"
    assert sm._background("Why", ["x"])[1] == "<summary>📖 Why</summary>"
    assert sm._check(sm.FAIL, "Bad.", "Why.") == ["⛔ Bad.", "Why.", ""]
    items = [f"run {i}" for i in range(sm.RUN_LIST_MAX + 2)]
    listed = sm._run_list("Runs without a result", items)
    assert listed[1] == f"<summary>🗂️ Runs without a result ({sm.RUN_LIST_MAX + 2})</summary>"
    assert "- … and 2 more (`runs.csv`)" in listed and sm._run_list("x", []) == []
    assert sm._link("Units per tool") == "[Units per tool](#units-per-tool)"
    assert sm._text("A.", "", "B.") == ["A.", "B.", ""] and sm._text() == []
