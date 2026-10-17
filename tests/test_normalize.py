"""Normaliser (analysis/normalize): adapter specs per tool, shared vocabulary, conformance, CBOM^norm output.

Fixtures: tests/fixtures/pilot/ (pilot of 2026-10-08), tests/fixtures/normalize/ (small excerpts of real outputs of
the open tools from experiment e1-pilot-corpus, 2026-10-16; origin in each file's metadata). The xgrep
CBOMs below are hand-written in the tool's format.
"""

import json
from pathlib import Path

import pytest
import yaml

from analysis.groundtruth import expected_agreement, load_ground_truth
from analysis.normalize import normalize_cbom
from analysis.normalize.specs import ADAPTER_DIR, SpecError, load_adapter, spec_files
from analysis.normalize.vocab import load_vocabulary
from analysis.sites import extract_sites

TESTS = Path(__file__).parent
V = load_vocabulary()


def _load(rel: str) -> dict:
    return json.loads((TESTS / rel).read_text(encoding="utf-8"))


def _comp(name, *, ap=None, asset="algorithm", occ=None, props=None, **extra):
    c = {"type": "cryptographic-asset", "name": name, "cryptoProperties": {"assetType": asset}}
    if ap:
        c["cryptoProperties"]["algorithmProperties"] = ap
    if occ:
        c["evidence"] = {"occurrences": occ}
    if props:
        c["properties"] = [{"name": k, "value": v} for k, v in props.items()]
    c.update(extra)
    return c


def _bom(*components, version="1.6", fmt="CycloneDX"):
    return {"bomFormat": fmt, "specVersion": version, "components": list(components)}


# ----- the paper figure and the pitch example -----


def test_paper_figure_three_tools_one_record_each(tmp_path):
    (tmp_path / "t2.yaml").write_text(
        yaml.safe_dump(
            {
                "spec": "t2",
                "inherits": "_cyclonedx",
                "sources": [{"fields": "standard"}, {"property": {"t2:mode": "mode"}}, {"parse": "name"}],
                "locations": [{"properties": {"file": "t2:file", "line": "t2:line"}}],
            }
        ),
        encoding="utf-8",
    )
    t1 = _bom(
        _comp("AES-256-CBC", ap={"primitive": "block-cipher"}, occ=[{"location": "src/KdbxFile.cs", "line": 120}])
    )
    t2 = _bom(_comp("AES", props={"t2:mode": "CBC", "t2:file": "KdbxFile.cs", "t2:line": "120"}))
    t3 = _bom(
        _comp(
            "aes",
            ap={"primitive": "block-cipher", "parameterSetIdentifier": "256"},
            occ=[{"location": "./src/KdbxFile.cs:121"}],
        )
    )
    files = frozenset({"src/KdbxFile.cs"})
    r1 = normalize_cbom(t1, "t1").records[0]
    r2 = normalize_cbom(t2, "t2", spec=load_adapter("t2", str(tmp_path)), repo_files=files).records[0]
    r3 = normalize_cbom(t3, "t3").records[0]
    reported = lambda r: {d: v for d, v in r.values.items() if not str(r.src.get(d)).startswith("derived")}  # noqa: E731
    assert {k: reported(r1).get(k) for k in ("family", "primitive", "mode", "key_size")} == {
        "family": "AES", "primitive": "block-cipher", "mode": "CBC", "key_size": 256}  # fmt: skip
    assert (reported(r2)["family"], reported(r2)["mode"], "key_size" in reported(r2)) == ("AES", "CBC", False)
    assert (reported(r3)["family"], reported(r3)["key_size"], "mode" in reported(r3)) == ("AES", 256, False)
    assert [(r.file, r.line) for r in (r1, r2, r3)] == [("src/KdbxFile.cs", 120)] * 2 + [("src/KdbxFile.cs", 121)]
    assert r2.file_exists and r2.src["mode"] == "properties[t2:mode]" and r2.src["primitive"] == "derived:family"


