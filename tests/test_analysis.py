"""Tests for the analysis package: micro ground truth, CSV tables, the report and the summary.

The CBOM fixtures under tests/fixtures/pilot/ are real tool outputs from the pilot of 2026-10-08
on the micro projects (CBOMkit action jar, pqcheck 0.1.0, CipherRadar efa5d9d).
"""

import json
from pathlib import Path

import pytest

from analysis.groundtruth import load_ground_truth, project_for_repo

FIXTURES = Path(__file__).parent / "fixtures" / "pilot"


def _cbom(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----- ground truth -----


def test_ground_truth_loads_and_maps_repo_names():
    truth = load_ground_truth()
    assert set(truth) == {"micro-java", "micro-python", "micro-go", "micro-javascript", "micro-c"}
    for p in truth.values():
        assert len(p.assets) == 11
    assert project_for_repo("bfcbom/micro-java").file == "src/main/java/app/CryptoDemo.java"
    assert project_for_repo("x/other") is None


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
    """Call-site numbers as the pilot found them (CBOMkit micro-python: 6/11 in the pilot counted a key-material
    asset) plus the repo level, which is at least the call-site level (assets sharing a family are found together)."""
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


# ----- CSV tables -----

SNAPSHOT = "bfcbom__micro-java@0123456789ab"


def _fake_experiment(root: Path) -> Path:
    """Tiny stored experiment: two ok runs on micro-java plus one unsupported run without a CBOM."""
    exp = root / "exp-tables"
    exp.mkdir()
    (exp / "_experiment.yaml").write_text(
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
    assert all(g["level"] == "L3" and g["parent"] == "" for g in repo) and all(g["parent"] in group_ids for g in sites)
    for g in groups:
        assert int(g["n_records"]) == sum(m["group_id"] == g["group_id"] for m in members)
    # CBOMkit and CipherRadar agree on micro-java: most repo groups have both voters, most sites too
    assert sum(g["k"] == "2" for g in repo) >= 8 and sum(g["k"] == "2" for g in sites) >= 10
    dims = {a["dimension"] for a in agreement}
    assert dims == {"key_size", "mode", "padding", "curve", "digest", "location"}
    info = json.loads((out / "tables-run1.json").read_text())
    assert info["settings"]["level"] == "L3" and info["settings"]["window"] == 2


def test_report_command_writes_every_table_and_the_summary(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from analysis import levels as lv
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
    assert list(_read_csv(out / "coverage.csv")[0]) == COVERAGE_COLUMNS
    assert (out / "conformance.csv").read_text(encoding="utf-8").startswith(",".join(CONFORMANCE_COLUMNS))
    algos = _read_csv(out / "algorithms-run1.csv")
    assert list(algos[0]) == lv.ALGORITHM_COLUMNS and all(int(a["k_L0"]) <= int(a["k_L4"]) for a in algos)
    levels = _read_csv(out / "matchlevels-run1.csv")
    assert [r["level"] for r in levels] == list(lv.LEVELS) and all(r["matcher"] == "exact" for r in levels)
    assert int(levels[0]["items"]) == int(levels[-1]["items"])  # the unit is fixed across levels
    k2plus = [int(r["k2"]) + int(r["k3"]) + int(r["k4plus"]) for r in levels]
    assert k2plus == sorted(k2plus)  # cumulative: agreement never drops at a higher level
    assert {a["tool"] for a in algos} == {"cbomkit", "cipherradar"} and algos[0]["language"] == "Java"
    info = json.loads((out / "tables-run1.json").read_text())
    assert info["rows"]["algorithms-run1.csv"] == len(algos) and info["vocabulary"]["algorithms"]
    summary = (out / "_SUMMARY.md").read_text(encoding="utf-8")
    assert summary.startswith("# exp-tables: analysis summary (run 1)")
    sections = ("## Runs", "## What the tools report", "## Micro projects", "## RQ1", "## Agreement ladder",
                "## Matching (RQ3)", "## Scores")  # fmt: skip
    for section in (*sections, "## Files"):
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
    assert "| t1 | 2 | 43 | 10 | 8 | 2 | 0 | 0 | 0 | 60% | 50% | 1 | 5 |" in lines
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
    assert summary.index("## Experiment") < summary.index("## Runs")
    assert "| ran | 2026-10-18 08:00 UTC to 2026-10-18 10:30 UTC |" in summary
    assert "| duration | 2 h 30 min in 1 runner session(s) |" in summary
    assert "| host | 20.0 GiB RAM, 10 CPUs (Docker VM) |" in summary
    assert "| memory budget | 14 GiB -> 2 wave(s) (last session) |" in summary
    assert "| peak RAM in use | 15.0 GiB of 20.0 GiB (75%) on the VM" in summary
    assert "| peak RAM of a job | 2.9 GiB: `cbomkit` on `bfcbom/micro-java`" in summary
    assert "| queue wait | median 90 s, max 90 s before a job started |" in summary
    assert (
        "| BF-CBOM commit | `abc123def456`, `abc123def456-dirty` (`-dirty`: built from uncommitted changes) |"
        in summary
    )
    assert "| worker image ids | 1 recorded in `_run-log.json` (by content, per session) |" in summary
    assert "| tool | ok | error | timeout | unsupported | median s | peak GiB | limit GiB | OOM kills |" in summary
    assert "| cbomkit | 1 |  |  |  | 2 | 2.9 | 4.0 |  |" in summary
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
        "cryptoscan cmd: cryptoscan scan /tmp/r\n"
        "cryptoscan panics on a/b/bad.go\n"
        "workaround: cryptoscan --exclude a/b/bad.go (Go panic in patterns.MatchWithContext on these files)\n"
    )
    assert (
        workaround_line(notes)
        == "cryptoscan --exclude a/b/bad.go (Go panic in patterns.MatchWithContext on these files)"
    )
    assert workaround_line("cradar cmd: cradar scan\n") is None and workaround_line(None) is None
