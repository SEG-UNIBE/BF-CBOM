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
from analysis.normalize import Report, normalize_cbom
from analysis.normalize.locations import relative_location
from analysis.normalize.specs import ADAPTER_DIR, SpecError, load_adapter, spec_files
from analysis.normalize.vocab import load_vocabulary

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
        ("weak-rand", {}),  # CipherRadar's rule name: in its spec since 2026-10-18, not in the shared vocabulary
        ("OpenSSL-RAND", {"family": "CSPRNG"}),  # the OpenSSL RAND API; a bare `rand` stays unmapped (ambiguous)
        ("crypto/rand", {"family": "CSPRNG"}),
        ("secret-key@0c5514ad-e980-4b20", {"material": "secret-key"}),
    ],
)
def test_parse_names(name, expected):
    got = V.parse(name).values
    assert {k: got.get(k) for k in expected} == expected


@pytest.mark.parametrize("garbage", ["parseCsvMap", "getJavaAlgorithm", "0", "DigestUtil.hash"])
def test_code_fragments_stay_unmapped(garbage):
    p = V.parse(garbage)
    assert not p.values and not p.known


def test_a_bare_sha_is_the_generic_marker_never_a_specific_hash():
    # xgrep's `SHA-" + this.strength`: some SHA with a dynamic length; CipherRadar's `Algorithm reference: SHA`
    for text in ('SHA-" + this.strength', "Algorithm reference: SHA", "SHA"):
        assert (V.parse(text).values.get("algorithm"), V.parse(text).values.get("family")) == ("SHA", "SHA")
    assert (
        V.parse("HMAC-SHA").values == {"family": "HMAC", "algorithm": "HMAC", "digest": "SHA"}
        or V.parse("HMAC-SHA").values.get("digest") == "SHA"
    )
    assert V.generic("SHA") and V.generic("SHA-2") and V.generic("EC") and V.generic("RSA")
    assert not V.generic("SHA-256") and not V.generic("Ed25519") and not V.generic("ChaCha20-Poly1305")
    assert V.specifics("HMAC") == {"TOTP", "HOTP"}  # HMAC-based schemes are its only specifics (digests are parameters)
    assert {"SHA-1", "SHA-256", "SHA3-256", "SHA-2"} <= V.specifics("SHA")
    assert {"ECDSA", "ECDH", "EdDSA", "Ed25519", "X25519"} <= V.specifics("EC")
    assert (
        V.relation("algorithm", "SHA", "SHA-256") == "refines" and V.relation("digest", "SHA-512", "SHA") == "coarser"
    )
    assert V.relation("algorithm", "SHA-256", "SHA-512") is None


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
    key = normalize_cbom(_bom(_comp("RSA", asset="key")), "x").records[0]
    assert key.spec_issues == ["enum:cryptoProperties.assetType=key"]  # recorded, and no asset type is invented
    assert "asset_type" not in key.values
    old = normalize_cbom(_bom(_comp("AES"), version="1.5"), "x")
    assert old.document_issues == ["version:1.5"] and old.checked_against is None


def test_ibm_draft_windows_become_located_records():
    bom = _load("MinerU_cryptobomforge.json")
    n = normalize_cbom(bom, "cryptobomforge", language="python")
    assert n.document_issues == ["dialect:CBOM/1.4-cbom-1.0"]
    assert len(n.records) == 19 and all(r.file and r.line and r.line_end >= r.line for r in n.records)


def test_relative_location():
    assert relative_location("./src/a.py") == "src/a.py"
    assert relative_location("/tmp/x/repo/src/a.py") == "src/a.py"
    assert relative_location("/abs/elsewhere/a.py") == "/abs/elsewhere/a.py"


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
    # hmac.new: the name parses to HMAC, CipherSift's field says SHA-2; the field stands and the conflict is recorded
    # (what the tool wrote is the finding; nothing is read as "really" meaning something else)
    hm = by[("hmac.new", "SHA-2")]
    assert hm.conflicts["family"]["value"] == "HMAC" and hm.resolution == "mapped" and hm.name_kind is None
    assert all(r.resolution == "mapped" for r in recs if r.values.get("asset_type") == "algorithm")


