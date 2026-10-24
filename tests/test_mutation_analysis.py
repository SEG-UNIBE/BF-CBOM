"""RQ4 detection (analysis/mutation.py) on synthetic records, the rates, and the report on a micro-java mutant."""

import copy
import csv
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from analysis import mutation as mu, summary as sm
from analysis.normalize.record import NormRecord
from analysis.score import spearman
from common.experiment import MutationSpec, RepoSpec, ensure_mirror, publish_fixture, repo_dir_name, save_mutants
from mutation.mutants import ensure_mutants

FIXTURES = Path(__file__).parent / "fixtures" / "pilot"
MICRO_JAVA = Path(__file__).resolve().parents[1] / "experiments" / "corpus" / "micro" / "java"

MUTANT = {
    "id": "ins-sha-256-direct-abcdef",
    "snippet": "sha-256",
    "style": "direct",
    "file": "a/App.java",
    "call_line": 20,
    "spans": [{"kind": "snippet", "start": 18, "end": 24}],
    "shifts": [[17, 7]],
    "expected": {"unit": "SHA-256", "family": "SHA-2"},
    "also": [],
}


def _rec(unit: str, file: str | None, line: int | None, tool: str = "t", **values) -> NormRecord:
    fam = {"SHA-256": "SHA-2", "SHA-2": "SHA-2", "MD5": "MD5", "RC4": "RC4", "AES": "AES", "HMAC": "HMAC"}
    group = {"SHA-2": "SHA-2", "MD5": "MD5", "RC4": "CIPHER-other", "AES": "AES", "HMAC": "HMAC"}
    family = fam.get(unit, unit)
    vals = {"family": family, **({"algorithm": unit} if unit != family else {}), **values}
    return NormRecord(record_id=f"{tool}:{unit}:{line}", tool=tool, component_index=0, occurrence_index=0,
                      name=unit, group=group.get(family), values=vals, file=file, line=line)  # fmt: skip


ORIGINAL = [_rec("SHA-256", "a/App.java", 30), _rec("MD5", "b/B.java", 5)]


def test_a_new_record_at_the_call_line_is_detected_and_a_moved_one_is_not():
    moved = _rec("SHA-256", "a/App.java", 37)  # the original's line 30, after the 7 inserted lines
    out = mu.evaluate(MUTANT, {1: [moved, _rec("SHA-256", "a/App.java", 20), ORIGINAL[1]]}, {1: ORIGINAL})
    assert out["found_site"] and out["found_span"] and out["found_file"] and out["found_group"]
    assert out["masked"] and out["found_repo"] is None  # the original has SHA-256 already: repo level undecided
    assert (out["extra_units"], out["lost_units"], out["extra_sites"], out["lost_sites"]) == ([], [], 0, 0)
    assert out["new_records"] == 1
    missed = mu.evaluate(MUTANT, {1: [moved, ORIGINAL[1]]}, {1: ORIGINAL})
    assert missed["found_site"] is False and missed["found_span"] is False and missed["found_file"] is False


def test_a_record_without_identity_is_matched_by_name_so_a_moved_marker_is_not_new():
    # CipherRadar's library-import markers carry no algorithm; on jjwt 1'700 of them counted as new on every mutant
    def marker(line):
        return NormRecord(record_id=f"t:lib:{line}", tool="t", component_index=0, occurrence_index=0, name="jca-import",
                          values={"asset_type": "library"}, file="a/App.java", line=line)  # fmt: skip

    original = [*ORIGINAL, marker(3)]
    moved = [_rec("SHA-256", "a/App.java", 37), ORIGINAL[1], marker(3)]  # line 3 is above the insertion: unmoved
    out = mu.evaluate(MUTANT, {1: [*moved, _rec("SHA-256", "a/App.java", 20)]}, {1: original})
    assert out["new_records"] == 1  # the planted call only; the marker is the original's
    appeared = mu.evaluate(MUTANT, {1: [*moved, marker(31)]}, {1: original})
    assert appeared["new_records"] == 1 and appeared["found_site"] is False  # a new marker counts, finds nothing


