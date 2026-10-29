"""The micro projects and their ground truth (mutation/micro.py): generated as checked in, consistent, compilable,
and scored by the rules the ground truth's header states (analysis/groundtruth.py)."""

import re
import runpy
import shutil
import subprocess

import pytest
import yaml

from analysis.groundtruth import load_ground_truth, score_records
from analysis.normalize import NormRecord
from mutation.catalog import load_catalog
from mutation.micro import FORM, LANGUAGES, MICRO_DIR, SKIPPED, generated


def test_checked_in_files_are_what_the_generator_writes():
    for path, text in generated().items():
        assert path.read_text(encoding="utf-8") == text, f"{path} drifted: run `uv run python -m mutation.micro`"


def _bound(language: str, statement: str) -> str:
    """The name an import binds (the catalog's snippets use it as `name.` or `name(`)."""
    if language == "python":
        return statement.split()[-1]
    return statement.rsplit(".", 1)[-1] if language == "java" else statement.rsplit("/", 1)[-1]


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_item_owns_its_lines_and_the_decoys_use_no_cryptography(language):
    truth = load_ground_truth()[f"micro-{language}"]
    catalog = load_catalog(language)
    assert {a.id for a in truth.assets} == {s.id for s in catalog.snippets} - SKIPPED
    assert set(truth.decoys) == {d.id for d in catalog.decoys}
    owner: dict[int, str] = {}
    for key, lines in [*((a.id, a.lines) for a in truth.assets), *truth.decoys.items()]:
        for n in lines:
            assert n not in owner, (key, n, owner.get(n))  # no line belongs to two items or decoys
            owner[n] = key
    src = (MICRO_DIR / language / truth.file).read_text(encoding="utf-8").split("\n")
    calls = yaml.safe_load((MICRO_DIR / "ground_truth.yaml").read_text())["projects"][f"micro-{language}"]["calls"]
    for a in truth.assets:
        assert calls[a.id] in a.lines and src[calls[a.id] - 1].strip(), a.id
        assert a.expected["unit"] == catalog.get(a.id).unit and a.group, a.id
    names = {_bound(language, i) for s in catalog.snippets for i in s.forms[FORM].imports}
    use = re.compile(r"\b(" + "|".join(map(re.escape, sorted(names))) + r")\s*[.(]")
    for decoy, lines in truth.decoys.items():
        assert not [src[n - 1] for n in lines if use.search(src[n - 1])], decoy


def _rec(i, group, line, *, asset_type="algorithm", file="app/crypto_demo.py"):
    return NormRecord(f"t:{i}:0", "t", i, 0, name="x", group=group, values={"asset_type": asset_type}, file=file,
                      line=line)  # fmt: skip


def test_keygen_items_take_key_records_and_decoys_count_as_false_positives():
    truth = load_ground_truth()["micro-python"]
    item = {a.id: a for a in truth.assets}
    rsa, oaep, hmac = item["rsa-2048-keygen"], item["rsa-oaep-sha256"], item["hmac-sha256"]
    records = [
        _rec(0, "RSA", rsa.lines[0], asset_type="related-crypto-material"),  # the generated key: counts for keygen
        _rec(1, "RSA", oaep.lines[0], asset_type="related-crypto-material"),  # a key in the OAEP function: does not
        _rec(2, "SHA-2", hmac.lines[0]),  # HMAC's digest: accepted, no hit
        _rec(3, "MD5", truth.decoys["comment"][0]),  # a comment naming MD5: a false positive
    ]
    score = score_records(records, truth, "t")
    assert score.hits_strict == ["rsa-2048-keygen"] and score.decoys_hit == ["comment"] and score.n_decoys == 8
    assert [f["line"] for f in score.false_sites] == [truth.decoys["comment"][0]]  # the SHA-2 record is a sub-use
    assert "hmac-sha256" not in score.hits_repo and "rsa-oaep-sha256" not in score.hits_strict


@pytest.mark.skipif(shutil.which("javac") is None, reason="no javac on PATH")
def test_micro_java_compiles(tmp_path):
    src = MICRO_DIR / "java" / load_ground_truth()["micro-java"].file
    proc = subprocess.run(["javac", "-d", str(tmp_path), str(src)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-800:]


@pytest.mark.skipif(shutil.which("go") is None, reason="no go toolchain on PATH")
def test_micro_go_builds():
    proc = subprocess.run(["go", "vet", "."], cwd=MICRO_DIR / "go", capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-800:]  # standard library only: no go.sum, no module to download


def test_micro_python_runs():
    pytest.importorskip("cryptography")
    runpy.run_path(str(MICRO_DIR / "python" / "app" / "crypto_demo.py"), run_name="__main__")


def test_entry_bar_f1_and_the_tool_verdict():
    from analysis.groundtruth import DISQUALIFIED, NOT_JUDGED, PASSED, TruthScore, entry_verdict

    def score(found: int, correct: int, reported: int) -> TruthScore:
        return TruthScore("t", "micro-x", 14, hits_lenient=[f"i{k}" for k in range(found)], sites_correct=correct,
                          sites_in_file=reported)  # fmt: skip

    six, seven = score(6, 6, 8), score(7, 7, 7)
    assert seven.bar_items == 7 and seven.passes_bar and not six.passes_bar  # the bar: half of 14
    assert seven.f1 == pytest.approx(2 * 0.5 * 1.0 / 1.5) and six.precision == 0.75
    assert score(0, 0, 0).precision is None and score(0, 0, 0).f1 == 0.0  # nothing reported: no precision, F1 0
    assert entry_verdict([six, seven]) == PASSED  # one language suffices
    assert entry_verdict([six, None]) == DISQUALIFIED  # scored and below the bar wherever scored
    assert entry_verdict([None, None]) == NOT_JUDGED  # no scored run (failed, unsupported)