def test_bar_failed_specs_read_their_tools_as_shipped():
    """qelens, cryptarium and qScan (failed the bar) need no dictionary: the generic CycloneDX reading takes
    their names and fields; their oddities stay visible instead of being repaired."""
    qe = normalize_cbom(_load("fixtures/normalize/qelens.json"), "qelens").records
    assert all(
        r.file in ("app/crypto_demo.py", "requirements.txt") and r.line for r in qe
    )  # absolute path cut at the clone
    by = {r.name: r for r in qe}
    assert by["HASHING (see usage)"].resolution == "undefined" and by["SHA256"].values["algorithm"] == "SHA-256"
    cr = normalize_cbom(_load("fixtures/normalize/cryptarium.json"), "cryptarium").records
    assert not any(r.file or r.line for r in cr)  # its CBOM has no locations (only its JSON format does)
    assert {r.name: r.values.get("key_size") for r in cr}["AES-256"] == 256
    assert {r.resolution for r in cr if r.name == "UNKNOWN"} == {"undefined"}
    qs = normalize_cbom(_load("fixtures/normalize/qscan.json"), "qscan").records
    rsa = next(r for r in qs if r.name == "RSA (kem)")
    assert (rsa.file, rsa.line, rsa.values["family"]) == ("app/crypto_demo.py", 15, "RSA")  # `<file>:<line>` read
    assert rsa.values["param_set"] == "RSA"  # its algorithm name in parameterSetIdentifier: kept, not repaired
    assert not any(r.spec_issues for r in qe + cr + qs)


def test_cryptobom_forge_empty_document_is_read_as_the_tools_own_zero():
    """CodeQL found nothing: cryptobom-forge writes its draft document without a components key (the worker's own
    empty CycloneDX placeholder for that case went on 2026-10-20). Zero records, no error, the draft's dialect issue
    like every other run of the tool."""
    doc = _load("fixtures/normalize/cryptobomforge-empty.json")
    assert "components" not in doc
    res = normalize_cbom(doc, "cryptobomforge")
    assert res.records == [] and res.document_issues == ["dialect:CBOM/1.4-cbom-1.0"]


