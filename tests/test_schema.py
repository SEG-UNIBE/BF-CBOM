"""RQ1 (analysis/schema.py): whole-document validation, the meaningful shares and the traceability check."""

import json
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


def test_meaningful_counts_standard_fields_apart_from_any_source_per_asset_type(tmp_path):
    bom = _bom(
        _comp("AES-256-GCM"),  # key size and mode only in the name
        _comp("RSA", ap={"primitive": "pke", "parameterSetIdentifier": "2048"}),  # standard fields
        {
            "type": "cryptographic-asset",
            "name": "server key",
            "cryptoProperties": {
                "assetType": "related-crypto-material",
                "relatedCryptoMaterialProperties": {"type": "private-key", "size": 2048},
            },
        },
        _comp("TLS", asset_type="protocol"),
    )
    path = tmp_path / "x" / "original" / "t" / "run1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(bom), encoding="utf-8")
    n = normalize_cbom(bom, "t")
    rows = {(r["asset_type"], r["dimension"]): r for r in schema.meaningful_rows([({}, path, n)], {"t": "tool"})}
    algo = {d: rows[("algorithm", d)] for d in ("primitive", "parameter", "key_size", "mode", "security_level")}
    assert all(r["assets"] == 2 for r in algo.values()) and "asset_type" not in {d for _, d in rows}
    assert (algo["key_size"]["standard"], algo["key_size"]["any"]) == (1, 2)
    assert (algo["mode"]["standard"], algo["mode"]["any"]) == (0, 1)
    assert (algo["primitive"]["standard"], algo["primitive"]["any"]) == (1, 2)  # AES's primitive is derived
    assert algo["security_level"]["any"] is None and algo["security_level"]["any_share"] is None  # not normalised
    assert algo["key_size"]["cbom_by"] == "tool" and algo["mode"]["any_share"] == 0.5
    key = rows[("related-crypto-material", "type")]
    assert (key["assets"], key["standard"], key["any"]) == (1, 1, 1)
    assert rows[("related-crypto-material", "size")]["standard"] == 1 and rows[("protocol", "type")]["standard"] == 0
    assert set(schema.MEANINGFUL_FIELDS) == {"algorithm", "certificate", "protocol", "related-crypto-material"}


def test_asset_type_and_subtype_sets_are_cyclonedx():
    """The finite sets the paper cites: CycloneDX's assetType enum and its per-type enums (vendored 1.7 schema)."""
    cdx = json.loads((schema.SCHEMA_DIR / "bom-1.7.schema.json").read_text(encoding="utf-8"))
    crypto = cdx["definitions"]["cryptoProperties"]["properties"]
    norm = json.loads((Path(schema.__file__).parent / "data" / "cbom-norm.schema.json").read_text(encoding="utf-8"))
    record = norm["$defs"]["record"]["properties"]
    no_info = {"other", "unknown"}
    assert set(record["asset_type"]["enum"]) == set(crypto["assetType"]["enum"]) | {"library"}
    assert (
        set(record["material"]["enum"])
        == set(crypto["relatedCryptoMaterialProperties"]["properties"]["type"]["enum"]) - no_info
    )
    assert (
        set(record["primitive"]["enum"])
        == set(crypto["algorithmProperties"]["properties"]["primitive"]["enum"]) - no_info
    )
    assert set(schema.MEANINGFUL_FIELDS) == set(crypto["assetType"]["enum"])
    for at, fields in schema.MEANINGFUL_FIELDS.items():  # every standard path exists in the 1.7 schema
        for _, paths, _ in fields:
            for path in paths:
                cur = crypto
                for part in path.split("."):
                    cur = cur[part]["properties"] if part != path.split(".")[-1] else cur[part]
                assert cur, (at, path)
    from analysis.normalize.vocab import fold, load_vocabulary

    protocol_enum = {fold(v) for v in crypto["protocolProperties"]["properties"]["type"]["enum"]}
    ours = set(load_vocabulary().protocol_alias.values())
    assert all(fold(p) in protocol_enum or p in schema.PROTOCOLS_TYPE_OTHER for p in ours), ours


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
    bom["components"].append(_comp("3DES", occ=[{"location": "pkg/crypto.py"}]))  # a file without a line
    bom["components"].append(
        {
            "type": "cryptographic-asset",
            "name": "Hardcoded secret: x",
            "cryptoProperties": {
                "assetType": "related-crypto-material",
                "relatedCryptoMaterialProperties": {"type": "secret-key"},
            },
            "evidence": {"occurrences": [{"location": "README.md", "line": 1}]},
        }
    )
    n = normalize_cbom(bom, "t")
    meta = {"worker": "t", "run_index": 1, "base_commit": sha}
    rows = {
        r["asset_type"]: r
        for r in schema.traceability_rows([(meta, Path("o__r@x/original/t/run1.json"), n)], store, languages={})
    }
    row = rows["algorithm"]
    assert (row["source"], row["records"], row["located_file"], row["located_line"]) == ("mirror", 6, 5, 4)
    assert (row["file_exists"], row["line_in_range"], row["strong"]) == (3, 2, 1)
    assert (row["share_located_file"], row["share_located_line"]) == (round(5 / 6, 3), round(4 / 6, 3))
    assert (row["share_weak"], row["share_strong"]) == (0.5, 0.25)  # shares of the records with a file and a line
    key = rows["related-crypto-material"]  # key material is kept apart: it does not dilute the algorithm rows
    assert (key["records"], key["located_line"], key["line_in_range"], key["strong"]) == (1, 1, 1, 0)
    none = schema.traceability_rows(
        [(meta, Path("o__r@x/original/t/run1.json"), n)], SnapshotStore(tmp_path / "a", tmp_path / "b"), languages={}
    )[0]
    assert none["source"] is None and none["file_exists"] is None and none["share_weak"] is None


class _FakeStore:
    """A snapshot with a few files in memory (SnapshotStore's files/read)."""

    def __init__(self, files: dict[str, str]):
        self._files = files

    def files(self, repo, commit):
        return frozenset(self._files)

    def read(self, repo, commit, path):
        return self._files.get(path)


def test_location_kind_tells_calls_from_imports_windows_and_missing_places():
    from types import SimpleNamespace

    java = ["package p;", "import java.security.MessageDigest;", 'class A { void f() { md("SHA"); } }']
    python = ["from cryptography.hazmat.primitives import hashes", "import hashlib", "hashlib.sha256(b'x')"]
    go = ["package a", "", "import (", '\tcrand "crypto/rand"', '\t"crypto/sha256"', ")", "", "var names = []string{"]
    go += ['\t"x",', "}", "", "func f() { sha256.Sum256(nil) }"]
    store = _FakeStore({"A.java": "\n".join(java), "a.py": "\n".join(python), "a.go": "\n".join(go)})

    def kind(file=None, line=None, line_end=None):
        return schema.location_kind(SimpleNamespace(file=file, line=line, line_end=line_end), store, "o/r", "c")

    assert kind() == "none" and kind("a.py") == "file" and kind("a.py", 1, 3) == "window"
    assert [kind("A.java", n) for n in (2, 3)] == ["import", "line"]
    assert [kind("a.py", n) for n in (1, 2, 3)] == ["import", "import", "line"]
    assert [kind("a.go", n) for n in (3, 4, 5, 9, 12)] == ["import", "import", "import", "line", "line"]
    assert kind("missing.py", 1) == "unread" and kind("a.py", 99) == "unread"
    assert (
        schema.location_kind(SimpleNamespace(file="a.py", line=1, line_end=None), SnapshotStore(), "o/r", None)
        == "unread"
    )
