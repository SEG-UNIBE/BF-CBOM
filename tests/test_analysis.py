"""Tests for the analysis package: micro ground truth, CSV tables, the report and the summary.

The CBOM fixtures under tests/fixtures/pilot/ are real tool outputs from the pilot of 2026-10-08
on the micro projects (CBOMkit action jar, pqcheck 0.1.0, CipherRadar efa5d9d).
"""

import json
import re
from pathlib import Path

import pytest

from analysis.groundtruth import load_ground_truth, project_for_repo

FIXTURES = Path(__file__).parent / "fixtures" / "pilot"


OLD_TRUTH = FIXTURES / "ground_truth-2026-10-08.yaml"  # the micro projects the pilot CBOMs above were made on


def _cbom(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def old_truth(monkeypatch):
    """The CLI's default ground truth replaced by the frozen one of 2026-10-08, for the pilot CBOMs."""
    import analysis.groundtruth as gt

    monkeypatch.setattr(gt, "DEFAULT_TRUTH", OLD_TRUTH)
    gt.load_ground_truth.cache_clear()
    yield
    gt.load_ground_truth.cache_clear()


# ----- ground truth -----


def test_ground_truth_loads_and_maps_repo_names():
    truth = load_ground_truth()
    assert set(truth) == {"micro-java", "micro-python", "micro-go"}
    for p in truth.values():
        assert len(p.assets) == 14 and len(p.decoys) == 4
    assert project_for_repo("bfcbom/micro-java").file == "src/main/java/app/CryptoDemo.java"
    assert project_for_repo("x/other") is None


@pytest.mark.parametrize(
    "fixture, tool, project, strict, lenient, repo",
    [
        ("cbomkit_micro-java", "cbomkit", "micro-java", 11, 11, 11),
        ("cbomkit_micro-python", "cbomkit", "micro-python", 6, 6, 9),  # misses MD5 and HMAC anywhere
        ("pqcheck_micro-python", "pqcheck", "micro-python", 8, 8, 11),
        ("cipherradar_micro-java", "cipherradar", "micro-java", 11, 11, 11),
        ("cbomkit_micro-go", "cbomkit", "micro-go", 8, 8, 11),
    ],
)
def test_score_records_matches_call_sites_and_adds_repo_level(fixture, tool, project, strict, lenient, repo):
    """Call-site numbers as the pilot found them (CBOMkit micro-python 6/11: its EC key record at the EC key
    generation counts, the keygen rule) plus the repo level, which is at least the call-site level (assets sharing a
    family are found together)."""
    from analysis.groundtruth import score_records
    from analysis.normalize import normalize_cbom

    truth = load_ground_truth(OLD_TRUTH)[project]
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
    assert score.repo_extra == ["ChaCha"] and "hmac-sha256" in score.repo_missed


# ----- CSV tables -----

SNAPSHOT = "bfcbom__micro-java@0123456789ab"


PYTHON_SNAPSHOT = "bfcbom__micro-python@0123456789ac"


def _fake_experiment(root: Path, *, python: bool = False) -> Path:
    """Tiny stored experiment: two ok runs on micro-java plus one unsupported run without a CBOM; with `python` also
    micro-python (cbomkit and pqcheck ok, cipherradar unsupported), so the matchers have two repositories."""
    exp = root / "exp-tables"
    exp.mkdir()
    (exp / "_experiment.yaml").write_text(
        "name: exp-tables\ntools: [cbomkit, cipherradar, pqcheck]\nrepos:\n"
        "  - full_name: bfcbom/micro-java\n    language: Java\n    files: 3\n    source_files: 2\n"
        "    checkout_bytes: 3145728\n    source_bytes: 1048576\n"
        + ("  - full_name: bfcbom/micro-python\n    language: Python\n" if python else ""),
        encoding="utf-8",
    )
    snapshots = {SNAPSHOT: ("java", "pqcheck", "0123456789ab")}
    if python:
        snapshots[PYTHON_SNAPSHOT] = ("python", "cipherradar", "0123456789ac")
    for snapshot, (language, unsupported, sha12) in snapshots.items():
        for tool in ("cbomkit", "cipherradar", "pqcheck"):
            d = exp / snapshot / "original" / tool
            d.mkdir(parents=True)
            meta = {"worker": tool, "run_index": 1, "variant": "original", "base_commit": sha12 + "0" * 28}
            if tool == unsupported:
                meta.update(status="unsupported", language=language.capitalize())
            else:
                meta.update(status="ok", duration_sec=1.5, tool_version="1.0")
                (d / "run1.json").write_text(json.dumps(_cbom(f"{tool}_micro-{language}")), encoding="utf-8")
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
    kinds = ("algorithm_assets", "certificate_assets", "protocol_assets", "material_assets", "other_assets")
    assert sum(rows["cbomkit"][k] for k in kinds) == rows["cbomkit"]["crypto_assets"]
    assert 0 < rows["cbomkit"]["located_line_assets"] <= rows["cbomkit"]["located_file_assets"]
    assert rows["cbomkit"]["located_file_assets"] <= rows["cbomkit"]["crypto_assets"]
    rows = {r["tool"]: r for r in run_rows(tmp_path / "exp-tables", expects={"bfcbom/micro-java": "low"})}
    assert rows["cbomkit"]["expect_crypto"] == "low" and rows["pqcheck"]["expect_crypto"] == "low"


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
    out = exp / "_ANALYSIS"
    expected = ["runs.csv", "records-run1.csv", "groups-exact-run1.csv", "members-exact-run1.csv"]
    assert all(
        (out / f).exists() for f in expected + ["agreement-exact-run1.csv", "micro-run1.csv", "tables-run1.json"]
    )

    records = _read_csv(out / "records-run1.csv")
    groups = _read_csv(out / "groups-exact-run1.csv")
    members = _read_csv(out / "members-exact-run1.csv")
    agreement = _read_csv(out / "agreement-exact-run1.csv")
    assert list(groups[0]) == tbl.EXACT_GROUP_COLUMNS and list(members[0]) == tbl.MEMBERS_COLUMNS
    assert list(agreement[0]) == tbl.AGREEMENT_COLUMNS
    record_ids = {r["record_id"] for r in records}
    assert {m["record_id"] for m in members} <= record_ids
    group_ids = {g["group_id"] for g in groups}
    assert {m["group_id"] for m in members} == group_ids == {a["group_id"] for a in agreement}
    repo = [g for g in groups if g["scope"] == "repo"]
    sites = [g for g in groups if g["scope"] == "site"]
    assert all(g["level"] == "L4" and g["parent"] == "" for g in repo) and all(g["parent"] in group_ids for g in sites)
    for g in groups:
        assert int(g["n_records"]) == sum(m["group_id"] == g["group_id"] for m in members)
    # CBOMkit and CipherRadar agree on micro-java: most repo groups have both voters, most sites too
    assert sum(g["n_tools"] == "2" for g in repo) >= 8 and sum(g["n_tools"] == "2" for g in sites) >= 10
    dims = {a["dimension"] for a in agreement}
    assert dims == {"key_size", "mode", "padding", "curve", "digest", "location"}
    info = json.loads((out / "tables-run1.json").read_text())
    assert info["settings"]["level"] == "L4" and info["settings"]["window"] == 2


def test_report_command_writes_every_table_and_the_summary(tmp_path, monkeypatch, old_truth):
    from typer.testing import CliRunner

    from analysis import levels as lv
    from analysis.matchers import raqun
    from analysis.normalize import CONFORMANCE_COLUMNS, COVERAGE_COLUMNS
    from misc.cli import analysis_cmd

    exp = _fake_experiment(tmp_path)
    monkeypatch.setattr(analysis_cmd, "experiment_dir", lambda name: tmp_path / name)
    result = CliRunner().invoke(analysis_cmd.analysis_app, ["report", "exp-tables"])
    assert result.exit_code == 0, result.output
    out = exp / "_ANALYSIS"
    for f in ("normalize.json", "vocab.csv", "levels-run1.csv", "records-run1.csv", "micro-run1.csv"):
        assert (out / f).exists(), f
    for f in ("schema-run1.csv", "meaningful-run1.csv", "traceability-run1.csv", "score-run1.csv", "loo-run1.csv"):
        assert (out / f).exists(), f
    assert (out / "stability.csv").exists() and _read_csv(out / "stability.csv") == []  # one run each: no rows
    scores = {(r["dimension"], r["tool"]): r for r in _read_csv(out / "score-run1.csv")}
    assert int(scores[("algorithm", "cbomkit")]["items"]) >= 11 and scores[("algorithm", "cipherradar")]["items"]
    assert float(scores[("algorithm", "cbomkit")]["rate_pooled"]) >= 0.8  # CBOMkit and CipherRadar agree on micro-java
    assert scores[("algorithm", "cbomkit")]["ds_support"] and scores[("location", "cbomkit")]["items"]
    grouping = {(r["dimension"], r["grouping"]): r for r in _read_csv(out / "grouping-run1.csv")}
    assert grouping[("algorithm", "declared")]["kendall_tau"] in ("", "1.0")  # one ranked tool or the same order
    assert {g for _, g in grouping} == {"declared", "per-tool", "llm-merged", "engine-merged"}
    assert list(_read_csv(out / "coverage-run1.csv")[0]) == COVERAGE_COLUMNS
    assert (out / "conformance-run1.csv").read_text(encoding="utf-8").startswith(",".join(CONFORMANCE_COLUMNS))
    units = {u["tool"]: u for u in _read_csv(out / "units-run1.csv")}
    assert units["cbomkit"]["assets"] == _read_csv(out / "coverage-run1.csv")[0]["assets"]  # the same CBOMs
    assert units["cbomkit"]["outside_code"] == "0"  # micro-java: every place is in a .java file
    groups = _read_csv(out / "groups-exact-run1.csv")
    assert all(int(g["member_voter_groups"]) <= int(g["voter_groups"]) for g in groups)
    per_repo = _read_csv(out / "units-repo-run1.csv")
    assert sum(int(r["items"]) for r in per_repo if r["tool"] == "cbomkit") == int(units["cbomkit"]["items"])
    algos = _read_csv(out / "items-run1.csv")
    assert list(algos[0]) == lv.ITEM_COLUMNS and all(int(a["k_L0"]) <= int(a["k_L4"]) for a in algos)
    levels = _read_csv(out / "matchlevels-run1.csv")
    exact = [r for r in levels if r["matcher"] == "exact"]
    assert [r["level"] for r in exact] == list(lv.LEVELS)
    assert int(exact[0]["items"]) == int(exact[-1]["items"])  # the unit is fixed across levels
    k2plus = [int(r["k2"]) + int(r["k3"]) + int(r["k4plus"]) for r in exact]
    assert k2plus == sorted(k2plus)  # cumulative: agreement never drops at a higher level
    assert all(float(r["seconds"]) >= 0 for r in levels)  # each matcher's time per level
    assert "**Matching time**" in (out / "_SUMMARY.md").read_text(encoding="utf-8")
    if raqun.available():  # the `raqun` extra (CI, the runner image): matcher (c) at every level, next to exact
        assert [r["level"] for r in levels if r["matcher"] == "raqun"] == list(lv.LEVELS)
        matchers = _read_csv(out / "matchers-run1.csv")
        assert {r["level"] for r in matchers} == set(lv.LEVELS)
        assert all(float(r["exact_seconds"]) >= 0 and float(r["raqun_seconds"]) >= 0 for r in matchers)
        assert _read_csv(out / "groups-raqun-run1.csv")
        for level in lv.LEVELS:
            inspection = _read_csv(out / f"raqun-{level}-run1.csv")
            assert inspection and {r["level"] for r in inspection} == {level}
            assert all("cbomkit" in r and "cipherradar" in r for r in inspection)
            assert all(sum(bool(r[t]) for t in ("cbomkit", "cipherradar")) == int(r["n_tools"]) for r in inspection)
            assert any("algorithm=" in r["cbomkit"] for r in inspection)
    assert {a["tool"] for a in algos} == {"cbomkit", "cipherradar"} and algos[0]["language"] == "Java"
    info = json.loads((out / "tables-run1.json").read_text())
    assert info["rows"]["items-run1.csv"] == len(algos) and info["vocabulary"]["algorithms"]
    summary = (out / "_SUMMARY.md").read_text(encoding="utf-8")
    assert summary.startswith("# Analysis Summary of Experiment `exp-tables`\n")
    toc = summary[summary.index("## Contents") : summary.index("## Preamble")]
    links = re.findall(r"^ *- \[(.+)\]\(#(.+)\)$", toc, flags=re.M)  # every entry links to a heading below
    headings = re.findall(r"^#{2,3} (.+)$", summary[len(toc) :], flags=re.M)
    assert [title for title, _ in links] == headings
    assert links[0] == ("Preamble", "preamble") and ("How to read this summary", "how-to-read-this-summary") in links
    assert ("RQ3a: the L0-L4 deltas (the agreement ladder)", "rq3a-the-l0-l4-deltas-the-agreement-ladder") in links
    assert info["voters"]["cipherradar"] == {"voter_group": "cipherradar", "role": "voter", "engine": "opengrep-rules"}
    sections = ("## Preamble", "### How to read this summary", "### Research questions", "### Runs",
                "### Repositories", "## Bar exam: the micro projects", "## RQ1 Conformance",
                "### Spec issues", "## RQ2 Volume", "### What the tools report, as written",
                "### Units per tool", "### Traceable", "## RQ3 Agreement", "### Voter groups",
                "### RQ3a: the L0-L4 deltas", "#### Spellings per identity", "### RQ3b: agreement at L4",
                "### RQ3b: per-tool rates")  # fmt: skip
    for section in (*sections, "## Files"):
        assert section in summary, section
    positions = [summary.index(s) for s in sections]
    assert positions == sorted(positions)  # the sections come in the order of the questions
    assert "| cbomkit | ✅ 11 |" in summary and "| pqcheck | – |" in summary
    assert "| tool | records | line | window | import | file | none | unread | outside code |" in summary
    assert "| tool | CBOMs | algorithm assets | records | items |" in summary  # algorithm components, not crypto assets
    # "Per language" counts the items of the matcher x level table (groups only observers are in are no items)
    per_language = summary[summary.index("**Per language**") : summary.index("**exact vs raqun")]
    assert re.search(r"^\| all \| ([\d']+) \|", per_language, flags=re.M).group(1) == exact[-1]["items"]
    # the record carries the repository's size, so the Repositories table is flat (no commit in it: no /tree link)
    assert "| [bfcbom/micro-java](https://github.com/bfcbom/micro-java) | Java | 3 | 2 | 3.0 | 1.0 |" in summary
    assert "Per repository (`units-repo-run<i>.csv`)" in summary
    meaningful = summary.index("### Meaningful")
    assert meaningful < summary.index("#### Asset type `algorithm`", meaningful) < summary.index("### Traceable")
    assert "**RQ3 Agreement**" in summary[summary.index("## Files") :]  # files by question
    assert "| cbomkit | 11 / 11 · 11 |" in summary


def test_per_language_table_skips_groups_only_observers_are_in():
    # micro-java, 2026-10-22: qScan alone reported ECDH, the voters' generic EC records were attached to its cluster;
    # matchlevels skipped it, the summary's "Per language" counted it with k = 2 (corpus-pilot-medium: 1'455 vs 1'425)
    from analysis import summary as sm, tables as tbl
    from analysis.matchers import ExactMatcher
    from analysis.normalize import normalize_cbom
    from common.tools_registry import VoterMap

    def records(tool, *names):
        comps = [{"type": "cryptographic-asset", "name": n, "cryptoProperties": {"assetType": "algorithm"}}
                 for n in names]  # fmt: skip
        return normalize_cbom({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": comps}, tool).records

    voter_of = VoterMap({}, observers={"o"})
    recs = records("a", "EC") + records("b", "EC", "AES") + records("o", "ECDH", "AES")
    groups = ExactMatcher(level="L3", voter_of=voter_of).match(recs)
    row = tbl.matchlevel_row("exact", "L3", 1, {("s", "original"): groups}, voter_of)
    g_rows, _, _ = tbl.group_rows("exact", groups, snapshot="s", variant="original", run=1, commit="c",
                                  language="Java", voter_of=voter_of)  # fmt: skip
    ecdh = next(g for g in g_rows if g["key"] == "ECDH")
    assert (ecdh["voter_groups"], ecdh["member_voter_groups"]) == (2, 0)  # two voters attached, none of its own
    as_csv = [{k: str(tbl._cell(v)) for k, v in g.items()} for g in g_rows]
    lines = sm._overlap(as_csv)
    (all_row,) = [ln for ln in lines if ln.startswith("| all |")]
    assert all_row.split("|")[2].strip() == str(row["items"]) == "2"


def test_an_oom_killed_run_is_listed_but_its_cbom_is_not_read(tmp_path, monkeypatch, old_truth):
    from typer.testing import CliRunner

    from misc.cli import analysis_cmd

    exp = _fake_experiment(tmp_path)
    meta_path = exp / SNAPSHOT / "original" / "cipherradar" / "run1.meta.json"
    meta = json.loads(meta_path.read_text())
    meta.update(oom_kills=1, peak_memory_mb=4096.0, memory_limit_mb=4096.0)
    meta_path.write_text(json.dumps(meta))  # stored ok, yet the container was OOM-killed meanwhile
    monkeypatch.setattr(analysis_cmd, "experiment_dir", lambda name: tmp_path / name)
    result = CliRunner().invoke(analysis_cmd.analysis_app, ["report", "exp-tables"])
    assert result.exit_code == 0, result.output
    out = exp / "_ANALYSIS"
    runs = {r["tool"]: r for r in _read_csv(out / "runs.csv")}
    assert runs["cipherradar"]["status"] == "oom" and runs["cbomkit"]["status"] == "ok"
    assert (runs["cbomkit"]["repo_files"], runs["cbomkit"]["repo_source_files"]) == ("3", "2")  # from the record
    assert (runs["cbomkit"]["repo_bytes"], runs["cbomkit"]["repo_source_bytes"]) == ("3145728", "1048576")
    assert {u["tool"] for u in _read_csv(out / "units-run1.csv")} == {"cbomkit"}  # its CBOM was not normalised
    summary = (out / "_SUMMARY.md").read_text(encoding="utf-8")
    assert "| cipherradar |  |  |  | 1 |  |" in summary and "Runs OOM-killed meanwhile" in summary
    assert "`cipherradar` on `bfcbom/micro-java` (original, run 1): 1 OOM kill(s) at 4.0 GiB" in summary


def test_report_is_identical_sequential_and_parallel(tmp_path, monkeypatch, old_truth):
    """The process pool (misc/cli/parallel.py) changes nothing in any table, the summary or the normalised CBOMs:
    every file written with --jobs 1 and with two spawned workers is the same byte for byte (the timestamps and the
    matchers' timings aside)."""
    from typer.testing import CliRunner

    from misc.cli import analysis_cmd

    exp = _fake_experiment(tmp_path, python=True)
    monkeypatch.setattr(analysis_cmd, "experiment_dir", lambda name: tmp_path / name)

    def files() -> dict[str, bytes]:
        out = {
            p.relative_to(exp).as_posix(): p.read_bytes()
            for p in exp.rglob("*")
            if p.is_file() and (p.parent.name == "_ANALYSIS" or p.name == "run1.norm.json")
        }
        summary = out["_ANALYSIS/_SUMMARY.md"].split(b"\n")
        timing_rows = (b"| exact matcher |", b"| RaQuN |")  # the "Matching time" table
        out["_ANALYSIS/_SUMMARY.md"] = b"\n".join(
            line for line in summary if b"summary generated" not in line and not line.startswith(timing_rows)
        )
        timing = {"matchlevels-run1.csv": {"seconds"}, "matchers-run1.csv": {"exact_seconds", "raqun_seconds"}}
        for name, columns in timing.items():
            if f"_ANALYSIS/{name}" in out:
                rows = [{k: v for k, v in r.items() if k not in columns} for r in _read_csv(exp / "_ANALYSIS" / name)]
                out[f"_ANALYSIS/{name}"] = json.dumps(rows).encode()
        info = json.loads(out["_ANALYSIS/tables-run1.json"])
        info.pop("generated_at", None)
        out["_ANALYSIS/tables-run1.json"] = json.dumps(info).encode()
        return out

    runner = CliRunner()
    result = runner.invoke(analysis_cmd.analysis_app, ["report", "exp-tables", "--jobs", "1"])
    assert result.exit_code == 0, result.output
    sequential = files()
    result = runner.invoke(analysis_cmd.analysis_app, ["report", "exp-tables", "--jobs", "2"])
    assert result.exit_code == 0, result.output
    parallel = files()
    assert len(sequential) > 20 and sequential.keys() == parallel.keys()
    assert [name for name in sequential if sequential[name] != parallel[name]] == []
    # two repositories went through the match step, four CBOMs through normalisation and validation
    groups = _read_csv(exp / "_ANALYSIS" / "groups-exact-run1.csv")
    assert {r["snapshot"] for r in groups} == {SNAPSHOT, PYTHON_SNAPSHOT}
    assert len(_read_csv(exp / "_ANALYSIS" / "schema-run1.csv")) == 4


def test_repositories_section_links_pinned_commits_folds_by_language_or_lists_sizes():
    from analysis.summary import _repositories

    sha = "868cf4ab2ca5a0a39da40e5a14dd740b203662b2"
    repos = [
        {"full_name": "jpadilla/pyjwt", "language": "Python", "commit": sha},
        {"full_name": "bfcbom/micro-go", "language": "Go", "commit": "74aa", "path": "/app/experiments/corpus/micro"},
        {"full_name": "jwtk/jjwt", "language": "Java", "commit": "0df9"},
        {"full_name": "golang-jwt/jwt", "language": "Go", "commit": "d4bd"},
        {"full_name": "example/rusty", "language": "Rust", "git_url": "https://gitlab.com/example/rusty.git"},
    ]
    lines = _repositories({"repos": repos})
    assert lines[0] == "### Repositories" and "The 5 repositories the stored runs cover" in lines[2]
    header = next(line for line in lines if line.startswith("| Java"))
    assert header == "| Java (1) | Python (1) | Go (2) | Rust (1) |"  # paper languages first, then the rest
    rows = [line for line in lines if line.startswith("| ") and not line.startswith(("| Java", "| ---"))]
    assert rows[0] == (
        "| [jwtk/jjwt](https://github.com/jwtk/jjwt/tree/0df9) "
        f"| [jpadilla/pyjwt](https://github.com/jpadilla/pyjwt/tree/{sha}) "
        "| bfcbom/micro-go (fixture) | example/rusty |"
    )
    assert rows[1] == "|  |  | [golang-jwt/jwt](https://github.com/golang-jwt/jwt/tree/d4bd) |  |"  # sorted by name
    sized = _repositories({"repos": [{**repos[0], "size_kb": 2048}, {**repos[2], "size_kb": 512}, repos[1]]})
    assert "| repository | language | size MB |" in sized
    assert "| [jwtk/jjwt](https://github.com/jwtk/jjwt/tree/0df9) | Java | 0.5 |" in sized
    assert f"| [jpadilla/pyjwt](https://github.com/jpadilla/pyjwt/tree/{sha}) | Python | 2.0 |" in sized
    assert "| bfcbom/micro-go (fixture) | Go |  |" in sized
    counted = _repositories({"repos": [
        {**repos[2], "files": 1200, "source_files": 980, "checkout_bytes": 7 * 1024**2, "source_bytes": 3 * 1024**2},
        {**repos[0], "size_kb": 2048},
    ]})  # fmt: skip
    assert "| repository | language | files | source files | MiB | source MiB |" in counted
    assert "| [jwtk/jjwt](https://github.com/jwtk/jjwt/tree/0df9) | Java | 1'200 | 980 | 7.0 | 3.0 |" in counted
    assert f"| [jpadilla/pyjwt](https://github.com/jpadilla/pyjwt/tree/{sha}) | Python |  |  |  |  |" in counted
    assert _repositories({}) == [] and _repositories({"repos": []}) == []


def test_spellings_per_identity_count_distinct_raw_values_per_tool():
    from analysis.summary import _spellings

    def row(tool, raw, mapped, status="mapped"):
        return {"tool": tool, "raw": raw, "mapped": mapped, "status": status}

    rows = [
        row("a", "sha256", "algorithm=SHA-256;family=SHA-2"),
        row("a", "SHA-256", "algorithm=SHA-256;family=SHA-2"),
        row("a", "RSA", "family=RSA"),
        row("b", "SHA256", "algorithm=SHA-256;family=SHA-2"),
        row("b", "foo", "", status="unmapped"),
        row("b", "['digest']", "functions=digest"),  # no identity: not a spelling of an algorithm
    ]
    text = "\n".join(_spellings(rows))
    assert "| a | 2 | 3 | 1.50 | SHA-256 (2: SHA-256, sha256) |" in text
    assert "| b | 1 | 1 | 1.00 | SHA-256 (1: SHA256) |" in text
    assert "`SHA-256` 3 spellings by 2 tools" in text and _spellings([]) == []


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
    # off a terminal, every step prints one line when it ends (misc/cli/progress.py)
    assert "normalise CBOMs: " in result.output and "zip: " in result.output
    names = zipfile.ZipFile(dest / "exp-tables.zip").namelist()
    assert "exp-tables/_ANALYSIS/_SUMMARY.md" in names and "exp-tables/_ANALYSIS/micro-run1.csv" in names
    assert (exp / "_ANALYSIS" / "_SUMMARY.md").exists()

    (exp / "_ANALYSIS" / "_SUMMARY.md").unlink()
    result = CliRunner().invoke(
        experiment_cmd.experiment_app, ["export", "exp-tables", "--dest", str(dest), "--no-report"]
    )
    assert result.exit_code == 0 and not (exp / "_ANALYSIS" / "_SUMMARY.md").exists()


def test_summary_markdown_from_csv_rows():
    from analysis import summary as sm

    assert sm.md_table(["a", "b"], [["x|y", 1]]) == ["| a | b |", "| --- | ---: |", "| x\\|y | 1 |"]
    # numbers get ' between thousands, in cells and through `_n`; other text stays as it is
    assert sm.md_table(["n", "x"], [[13043, "1234.5"]])[2] == "| 13'043 | 1'234.5 |"
    values = (999, "-1000", "1.4.0", "2026-10-21", None)
    assert [sm._n(v) for v in values] == ["999", "-1'000", "1.4.0", "2026-10-21", ""]
    # CycloneDX's asset types in the standard's order, then a value outside them
    types = ["unknown", "related-crypto-material", "algorithm", "certificate"]
    assert sm._asset_types(types) == ["algorithm", "certificate", "related-crypto-material", "unknown"]
    base = {"n_items": "11"}
    weak = {**base, "tool": "weak", "entry_bar": "disqualified"}
    good = {**base, "tool": "good", "entry_bar": "passed"}
    rows = [
        {**weak, "language": "java", "verdict": "fail", "strict": "0", "lenient": "2", "repo": "7",
         "recall_lenient": "0.182", "precision": "0.5", "f1": "0.267"},
        {**weak, "language": "go", "verdict": "error"},
        {**good, "language": "java", "verdict": "pass", "strict": "9", "lenient": "11", "repo": "11",
         "recall_lenient": "1.0", "precision": "1.0", "f1": "1.0"},
        {**good, "language": "go", "verdict": "unsupported"},
    ]  # fmt: skip
    lines = sm._micro(rows, [])
    assert "| tool | java | go | entry bar |" in lines  # paper languages first, then the tool's verdict
    assert lines.index("| good | ✅ 11 | – | ✅ passed |") < lines.index("| weak | ⛔ 2 | error | ⛔ disqualified |")
    verdicts = next(x for x in lines if x.startswith("**Verdicts**"))  # the hand-over into the later specs
    assert verdicts.startswith("**Verdicts**: 1 of 2 tools pass the bar, 1 is disqualified (weak). Every passed tool ")
    assert "no `tool_languages:` entry follows" in verdicts and "tools: [good]" in lines
    assert "tool_languages:" not in lines
    assert "| weak | 0 / 2 · 7 |  |" in lines
    assert "| weak | 0.18 / 0.50 / 0.27 |  |" in lines and "| good | 1.00 / 1.00 / 1.00 | – |" in lines


def test_reported_section_shows_raw_counts_and_the_repo_matrix():
    from analysis import summary as sm

    def row(repo, tool, status, lang="Java", expect="", **counts):
        r = {"repo": repo, "language": lang, "expect_crypto": expect, "tool": tool, "run": "1", "variant": "original",
             "status": status}  # fmt: skip
        if status == "ok":
            r.update(
                {
                    k: "0"
                    for k in (
                        "components",
                        "crypto_assets",
                        "located_file_assets",
                        "located_line_assets",
                        "algorithm_assets",
                        "certificate_assets",
                        "protocol_assets",
                        "material_assets",
                        "other_assets",
                    )
                }
            )
            r.update({k: str(v) for k, v in counts.items()})
        return r

    rows = [
        row(
            "o/a",
            "t1",
            "ok",
            components=40,
            crypto_assets=10,
            algorithm_assets=8,
            certificate_assets=2,
            located_file_assets=6,
            located_line_assets=5,
        ),
        row("o/b", "t1", "ok", lang="Python", expect="low", components=3, crypto_assets=0),
        row("o/a", "t2", "unsupported"),
        row("o/b", "t2", "timeout", lang="Python", expect="low"),
        row("o/a", "t1", "ok", components=99, crypto_assets=99),  # a second repetition is not counted
    ]
    rows[-1]["run"] = "2"
    lines = sm._reported(rows, 1)
    assert "| t1 | 2 | 43 | 10 | 8 | 2 | 0 | 0 | 0 | 0.60 | 0.50 | 1 | 5 |" in lines
    assert "| t2 | 0 |  |  |  |  |  |  |  |  |  |  |  |" in lines
    assert "| repo | language | expect | t1 | t2 |" in lines
    assert "| o/a | java |  | 10 | – |" in lines and "| o/b | python | low | 0 | × |" in lines
    assert "| all |  |  | 10 |  |" in lines
    assert "expect" not in "\n".join(sm._reported([r for r in rows if r["repo"] == "o/a"], 1))


def test_summary_starts_with_when_how_long_and_how_much_ram(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from misc.cli import analysis_cmd

    exp = _fake_experiment(tmp_path)
    for tool, peak in (("cbomkit", 3000.0), ("cipherradar", 900.0)):
        meta_path = exp / SNAPSHOT / "original" / tool / "run1.meta.json"
        meta = json.loads(meta_path.read_text())
        meta.update(
            peak_memory_mb=peak,
            memory_limit_mb=4096.0,
            oom_kills=0,
            received_at="2026-10-18T10:00:00Z",
            enqueued_at="2026-10-18T09:58:00Z",
            started_at="2026-10-18T09:59:30Z",
            finished_at="2026-10-18T10:00:00Z",
            bfcbom_commit="abc123def456",
        )
        meta_path.write_text(json.dumps(meta))
    session = {
        "session": "s1",
        "command": "run",
        "status": "completed",
        "started_at": "2026-10-18T08:00:00Z",
        "finished_at": "2026-10-18T10:30:00Z",
        "runner_commit": "abc123def456-dirty",
        "images": {"cbomkit": "sha256:" + "0" * 64},
        "host": {"cpus": 10, "ram_total_mb": 20480.0},
        "memory_budget_gb": 14,
        "waves": [["cbomkit"], ["cipherradar"]],
        "ram_peak_used_mb": 15360.0,
    }
    (exp / "_run-log.json").write_text(json.dumps([session]))
    monkeypatch.setattr(analysis_cmd, "experiment_dir", lambda name: tmp_path / name)
    result = CliRunner().invoke(analysis_cmd.analysis_app, ["report", "exp-tables"])
    assert result.exit_code == 0, result.output
    summary = (exp / "_ANALYSIS" / "_SUMMARY.md").read_text(encoding="utf-8")
    assert summary.index("| repositories |") < summary.index("## Contents") < summary.index("### Runs")
    assert "| ran | 2026-10-18 08:00 UTC to 2026-10-18 10:30 UTC |" in summary
    assert "| duration | 2 h 30 min in 1 runner session(s) |" in summary
    assert "| host | 20.0 GiB RAM, 10 CPUs (Docker VM) |" in summary
    assert "| memory budget | 14 GiB -> 2 wave(s) (last session) |" in summary
    assert "| peak RAM in use | 15.0 GiB of 20.0 GiB (0.75) on the VM" in summary
    assert "| peak RAM of a job | 2.9 GiB: `cbomkit` on `bfcbom/micro-java`" in summary
    assert "| queue wait | median 90 s, max 90 s before a job started |" in summary
    assert (
        "| BF-CBOM commit | `abc123def456`, `abc123def456-dirty` (`-dirty`: built from uncommitted changes) |"
        in summary
    )
    assert "| worker image ids | 1 recorded in `_run-log.json` (by content, per session) |" in summary
    header = "| tool | ok | error | timeout | oom | unsupported | median s | peak GiB | limit GiB | OOM kills |"
    assert header in summary and "| cbomkit | 1 |  |  |  |  | 2 | 2.9 | 4.0 |  |" in summary
    runs = {r["tool"]: r for r in _read_csv(exp / "_ANALYSIS" / "runs.csv")}
    assert runs["cbomkit"]["queue_sec"] == "90.0" and runs["cbomkit"]["bfcbom_commit"] == "abc123def456"


def test_run_rows_carry_usage_and_tailored(tmp_path):
    from analysis.tables import run_rows

    exp = _fake_experiment(tmp_path)
    meta_path = exp / SNAPSHOT / "original" / "cbomkit" / "run1.meta.json"
    meta = json.loads(meta_path.read_text())
    meta["usage"] = {
        "requests": 4,
        "requests_failed": 1,
        "input_tokens": 1000,
        "cached_input_tokens": 300,
        "cache_write_tokens": 0,
        "output_tokens": 50,
        "reasoning_tokens": 7,
        "models": {"m-2026": 3},
    }
    meta_path.write_text(json.dumps(meta))
    rows = {r["tool"]: r for r in run_rows(exp, tailored={"cbomkit": frozenset({"bfcbom/micro-java"})})}
    assert rows["cbomkit"]["tailored"] is True and rows["cipherradar"]["tailored"] is False
    assert (rows["cbomkit"]["requests"], rows["cbomkit"]["cached_input_tokens"]) == (4, 300)
    assert rows["cbomkit"]["models_answered"] == "m-2026" and "models_answered" not in rows["cipherradar"]


def test_error_line_skips_the_echoed_command():
    from analysis.tables import error_line

    text = (
        "cdxgen cmd: cdxgen -o /tmp/x/bom.json /tmp/x/repo\ncdxgen_failed rc=-9: Downloading gradle\n"
        'Traceback (most recent call last):\n  File "/app/w.py", line 1, in f\n    raise RuntimeError(msg)\n'
        "RuntimeError: cdxgen_failed rc=-9: Downloading gradle\n  BUILD FAILED\n"
    )
    assert error_line(text) == "RuntimeError: cdxgen_failed rc=-9: Downloading gradle"
    assert error_line("cdxgen cmd: cdxgen x\ncdxgen timeout during run") == "cdxgen timeout during run"
    assert error_line("cradar cmd: cradar scan x") is None and error_line(None) is None


def test_workaround_line_collects_the_recorded_setup_workarounds():
    from analysis.tables import workaround_line

    notes = (
        "cradar: 1 Java files with the known crash trigger added to .cradarignore\n"
        "cradar cmd: cradar --verbose scan /tmp/r\n"
        "workaround: .cradarignore a/B.java (known nil-pointer crash trigger / crashes; the tool is unchanged)\n"
    )
    expected = ".cradarignore a/B.java (known nil-pointer crash trigger / crashes; the tool is unchanged)"
    assert workaround_line(notes) == expected
    assert workaround_line("cradar cmd: cradar scan\n") is None and workaround_line(None) is None


def test_every_integrated_tool_declares_its_packaging():
    """analysis/README.md "Packaging per tool" has a row for every integrated tool (refinement: compare by items)."""
    import re

    from common.tools_registry import load_tools

    readme = (Path(__file__).resolve().parents[1] / "analysis" / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Packaging per tool", 1)[1].split("\n## ", 1)[0]
    rows = [line.split("|")[1] for line in section.splitlines() if line.startswith("| ") and "---" not in line][1:]
    declared = {re.sub(r"[^a-z0-9]", "", name.lower()) for row in rows for name in row.split(",")}
    integrated = {t for t, info in load_tools().items() if info.get("status") == "integrated"}
    assert {t for t in integrated if re.sub(r"[^a-z0-9]", "", t) not in declared} == set()


def _run(tool, snapshot, seconds, *, status="ok", variant="original", run=1, language="Java", source=None):
    return {"tool": tool, "snapshot": snapshot, "variant": variant, "run": run, "language": language,
            "status": status, "duration_sec": seconds, "repo_source_files": source}  # fmt: skip


def test_runtime_rows_count_each_repository_once_with_spread_ci_and_size():
    from analysis.tables import runtime_rows

    runs = [
        # slow: four Java repos whose time grows with their size; repo a ran twice and has a mutant
        _run("slow", "a", 100.0, source=100), _run("slow", "a", 120.0, run=2, source=100),
        _run("slow", "a", 1000.0, variant="m-1", source=100),  # a's median is 120: the mutant does not dominate
        _run("slow", "b", 200.0, source=200), _run("slow", "c", 400.0, source=400),
        _run("slow", "d", 900.0, status="timeout", source=800),  # ran, no ok time: counts in hours and timeouts only
        _run("slow", "e", 800.0, language="Python", source=800),
        _run("slow", "f", None, status="unsupported", language="Go"),  # never ran
        # fast: about the same everywhere
        _run("fast", "a", 10.0), _run("fast", "b", 11.0), _run("fast", "c", 10.0),
    ]  # fmt: skip
    rows = {(r["tool"], r["language"]): r for r in runtime_rows(runs)}
    assert set(rows) == {("fast", "all"), ("fast", "Java"), ("slow", "all"), ("slow", "Java"), ("slow", "Python")}
    slow = rows[("slow", "all")]
    assert (slow["repos"], slow["runs"], slow["timeouts"]) == (4, 7, 1)  # a, b, c, e; the unsupported run is out
    assert slow["median_sec"] == 300.0  # median of 120, 200, 400, 800
    assert slow["max_sec"] == 1000.0  # the slowest single ok run
    assert slow["median_ci_low"] <= 300.0 <= slow["median_ci_high"]
    assert slow["timeout_share"] == round(1 / 7, 3)
    assert slow["hours"] == round((100 + 120 + 1000 + 200 + 400 + 900 + 800) / 3600, 2)
    assert slow["rerun_cv"] == round(10 / 110, 2) and slow["rerun_pairs"] == 1  # a/original: 100 and 120
    assert slow["size_rho"] == 1.0 and slow["sec_per_1k_source_files"] == 1000.0
    fast = rows[("fast", "all")]
    assert fast["spread_cv"] < 0.1 < slow["spread_cv"]
    assert "size_rho" not in fast and "rerun_cv" not in fast  # no sizes, no repetitions
    assert "median_ci_low" not in rows[("slow", "Python")]  # one repository: no interval
    assert runtime_rows(runs) == runtime_rows(runs)  # the bootstrap is seeded


def test_runtime_section_lists_the_slowest_tool_first_with_its_languages():
    from analysis.summary import _rq2
    from analysis.tables import RUNTIME_COLUMNS, runtime_rows

    runs = [_run("fast", s, 10.0, source=50) for s in "abc"] + [_run("slow", s, 600.0, source=50) for s in "abc"]
    runs += [_run("slow", "p", 60.0, language="Python", source=50)]
    rows = [{c: ("" if r.get(c) is None else str(r[c])) for c in RUNTIME_COLUMNS} for r in runtime_rows(runs)]
    text = "\n".join(_rq2([], [], [], [], [], rows))
    assert "### Run time" in text
    assert text.index("| slow |") < text.index("| fast |")
    assert "| tool | Java | Python |" in text and "| slow | 600 (3) | 60 (1) |" in text