def test_span_site_and_group_are_told_apart():
    on_span = mu.evaluate(MUTANT, {1: [_rec("SHA-256", "a/App.java", 24)]}, {1: [ORIGINAL[1]]})
    assert on_span["found_span"] and not on_span["found_site"] and on_span["found_repo"]  # new to the repository
    generic = mu.evaluate(MUTANT, {1: [_rec("SHA-2", "a/App.java", 21)]}, {1: [ORIGINAL[1]]})
    assert not generic["found_site"] and generic["found_group"] and not generic["found_repo"]
    elsewhere = mu.evaluate(MUTANT, {1: [_rec("SHA-256", "c/C.java", 20)]}, {1: [ORIGINAL[1]]})
    assert elsewhere["found_repo"] and not elsewhere["found_file"]
    unlocated = mu.evaluate(MUTANT, {1: [_rec("SHA-256", None, None)]}, {1: [ORIGINAL[1]]})
    assert unlocated["found_repo"] and not unlocated["found_site"]  # a location-less tool: judged at repo level


DECOY = {
    "id": "ins-comment-direct-abcdef",
    "snippet": "comment",
    "style": "direct",
    "file": "a/App.java",
    "call_line": 20,
    "spans": [{"kind": "snippet", "start": 20, "end": 22}],
    "shifts": [[19, 3]],
    "expected": {},
    "also": [],
    "decoy": True,
}


def test_a_decoy_is_a_false_positive_only_when_a_new_crypto_record_lands_on_it():
    moved = _rec("SHA-256", "a/App.java", 33)  # the original's line 30 after the three inserted lines
    quiet = mu.evaluate(DECOY, {1: [moved]}, {1: [_rec("SHA-256", "a/App.java", 30)]})
    assert quiet["false_positive"] is False and quiet["found_site"] is None and quiet["unit"] is None
    assert quiet["found_repo"] is None and quiet["key_size"] is None and not quiet["masked"]
    hit = mu.evaluate(DECOY, {1: [_rec("MD5", "a/App.java", 21)]}, {1: []})
    assert hit["false_positive"] is True and hit["extra_units"] == ["MD5"]
    assert (
        mu.evaluate(DECOY, {1: [_rec("MD5", "a/App.java", 21, asset_type="library")]}, {1: []})["false_positive"]
        is False
    )
    assert mu.evaluate(DECOY, {1: [_rec("MD5", "a/App.java", 40)]}, {1: []})["false_positive"] is False  # elsewhere
    assert mu.evaluate(MUTANT, {1: []}, {1: []})["false_positive"] is None  # a planted call is never one