def test_pitch_example_secp256r1_ecdsa_p256_ec256_agree():
    bom = _bom(
        _comp("secp256r1", occ=[{"location": "a.java", "line": 3}]),
        _comp("ECDSA-P256"),
        _comp("EC", ap={"parameterSetIdentifier": "256"}),
    )
    recs = normalize_cbom(bom, "x").records
    assert {r.group for r in recs} == {"EC"} and {r.values["key_size"] for r in recs} == {256}
    assert [r.values["family"] for r in recs] == ["EC", "ECDSA", "EC"]
    assert V.relation("family", "EC", "ECDSA") == "refines"


# ----- vocabulary -----


@pytest.mark.parametrize(
    "name, expected",
    [
        ("AES-256-GCM", {"family": "AES", "key_size": 256, "mode": "GCM"}),
        ("AES/GCM/NoPadding", {"family": "AES", "mode": "GCM", "padding": "NONE"}),
        ("HmacSHA512", {"family": "HMAC", "digest": "SHA-512"}),
        ("SHA256withECDSA", {"family": "ECDSA", "digest": "SHA-256"}),
        ("PBKDF2WithHmacSHA256", {"family": "PBKDF2", "digest": "SHA-256"}),
        ("RSA/ECB/OAEPWithSHA-256AndMGF1Padding", {"family": "RSAES-OAEP", "padding": "OAEP", "digest": "SHA-256"}),
        ("RSA-PKCS1-1.5-SHA-256", {"family": "RSASSA-PKCS1", "digest": "SHA-256"}),
        ("DESede/CBC/PKCS5Padding", {"family": "3DES", "mode": "CBC", "padding": "PKCS5"}),
        ("EC-secp256r1", {"family": "EC", "curve": "secp256r1"}),
        ("prime256v1", {"curve": "secp256r1"}),
        ("Ed25519", {"family": "EdDSA", "algorithm": "Ed25519"}),
        ("ChaCha20-Poly1305-256", {"algorithm": "ChaCha20-Poly1305", "key_size": 256}),
        ("ML-KEM-768", {"family": "ML-KEM", "algorithm": "ML-KEM-768"}),
        ("HMACSHA2", {"family": "HMAC", "digest": "SHA-2"}),
        ("PBEWithMD5AndDES", {"family": "PBES1", "digest": "MD5"}),
        ("SHA256 ", {"algorithm": "SHA-256"}),
        ("RS256", {"family": "RSASSA-PKCS1", "digest": "SHA-256"}),
        ("ssh-rsa", {"family": "RSASSA-PKCS1", "digest": "SHA-1"}),
        ("KeySize = 2048", {"key_size": 2048}),
        ("iterations = 5", {"iterations": 5}),
        ("TLSv1.2", {"protocol": "TLS", "protocol_version": "1.2"}),
        ("weak-rand", {"family": "PRNG"}),
        ("crypto/rand", {"family": "CSPRNG"}),
        ("secret-key@0c5514ad-e980-4b20", {"material": "secret-key"}),
    ],
)
def test_parse_names(name, expected):
    got = V.parse(name).values
    assert {k: got.get(k) for k in expected} == expected


@pytest.mark.parametrize("garbage", ["parseCsvMap", 'SHA-" + this.strength', "0"])
def test_code_fragments_stay_unmapped(garbage):
    p = V.parse(garbage)
    assert not p.values and not p.known


def test_list_of_algorithms_is_ambiguous():
    assert V.parse("RSA, ECC, AES, ChaCha20").candidates == ["AES", "ChaCha20", "EC", "RSA"]


def test_canonical_field_values():
    assert V.canonical("primitive", "blockcipher") == "block-cipher"
    assert V.canonical("primitive", "unknown") is None
    assert V.canonical("curve", "secg/secp256r1") == V.canonical("curve", "P-256") == "secp256r1"
    assert V.canonical("asset_type", "relatedCryptoMaterial") == "related-crypto-material"
    assert V.canonical("functions", ["keygen", "bogus"]) == ("keygen",)


# ----- conformance -----