def test_cbomkit_micro_java_against_the_ground_truth():
    recs = normalize_cbom(_load("fixtures/pilot/cbomkit_micro-java.json"), "cbomkit", language="java").records
    truth = load_ground_truth(TESTS / "fixtures" / "pilot" / "ground_truth-2026-10-08.yaml")  # the projects it ran on
    agreement = expected_agreement(recs, truth["micro-java"])
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
    (exp / "_experiment.yaml").write_text(
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
    report = json.loads((exp / "_ANALYSIS" / "normalize.json").read_text())
    assert set(report) == {"cbomkit", "cipherradar"} and (exp / "_ANALYSIS" / "vocab.csv").exists()
    result = CliRunner().invoke(analysis_cmd.analysis_app, ["tables", "exp-norm"])
    assert result.exit_code == 0, result.output
    records = _csv(exp / "_ANALYSIS" / "records-run1.csv")
    members = {m["record_id"] for m in _csv(exp / "_ANALYSIS" / "members-exact-run1.csv")}
    algorithms = {r["record_id"] for r in records if r["asset_type"] == "algorithm"}
    assert records and members and members <= algorithms
    runs = {r["tool"]: r for r in _csv(exp / "_ANALYSIS" / "runs.csv")}
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
        {
            "name": "ECDSA",
            "assetType": "algorithm",
            "algorithmFamily": "ecdsa",
            "curve": "P-256",
            "file": "a.go",
            "line": 6,
        },
        {"name": "ECDH", "assetType": "algorithm", "curve": "X25519", "file": "a.go", "line": 7},
        {"name": "signing", "assetType": "algorithm", "algorithmFamily": "RSA", "file": "a.go", "line": 8},
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
    # CycloneDX 1.7: registry values in the standard fields, the model's other answers from the bf-cbom properties
    ecdsa = recs["ECDSA"]
    assert (ecdsa.values["family"], ecdsa.values["curve"]) == ("ECDSA", "secp256r1")
    assert ecdsa.src["curve"] == "cryptoProperties.algorithmProperties.ellipticCurve"
    assert (recs["ECDH"].values["curve"], recs["ECDH"].src["curve"]) == ("Curve25519", "properties[bf-cbom:curve]")
    assert recs["signing"].src["family"] == "properties[bf-cbom:algorithm-family]"


def test_scanoss_registry_and_api_spellings_and_byte_key_length():
    recs = normalize_cbom(_load("fixtures/normalize/scanoss.json"), "scanoss").records
    by = {r.name: r for r in recs}
    assert (by["HMAC-sha512"].values["family"], by["HMAC-sha512"].values["digest"]) == ("HMAC", "SHA-512")
    assert (by["RSA-OAEP-sha256"].values["family"], by["RSA-OAEP-sha256"].values["digest"]) == ("RSAES-OAEP", "SHA-256")
    assert by["ECDSA-P256"].values["curve"] == "secp256r1" and by["des_ede3_cbc"].values["mode"] == "CBC"
    assert (by["OpenSSL-RAND"].values["family"], by["PBKDF2---"].values["family"]) == ("CSPRNG", "PBKDF2")
    assert by["AES"].values["key_size"] == 32  # the tool's byte length, read as reported (not corrected)
    assert all(r.file in ("main.go", "crypto_demo.c") and r.line for r in recs)
    assert not any(r.unmapped or r.spec_issues for r in recs)


# ----- kinds, resolution, hierarchies (vocabulary 2026-10-19.1) -----


XBOM_MODULE = "opt.xbom.lib.python3.12.site-packages.xbom.modules.cbom.rules"  # where xbom's own rules live


def test_securin_xbom_rule_names_resolve_or_are_non_crypto():
    prefix = "opt.xbom-registry.rules.java.lang.security.audit."
    rid = lambda rule: {"xbom:crypto:rule_id": prefix + rule}  # noqa: E731
    bom = _bom(
        _comp(prefix + "crypto.use-of-sha1.use-of-sha1", props=rid("crypto.use-of-sha1.use-of-sha1")),
        _comp(prefix + "unsafe-reflection.unsafe-reflection", props=rid("unsafe-reflection.unsafe-reflection")),
        _comp(prefix + "sqli.jpa-sqli.jpa-sqli", props=rid("sqli.jpa-sqli.jpa-sqli")),
        _comp(prefix + "crypto.ssl.insecure-trust-manager.insecure-trust-manager"),
        _comp(prefix + "crypto.gcm-detection.gcm-detection"),
        _comp("SHA-256", props={"xbom:crypto:rule_id": "xbom-python-hash-sha256"}),
        _comp(  # xbom's own rules live in its cbom module: a module path with no `crypto` segment, still a crypto rule
            "PRNG",
            props={"xbom:crypto:rule_id": f"{XBOM_MODULE}.xbom-java-insecure-random"},
        ),
    )
    bom["components"] += [
        _comp(prefix + "cookie-missing-secure-flag.cookie-missing-secure-flag"),  # a rule nobody listed
        _comp(prefix + "crypto.unencrypted-socket.unencrypted-socket"),  # under `crypto`: the tool calls it crypto
    ]
    recs = normalize_cbom(bom, "securin-xbom").records
    by = {r.name.rsplit(".", 1)[-1]: r for r in recs}
    assert (by["use-of-sha1"].values["algorithm"], by["use-of-sha1"].resolution) == ("SHA-1", "mapped")
    # the registry's own taxonomy (the rule path) says which rules are crypto rules: no list of ours
    assert by["unsafe-reflection"].resolution == by["jpa-sqli"].resolution == "non-crypto"
    assert by["cookie-missing-secure-flag"].resolution == "non-crypto"
    assert by["unencrypted-socket"].resolution == "undefined"
    assert not by["unsafe-reflection"].values.get("family") and not by["unsafe-reflection"].unmapped
    # TLS configuration rules and GCM detection are read as they parse, not interpreted (no judgement entries)
    assert (
        by["insecure-trust-manager"].resolution == "undefined" and "protocol" not in by["insecure-trust-manager"].values
    )
    assert by["gcm-detection"].values.get("mode") == "GCM" and not by["gcm-detection"].values.get("family")
    assert (by["SHA-256"].values["algorithm"], by["SHA-256"].resolution) == ("SHA-256", "mapped")
    assert (by["PRNG"].values["family"], by["PRNG"].resolution) == ("PRNG", "mapped")


def test_api_names_resolve_as_api_not_undefined():
    jca = normalize_cbom(_bom(_comp("jca", ap={"primitive": "unknown"}), version="1.7"), "qdarpan").records[0]
    assert jca.resolution == "api" and not jca.values.get("family") and not jca.unmapped
    # code identifiers are recognised by shape for every tool, never by a list of a tool's outputs
    for tool, name in (("ciphersift", "Cipher.getInstance"), ("ciphersift", "crypto/x509.ParseCertificate"),
                       ("xgrep", "getJavaAlgorithm"), ("deepseek", "DigestUtil.hash")):  # fmt: skip
        r = normalize_cbom(_bom(_comp(name), version="1.7"), tool).records[0]
        assert r.resolution == "api" and not r.values.get("family"), name
    vault = normalize_cbom(_bom(_comp("HashiCorp Vault Path")), "cryptoscan").records[0]
    assert vault.resolution == "api"
    for name in ("Sig", "0", "UNKNOWN", "signature key pair"):  # free text nobody can read
        assert normalize_cbom(_bom(_comp(name)), "deepseek").records[0].resolution == "undefined", name
    listed = normalize_cbom(_bom(_comp("RSA, ECC, AES, ChaCha20")), "cryptoscan").records[0]
    assert listed.resolution == "ambiguous"
    for name, kind in (("S256", "api"), ("PLAINTEXT", "non-crypto")):
        r = normalize_cbom(_bom(_comp(name)), "deepseek").records[0]
        assert r.resolution == kind, name


def test_material_hierarchy_refines_instead_of_conflicting():
    comp = _comp(
        "Hardcoded secret: ssl.keystore.password",
        asset="related-crypto-material",
        cryptoProperties={
            "assetType": "related-crypto-material",
            "relatedCryptoMaterialProperties": {"type": "secret-key"},
        },
        occ=[{"location": "kafka.properties", "line": 73}],
    )
    r = normalize_cbom(_bom(comp, version="1.7"), "cipherradar").records[0]
    assert r.values["material"] == "secret-key" and r.conflicts == {} and r.resolution == "mapped"
    assert V.relation("material", "credential", "password") == "refines"
    assert V.relation("material", "private-key", "key") == "coarser"
    assert V.relation("material", "salt", "nonce") is None


def test_scanoss_key_value_certificate_names():
    occ = [{"location": "lib/vtls/openssl.c", "line": 1560, "additionalContext": "scanoss:match,X509_get_pubkey(x)"}]
    cert = _comp(
        "certificate:1560:1560::X.509:library=OpenSSL",
        asset="certificate",
        cryptoProperties={"assetType": "certificate", "certificateProperties": {"certificateFormat": "X.509"}},
        occ=occ,
    )
    verify = _comp(
        "certificate:1137:1137:api=Certificate.CheckSignature:cryptoFunction=verify:findingType=certificate_handling",
        asset="certificate",
        cryptoProperties={"assetType": "certificate"},
        occ=[{"location": "x/cert.go", "line": 1137}],
    )
    recs = normalize_cbom(_bom(cert, verify, version="1.7"), "scanoss").records
    assert all(r.values["asset_type"] == "certificate" and r.group == "CERT" and not r.unmapped for r in recs)
    assert recs[1].values["functions"] == ("verify",) and recs[1].resolution == "mapped"
    assert (recs[0].file, recs[0].line) == ("lib/vtls/openssl.c", 1560)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("XChaCha20", {"family": "ChaCha20", "algorithm": "XChaCha20"}),
        ("ECDH-1PU+A128KW", {"family": "ECDH"}),
        ("ECDH-1PU", {"family": "ECDH"}),
        ("C20P", {"algorithm": "ChaCha20-Poly1305"}),
        ("XC20P", {"algorithm": "XChaCha20-Poly1305"}),
        ("OTR", {"protocol": "OTR"}),
        ("PGP private key", {"protocol": "OpenPGP", "material": "private-key"}),
        ("OpenPGP signature verification", {"protocol": "OpenPGP"}),
        ("BLAKE2b-256", {"algorithm": "BLAKE2b", "param_set": "256"}),
        ("OctKey", {"material": "secret-key"}),
        ("JWKS", {"material": "key"}),
        ("HMAC-SHA", {"family": "HMAC", "digest": "SHA"}),
    ],
)
def test_parse_names_added_2026_10_19(name, expected):
    got = V.parse(name).values
    assert {k: got.get(k) for k in expected} == expected


