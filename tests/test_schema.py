"""RQ1 (analysis/schema.py): whole-document validation, the meaningful shares and the traceability check."""

import subprocess
from pathlib import Path

from analysis import schema
from analysis.normalize import normalize_cbom
from analysis.snapshots import SnapshotStore


def _bom(*components, version="1.6"):
    return {"bomFormat": "CycloneDX", "specVersion": version, "version": 1, "components": list(components)}


def _comp(name, ap=None, occ=None, asset_type="algorithm"):
    comp = {"type": "cryptographic-asset", "name": name, "cryptoProperties": {"assetType": asset_type}}
    if ap:
        comp["cryptoProperties"]["algorithmProperties"] = ap
    if occ:
        comp["evidence"] = {"occurrences": occ}
    return comp


def test_validate_document_against_the_declared_version():
    ok = schema.validate_document(_bom(_comp("AES", ap={"primitive": "block-cipher", "mode": "gcm"})))
    assert ok == {"schema": "1.6", "valid": True, "errors": 0, "error_kinds": {}}
    bad = schema.validate_document(_bom(_comp("AES", ap={"primitive": "block-cipher", "mode": "GCM"})))
    assert bad["schema"] == "1.6" and bad["valid"] is False and bad["errors"] >= 1
    assert "enum:components.cryptoProperties.algorithmProperties.mode" in bad["error_kinds"]
    v17 = schema.validate_document(_bom(_comp("AES", ap={"algorithmFamily": "AES"}), version="1.7"))
    assert v17["valid"] is True
    draft = schema.validate_document({"bomFormat": "CycloneDX", "specVersion": "1.4-cbom-1.0", "components": []})
    assert draft == {"schema": None, "valid": None, "errors": None, "error_kinds": {}}


def test_meaningful_counts_standard_fields_apart_from_any_source():
    bom = _bom(
        _comp("AES-256-GCM"),  # key size and mode only in the name
        _comp("RSA", ap={"primitive": "pke", "parameterSetIdentifier": "2048"}),  # standard fields
    )
    n = normalize_cbom(bom, "t")
    rows = {r["dimension"]: r for r in schema.meaningful_rows([({}, Path("x/original/t/run1.json"), n)], {"t": True})}
    assert rows["asset_type"]["crypto_assets"] == 2 and rows["asset_type"]["standard"] == 2
    assert (rows["key_size"]["standard"], rows["key_size"]["any"]) == (1, 2)
    assert (rows["mode"]["standard"], rows["mode"]["any"]) == (0, 1)
    assert (rows["primitive"]["standard"], rows["primitive"]["any"]) == (1, 2)  # AES's primitive is derived
    assert rows["key_size"]["native_cyclonedx"] is True and rows["mode"]["any_share"] == 0.5


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _snapshot_repo(tmp_path: Path) -> tuple[Path, str]:
    """A tiny repo with one crypto call; returns (its path, HEAD sha)."""
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "crypto.py").write_text("import hashlib\n\n\ndef digest(x):\n    return hashlib.sha256(x)\n")
    (src / "README.md").write_text("nothing here\n")
    _git("init", "-q", "-b", "main", cwd=src)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "add", ".", cwd=src)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=src)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip()
    return src, sha


def test_snapshot_store_reads_the_mirror_or_fetches_into_the_cache(tmp_path):
    src, sha = _snapshot_repo(tmp_path)
    mirrors, cache = tmp_path / "mirrors", tmp_path / "cache"
    (mirrors / "o").mkdir(parents=True)
    _git("clone", "-q", "--bare", str(src), str(mirrors / "o" / "r.git"), cwd=tmp_path)
    store = SnapshotStore(mirror_dir=mirrors, cache_dir=cache)
    assert store.source("o/r", sha) == "mirror" and "pkg/crypto.py" in store.files("o/r", sha)
    assert (
        store.read("o/r", sha, "pkg/crypto.py").startswith("import hashlib") and store.read("o/r", sha, "nope") is None
    )
    assert store.files("o/r", "0" * 40) is None and store.source("x/y", sha) is None

    fetched = SnapshotStore(mirror_dir=tmp_path / "none", cache_dir=cache)
    assert fetched.source("o/r", sha) is None
    assert fetched.fetch("o/r", str(src), sha) and fetched.source("o/r", sha) == "cache"
    assert "README.md" in fetched.files("o/r", sha)


def test_traceability_weak_and_strong(tmp_path):
    src, sha = _snapshot_repo(tmp_path)
    mirrors = tmp_path / "mirrors"
    (mirrors / "o").mkdir(parents=True)
    _git("clone", "-q", "--bare", str(src), str(mirrors / "o" / "r.git"), cwd=tmp_path)
    store = SnapshotStore(mirror_dir=mirrors, cache_dir=tmp_path / "cache")
    bom = _bom(
        _comp("SHA-256", occ=[{"location": "pkg/crypto.py", "line": 5}]),  # exists, in range, token nearby
        _comp("AES", occ=[{"location": "README.md", "line": 1}]),  # exists, in range, no token
        _comp("RSA", occ=[{"location": "pkg/crypto.py", "line": 99}]),  # beyond the file
        _comp("DES", occ=[{"location": "gone.py", "line": 1}]),  # no such file
        _comp("MD5"),  # not located
    )
    n = normalize_cbom(bom, "t")
    meta = {"worker": "t", "run_index": 1, "base_commit": sha}
    (row,) = schema.traceability_rows([(meta, Path("o__r@x/original/t/run1.json"), n)], store, languages={})
    assert (row["source"], row["records"], row["located"]) == ("mirror", 5, 4)
    assert (row["file_exists"], row["line_in_range"], row["strong"]) == (3, 2, 1)
    assert (row["share_weak"], row["share_strong"]) == (0.5, 0.25)
    (none,) = schema.traceability_rows(
        [(meta, Path("o__r@x/original/t/run1.json"), n)], SnapshotStore(tmp_path / "a", tmp_path / "b"), languages={}
    )
    assert none["source"] is None and none["file_exists"] is None and none["share_weak"] is None
