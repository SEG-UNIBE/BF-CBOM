"""cdxgen worker: project type per repo language and the command line (no cdxgen binary needed)."""

from pathlib import Path

from workers.cdxgen import main as cw


def test_project_type_only_where_cdxgen_needs_it():
    # without -t the code-level crypto analysis falls back to Java; JS/TS inventory needs the all-types mode
    assert cw.project_type_for("Python") == "python" and cw.project_type_for("go") == "go"
    assert [cw.project_type_for(lang) for lang in ("Java", "JavaScript", "TypeScript", "C", None, "")] == [None] * 6


def test_command_with_and_without_project_type():
    cmd = cw.build_command(Path("/w/repo"), Path("/w/bom.json"), project_type="python")
    assert cmd[:3] == ["cdxgen", "-t", "python"]
    assert {"--include-crypto", "--evidence", "--no-install-deps"} <= set(cmd) and cmd[-1] == "/w/repo"
    assert cmd[cmd.index("-o") + 1] == "/w/bom.json"
    plain = cw.build_command(Path("/w/repo"), Path("/w/bom.json"), evidence=False)
    assert "-t" not in plain and "--evidence" not in plain
