"""Three workers of tools that failed the bar exam (qelens, cryptarium, qScan): command line, output handling
and failure reporting. The tools are not installed in the test environment; a fake executable stands in."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from common.models import Trace

ROOT = Path(__file__).resolve().parents[1]
CBOM = {"bomFormat": "CycloneDX", "specVersion": "1.6", "components": []}


def _worker(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_worker", ROOT / "workers" / name / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake(tmp_path: Path, body: str) -> str:
    """A fake tool: `body` is Python run with `args` (argv[1:]) and `out(path)` writing the CBOM to a path."""
    script = tmp_path / "fake-tool"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        f"def out(p): json.dump({CBOM!r}, open(p, 'w'))\n" + body
    )
    script.chmod(0o755)
    return str(script)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "work" / "repo"
    path.mkdir(parents=True)
    (path / "a.py").write_text("import hashlib\n")
    return path


def test_qelens_writes_stdout_to_the_cbom_file(tmp_path, repo, monkeypatch):
    qe = _worker("qelens")
    monkeypatch.setattr(
        qe, "BINARY", _fake(tmp_path, f"sys.stderr.write('Scanning...\\n'); print(json.dumps({CBOM!r}))")
    )
    trace, out = Trace(), tmp_path / "work" / "cbom.json"
    qe.run_qelens(repo, out, trace, timeout=30)
    assert json.loads(out.read_text()) == CBOM
    assert any(f"--path {repo} --format cyclonedx" in n for n in trace._items)
    monkeypatch.setattr(qe, "BINARY", _fake(tmp_path, "sys.stderr.write('boom\\n'); sys.exit(3)"))
    with pytest.raises(RuntimeError, match="qelens_no_output rc=3: boom"):
        qe.run_qelens(repo, out, Trace(), timeout=30)


def test_cryptarium_command_and_missing_output(tmp_path, repo, monkeypatch):
    cr = _worker("cryptarium")
    monkeypatch.setattr(cr, "BINARY", _fake(tmp_path, "out(args[args.index('-output') + 1]); sys.exit(0)"))
    trace, out = Trace(), tmp_path / "work" / "cbom.json"
    cr.run_cryptarium(repo, out, trace, timeout=30)
    assert json.loads(out.read_text()) == CBOM
    cmd = next(n for n in trace._items if n.startswith("cryptarium cmd:"))
    assert "scan -format cbom -deterministic -output" in cmd and cmd.endswith(str(repo))
    out.unlink()
    monkeypatch.setattr(cr, "BINARY", _fake(tmp_path, "sys.exit(1)"))
    with pytest.raises(RuntimeError, match="cryptarium_no_output rc=1"):
        cr.run_cryptarium(repo, out, Trace(), timeout=30)


def test_qscan_findings_exit_code_is_no_error_and_no_llm_key_reaches_it(tmp_path, repo, monkeypatch):
    qs = _worker("qscan")
    body = (
        "assert not any(k in os.environ for k in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'QK_LLM_API_KEY'))\n"
        "out(args[args.index('-o') + 1]); sys.exit(1)\n"  # 1 = findings at the severity threshold
    )
    monkeypatch.setattr(qs, "BINARY", _fake(tmp_path, body))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    (repo / qs.CONFIG_FILE).write_text("{}")
    trace, out = Trace(), tmp_path / "work" / "cbom.json"
    qs.run_qscan(repo, out, trace, timeout=30)
    assert json.loads(out.read_text()) == CBOM
    assert any("--format cbom -o" in n for n in trace._items)
    assert any(qs.CONFIG_FILE in n for n in trace._items)  # a repo's own config is recorded
    monkeypatch.setattr(qs, "BINARY", _fake(tmp_path, "out(args[args.index('-o') + 1]); sys.exit(2)"))
    with pytest.raises(RuntimeError, match="qscan_no_output rc=2"):  # 2 = usage or I/O error, even with output
        qs.run_qscan(repo, out, Trace(), timeout=30)


def test_a_hanging_tool_times_out(tmp_path, repo, monkeypatch):
    qe = _worker("qelens")
    monkeypatch.setattr(qe, "BINARY", _fake(tmp_path, "import time; time.sleep(30)"))
    with pytest.raises(TimeoutError, match="qelens_timeout"):
        qe.run_qelens(repo, tmp_path / "cbom.json", Trace(), timeout=0.5)
