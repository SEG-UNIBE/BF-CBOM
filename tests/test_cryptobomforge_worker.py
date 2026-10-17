"""cryptobomforge worker: crypto query selection."""

from workers.cryptobomforge import main as cw


def test_crypto_queries_only_for_python_and_cpp(monkeypatch):
    monkeypatch.delenv("CRYPTOBOMFORGE_QUERIES_JAVA", raising=False)
    assert cw.crypto_query_for("python").endswith("experimental/cryptography/inventory/new_models")
    assert cw.crypto_query_for("cpp").startswith("codeql/cpp-queries:")
    assert cw.crypto_query_for("java") is None
    monkeypatch.setenv("CRYPTOBOMFORGE_QUERIES_JAVA", "/q/java-allalg")
    assert cw.crypto_query_for("java") == "/q/java-allalg"