def test_spec_issues_against_the_declared_version():
    r16 = normalize_cbom(_bom(_comp("X", ap={"algorithmFamily": "AES", "padding": "PSS"})), "x").records[0]
    assert "field:cryptoProperties.algorithmProperties.algorithmFamily" in r16.spec_issues
    assert "enum:cryptoProperties.algorithmProperties.padding=PSS" in r16.spec_issues
    r17 = normalize_cbom(_bom(_comp("EC", ap={"curve": "P-256"}), version="1.7"), "x").records[0]
    assert r17.spec_issues == ["deprecated:cryptoProperties.algorithmProperties.curve"]
    key = normalize_cbom(_bom(_comp("RSA", asset="key")), "deepseek").records[0]
    assert key.spec_issues == ["enum:cryptoProperties.assetType=key"]
    assert (key.values["asset_type"], key.values["material"]) == ("related-crypto-material", "key")
    old = normalize_cbom(_bom(_comp("AES"), version="1.5"), "x")
    assert old.document_issues == ["version:1.5"] and old.checked_against is None


def test_ibm_draft_gains_locations_that_sites_misses():
    bom = _load("MinerU_cryptobomforge.json")
    n = normalize_cbom(bom, "cryptobomforge", language="python")
    assert n.document_issues == ["dialect:CBOM/1.4-cbom-1.0"]
    assert len(n.records) == 19 and all(r.file and r.line and r.line_end >= r.line for r in n.records)
    assert extract_sites(bom, "cryptobomforge")[0] == []


# ----- tool specs on real outputs -----


def test_cryptoscan_reads_the_symbol_of_each_occurrence():
    recs = normalize_cbom(_load("fixtures/normalize/cryptoscan.json"), "cryptoscan").records
    by = {}
    for r in recs:
        by.setdefault(r.name, []).append(r)
    assert {r.values.get("key_size") for r in by["Hardcoded Key Size"]} == {2048, 4096}
    assert {r.values.get("iterations") for r in by["Weak PBKDF Usage"]} == {5}
    assert by["ECC"][0].values["algorithm"] == "Ed25519"
    assert all(r.ambiguous and not r.values.get("family") for r in by["RSA, ECDSA, EdDSA"])


def test_cbomkit_dependencies_give_digest_and_key_family():
    recs = normalize_cbom(_load("fixtures/normalize/cbomkit-jar.json"), "cbomkit-jar").records
    oaep = next(r for r in recs if r.name == "RSA-OAEP")
    assert (oaep.values["family"], oaep.values["digest"], oaep.src["digest"]) == (
        "RSAES-OAEP",
        "SHA-256",
        "dependencies",
    )
    salts = [r for r in recs if r.name.startswith("salt@")]
    assert salts and all(r.values["family"] == "HKDF" and r.values["material"] == "salt" for r in salts)
    assert all("key_size" not in r.values for r in salts)  # a salt's size is not a key size


def test_cipherradar_conflict_refs_and_libraries():
    recs = normalize_cbom(_load("fixtures/normalize/cipherradar.json"), "cipherradar").records
    sha1 = next(r for r in recs if r.name == "SHA-1")
    assert sha1.values["family"] == "SHA-2" and sha1.conflicts["family"]["value"] == "SHA-1"
    cert = next(r for r in recs if r.values["asset_type"] == "certificate")
    assert (cert.values["family"], cert.values["digest"]) == ("ECDSA", "SHA-256")
    lib = next(r for r in recs if r.values["asset_type"] == "library")
    assert lib.values["purl"] == "pkg:pypi/cryptography" and not lib.unmapped


def test_inventories_are_skipped():
    n = normalize_cbom(_load("authlib_cdxgen.json"), "cdxgen")
    assert n.records == [] and n.stats["skipped"]
    n = normalize_cbom(_load("fixtures/pilot/pqcheck_micro-python.json"), "pqcheck")
    assert n.stats["skipped"] == {"library": 2}


def test_xgrep_code_names_stay_unmapped_but_api_context_helps():
    occ = [{"location": "A.java", "line": 48, "additionalContext": "java.security.Signature#getInstance"}]
    r = normalize_cbom(_bom(_comp("getJavaAlgorithm", ap={"primitive": "unknown"}, occ=occ)), "xgrep").records[0]
    assert r.values["primitive"] == "signature" and r.via["primitive"] == "api"
    assert r.unmapped == [{"src": "name", "raw": "getJavaAlgorithm"}]


