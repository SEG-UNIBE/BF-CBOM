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