@pytest.mark.parametrize(
    "name, expected, unmapped",
    [
        # shapes: a UUID, a URI, an OID, a file name carry no key size (corpus-pilot-medium review, 2026-10-23)
        ("15ec45c2-75c0-4a1a-a634-3adf648ce763", {}, []),
        ("http://www.w3.org/2001/04/xmldsig-more#rsa-sha256", {"family": "RSASSA-PKCS1", "digest": "SHA-256"}, []),
        ("http://www.w3.org/2001/04/xmlenc#aes128-cbc", {"family": "AES", "key_size": 128, "mode": "CBC"}, []),
        ("OID(1.2.840.113549.1.1.1)", {"family": "RSA", "key_size": None}, []),
        ("ec-1.2.840.10045.3.1.7.p12", {"family": "EC", "curve": "secp256r1", "key_size": None}, []),
        ("1.2.3.4.5.6.7.8", {}, ["1.2.3.4.5.6.7.8"]),
        ("issue_369_rsa_pem.pub", {"family": "RSA", "key_size": None}, ["issue", "369"]),
        # a number is a key size only next to its family and when the family may have it (`key_sizes`)
        ("AES/CBC/ISO10126-2Padding", {"family": "AES", "mode": "CBC", "padding": "ISO10126", "key_size": None}, ["2"]),
        ("AES-512", {"family": "AES", "key_size": None}, ["512"]),
        ("RSA256", {"family": "RSA", "key_size": None}, ["256"]),
        ("ECDSA-384", {"family": "ECDSA", "key_size": 384}, []),
        ("ALG_KEY_ECC_X962_DER", {"family": "EC", "key_size": None}, ["X", "962"]),
        ("2048-bit RSA", {"family": "RSA", "key_size": 2048}, []),
        ("Blowfish 56", {"family": "Blowfish", "key_size": 56}, []),
        ("KeySize = 15", {"key_size": None}, ["15"]),
        # cipher suites: the record is the bulk cipher; SHA is SHA-1 (RFC 5246 A.5); NULL leaves the MAC hash
        (
            "TLS_DHE_DSS_EXPORT_WITH_DES40_CBC_SHA",
            {"family": "DES", "key_size": 40, "mode": "CBC", "protocol": "TLS", "digest": None},
            [],
        ),
        ("TLS_RSA_WITH_3DES_EDE_CBC_SHA", {"family": "3DES", "mode": "CBC", "protocol": "TLS"}, []),
        ("TLS_AES_128_GCM_SHA256", {"family": "AES", "key_size": 128, "mode": "GCM", "digest": None}, []),
        ("TLS_AES_128_CCM_8_SHA256", {"family": "AES", "key_size": 128, "mode": "CCM"}, []),
        ("TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256", {"algorithm": "ChaCha20-Poly1305", "protocol": "TLS"}, []),
        ("TLS_KRB5_EXPORT_WITH_RC2_CBC_40_SHA", {"family": "RC2", "key_size": 40, "mode": "CBC"}, []),
        ("SSL_RSA_WITH_RC4_128_MD5", {"family": "RC4", "key_size": 128, "protocol": "TLS"}, []),
        ("TLS_RSA_WITH_NULL_SHA256", {"algorithm": "SHA-256", "protocol": "TLS"}, []),
        ("ECDHE-RSA-AES128-GCM-SHA256", {"family": "AES", "key_size": 128, "mode": "GCM", "protocol": "TLS"}, []),
        ("DES-CBC3-SHA", {"family": "3DES", "mode": "CBC", "protocol": "TLS"}, []),
        ("Weak cipher: DES-CBC3-SHA", {"family": "3DES", "mode": "CBC", "protocol": "TLS"}, []),
        ("ADH-AES128-SHA", {"family": "AES", "key_size": 128, "protocol": "TLS"}, []),
        (
            "TLS_EMPTY_RENEGOTIATION_INFO_SCSV",
            {"protocol": "TLS", "family": None},
            ["EMPTY", "RENEGOTIATION", "INFO", "SCSV"],
        ),
        # no suites: the RSA refinement and the composites stay as they were
        ("SHA256withRSA", {"family": "RSASSA-PKCS1", "digest": "SHA-256", "protocol": None}, []),
        ("HMAC-SHA256", {"family": "HMAC", "digest": "SHA-256", "protocol": None}, []),
        ("RSA-SHA256", {"family": "RSASSA-PKCS1", "digest": "SHA-256", "protocol": None}, []),
        ("ChaCha20-Poly1305-256", {"algorithm": "ChaCha20-Poly1305", "key_size": 256}, []),
    ],
)
def test_parse_names_2026_10_23(name, expected, unmapped):
    p = V.parse(name)
    assert {k: p.values.get(k) for k in expected} == expected
    assert p.unmapped == unmapped


