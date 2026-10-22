"""RaQuN as an analysis matcher (analysis/matchers/raqun.py; needs the PyQuN package, skipped otherwise) and the
matcher comparison (analysis/matchers/compare.py, always)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from analysis.matchers import ExactMatcher, MatchGroup
from analysis.matchers.compare import compare
from analysis.matchers.raqun import attributes
from analysis.normalize import NormRecord
from analysis.tables import matchlevel_row

ROOT = Path(__file__).resolve().parents[1]


def _rec(i, tool, name, **values):
    vals = {"asset_type": "algorithm", **values}
    via, src = ({k: "parse" for k in values}, {k: "name" for k in values})  # admitted from L3 on
    return NormRecord(f"{tool}:{i}:0", tool, i, 0, name=name, values=vals, via=via, src=src)


def _matching_records() -> list[NormRecord]:
    records = [
        _rec(0, "a", "AES-256-GCM", family="AES", key_size=256, mode="GCM"),
        _rec(1, "b", "aes256gcm", family="AES", key_size=256, mode="GCM"),  # a's attribute set exactly
        _rec(2, "c", "AES", family="AES"),
        _rec(3, "a", "RSA-2048", family="RSA", key_size=2048),
        _rec(4, "b", "RSA", family="RSA", key_size=2048),
        _rec(5, "c", "SHA-256", family="SHA-2"),
        _rec(6, "b", "SHA-256", family="SHA-2"),
        _rec(7, "c", "AES-128-CBC", family="AES", key_size=128, mode="CBC"),
    ]
    for r in records:
        r.group = r.values["family"].split("-")[0]
    undefined = _rec(8, "c", "HASHING (see usage)")  # a name nobody understands: no L4 identity, no item
    undefined.resolution = "undefined"
    return [*records, undefined]


def test_structured_attributes_are_canonical_dimension_values():
    rec = _rec(0, "a", "AES-256-GCM", family="AES", key_size=256, mode="GCM", primitive="ae")
    rec.group = "AES"
    assert attributes(rec, "L3") == {
        "algorithm=AES",
        "family=AES",
        "key_size=256",
        "mode=GCM",
        "primitive=ae",
    }  # the standard's dimensions: our coarse group is no attribute
    assert attributes(_rec(1, "a", "parseCsvMap"), "L3") == {"algorithm=parsecsvmap"}
    assert attributes(rec, "L1") == {"algorithm=aes256gcm"}  # no normalised values below L2


def test_compare_partitions_by_co_membership():
    recs = [_rec(i, t, n) for i, (t, n) in enumerate([("a", "x"), ("b", "x"), ("c", "x"), ("a", "y"), ("b", "y")])]
    one = [MatchGroup("E1", "repo", "x", recs[:3]), MatchGroup("E2", "repo", "y", recs[3:])]
    other = [
        MatchGroup("R1", "repo", "x", recs[:2]),
        MatchGroup("R2", "repo", "x", recs[2:3]),
        MatchGroup("R3", "repo", "y", recs[3:]),
    ]
    row = compare(one, other, "exact", "raqun")
    assert (row["records_common"], row["groups_a"], row["groups_b"]) == (5, 2, 3)
    assert (row["pairs_a"], row["pairs_b"], row["pairs_both"], row["pair_jaccard"]) == (4, 2, 2, 0.5)
    assert (row["a_in_one_b"], row["b_in_one_a"]) == (0.5, 1.0)  # E1 splits over R1+R2; every R group is inside one E


def test_raqun_groups_structured_assets_across_tools():
    pytest.importorskip("RaQuN_Lab")
    from analysis.matchers.raqun import RaqunMatcher, available

    assert available()
    records = _matching_records()
    groups = RaqunMatcher(level="L3", voter_of={"a": "a", "b": "b", "c": "c"}).match(records)
    assert not [g for g in groups if any(r.record_id == "c:8:0" for r in g.members)]  # the exact matcher's records
    (gcm,) = [g for g in groups if any(r.record_id == "a:0:0" for r in g.members)]
    assert "b:1:0" in {r.record_id for r in gcm.members}  # identical attributes match (PyQuN < 1.0.0: b with CBC)
    by_key = {}
    for g in groups:
        by_key.setdefault(g.key, []).append(g)
    assert all(g.scope == "repo" and g.k == g.extra["n_elements"] for g in groups)
    aes = max(by_key["AES"], key=lambda g: g.k)
    assert aes.k >= 2 and {"a", "b"} <= set(aes.tools) and 0 < aes.extra["similarity"] <= 1
    rsa = max(by_key["RSA"], key=lambda g: g.k)
    assert set(rsa.tools) == {"a", "b"} and rsa.extra["similarity"] == 1.0
    sha = max(by_key["SHA-256"], key=lambda g: g.k)
    assert set(sha.tools) == {"b", "c"}
    exact = ExactMatcher(level="L3").match(records)
    row = compare(exact, groups, "exact", "raqun")
    assert row["records_common"] == 8 and row["pair_jaccard"] is not None and row["b_in_one_a"] == 1.0
    # both count items as L4 identities (matchlevels), not RaQuN's group keys at the level
    units = {u for g in groups for u in g.extra["units"]}
    assert units == {u for g in exact if g.scope == "repo" for u in g.extra["units"]}
    for lv in ("L0", "L3"):
        level_groups = RaqunMatcher(level=lv).match(records)
        assert {u for g in level_groups for u in g.extra["units"]} == units
        assert matchlevel_row("raqun", lv, 1, {"s": level_groups})["items"] == len(units)


_SCRIPT = """
import json, sys
sys.path[:0] = sys.argv[1:]
from analysis.levels import LEVELS
from analysis.matchers.raqun import RaqunMatcher
from test_raqun import _matching_records
records = _matching_records()
print(json.dumps({lv: sorted(sorted(r.record_id for r in g.members) for g in RaqunMatcher(level=lv).match(records))
                  for lv in LEVELS}))
"""


def test_raqun_groups_do_not_depend_on_the_hash_seed():
    pytest.importorskip("RaQuN_Lab")

    def groups_under(seed: str) -> dict:
        done = subprocess.run(
            [sys.executable, "-c", _SCRIPT, str(ROOT), str(ROOT / "tests")],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(done.stdout)

    first = groups_under("0")
    assert first == groups_under("1") and all(first.values())  # one process per seed: set order differs
