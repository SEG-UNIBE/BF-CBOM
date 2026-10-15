"""Tests for the analysis package: family table, call-site extraction, matching and micro ground truth.

The CBOM fixtures under tests/fixtures/pilot/ are real tool outputs from the pilot of 2026-10-08
on the micro projects (CBOMkit action jar, pqcheck 0.1.0, CipherRadar efa5d9d).
"""

import json
from pathlib import Path

import pytest

from analysis.families import load_families, rule_id_to_text
from analysis.groundtruth import load_ground_truth, project_for_repo, score_sites
from analysis.match import cluster_sites, summarize_clusters
from analysis.sites import Site, extract_sites, relative_location

FIXTURES = Path(__file__).parent / "fixtures" / "pilot"


def _cbom(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----- families -----


@pytest.mark.parametrize(
    "name, expected",
    [
        ("SHA256withECDSA", {"EC", "SHA-2"}),
        ("HmacSHA512", {"HMAC", "SHA-2"}),
        ("AES/GCM/NoPadding", {"AES"}),
        ("RSA/ECB/OAEPWithSHA-256AndMGF1Padding", {"RSA", "SHA-2"}),
        ("secp256r1", {"EC"}),
        ("P-256", {"EC"}),
        ("ECB", set()),
        ("DES/CBC/PKCS5Padding", {"DES"}),
        ("DESede", {"DES"}),
        ("CODES", set()),
        ("PBEWithMD5AndDES", {"PBKDF", "MD5", "DES"}),
        ("SHA", {"SHA-1"}),
        ("SHA3-256", {"SHA-3"}),
        ("openssl-des", {"DES"}),
        ("SSLContext", {"TLS"}),
        ("ES256", {"EC"}),
        ("RS256", {"RSA"}),
        ("rand.Reader", {"RANDOM"}),
        ("X25519MLKEM768", {"EC", "PQC"}),
        ("jca-cipher-getinstance", set()),
        ("private-key@1234", set()),
        ("", set()),
    ],
)
def test_family_classification(name, expected):
    assert set(load_families().classify(name)) == expected


def test_family_fallback_texts_and_rule_ids():
    table = load_families()
    assert table.classify("jca-cipher-getinstance", "AES") == {"AES"}
    assert rule_id_to_text("cbom-java-ecb-mode-usage") == "ecb mode usage"
    assert rule_id_to_text("tmp.x.rules.xbom-java-pqc-vulnerable-rsa-keygen") == "pqc vulnerable rsa keygen"
    assert table.classify(rule_id_to_text("xbom-java-pqc-vulnerable-rsa-keygen")) == {"RSA"}


# ----- sites -----


def test_relative_location():
    assert relative_location("./src/a.py") == "src/a.py"
    assert relative_location("/tmp/x/repo/src/a.py") == "src/a.py"
    assert relative_location("/abs/elsewhere/a.py") == "/abs/elsewhere/a.py"


def test_extract_sites_merges_per_line_and_drops_material():
    sites, stats = extract_sites(_cbom("cbomkit_micro-java"), "cbomkit", language="java")
    assert stats.components == 23 and stats.skipped_asset_type == 8  # 8 key/iv/salt material assets
    assert stats.sites == 12 and all(s.in_scope for s in sites)
    by_line = {s.line: s for s in sites}
    assert by_line[32].families == {"HMAC", "SHA-2"}  # HMAC-SHA-512 and its SHA-512 component on one line
    assert by_line[12].raw == ["RSA-2048"]
    assert all(s.file == "src/main/java/demo/CryptoDemo.java" for s in sites)


def test_extract_sites_keeps_all_assets_on_request():
    _, strict = extract_sites(_cbom("cbomkit_micro-python"), "cbomkit", language="python")
    _, lenient = extract_sites(_cbom("cbomkit_micro-python"), "cbomkit", language="python", asset_types=None)
    assert lenient.sites > strict.sites and lenient.skipped_asset_type == 0


def test_extract_sites_custom_property_locations():
    cbom = {
        "components": [
            {
                "type": "cryptographic-asset",
                "name": "AES",
                "cryptoProperties": {"assetType": "algorithm"},
                "properties": [{"name": "qdarpan:location.0", "value": "/x/repo/src/Main.java:20"}],
            }
        ]
    }
    sites, _ = extract_sites(cbom, "q-darpan")
    assert [(s.file, s.line, set(s.families)) for s in sites] == [("src/Main.java", 20, {"AES"})]


# ----- matching -----


def _site(tool, line, fams, file="a.py"):
    return Site(tool=tool, file=file, line=line, families=frozenset(fams))


def test_cluster_window_family_and_unknown_rules():
    sites = [
        _site("t1", 10, {"AES"}),
        _site("t2", 12, {"AES"}),  # within +-2 -> same cluster
        _site("t3", 13, {"AES"}),  # 3 lines from t1, but 1 from t2 -> nearest-neighbour joins it
        _site("t1", 30, {"RSA"}),
        _site("t2", 31, {"SHA-2"}),  # incompatible family -> separate
        _site("t1", 50, set()),
        _site("t2", 50, set()),  # unknown never matches by default
        _site("t2", 10, {"AES"}, file="b.py"),  # other file
    ]
    clusters = cluster_sites(sites)
    keyed = {(c.file, c.line_min): c for c in clusters}
    assert keyed[("a.py", 10)].k == 3 and keyed[("a.py", 10)].line_max == 13
    assert keyed[("a.py", 30)].k == 1 and keyed[("a.py", 31)].k == 1
    assert keyed[("a.py", 50)].k == 1 and len([c for c in clusters if c.line_min == 50]) == 2
    assert keyed[("b.py", 10)].k == 1
    lenient = cluster_sites(sites, match_unknown=True)
    assert [c.k for c in lenient if c.line_min == 50] == [2]


def test_summarize_clusters_counts():
    clusters = cluster_sites([_site("t1", 1, {"AES"}), _site("t2", 1, {"AES"}), _site("t1", 9, {"RSA"})])
    s = summarize_clusters(clusters)
    assert s.k_distribution == {2: 1, 1: 1}
    assert s.per_tool["t1"].confirmed == 1 and s.per_tool["t1"].solo == 1
    assert s.per_tool["t2"].confirmed_share == 1.0
    assert s.pairwise_jaccard == {"t1|t2": 0.5}


def test_two_tools_agree_on_micro_java():
    sites = []
    for fixture, tool in (("cbomkit_micro-java", "cbomkit"), ("cipherradar_micro-java", "cipherradar")):
        s, _ = extract_sites(_cbom(fixture), tool, language="java")
        sites += s
    clusters = cluster_sites(sites)
    assert {c.k for c in clusters} == {2} and len(clusters) == 12


# ----- ground truth -----


def test_ground_truth_loads_and_maps_repo_names():
    truth = load_ground_truth()
    assert set(truth) == {"micro-java", "micro-python", "micro-go", "micro-javascript", "micro-c"}
    for p in truth.values():
        assert len(p.assets) == 11
    assert project_for_repo("bfcbom/micro-java").file == "src/main/java/demo/CryptoDemo.java"
    assert project_for_repo("x/other") is None


@pytest.mark.parametrize(
    "fixture, tool, project, strict, lenient",
    [
        ("cbomkit_micro-java", "cbomkit", "micro-java", 11, 11),  # pilot: 11/11
        ("cbomkit_micro-python", "cbomkit", "micro-python", 5, 5),  # pilot: 6/11 counting a key-material asset
        ("pqcheck_micro-python", "pqcheck", "micro-python", 8, 8),
        ("cipherradar_micro-java", "cipherradar", "micro-java", 11, 11),
        ("cbomkit_micro-go", "cbomkit", "micro-go", 8, 8),  # pilot: 8-10/11
    ],
)
def test_micro_scores_reproduce_pilot(fixture, tool, project, strict, lenient):
    truth = load_ground_truth()[project]
    sites, _ = extract_sites(_cbom(fixture), tool, language=truth.language)
    score = score_sites(sites, truth, tool)
    assert (len(score.hits_strict), len(score.hits_lenient)) == (strict, lenient)
    assert score.false_sites == [] and score.precision == 1.0
    assert score.passes_bar is (lenient >= 6)


def test_score_sites_lenient_and_false_positives():
    truth = load_ground_truth()["micro-python"]
    sites = [
        Site("t", truth.file, 15, frozenset()),  # unknown family on the RSA line: lenient hit only
        Site("t", truth.file, 23, frozenset({"MD5"})),  # wrong family on the SHA-256 line: false site
        Site("t", truth.file, 11, frozenset({"SHA-2"})),  # related line (HASH constant): not a false site
        Site("t", "README.md", 1, frozenset({"AES"})),  # other file: ignored for precision
    ]
    score = score_sites(sites, truth, "t")
    assert score.hits_strict == [] and score.hits_lenient == ["rsa-2048-keygen"]
    assert score.sites == 4 and score.sites_in_file == 3 and score.sites_correct == 2
    assert score.false_sites == [{"line": 23, "families": ["MD5"], "raw": []}]
    assert score.passes_bar is False and "sha-256" in score.missed