@pytest.mark.parametrize(
    "name, expected",
    [
        ("XDH", {"algorithm": "XDH", "family": "XDH"}),
        ("X963KDF", {"family": "ANSI-KDF"}),
        ("ECDH-X963KDF", {"family": "ECDH", "key_size": None}),
        ("ANSI-KDF-X9.63", {"family": "ANSI-KDF"}),
        ("DES3", {"family": "3DES"}),
        ("AES-OCB3", {"family": "AES", "mode": "OCB"}),
        ("AESOCB3", {"family": "AES", "mode": "OCB"}),
        ("AES-CBC/ISO7816-4Padding", {"family": "AES", "mode": "CBC", "padding": "ISO7816-4", "key_size": None}),
        ("Algorithm.RSA256", {"family": "RSASSA-PKCS1", "digest": "SHA-256", "key_size": None}),
        ("Algorithm.ECDSA512", {"family": "ECDSA", "curve": "secp521r1", "digest": "SHA-512"}),
        ("Algorithm.HMAC256", {"family": "HMAC", "digest": "SHA-256"}),
        ("Streamlined NTRU Prime", {"family": "NTRU-Prime"}),
    ],
)
def test_parse_names_added_2026_10_23(name, expected):
    p = V.parse(name)
    assert {k: p.values.get(k) for k in expected} == expected and not p.unmapped


