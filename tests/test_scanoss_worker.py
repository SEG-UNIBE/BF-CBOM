"""SCANOSS crypto-finder worker: command line, error contract and version string.

The tool is not installed in the test environment, so `crypto-finder` is replaced by a fake executable.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from common.models import Trace

_spec = importlib.util.spec_from_file_location(
    "scanoss_worker", Path(__file__).resolve().parents[1] / "workers" / "scanoss" / "main.py"
)
scanoss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scanoss)


def test_command_is_offline_with_the_frozen_rules_and_the_worker_timeout():
    cmd = scanoss.build_command(Path("/w/repo"), Path("/w/cbom.json"), timeout_sec=1770)
    assert cmd[:2] == [scanoss.BINARY, "scan"] and cmd[-1] == "/w/repo"
    assert "--no-remote-rules" in cmd and cmd[cmd.index("--rules-dir") + 1] == scanoss.RULES_DIR
    assert cmd[cmd.index("--format") + 1] == "cyclonedx" and cmd[cmd.index("--output") + 1] == "/w/cbom.json"
    assert cmd[cmd.index("--timeout") + 1] == "31m"  # beyond the worker's limit: a long scan ends as a worker timeout
    assert not any("api-key" in a or "git" in a for a in cmd)


@pytest.fixture
def fake_finder(tmp_path, monkeypatch):
    """An executable standing in for crypto-finder: writes a CBOM to --output, or fails with rc 1."""
    script = tmp_path / "fake-crypto-finder"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "if os.environ.get('FAKE_SCANOSS_FAIL'):\n"
        "    print('Error: no rules found in rules dir', file=sys.stderr); sys.exit(1)\n"
        "out = args[args.index('--output') + 1]\n"
        "json.dump({'bomFormat': 'CycloneDX', 'components': []}, open(out, 'w'))\n"
        "print('# Scan Summary', file=sys.stderr)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr(scanoss, "BINARY", str(script))
    return script


def test_run_writes_cbom_and_notes_a_committed_scanoss_json(tmp_path, fake_finder):
    repo, out, trace = tmp_path / "repo", tmp_path / "cbom.json", Trace()
    repo.mkdir()
    (repo / "scanoss.json").write_text("{}", encoding="utf-8")
    scanoss.run_scanoss(repo, out, trace, timeout=60)
    assert json.loads(out.read_text())["components"] == []
    assert "crypto-finder cmd:" in trace.text() and "scanoss.json" in trace.text()


def test_nonzero_exit_is_a_failure(tmp_path, fake_finder, monkeypatch):
    monkeypatch.setenv("FAKE_SCANOSS_FAIL", "1")
    (tmp_path / "repo").mkdir()
    with pytest.raises(RuntimeError, match=r"scanoss_failed rc=1: .*no rules found"):
        scanoss.run_scanoss(tmp_path / "repo", tmp_path / "cbom.json", Trace(), timeout=60)


def test_version_string_names_engine_and_rules():
    out = "crypto-finder version 0.37.1\n  Git commit:  42f28ef7\n"
    assert scanoss.format_version(out, "1.22.0", "c5f32b7bbd07f53acd08") == (
        "crypto-finder 0.37.1 (opengrep 1.22.0, open-crypto-rules@c5f32b7bbd07)"
    )
    assert scanoss.format_version(out) == "crypto-finder 0.37.1"
    assert scanoss.format_version("") is None
