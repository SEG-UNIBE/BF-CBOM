"""RaQuN as an analysis matcher (analysis/matchers/raqun.py; needs the PyQuN package, skipped otherwise) and the
matcher comparison (analysis/matchers/compare.py, always)."""

import pytest

from analysis.matchers import ExactMatcher, MatchGroup
from analysis.matchers.compare import compare
from analysis.matchers.raqun import attributes
from analysis.normalize import NormRecord


def _rec(i, tool, name, **values):
    vals = {"asset_type": "algorithm", **values}
    via, src = ({k: "parse" for k in values}, {k: "name" for k in values})  # admitted from L3 on
    return NormRecord(f"{tool}:{i}:0", tool, i, 0, name=name, values=vals, via=via, src=src)


def test_structured_attributes_are_canonical_dimension_values():
    rec = _rec(0, "a", "AES-256-GCM", family="AES", key_size=256, mode="GCM", primitive="ae")
    rec.group = "AES"
    assert attributes(rec, "L3") == {
        "algorithm=AES",
        "group=AES",
        "family=AES",
        "key_size=256",
        "mode=GCM",
        "primitive=ae",
    }
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
    records = [
        _rec(0, "a", "AES-256-GCM", family="AES", key_size=256, mode="GCM"),
        _rec(1, "b", "aes256gcm", family="AES", key_size=256, mode="GCM"),
        _rec(2, "c", "AES", family="AES"),
        _rec(3, "a", "RSA-2048", family="RSA", key_size=2048),
        _rec(4, "b", "RSA", family="RSA", key_size=2048),
        _rec(5, "c", "SHA-256", family="SHA-2"),
        _rec(6, "b", "SHA-256", family="SHA-2"),
    ]
    for r in records:
        r.group = r.values["family"].split("-")[0]
    groups = RaqunMatcher(level="L3", voter_of={"a": "a", "b": "b", "c": "c"}).match(records)
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
    assert row["records_common"] == 7 and row["pair_jaccard"] is not None and row["b_in_one_a"] == 1.0