def test_xdh_is_a_generic_marker_over_x25519_and_x448_only():
    assert V.generic("XDH") and V.specifics("XDH") == {"X25519", "X448"}
    assert V.relation("algorithm", "XDH", "X25519") == "refines" and V.relation("algorithm", "XDH", "ECDH") is None


def test_parsed_parameters_are_admitted_by_the_records_primitive():
    # CipherSift writes one component per part of `TLS_AES_128_GCM_SHA256` (algorithmFamily SHA-2 and AES); the
    # name's 128 and GCM belong to the AES part only (corpus-pilot-medium: 396 key sizes and 21 modes on hashes)
    comps = [
        _comp("TLS_AES_128_GCM_SHA256", ap={"algorithmFamily": "SHA-2"}),
        _comp("TLS_AES_128_GCM_SHA256", ap={"algorithmFamily": "AES"}),
        _comp("ES512", ap={"algorithmFamily": "SHA-2"}),
        _comp("SHA256-CBC"),
    ]
    sha, aes, es, cbc = normalize_cbom(_bom(*comps, version="1.7"), "ciphersift").records
    assert (sha.values["algorithm"], sha.values.get("key_size"), sha.values.get("mode")) == ("SHA-256", None, None)
    assert (aes.values["family"], aes.values["key_size"], aes.values["mode"], aes.values.get("digest")) == (
        "AES", 128, "GCM", None)  # fmt: skip
    assert (es.values["algorithm"], es.values.get("curve"), es.values.get("key_size")) == ("SHA-512", None, None)
    assert (cbc.values["algorithm"], cbc.values.get("mode")) == ("SHA-256", None)
    # JOSE `A128CBC-HS256` under CipherSift's HMAC component: the name's AES primitive (ae) and mode go, the MAC key
    # size and digest stay (RFC 7518 5.2.3: a 128-bit MAC key, HMAC-SHA-256); under its AES component everything stays
    comps = [
        _comp("A128CBC-HS256", ap={"algorithmFamily": "HMAC"}),
        _comp("A128CBC-HS256", ap={"algorithmFamily": "AES"}),
    ]
    hmac, aes = normalize_cbom(_bom(*comps, version="1.7"), "ciphersift").records
    assert (hmac.values["family"], hmac.values["primitive"], hmac.values.get("mode")) == ("HMAC", "mac", None)
    assert (hmac.values["key_size"], hmac.values["digest"]) == (128, "SHA-256")
    assert (aes.values["primitive"], aes.values["mode"], aes.values["digest"]) == ("ae", "CBC", "SHA-256")
    # a tool's own field is never dropped: a hash with a padding field keeps it (CryptobomForge's draft)
    (rec,) = normalize_cbom(_bom(_comp("SHA-256", ap={"padding": "pkcs7"}), version="1.7"), "ciphersift").records
    assert rec.values["padding"] == "PKCS7" and rec.via["padding"] == "field"


