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
    assert all(s.file == "src/main/java/app/CryptoDemo.java" for s in sites)


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


def test_extract_sites_reads_cyclonedx_17_algorithm_family():
    """Components named after the API call (CipherSift, CipherRadar) carry the family in algorithmFamily."""

    def comp(name, line, family=None):
        ap = {"algorithmFamily": family} if family else {}
        return {
            "type": "cryptographic-asset",
            "name": name,
            "cryptoProperties": {"assetType": "algorithm", "algorithmProperties": ap},
            "evidence": {"occurrences": [{"location": "src/A.java", "line": line}]},
        }

    cbom = {"components": [comp("GCMParameterSpec", 10, "AES"), comp("Cipher.getInstance", 20)]}
    sites, stats = extract_sites(cbom, "ciphersift", language="java")
    assert [(s.line, set(s.families)) for s in sites] == [(10, {"AES"}), (20, set())]
    assert stats.unknown_family == 1  # an unresolved algorithm string stays unknown


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
    assert project_for_repo("bfcbom/micro-java").file == "src/main/java/app/CryptoDemo.java"
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


@pytest.mark.parametrize(
    "fixture, tool, project, strict, lenient, repo",
    [
        ("cbomkit_micro-java", "cbomkit", "micro-java", 11, 11, 11),
        ("cbomkit_micro-python", "cbomkit", "micro-python", 5, 5, 9),  # misses MD5 and HMAC anywhere
        ("pqcheck_micro-python", "pqcheck", "micro-python", 8, 8, 11),
        ("cipherradar_micro-java", "cipherradar", "micro-java", 11, 11, 11),
        ("cbomkit_micro-go", "cbomkit", "micro-go", 8, 8, 11),
    ],
)
def test_score_records_matches_call_sites_and_adds_repo_level(fixture, tool, project, strict, lenient, repo):
    """The record-based scorer reproduces the call-site numbers of score_sites (same fixtures) and adds the repo
    level, which is at least the call-site level (assets sharing a family are found together)."""
    from analysis.groundtruth import score_records
    from analysis.normalize import normalize_cbom

    truth = load_ground_truth()[project]
    score = score_records(normalize_cbom(_cbom(fixture), tool, language=truth.language).records, truth, tool)
    assert (len(score.hits_strict), len(score.hits_lenient), len(score.hits_repo)) == (strict, lenient, repo)
    assert score.false_sites == [] and score.precision == 1.0 and score.repo_extra == []
    assert set(score.hits_lenient) <= set(score.hits_repo)


def test_score_records_windows_location_free_records_and_extra_families():
    from analysis.groundtruth import score_records
    from analysis.normalize import NormRecord

    truth = load_ground_truth()["micro-python"]
    rsa_line = next(a for a in truth.assets if a.id == "rsa-2048-keygen").lines[0]

    def rec(i, group, *, file=truth.file, line=None, line_end=None, name="x"):
        values = {"asset_type": "algorithm"}
        return NormRecord(f"t:{i}:0", "t", i, 0, name=name, group=group, values=values, file=file, line=line,
                          line_end=line_end)  # fmt: skip

    records = [
        rec(0, "RSA", line=rsa_line - 2, line_end=rsa_line + 2),  # a 5-line window around the call
        rec(1, "MD5", file=None),  # no location: repository level only
        rec(2, "SHA-2", file=None),  # SHA-256 asset, but not HMAC-SHA512 (its own family is HMAC)
        rec(3, "ChaCha", file=None),  # not in the project
    ]
    score = score_records(records, truth, "t")
    assert score.hits_strict == ["rsa-2048-keygen"] and score.sites == 1 and score.precision == 1.0
    assert set(score.hits_repo) == {"rsa-2048-keygen", "rsa-oaep-sha256", "md5", "sha-256"}
    assert score.repo_extra == ["ChaCha"] and "hmac-sha512" in score.repo_missed


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


# ----- CSV tables -----

SNAPSHOT = "bfcbom__micro-java@0123456789ab"


