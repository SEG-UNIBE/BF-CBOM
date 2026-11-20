"""CipherSift worker: command line, error contract and version string, its adapter spec and its registry entry.

The tool is not installed in the test environment, so `java` is replaced by a fake executable.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from analysis.normalize import normalize_cbom
from common import tools_registry as reg
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


FIXTURE = Path(__file__).parent / "fixtures" / "normalize" / "ciphersift.json"  # v1.1.1 on the micro projects


def test_rule_family_fills_the_gap_and_api_names_stay_quiet():
    recs = normalize_cbom(json.loads(FIXTURE.read_text(encoding="utf-8")), "ciphersift").records
    by = {(r.name, r.values.get("family")): r for r in recs}
    ec = by[("EC", "EC")]  # plain EC key generation: no algorithmFamily, the rule's family fills it
    assert ec.src["family"] == "properties[ciphersift:family]" and ec.line == 16
    assert by[("crypto/ecdsa.GenerateKey", "ECDSA")].values["curve"] == "secp256r1"
    assert by[("AES/GCM/NoPadding", "AES")].values["mode"] == "GCM"
    lib = next(r for r in recs if r.values["asset_type"] == "library")
    assert (lib.file, lib.values["purl"]) == ("go.mod", "pkg:golang/golang.org/x/crypto@v0.17.0")
    assert not any(r.unmapped or r.spec_issues for r in recs)
    # hmac.new: the name parses to HMAC, CipherSift's field says SHA-2; the field stands and the conflict is recorded
    # (what the tool wrote is the finding; nothing is read as "really" meaning something else)
    hm = by[("hmac.new", "SHA-2")]
    assert hm.conflicts["family"]["value"] == "HMAC" and hm.resolution == "mapped" and hm.name_kind is None
    assert all(r.resolution == "mapped" for r in recs if r.values.get("asset_type") == "algorithm")


def test_registry_entry_is_tailored_on_the_pilot_corpus_and_the_micro_projects():
    repos = reg.tailored_repos("ciphersift")
    assert "bfcbom/micro-java" in repos and "apache/kafka" in repos
    assert "curl/curl" in repos  # the full pilot list (pilot-2026-10-08-all.yaml), not only the paper-language one
    assert "authlib/authlib" not in repos  # the ICPC set is not in its list
    assert reg.tool_packaging("ciphersift") == "per rule"