def test_the_oid_field_fills_gaps_and_a_name_that_is_an_oid_resolves():
    def with_oid(name, oid):
        c = _comp(name)
        c["cryptoProperties"]["oid"] = oid
        return c

    bom = _bom(
        with_oid("key-pair", "1.2.840.113549.1.1.11"),
        _comp("OID(1.2.840.10045.4.3.2)"),
        with_oid("AES-256-GCM", "1.2.840.113549.1.1.1"),  # the name wins; the OID fills nothing
    )
    sig, ecdsa, aes = normalize_cbom(bom, "qelens").records
    assert (sig.values["family"], sig.values["digest"], sig.via["family"]) == ("RSASSA-PKCS1", "SHA-256", "derived")
    assert sig.src["family"] == "derived:oid" and sig.resolution == "mapped"
    assert (ecdsa.values["family"], ecdsa.values["digest"], ecdsa.resolution) == ("ECDSA", "SHA-256", "mapped")
    assert (aes.values["family"], aes.values["oid"]) == ("AES", "1.2.840.113549.1.1.1") and not aes.conflicts


def test_a_list_in_a_field_is_ambiguous_not_unknown():
    comp = _comp("Hash160", props={"bf-cbom:digest": "SHA-256, RIPEMD-160"})
    r = normalize_cbom(_bom(comp), "deepseek").records[0]
    assert r.values.get("digest") is None and r.resolution == "ambiguous" and r.ambiguous.startswith("SHA-256, RIPEMD")
    assert all(u["src"] != "properties[bf-cbom:digest]" for u in r.unmapped)  # the list is understood, not unknown