def test_json_context_extractor(tmp_path):
    (tmp_path / "t.yaml").write_text("spec: t\ninherits: _cyclonedx\nsources: [base, {extract: json_context}]\n")
    occ = [{"location": "a.py", "line": 3, "additionalContext": json.dumps({"mode": "GCM", "keySize": 256})}]
    bom = _bom(_comp("GCMParameterSpec", ap={"algorithmFamily": "AES", "primitive": "ae"}, occ=occ), version="1.7")
    r = normalize_cbom(bom, "t", spec=load_adapter("t", str(tmp_path))).records[0]
    assert (r.values["family"], r.values["mode"], r.values["key_size"], r.spec_issues) == ("AES", "GCM", 256, [])


def test_ciphersift_rule_family_fills_the_gap_and_api_names_stay_quiet():
    recs = normalize_cbom(_load("fixtures/normalize/ciphersift.json"), "ciphersift").records
    by = {(r.name, r.values.get("family")): r for r in recs}
    ec = by[("EC", "EC")]  # plain EC key generation: no algorithmFamily, the rule's family fills it
    assert ec.src["family"] == "properties[ciphersift:family]" and ec.line == 16
    assert by[("crypto/ecdsa.GenerateKey", "ECDSA")].values["curve"] == "secp256r1"
    assert by[("AES/GCM/NoPadding", "AES")].values["mode"] == "GCM"
    lib = next(r for r in recs if r.values["asset_type"] == "library")
    assert (lib.file, lib.values["purl"]) == ("go.mod", "pkg:golang/golang.org/x/crypto@v0.17.0")
    assert not any(r.unmapped or r.spec_issues for r in recs)
    # hmac.new names the API, not the algorithm: the SHA-2 component keeps its field, the name is a recorded conflict
    assert by[("hmac.new", "SHA-2")].conflicts["family"]["value"] == "HMAC"


@pytest.mark.parametrize(
    "fixture, tool",
    [
        ("cbomkit_micro-java", "cbomkit"),
        ("cipherradar_micro-java", "cipherradar"),
        ("pqcheck_micro-python", "pqcheck"),
        ("cbomkit_micro-go", "cbomkit"),
    ],  # fmt: skip
)
def test_locations_equal_the_call_sites(fixture, tool):
    bom = _load(f"fixtures/pilot/{fixture}.json")
    sites, _ = extract_sites(bom, tool)
    recs = normalize_cbom(bom, tool).records
    assert {(r.file, r.line) for r in recs if r.values.get("asset_type") == "algorithm" and r.line} == {
        (s.file, s.line) for s in sites
    }


def test_cbomkit_micro_java_against_the_ground_truth():
    recs = normalize_cbom(_load("fixtures/pilot/cbomkit_micro-java.json"), "cbomkit", language="java").records
    agreement = expected_agreement(recs, load_ground_truth()["micro-java"])
    misses = {(a, d): pair for a, dims in agreement.items() for d, pair in dims.items() if pair[0] != pair[1]}
    assert misses == {
        ("aes-gcm-encrypt", "key_size"): (256, 128),  # CBOMkit's invented key size (pilot finding)
        ("ecdsa-sha256-sign", "curve"): ("secp256r1", None),
        ("pbkdf2-hmac-sha256", "iterations"): (600000, None),
    }


# ----- specs and output format -----