def test_scores_pool_calls_and_decoys_and_the_bridge_correlates_them_with_confirmation():
    def row(repo, kind, style, tool, **v):
        return {"repo": repo, "language": "java", "tool": tool, "voter_group": tool, "role": "voter",
                "status": "ok", "kind": kind, "style": style, **v}  # fmt: skip

    rows = []
    for tool, found, hits in (("good", (1, 1, 1, 1), (0, 0, 0, 0)), ("noisy", (1, 1, 1, 0), (1, 1, 0, 0)),
                              ("blind", (0, 0, 0, 0), (0, 0, 0, 0))):  # fmt: skip
        for i, f in enumerate(found):
            style = "direct" if i % 2 else "indirect"
            rows.append(row(f"r{i}", "call", style, tool, found_site=bool(f), found_group=bool(f)))
        for i, h in enumerate(hits):
            rows.append(row(f"r{i}", "decoy", "direct", tool, false_positive=bool(h)))
    scores = {(s["tool"], s["style"]): s for s in mu.score_rows(rows, samples=20)}
    good, noisy, blind = scores[("good", "all")], scores[("noisy", "all")], scores[("blind", "all")]
    assert (good["calls"], good["found_site"], good["decoys"], good["decoys_hit"]) == (4, 4, 4, 0)
    assert (good["recall_site"], good["precision"], good["f1"]) == (1.0, 1.0, 1.0)
    assert (noisy["recall_site"], noisy["precision"]) == (0.75, 0.6)
    assert noisy["f1"] == pytest.approx(2 * 0.6 * 0.75 / 1.35, abs=1e-4)
    assert blind["recall_site"] == 0.0 and blind["precision"] is None and blind["f1"] == 0.0
    assert scores[("good", "direct")]["decoys"] == 4  # the decoys are style-less: they count for every style
    assert not good["supported"] and good["f1_low"] is None  # 4 repositories, 4 calls: below the support rule
    without = mu.score_rows([r for r in rows if r["kind"] == "call"], samples=20)
    assert all(s["precision"] is None and s["f1"] is None for s in without)  # no decoys: recall only
    bridge = mu.bridge_rows(
        {("good", "java"): 0.9, ("noisy", "java"): 0.7, ("blind", "java"): 0.2}, list(scores.values())
    )
    summary = {r["language"]: r for r in bridge if r["tool"] == "all"}
    assert (summary["java"]["n"], summary["java"]["spearman"], summary["java"]["kendall_tau"]) == (3, 1.0, 1.0)
    assert summary["all"]["n"] == 3 and len([r for r in bridge if r["tool"] != "all"]) == 6
    assert spearman({"a": 1, "b": 2, "c": 3}, {"a": 3, "b": 2, "c": 1}) == -1.0
    marked = mu.score_rows(rows, samples=0, tailored=frozenset({"good"}))
    assert {s["tool"] for s in marked if s["tailored"]} == {"good"}
    table = "\n".join(sm._mutation_scores(marked, {}))
    assert "| java | good\\* |" in table and "| java | noisy |" in table and sm.TAILORED_CATALOG_NOTE in table
    assert spearman({"a": 1, "b": 1, "c": 3}, {"a": 1, "b": 2, "c": 3}) == pytest.approx(0.866, abs=1e-3)  # a tie
    assert spearman({"a": 1, "b": 2}, {"a": 1, "b": 2}) is None


def test_parameters_are_judged_on_the_inserted_lines():
    mutant = {**MUTANT, "expected": {"unit": "AES", "family": "AES", "key_size": 256, "mode": "GCM"}}
    out = mu.evaluate(mutant, {1: [_rec("AES", "a/App.java", 22, key_size=128, mode="GCM")]}, {1: []})
    assert out["found_site"] and out["key_size"] is False and out["mode"] is True and out["padding"] is None
    bare = mu.evaluate(mutant, {1: [_rec("AES", "a/App.java", 22)]}, {1: []})
    assert bare["key_size"] is None  # no value reported: not judged


def test_majority_over_runs_and_other_changes():
    hit = _rec("SHA-256", "a/App.java", 20)
    runs = {1: [hit], 2: [], 3: []}
    assert mu.evaluate(MUTANT, runs, {1: []})["found_site"] is False  # 1 of 3 runs
    runs = {1: [hit], 2: [hit], 3: [_rec("RC4", "d/D.java", 3)]}
    out = mu.evaluate(MUTANT, runs, {1: [ORIGINAL[1]], 2: [ORIGINAL[1]]})
    assert out["found_site"] is True  # 2 of 3
    assert out["lost_units"] == ["MD5"] and out["extra_units"] == []  # RC4 in one run of three is noise
    noisy = mu.evaluate(MUTANT, {1: [hit, _rec("RC4", "d/D.java", 3)]}, {1: [ORIGINAL[1]]})
    assert noisy["extra_units"] == ["RC4"] and noisy["extra_sites"] == 1 and noisy["lost_units"] == ["MD5"]
    hmac = {**MUTANT, "expected": {"unit": "HMAC", "family": "HMAC", "digest": "SHA-256"}}
    implied = mu.evaluate(hmac, {1: [_rec("HMAC", "a/App.java", 20), _rec("SHA-256", "a/App.java", 20)]}, {1: []})
    assert implied["extra_units"] == []  # HMAC-SHA256 implies SHA-256 (refinement 2)