def _fake_experiment(root: Path) -> Path:
    """Tiny stored experiment: two ok runs on micro-java plus one unsupported run without a CBOM."""
    exp = root / "exp-tables"
    exp.mkdir()
    (exp / "experiment.yaml").write_text(
        "name: exp-tables\ntools: [cbomkit, cipherradar, pqcheck]\nrepos:\n"
        "  - full_name: bfcbom/micro-java\n    language: Java\n",
        encoding="utf-8",
    )
    for tool in ("cbomkit", "cipherradar", "pqcheck"):
        d = exp / SNAPSHOT / "original" / tool
        d.mkdir(parents=True)
        meta = {"worker": tool, "run_index": 1, "variant": "original", "base_commit": "0123456789ab" + "0" * 28}
        if tool == "pqcheck":
            meta.update(status="unsupported", language="Java")
        else:
            meta.update(status="ok", duration_sec=1.5, tool_version="1.0")
            (d / "run1.json").write_text(json.dumps(_cbom(f"{tool}_micro-java")), encoding="utf-8")
        (d / "run1.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return exp


def _read_csv(path: Path) -> list[dict]:
    import csv

    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_run_rows_counts_and_unsupported(tmp_path):
    from analysis.tables import run_rows

    rows = {r["tool"]: r for r in run_rows(_fake_experiment(tmp_path))}
    assert set(rows) == {"cbomkit", "cipherradar", "pqcheck"}
    assert rows["pqcheck"]["status"] == "unsupported" and "components" not in rows["pqcheck"]
    cbom = _cbom("cbomkit_micro-java")
    assert rows["cbomkit"]["components"] == len(cbom["components"])
    assert rows["cbomkit"]["repo"] == "bfcbom/micro-java" and rows["cbomkit"]["snapshot"] == SNAPSHOT
    assert 0 < rows["cbomkit"]["algorithm_assets"] <= rows["cbomkit"]["crypto_assets"]


def test_write_csv_round_trip_and_empty_header(tmp_path):
    from analysis.tables import RUNS_COLUMNS, write_csv

    assert write_csv(tmp_path / "empty.csv", [], RUNS_COLUMNS) == 0
    assert (tmp_path / "empty.csv").read_text(encoding="utf-8") == ",".join(RUNS_COLUMNS) + "\n"
    write_csv(tmp_path / "x.csv", [{"a": ["x", "y"], "b": None, "c": True}], ["a", "b", "c"])
    assert _read_csv(tmp_path / "x.csv") == [{"a": "x;y", "b": "", "c": "true"}]


def test_tables_command_writes_joinable_tables(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from analysis import tables as tbl
    from misc.cli import analysis_cmd

    exp = _fake_experiment(tmp_path)
    monkeypatch.setattr(analysis_cmd, "experiment_dir", lambda name: tmp_path / name)
    result = CliRunner().invoke(analysis_cmd.analysis_app, ["tables", "exp-tables"])
    assert result.exit_code == 0, result.output
    out = exp / "analysis"
    expected = ["runs.csv", "sites-run1.csv", "groups-callsite-run1.csv", "members-callsite-run1.csv"]
    assert all((out / f).exists() for f in expected + ["micro-run1.csv", "tables-run1.json"])

    sites = _read_csv(out / "sites-run1.csv")
    groups = _read_csv(out / "groups-callsite-run1.csv")
    members = _read_csv(out / "members-callsite-run1.csv")
    assert list(sites[0]) == tbl.SITES_COLUMNS and list(groups[0]) == tbl.CALLSITE_GROUP_COLUMNS
    record_ids = [s["record_id"] for s in sites]
    assert len(record_ids) == len(set(record_ids))
    group_ids = {g["group_id"] for g in groups}
    assert {m["record_id"] for m in members} <= set(record_ids) and {m["group_id"] for m in members} == group_ids
    in_scope = {s["record_id"] for s in sites if s["in_scope"] == "true"}
    assert sorted(m["record_id"] for m in members) == sorted(in_scope)
    for g in groups:
        assert int(g["n_sites"]) == sum(m["group_id"] == g["group_id"] for m in members)
    assert {g["k"] for g in groups} == {"2"} and len(groups) == 12  # as in test_two_tools_agree_on_micro_java

    micro = {r["tool"]: r for r in _read_csv(out / "micro-run1.csv")}
    assert set(micro) == {"cbomkit", "cipherradar", "pqcheck"} and micro["cbomkit"]["language"] == "java"
    cbomkit = micro["cbomkit"]
    assert (cbomkit["status"], cbomkit["verdict"], cbomkit["lenient"], cbomkit["repo"]) == ("ok", "pass", "11", "11")
    assert (micro["pqcheck"]["verdict"], micro["pqcheck"]["lenient"]) == ("unsupported", "")
    assets = _read_csv(out / "micro-assets-run1.csv")
    assert list(assets[0]) == tbl.MICRO_ASSET_COLUMNS and len(assets) == 2 * 11  # scored runs only
    assert json.loads((out / "tables-run1.json").read_text())["rows"]["runs.csv"] == 3


def test_report_command_writes_every_table_and_the_summary(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from analysis import levels as lv
    from analysis.normalize import CONFORMANCE_COLUMNS, COVERAGE_COLUMNS
    from misc.cli import analysis_cmd

    exp = _fake_experiment(tmp_path)
    monkeypatch.setattr(analysis_cmd, "experiment_dir", lambda name: tmp_path / name)
    result = CliRunner().invoke(analysis_cmd.analysis_app, ["report", "exp-tables"])
    assert result.exit_code == 0, result.output
    out = exp / "analysis"
    for f in ("normalize.json", "vocab.csv", "levels-run1.csv", "records-run1.csv", "micro-run1.csv"):
        assert (out / f).exists(), f
    assert list(_read_csv(out / "coverage.csv")[0]) == COVERAGE_COLUMNS
    assert (out / "conformance.csv").read_text(encoding="utf-8").startswith(",".join(CONFORMANCE_COLUMNS))
    algos = _read_csv(out / "algorithms-run1.csv")
    assert list(algos[0]) == lv.ALGORITHM_COLUMNS and {a["level"] for a in algos} == set(lv.LEVELS)
    assert {a["tool"] for a in algos} == {"cbomkit", "cipherradar"} and algos[0]["language"] == "Java"
    info = json.loads((out / "tables-run1.json").read_text())
    assert info["rows"]["algorithms-run1.csv"] == len(algos) and info["vocabulary"]["algorithms"]
    summary = (out / "SUMMARY.md").read_text(encoding="utf-8")
    assert summary.startswith("# exp-tables: analysis summary (run 1)")
    for section in ("## Runs", "## Micro projects", "## Normalisation", "## Agreement ladder", "## Files"):
        assert section in summary
    assert "| cbomkit | pass 11 |" in summary and "| pqcheck | – |" in summary
    assert "| cbomkit | 11 / 11 · 11 |" in summary


def test_export_runs_the_report_and_zips_the_summary(tmp_path, monkeypatch):
    import zipfile

    from typer.testing import CliRunner

    from misc.cli import analysis_cmd, experiment_cmd

    exp = _fake_experiment(tmp_path)
    monkeypatch.setattr(analysis_cmd, "experiment_dir", lambda name: tmp_path / name)
    monkeypatch.setattr(experiment_cmd, "experiment_dir", lambda name: tmp_path / name)
    dest = tmp_path / "out"
    result = CliRunner().invoke(experiment_cmd.experiment_app, ["export", "exp-tables", "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    names = zipfile.ZipFile(dest / "exp-tables.zip").namelist()
    assert "exp-tables/analysis/SUMMARY.md" in names and "exp-tables/analysis/micro-run1.csv" in names
    assert (exp / "analysis" / "SUMMARY.md").exists()

    (exp / "analysis" / "SUMMARY.md").unlink()
    result = CliRunner().invoke(
        experiment_cmd.experiment_app, ["export", "exp-tables", "--dest", str(dest), "--no-report"]
    )
    assert result.exit_code == 0 and not (exp / "analysis" / "SUMMARY.md").exists()


def test_summary_markdown_from_csv_rows():
    from analysis import summary as sm

    assert sm.md_table(["a", "b"], [["x|y", 1]]) == ["| a | b |", "| --- | ---: |", "| x\\|y | 1 |"]
    base = {"n_assets": "11"}
    rows = [
        {**base, "language": "java", "tool": "weak", "verdict": "fail", "strict": "0", "lenient": "2", "repo": "7"},
        {**base, "language": "go", "tool": "weak", "verdict": "error"},
        {**base, "language": "java", "tool": "good", "verdict": "pass", "strict": "9", "lenient": "11", "repo": "11"},
        {**base, "language": "go", "tool": "good", "verdict": "unsupported"},
    ]
    lines = sm._micro(rows, [])
    assert "| tool | java | go |" in lines  # paper languages first
    assert lines.index("| good | pass 11 | – |") < lines.index("| weak | fail 2 | error |")  # passes first
    assert "| weak | 0 / 2 · 7 |  |" in lines