def test_every_integrated_tool_has_a_spec_and_all_specs_load():
    tools = yaml.safe_load((TESTS.parent / "common" / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    active = [t for t, info in tools.items() if info.get("status") in ("integrated", "prepared")]
    missing = [t for t in active if not (ADAPTER_DIR / f"{t}.yaml").exists()]
    assert not missing, f"integrated tools without analysis/adapters/<tool>.yaml: {missing}"
    for path in spec_files():
        spec = load_adapter(path.stem)
        assert spec.sources and not spec.fallback


def test_spec_errors(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        "spec: bad\ninherits: _cyclonedx\nsources: [{parse: nowhere}]\n", encoding="utf-8"
    )
    with pytest.raises(SpecError):
        load_adapter("bad", str(tmp_path))


def test_outputs_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((TESTS.parent / "analysis" / "data" / "cbom-norm.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for rel, tool in [("fixtures/normalize/cryptoscan.json", "cryptoscan"), ("fixtures/normalize/cipherradar.json",
                      "cipherradar"), ("MinerU_cryptobomforge.json", "cryptobomforge"),
                      ("fixtures/normalize/deepseek.json", "deepseek")]:  # fmt: skip
        doc = normalize_cbom(_load(rel), tool, source={"file": "run1.json", "sha256": "0" * 64}).to_dict()
        assert not list(validator.iter_errors(doc)), rel


def _stored_experiment(root: Path) -> Path:
    """Tiny stored experiment on micro-java: two ok runs (CBOMkit, CipherRadar pilot outputs)."""
    exp = root / "exp-norm"
    snapshot = exp / "bfcbom__micro-java@0123456789ab" / "original"
    exp.mkdir()
    (exp / "experiment.yaml").write_text(
        "name: exp-norm\ntools: [cbomkit, cipherradar]\nrepos:\n  - full_name: bfcbom/micro-java\n    language: Java\n",
        encoding="utf-8",
    )
    for tool in ("cbomkit", "cipherradar"):
        (snapshot / tool).mkdir(parents=True)
        cbom = _load(f"fixtures/pilot/{tool}_micro-java.json")
        (snapshot / tool / "run1.json").write_text(json.dumps(cbom), encoding="utf-8")
        meta = {
            "worker": tool,
            "status": "ok",
            "run_index": 1,
            "variant": "original",
            "base_commit": "0123456789ab" + "0" * 28,
        }
        (snapshot / tool / "run1.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return exp


def _csv(path: Path) -> list[dict]:
    import csv

    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_normalize_and_tables_commands(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from misc.cli import analysis_cmd

    exp = _stored_experiment(tmp_path)
    monkeypatch.setattr(analysis_cmd, "experiment_dir", lambda name: tmp_path / name)
    result = CliRunner().invoke(analysis_cmd.analysis_app, ["normalize", "exp-norm"])
    assert result.exit_code == 0, result.output
    norm = next(exp.glob("*/original/cbomkit/run1.norm.json"))
    assert json.loads(norm.read_text())["source"]["file"] == "run1.json"
    report = json.loads((exp / "analysis" / "normalize.json").read_text())
    assert set(report) == {"cbomkit", "cipherradar"} and (exp / "analysis" / "vocab.csv").exists()
    result = CliRunner().invoke(analysis_cmd.analysis_app, ["tables", "exp-norm"])
    assert result.exit_code == 0, result.output
    records = _csv(exp / "analysis" / "records-run1.csv")
    sites = {s["record_id"] for s in _csv(exp / "analysis" / "sites-run1.csv")}
    located = {r["site_id"] for r in records if r["site_id"] and r["asset_type"] == "algorithm"}
    assert records and located and located <= sites
    runs = {r["tool"]: r for r in _csv(exp / "analysis" / "runs.csv")}
    assert (runs["cipherradar"]["bom_format"], runs["cipherradar"]["spec_version"]) == ("CycloneDX", "1.7")


def test_llm_cbom_assembled_valid_keeps_the_models_values():
    from common import llm_cbom as llm

    answer = [
        {
            "name": "RSA-PSS",
            "assetType": "algorithm",
            "primitive": "signature",
            "padding": "PSS",
            "file": "a.go",
            "line": 3,
        },
        {"name": "A128KW", "assetType": "algorithm", "primitive": "key-wrap", "mode": "KW", "file": "a.go", "line": 4},
        {"name": "RSA private key", "assetType": "key", "keySize": 2048, "file": "a.go", "line": 5},
    ]
    cbom = llm.assemble_cbom(answer, tool_name="deepseek", model="m", repo_info=None, repo_path=None,
                             stats=llm.GenerationStats())  # fmt: skip
    recs = {r.name: r for r in normalize_cbom(cbom, "deepseek").records}
    assert all(not r.spec_issues for r in recs.values())
    assert (recs["RSA-PSS"].values["padding"], recs["RSA-PSS"].src["padding"]) == ("PSS", "properties[bf-cbom:padding]")
    assert (recs["A128KW"].values["mode"], recs["A128KW"].values["primitive"]) == ("KW", "key-wrap")
    key = recs["RSA private key"]
    assert (key.values["asset_type"], key.values["material"], key.values["key_size"]) == (
        "related-crypto-material", "private-key", 2048)  # fmt: skip