def test_coverage_and_units_count_assets_records_and_items():
    from analysis.tables import unit_repo_rows, unit_rows

    occ = [{"location": "src/a.py", "line": k} for k in range(1, 6)]  # one asset found at five lines
    one_per_place = [_comp("AES", occ=[{"location": "src/b.py", "line": k}]) for k in (1, 2)]  # one per finding
    bom = _bom(_comp("AES-128-GCM", ap={"mode": "gcm"}, occ=occ), _comp("RSA"), _comp("Cipher.getInstance"))
    n = normalize_cbom(bom, "cbomkit")
    report = Report()
    report.add(n)
    rows = {r["dimension"]: r for r in report.coverage_rows()}
    assert (rows["mode"]["assets"], rows["mode"]["mapped"], rows["mode"]["api"]) == (3, 2, 1)
    assert (rows["mode"]["reported"], rows["location"]["reported"]) == (1, 1)  # once per asset, not five times
    (u,) = unit_rows([n])
    assert (u["assets"], u["records"], u["items"], u["records_per_asset_max"]) == (3, 7, 2, 5)  # AES, RSA; the API none
    assert (u["multi_occurrence_share"], u["unlocated_share"]) == (round(1 / 3, 3), round(2 / 3, 3))
    (v,) = unit_rows([normalize_cbom(_bom(*one_per_place), "cipherradar")])
    assert (v["assets"], v["records"], v["items"], v["assets_per_item"]) == (2, 2, 1, 2.0)  # two components, one item
    m = normalize_cbom(_bom(*one_per_place), "cbomkit")  # the same tool on a second repository
    per_repo = unit_repo_rows([n, m], ["o/a", "o/b"], {"o/a": "Python"})
    assert [(r["repo"], r["language"], r["assets"], r["records"], r["items"]) for r in per_repo] == [
        ("o/a", "Python", 3, 7, 2),
        ("o/b", None, 2, 2, 1),
    ]
    (w,) = unit_rows([n, m])
    assert (w["cboms"], w["assets"], w["records"], w["items"]) == (2, 5, 9, 3)  # the repositories' rows summed
    assert w["outside_code"] == 0  # no language given: every file counts as code
    fixture = _comp("AES", occ=[{"location": "test/vectors.json", "line": 3}, {"location": "src/a.java", "line": 9}])
    (x,) = unit_rows([normalize_cbom(_bom(fixture), "cbomkit", language="java")])
    assert (x["records"], x["outside_code"]) == (2, 1)  # the JSON fixture is no Java source file


def test_sources_rows_say_where_each_dimension_lives():
    """RQ2's "where the information lives": per (tool, dimension, kind of source) the algorithm assets whose value came
    from a standard field, the name, a property, ... and the share of the tool's algorithm assets
    (sources-run<i>.csv)."""
    from analysis import summary as sm
    from analysis.normalize.report import SOURCES_COLUMNS

    bom = _bom(
        _comp("AES-128-GCM", ap={"primitive": "ae"}),  # mode and key size from the name, primitive from the field
        _comp("RSA", ap={"primitive": "pke", "padding": "oaep"}),  # padding from the field
        _comp("my-key", asset="related-crypto-material"),  # no algorithm asset: not counted
    )
    report = Report()
    report.add(normalize_cbom(bom, "cbomkit"))
    rows = report.sources_rows()
    assert set(rows[0]) == set(SOURCES_COLUMNS) and all(r["assets"] == 2 for r in rows)
    by = {(r["dimension"], r["kind"]): r for r in rows}
    assert (by[("primitive", "field")]["count"], by[("primitive", "field")]["share"]) == (2, 1.0)
    assert (by[("mode", "name")]["count"], by[("padding", "field")]["count"]) == (1, 1)
    assert by[("family", "name")]["count"] == 2 and ("family", "field") not in by  # 1.6 has no algorithmFamily
    text = "\n".join(sm._sources(rows))
    assert "### Where the information lives" in text and "| cbomkit | 2 | 0.00 (name 1.00) | 1.00 |" in text
