"""CipherSift worker: command line, error contract and version string.

The tool is not installed in the test environment, so `java` is replaced by a fake executable.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from common.models import Trace

_spec = importlib.util.spec_from_file_location(
    "ciphersift_worker", Path(__file__).resolve().parents[1] / "workers" / "ciphersift" / "main.py"
)
ciphersift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ciphersift)


def test_command_uses_the_jar_offline_and_without_url():
    cmd = ciphersift.build_command(Path("/w/repo"), Path("/w/cbom.json"))
    assert cmd[0] == ciphersift.JAVA and "--enable-native-access=ALL-UNNAMED" in cmd
    assert cmd[cmd.index("-jar") + 1] == ciphersift.JAR
    assert cmd[cmd.index("scan") + 1] == "/w/repo" and cmd[cmd.index("-o") + 1] == "/w/cbom.json"
    assert "-XX:MaxRAMPercentage=75" in cmd  # default JVM heap would be a quarter of the container limit
    assert not any("git" in a for a in cmd)


@pytest.fixture
def fake_java(tmp_path, monkeypatch):
    """An executable standing in for java: writes a CBOM to `-o`, or fails with rc 3 (bad scan root)."""
    script = tmp_path / "fake-java"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "if os.environ.get('FAKE_CIPHERSIFT_FAIL'):\n"
        "    print('ciphersift: error: not a directory', file=sys.stderr); sys.exit(3)\n"
        "out = args[args.index('-o') + 1]\n"
        "json.dump({'components': []}, open(out, 'w'))\n"
        "print('ciphersift: warning: skipped 1 unparsable file', file=sys.stderr)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr(ciphersift, "JAVA", str(script))
    return script


def test_run_writes_cbom_and_records_warnings(tmp_path, fake_java):
    out, trace = tmp_path / "cbom.json", Trace()
    ciphersift.run_ciphersift(tmp_path, out, trace, timeout=30)
    assert json.loads(out.read_text()) == {"components": []}
    assert "skipped 1 unparsable file" in trace.text() and "ciphersift cmd:" in trace.text()


def test_nonzero_exit_is_a_failure(tmp_path, fake_java, monkeypatch):
    monkeypatch.setenv("FAKE_CIPHERSIFT_FAIL", "1")
    with pytest.raises(RuntimeError, match=r"ciphersift_failed rc=3: .*not a directory"):
        ciphersift.run_ciphersift(tmp_path, tmp_path / "cbom.json", Trace(), timeout=30)


def test_version_string_carries_the_release_tag():
    assert ciphersift.format_version("ciphersift 1.1.1\n", "v1.1.1") == "ciphersift 1.1.1 (release v1.1.1)"
    assert ciphersift.format_version("1.1.1", None) == "ciphersift 1.1.1"
    assert ciphersift.format_version("", "v1.1.1") == "ciphersift v1.1.1"
    assert ciphersift.format_version(None) is None
