"""cdxgen worker: project type per repo language and the command line (no cdxgen binary needed)."""

from pathlib import Path

from workers.cdxgen import main as cw


def test_project_type_only_where_cdxgen_needs_it():
    # 13.x skips the code-level crypto analysis without -t when the BOM lists no package of a language it analyses
    # (micro-java: no Maven package); JS/TS inventory needs the all-types mode; Python is not a cdxgen language
    assert cw.project_type_for("Java") == "java" and cw.project_type_for("go") == "go"
    assert [cw.project_type_for(lang) for lang in ("Python", "JavaScript", "TypeScript", "C", None, "")] == [None] * 6


def test_command_with_and_without_project_type():
    cmd = cw.build_command(Path("/w/repo"), Path("/w/bom.json"), project_type="java")
    assert cmd[:3] == ["cdxgen", "-t", "java"]
    assert {"--include-crypto", "--evidence", "--no-install-deps"} <= set(cmd) and cmd[-1] == "/w/repo"
    assert cmd[cmd.index("-o") + 1] == "/w/bom.json"
    plain = cw.build_command(Path("/w/repo"), Path("/w/bom.json"), evidence=False)
    assert "-t" not in plain and "--evidence" not in plain


def test_each_job_gets_its_own_writable_go_module_cache(monkeypatch, tmp_path):
    # the image's GOPATH cannot be created under cap_drop ALL: `go list` failed and golem found nothing in any Go
    # module with a dependency (e0-micro, 2026-10-22)
    seen = {}

    def fake_run(cmd, *, env, **kwargs):
        seen.update(env)
        Path(cmd[cmd.index("-o") + 1]).write_text("{}")
        return cw.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cw.subprocess, "run", fake_run)
    trace = cw.Trace()
    env = cw.go_env(tmp_path)
    cw.CdxgenClient()._run_cdxgen(tmp_path / "repo", tmp_path / "bom.json", trace, project_type="go", extra_env=env)
    assert seen["GOPATH"] == str(tmp_path / "go") and "-modcacherw" in seen["GOFLAGS"].split()
    assert f"GOPATH={tmp_path / 'go'}" in trace.text()


# lines of cdxgen 13.5.0's output on micro-java without -t and on micro-python (2026-10-31)
CDXGEN_OUTPUT = """\x1b[1mSkipping Maven since dependency installation is disabled.\x1b[0m
WARNING: sun.misc.Unsafe::objectFieldOffset will be removed in a future release
Unable to construct a purl from {"type":"maven","namespace":null,"name":"repo","version":"latest"}
Failure: Failed to enhance CPG: java.util.NoSuchElementException: next on empty iterator
WARN: atom exited with status 1; the analysis may be incomplete.
atom reported a failure and produced no reachables slice for python.
WARN: atom exited with status 1; the analysis may be incomplete.
\x1b[33mSkipping the evidence analysis since the BOM lists no package in a language it supports.\x1b[0m
"""


def test_skipped_or_failed_analysis_becomes_a_tool_warning(monkeypatch, tmp_path):
    assert cw.analysis_warnings(CDXGEN_OUTPUT) == [
        "Failure: Failed to enhance CPG: java.util.NoSuchElementException: next on empty iterator",
        "WARN: atom exited with status 1; the analysis may be incomplete.",
        "atom reported a failure and produced no reachables slice for python.",
        "Skipping the evidence analysis since the BOM lists no package in a language it supports.",
    ]
    assert cw.analysis_warnings("Slices have been successfully written\nWARNING: deprecated\n") == []

    def fake_run(cmd, **kwargs):
        Path(cmd[cmd.index("-o") + 1]).write_text("{}")
        return cw.subprocess.CompletedProcess(cmd, 0, CDXGEN_OUTPUT, "")

    monkeypatch.setattr(cw.subprocess, "run", fake_run)
    trace = cw.Trace()
    cw.CdxgenClient()._run_cdxgen(tmp_path / "repo", tmp_path / "bom.json", trace, project_type="java")
    assert "tool warning: Skipping the evidence analysis" in trace.text()