def test_rates_are_means_of_per_repository_rates():
    rows = [
        {"repo": "a/x", "tool": "t", "voter_group": "t", "role": "voter", "language": "Java", "style": "direct",
         "status": "ok", "found_site": True},
        {"repo": "a/x", "tool": "t", "voter_group": "t", "role": "voter", "language": "Java", "style": "wrapper",
         "status": "ok", "found_site": False},
        {"repo": "b/y", "tool": "t", "voter_group": "t", "role": "voter", "language": "Java", "style": "direct",
         "status": "ok", "found_site": True},
        {"repo": "b/y", "tool": "t", "voter_group": "t", "role": "voter", "language": "Java", "style": "direct",
         "status": "error"},
    ]  # fmt: skip
    rates = {(r["style"], r["dimension"]): r for r in mu.rate_rows(rows)}
    allr = rates[("all", "found_site")]
    assert (allr["repos"], allr["mutants"], allr["detected"]) == (2, 3, 2)
    assert allr["rate"] == 0.75 and allr["pooled"] == round(2 / 3, 4) and allr["supported"] is False
    assert allr["ci_low"] is None and rates[("direct", "found_site")]["rate"] == 1.0


def _shifted(cbom: dict, path: str, shifts: list) -> dict:
    out = copy.deepcopy(cbom)
    for c in out.get("components") or []:
        for occ in (c.get("evidence") or {}).get("occurrences") or []:
            if occ.get("location") == path and occ.get("line"):
                occ["line"] += sum(n for after, n in shifts if occ["line"] > after)
    return out


