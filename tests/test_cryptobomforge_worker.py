"""cryptobomforge worker: crypto query selection; an empty CodeQL result goes through cryptobom-forge like any other."""

import json
from pathlib import Path

from common.models import Trace
from workers.cryptobomforge import main as cw


def test_crypto_queries_only_for_python_and_cpp(monkeypatch):
    monkeypatch.delenv("CRYPTOBOMFORGE_QUERIES_JAVA", raising=False)
    assert cw.crypto_query_for("python").endswith("experimental/cryptography/inventory/new_models")
    assert cw.crypto_query_for("cpp").startswith("codeql/cpp-queries:")
    assert cw.crypto_query_for("java") is None
    monkeypatch.setenv("CRYPTOBOMFORGE_QUERIES_JAVA", "/q/java-allalg")
    assert cw.crypto_query_for("java") == "/q/java-allalg"


def test_an_empty_codeql_result_is_converted_by_cryptobom_forge_not_replaced(tmp_path, monkeypatch):
    """CodeQL found nothing: the worker still runs cryptobom-forge, which writes its own empty draft document, instead
    of returning an empty CycloneDX CBOM of its own (the 2026-10-15 audit's placeholder, removed 2026-10-20: every
    stored CryptobomForge CBOM is the tool's, refinement 15)."""
    driver = {"name": "CodeQL", "organization": "GitHub", "semanticVersion": "2.23.0"}
    empty_sarif = {"version": "2.1.0", "runs": [{"tool": {"driver": driver}, "results": []}]}
    draft = {"bomFormat": "CBOM", "specVersion": "1.4-cbom-1.0", "metadata": {}}  # the tool's: no components key
    converted = []

    def clone(git_url, branch, target_dir, commit=None):
        repo = Path(target_dir, "repo")
        repo.mkdir()
        (repo / "a.py").write_text("import hashlib\n", encoding="utf-8")
        return str(repo)

    def codeql(self, repo_path, lang, trace):
        Path(repo_path, "results.sarif").write_text(json.dumps(empty_sarif), encoding="utf-8")
        return True

    def cryptobom(self, sarif_path, output_path, trace):
        converted.append(json.loads(Path(sarif_path).read_text(encoding="utf-8"))["runs"][0]["results"])
        Path(output_path).write_text(json.dumps(draft), encoding="utf-8")
        return True

    monkeypatch.setattr(cw, "clone_repo", clone)
    monkeypatch.setattr(cw.CryptobomForgeClient, "_run_codeql_scan", codeql)
    monkeypatch.setattr(cw.CryptobomForgeClient, "_run_cryptobom", cryptobom)
    trace = Trace()
    out = cw.CryptobomForgeClient(timeout_sec=30).generate_cbom("http://git/x.git", main_language="python", trace=trace)
    assert json.loads(out) == draft and converted == [[]]  # the empty SARIF reached the tool
    assert any("codeql results: 0" in n for n in trace._items)
    assert not hasattr(cw, "_empty_cbom")
