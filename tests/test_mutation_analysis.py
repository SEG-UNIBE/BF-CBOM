"""E4 detection (analysis/mutation.py) on synthetic records, the rates, and the report on a micro-java mutant."""

import copy
import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from analysis import mutation as mu
from analysis.normalize.record import NormRecord
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


def test_span_site_and_group_are_told_apart():
    on_span = mu.evaluate(MUTANT, {1: [_rec("SHA-256", "a/App.java", 24)]}, {1: [ORIGINAL[1]]})
    assert on_span["found_span"] and not on_span["found_site"] and on_span["found_repo"]  # new to the repository
    generic = mu.evaluate(MUTANT, {1: [_rec("SHA-2", "a/App.java", 21)]}, {1: [ORIGINAL[1]]})
    assert not generic["found_site"] and generic["found_group"] and not generic["found_repo"]
    elsewhere = mu.evaluate(MUTANT, {1: [_rec("SHA-256", "c/C.java", 20)]}, {1: [ORIGINAL[1]]})
    assert elsewhere["found_repo"] and not elsewhere["found_file"]
    unlocated = mu.evaluate(MUTANT, {1: [_rec("SHA-256", None, None)]}, {1: [ORIGINAL[1]]})
    assert unlocated["found_repo"] and not unlocated["found_site"]  # a location-less tool: judged at repo level


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
    (mid,) = ensure_mutants(repo, commit, params, manifest, mirror_dir=mirror, log=lambda m: None)
    m = manifest["repos"][repo.full_name]["mutants"][0]
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
    cboms = {("original", "cbomkit"): original, (mid, "cbomkit"): found,
             ("original", "cbomkit-jar"): original, (mid, "cbomkit-jar"): shifted}  # fmt: skip
    snapshot = repo_dir_name(repo.full_name, commit)
    for (variant, tool), cbom in cboms.items():
        d = exp / snapshot / variant / tool
        d.mkdir(parents=True)
        (d / "run1.json").write_text(json.dumps(cbom))
        meta = {"worker": tool, "run_index": 1, "variant": variant, "status": "ok", "base_commit": commit,
                "variant_commit": m["commit"] if variant == mid else commit, "duration_sec": 1.0}  # fmt: skip
        (d / "run1.meta.json").write_text(json.dumps(meta))
    monkeypatch.setattr(analysis_cmd, "experiment_dir", lambda name: tmp_path / name)
    result = CliRunner().invoke(analysis_cmd.analysis_app, ["report", "e4"])
    assert result.exit_code == 0, result.output
    out = exp / "_ANALYSIS"

    def rows(name):
        with (out / name).open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))

    judged = {r["tool"]: r for r in rows("mutation.csv")}
    assert judged["cbomkit"]["found_site"] == "true" and judged["cbomkit"]["found_repo"] == "true"
    assert judged["cbomkit-jar"]["found_site"] == "false" and judged["cbomkit-jar"]["found_repo"] == "false"
    assert judged["cbomkit"]["extra_units"] == "" and judged["cbomkit"]["lost_sites"] == "0"
    assert {r["variant"] for r in rows("records-run1.csv")} == {"original"}  # E3 tables: originals only
    assert {r["variant"] for r in rows("items-run1.csv")} == {"original"}
    assert [r["variant"] for r in rows("mutants.csv")] == [mid]
    assert len({r["variant"] for r in rows("runs.csv")}) == 2  # runs.csv lists every stored run
    assert (exp / snapshot / mid / "cbomkit" / "run1.norm.json").exists()  # mutants are normalised too
    summary = (out / "_SUMMARY.md").read_text()
    assert "## E4: insertion mutants" in summary and "```diff" in summary and f"`{mid}`" in summary