def test_report_judges_mutants_and_keeps_them_out_of_the_agreement_tables(tmp_path, monkeypatch):
    from misc.cli import analysis_cmd

    mirror = tmp_path / "mirror"
    repo = RepoSpec(full_name="bfcbom/micro-java", path=str(MICRO_JAVA), language="Java")
    commit = publish_fixture(repo, mirror_dir=mirror)
    ensure_mirror(repo, commit, mirror_dir=mirror)
    params = MutationSpec(per_repo=1, styles=["direct"], snippets=["sha-1"])
    manifest: dict = {}
    mid, did = ensure_mutants(repo, commit, params, manifest, mirror_dir=mirror, log=lambda m: None)
    m, md = manifest["repos"][repo.full_name]["mutants"]  # the planted call, then the planted decoy
    assert not m["decoy"] and md["decoy"] and md["expected"] == {}
    exp = tmp_path / "e4"
    exp.mkdir()
    (exp / "_experiment.yaml").write_text(
        "name: e4\ntools: [cbomkit, cbomkit-jar]\nrepos:\n  - full_name: bfcbom/micro-java\n    language: Java\n"
        "mutation: {per_repo: 1, styles: [direct], snippets: [sha-1]}\n"
    )
    save_mutants(exp, manifest)
    original = json.loads((FIXTURES / "cbomkit_micro-java.json").read_text())
    shifted = _shifted(original, m["file"], m["shifts"])
    found = copy.deepcopy(shifted)
    found["components"].append({
        "type": "cryptographic-asset", "bom-ref": "sha1-new", "name": "SHA-1",
        "evidence": {"occurrences": [{"location": m["file"], "line": m["call_line"], "offset": 30}]},
        "cryptoProperties": {"assetType": "algorithm", "algorithmProperties": {"primitive": "hash"}},
    })  # fmt: skip
    quiet = _shifted(original, md["file"], md["shifts"])  # the decoy mutant: the original's findings, moved
    fooled = copy.deepcopy(quiet)
    fooled["components"].append({
        "type": "cryptographic-asset", "bom-ref": "md5-decoy", "name": "MD5",
        "evidence": {"occurrences": [{"location": md["file"], "line": md["call_line"], "offset": 3}]},
        "cryptoProperties": {"assetType": "algorithm", "algorithmProperties": {"primitive": "hash"}},
    })  # fmt: skip
    cboms = {
        ("original", "cbomkit"): original, (mid, "cbomkit"): found, (did, "cbomkit"): quiet,
        ("original", "cbomkit-jar"): original, (mid, "cbomkit-jar"): shifted, (did, "cbomkit-jar"): fooled,
    }  # fmt: skip
    commits = {mid: m["commit"], did: md["commit"]}
    snapshot = repo_dir_name(repo.full_name, commit)
    for (variant, tool), cbom in cboms.items():
        d = exp / snapshot / variant / tool
        d.mkdir(parents=True)
        (d / "run1.json").write_text(json.dumps(cbom))
        meta = {"worker": tool, "run_index": 1, "variant": variant, "status": "ok", "base_commit": commit,
                "variant_commit": commits.get(variant, commit), "duration_sec": 1.0}  # fmt: skip
        (d / "run1.meta.json").write_text(json.dumps(meta))
    monkeypatch.setattr(analysis_cmd, "experiment_dir", lambda name: tmp_path / name)
    result = CliRunner().invoke(analysis_cmd.analysis_app, ["report", "e4"])
    assert result.exit_code == 0, result.output
    out = exp / "_ANALYSIS"

    def rows(name):
        with (out / name).open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))

    judged = {r["tool"]: r for r in rows("mutation.csv") if r["variant"] == mid}
    assert judged["cbomkit"]["found_site"] == "true" and judged["cbomkit"]["found_repo"] == "true"
    assert judged["cbomkit-jar"]["found_site"] == "false" and judged["cbomkit-jar"]["found_repo"] == "false"
    assert judged["cbomkit"]["extra_units"] == "" and judged["cbomkit"]["lost_sites"] == "0"
    assert judged["cbomkit"]["kind"] == "call" and judged["cbomkit"]["false_positive"] == ""
    decoy = {r["tool"]: r for r in rows("mutation.csv") if r["variant"] == did}
    assert decoy["cbomkit"]["kind"] == "decoy" and decoy["cbomkit"]["false_positive"] == "false"
    assert decoy["cbomkit-jar"]["false_positive"] == "true" and decoy["cbomkit-jar"]["found_site"] == ""
    scores = {r["tool"]: r for r in rows("mutation-scores.csv") if r["style"] == "all"}
    assert (scores["cbomkit"]["calls"], scores["cbomkit"]["found_site"], scores["cbomkit"]["decoys"]) == ("1", "1", "1")
    assert (scores["cbomkit"]["precision"], scores["cbomkit"]["f1"]) == ("1.0", "1.0")
    assert (scores["cbomkit-jar"]["decoys_hit"], scores["cbomkit-jar"]["precision"], scores["cbomkit-jar"]["f1"]) == (
        "1",
        "0.0",
        "0.0",
    )
    bridge = {(r["language"], r["tool"]): r for r in rows("bridge-run1.csv")}
    assert ("Java", "cbomkit") in bridge and bridge[("Java", "all")]["n"] == "2"  # two cells: no coefficient
    assert {r["variant"] for r in rows("records-run1.csv")} == {"original"}  # the agreement tables: originals only
    assert {r["variant"] for r in rows("items-run1.csv")} == {"original"}
    assert [(r["variant"], r["kind"]) for r in rows("mutants.csv")] == [(mid, "call"), (did, "decoy")]
    assert len({r["variant"] for r in rows("runs.csv")}) == 3  # runs.csv lists every stored run
    assert (exp / snapshot / mid / "cbomkit" / "run1.norm.json").exists()  # mutants are normalised too
    summary = (out / "_SUMMARY.md").read_text()
    assert "## RQ4 Effectiveness" in summary and "```diff" in summary and f"`{mid}`" in summary
    assert "### Recall, precision and F1 on the planted items" in summary and "the bridge to RQ3" in summary


def test_not_judged_counts_the_planted_calls_by_status():
    mutant = {"repo": "o/a", "language": "java", "kind": "call", "style": "direct", "variant": "m-1", "file": "A.java",
              "call_line": 3, "host": "run", "patch": ""}  # fmt: skip
    statuses = (("a", "ok"), ("b", "unsupported"), ("c", "unsupported"), ("d", "original error"))
    rows = [{"tool": t, "language": "java", "kind": "call", "style": "direct", "role": "voter", "status": st}
            for t, st in statuses]  # fmt: skip
    text = "\n".join(sm._mutation([mutant], rows, []))
    assert "Not judged (3 planted call × tool" in text
    assert "2 unsupported (the language is outside the tool’s); 1 original error." in text
