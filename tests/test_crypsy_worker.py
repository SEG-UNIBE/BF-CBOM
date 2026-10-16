"""Crypsy (BF-CBOM adaptation) worker: command line, error contract, version string and location adapter.

The tool is not installed in the test environment, so the scan is replaced by a fake interpreter; the CBOM
below is hand-written in Crypsy's output format (absolute locations, a dependency without a line).
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from common.cbom_adapters import repo_root_prefixes, strip_location_prefixes
from common.models import Trace

_spec = importlib.util.spec_from_file_location(
    "crypsy_worker", Path(__file__).resolve().parents[1] / "workers" / "crypsy-bfcbom" / "main.py"
)
crypsy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crypsy)


def _cbom(root: str) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "components": [
            {
                "type": "cryptographic-asset",
                "name": "GCMParameterSpec",
                "cryptoProperties": {
                    "assetType": "algorithm",
                    "algorithmProperties": {"primitive": "ae", "algorithmFamily": "AES", "mode": "GCM"},
                },
                "evidence": {
                    "occurrences": [
                        {"location": f"{root}/src/main/java/demo/CryptoDemo.java", "line": 49},
                        {"location": f"{root}/src/test/java/demo/CryptoTest.java", "line": 7},
                    ]
                },
            },
            {
                "type": "cryptographic-asset",
                "name": "cryptography",
                "evidence": {"occurrences": [{"location": f"{root}/requirements.txt", "line": None}]},
            },
            {"type": "library", "name": "no-evidence"},
        ],
    }


def test_command_is_isolated_offline_and_without_url():
    cmd = crypsy.build_command(Path("/w/repo"), Path("/w/cbom.json"), target_name="acme/app", commit="abc123")
    assert cmd[:6] == [crypsy.PYTHON, "-I", "-m", "crypsy", "scan", "/w/repo"]
    assert cmd[cmd.index("--rule-registry") + 1] == str(crypsy.REGISTRY)
    assert cmd[cmd.index("-o") + 1] == "/w/cbom.json"
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--exclude"] == [".git/*", "*/.git/*"]
    assert cmd[cmd.index("--target-name") + 1] == "acme/app" and cmd[cmd.index("--git-commit") + 1] == "abc123"
    assert "--git-url" not in cmd
    assert "--target-name" not in crypsy.build_command(Path("/w/repo"), Path("/w/o.json"))


@pytest.fixture
def fake_python(tmp_path, monkeypatch):
    """An executable standing in for the venv's python: writes a CBOM to `-o`, or fails with rc 3."""
    script = tmp_path / "fake-python"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "if os.environ.get('FAKE_CRYPSY_FAIL'):\n"
        "    print('Traceback: boom', file=sys.stderr); sys.exit(3)\n"
        "out = args[args.index('-o') + 1]\n"
        "json.dump({'components': [], 'cwd': os.getcwd()}, open(out, 'w'))\n"
        "print('Scan complete.'); print('  Files discovered:    3'); print('  Components found:    0')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr(crypsy, "PYTHON", str(script))
    return script


def test_run_crypsy_uses_neutral_cwd_and_records_summary(tmp_path, fake_python):
    repo, cwd, out = tmp_path / "repo", tmp_path / "cwd", tmp_path / "cbom.json"
    repo.mkdir()
    cwd.mkdir()
    trace = Trace()
    crypsy.run_crypsy(repo, out, cwd, trace, timeout=30)
    assert json.loads(out.read_text())["cwd"] == str(cwd.resolve())
    assert "Files discovered:    3" in trace.text() and "crypsy cmd:" in trace.text()


def test_run_crypsy_nonzero_exit_is_a_failure(tmp_path, fake_python, monkeypatch):
    monkeypatch.setenv("FAKE_CRYPSY_FAIL", "1")
    with pytest.raises(RuntimeError, match=r"crypsy_failed rc=3: .*boom"):
        crypsy.run_crypsy(tmp_path, tmp_path / "cbom.json", tmp_path, Trace(), timeout=30)


def test_locations_become_repo_relative(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    cbom = _cbom(str(repo.resolve()))
    assert strip_location_prefixes(cbom, repo_root_prefixes(repo)) == 3
    locs = [o["location"] for c in cbom["components"] for o in (c.get("evidence") or {}).get("occurrences", [])]
    assert locs == ["src/main/java/demo/CryptoDemo.java", "src/test/java/demo/CryptoTest.java", "requirements.txt"]
    assert cbom["components"][1]["evidence"]["occurrences"][0]["line"] is None  # untouched apart from the path
    assert strip_location_prefixes(cbom, repo_root_prefixes(repo)) == 0  # idempotent


def test_version_string_carries_registry_and_source_commit():
    assert crypsy.format_version("0.2.0+bfcbom.1", "0.9.2", "1a2b3c4d5e6f7a8b9c0d") == (
        "crypsy 0.2.0+bfcbom.1 (crypistry kb 0.9.2, 1a2b3c4d5e6f)"
    )
    assert crypsy.format_version("0.2.0+bfcbom.1") == "crypsy 0.2.0+bfcbom.1"
    assert crypsy.format_version("", "0.9.2") is None


def test_registry_version(tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({"kb_version": "0.9.2"}), encoding="utf-8")
    assert crypsy.registry_version(tmp_path) == "0.9.2"
    assert crypsy.registry_version(tmp_path / "missing") is None
