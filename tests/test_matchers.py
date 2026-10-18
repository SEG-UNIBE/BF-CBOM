"""The exact record matcher (analysis/matchers/exact.py) and the per-dimension agreement of a group."""

import json
from pathlib import Path

from analysis.levels import LEVELS
from analysis.matchers import ExactMatcher, MatchGroup, agreement
from analysis.normalize import NormRecord, normalize_cbom

FIXTURES = Path(__file__).parent / "fixtures" / "pilot"


def _rec(i, tool, name, *, file=None, line=None, line_end=None, **values):
    """A normalised record as the normaliser would build it: the family of a known name is set (so the L4 identity
    is canonical), parameter values come from the parsed name."""
    families = {"aes": "AES", "rsa": "RSA", "hmac": "HMAC", "sha": "SHA-2"}
    vals = {"asset_type": "algorithm", **values}
    head = name.lower().replace("-", "").replace("_", "")
    for prefix, family in families.items():
        if head.startswith(prefix) and "family" not in vals:
            vals["family"] = family
            if family == "SHA-2":
                vals["algorithm"] = "SHA-256"
    via = {k: "parse" for k in values}
    src = {k: "name" for k in values}
    return NormRecord(
        f"{tool}:{i}:0", tool, i, 0, name=name, values=vals, via=via, src=src, file=file, line=line, line_end=line_end
    )


def test_repo_groups_by_identity_and_site_groups_by_file_and_window():
    records = [
        _rec(0, "a", "AES-256-GCM", file="x.py", line=10, key_size=256, mode="GCM"),
        _rec(1, "b", "aes", file="x.py", line=12, key_size=128),  # within the window: same site
        _rec(2, "c", "AES", file="x.py", line=15),  # 3 lines after b: a second site
        _rec(3, "d", "AES"),  # no location: repo level only
        _rec(4, "a", "AES", file="y.py", line=1),  # other file
        _rec(5, "a", "SHA-256", file="x.py", line=10),  # other algorithm at the same place
        _rec(6, "b", "parseCsvMap", file="x.py", line=10),  # unknown name: only its own folded key
    ]
    groups = ExactMatcher(level="L3", window=2, voter_of={"a": "g1", "b": "g1", "c": "g2", "d": "g3"}).match(records)
    repo = {g.key: g for g in groups if g.scope == "repo"}
    assert set(repo) == {"AES", "SHA-256", "parsecsvmap"}
    assert all(not g.matched for g in repo.values())  # every member's own identity is the group's
    aes = repo["AES"]
    assert aes.tools == ["a", "b", "c", "d"] and aes.k == 4 and aes.voters({"a": "g1", "b": "g1"}) == ["c", "d", "g1"]
    assert (aes.extra["n_records"], aes.extra["n_sites"]) == (5, 3)
    assert (aes.extra["located_voters"], aes.extra["colocated_voters"]) == (2, 1)  # a+b share a voter: 1 voter
    sites = [g for g in groups if g.scope == "site" and g.key == "AES"]
    spans = sorted((g.extra["file"], g.extra["line_min"], g.extra["line_max"], g.k) for g in sites)
    assert spans == [("x.py", 10, 12, 2), ("x.py", 15, 15, 1), ("y.py", 1, 1, 1)]
    assert all(g.extra["parent"] == aes.group_id and g.group_id.startswith(aes.group_id + ".") for g in sites)


def test_windows_extend_the_chain_and_closure_adds_implied_members():
    records = [
        _rec(0, "a", "RSA", file="x.py", line=5, line_end=9),  # a 5-line window
        _rec(1, "b", "RSA", file="x.py", line=11),  # 2 after the window's end: joins
        _rec(2, "c", "RSA", file="x.py", line=14),  # 3 after: new site
        _rec(3, "a", "HmacSHA256", file="x.py", line=20, digest="SHA-256"),
    ]
    plain = ExactMatcher(level="L3").match(records)  # L3 parses the names; L4 would need the values set
    assert {g.key for g in plain if g.scope == "repo"} == {"RSA", "HMAC"}
    rsa_sites = [(g.extra["line_min"], g.extra["line_max"]) for g in plain if g.scope == "site" and g.key == "RSA"]
    assert rsa_sites == [(5, 11), (14, 14)]
    closed = ExactMatcher(level="L3", closure=True).match(records)
    sha = next(g for g in closed if g.scope == "repo" and g.key == "SHA-256")
    assert [r.record_id for r in sha.members] == ["a:3:0"] and sha.implied == frozenset({"a:3:0"})


def test_agreement_per_dimension_counts_voters_and_the_majority_value():
    members = [
        _rec(0, "a", "AES", key_size=256, mode="GCM", file="x.py", line=1),
        _rec(1, "b", "AES", key_size=256, mode="CBC"),
        _rec(2, "c", "AES", key_size=128),
        _rec(3, "d", "AES"),
    ]
    group = MatchGroup("E1", "repo", "AES", members, {"located_voters": 1, "colocated_voters": 1})
    rows = {r["dimension"]: r for r in agreement(group, voter_of={"a": "g1", "b": "g1"})}
    assert (rows["key_size"]["reporting"], rows["key_size"]["agreeing"], rows["key_size"]["majority"]) == (2, 1, "128")
    assert rows["key_size"]["values"] == ["128", "256"]  # g1 says 256, c says 128: a tie, the smaller value wins
    assert (rows["mode"]["reporting"], rows["mode"]["agreeing"], rows["mode"]["values"]) == (1, 1, ["CBC", "GCM"])
    assert rows["curve"] == {"dimension": "curve", "reporting": 0, "agreeing": 0, "majority": None, "values": []}
    assert (rows["location"]["reporting"], rows["location"]["agreeing"]) == (1, 1)
    assert "location" not in {r["dimension"] for r in agreement(MatchGroup("E1.001", "site", "AES", members))}


def test_two_tools_agree_on_micro_java_as_the_call_site_matcher_did():
    """The retired call-site matcher found 12 clusters with k=2 for CBOMkit and CipherRadar on micro-java."""
    records = []
    for fixture, tool in (("cbomkit_micro-java", "cbomkit"), ("cipherradar_micro-java", "cipherradar")):
        bom = json.loads((FIXTURES / f"{fixture}.json").read_text(encoding="utf-8"))
        records += normalize_cbom(bom, tool, language="java").records
    groups = ExactMatcher(level="L3", window=2).match(records)
    sites = [g for g in groups if g.scope == "site"]
    assert sum(g.k == 2 for g in sites) >= 10 and all(g.k <= 2 for g in sites)
    repo = [g for g in groups if g.scope == "repo"]
    assert sum(g.k == 2 for g in repo) >= 8


def test_groups_follow_the_cumulative_relation_of_the_level():
    """Groups are the clusters of the level: at L0 only verbatim equal names match, an item may have several groups,
    and a record that joins through a lower level is flagged `matched`."""
    records = [
        _rec(0, "a", "AES-256-GCM"),
        _rec(1, "b", "aes"),
        _rec(2, "c", "AES-256-GCM"),
        _rec(3, "d", "foo", family="AES"),  # a tool-specific resolution: L4 identity AES, raw name foo
        _rec(4, "e", "foo"),  # no family: its own identity stays the folded name
    ]

    def keyed(level):
        out = {}
        for g in ExactMatcher(level=level).match(records):
            if g.scope == "repo":
                out.setdefault(g.key, []).append(g)
        return out

    by_level = {lv: keyed(lv) for lv in LEVELS}
    assert all(set(by_level[lv]) == {"AES", "foo"} for lv in LEVELS)  # the unit is the same at every level
    aes0 = by_level["L0"]["AES"]
    assert [tuple(g.tools) for g in aes0] == [("a", "c"), ("b",), ("d", "e")]  # a = c verbatim; d = e verbatim
    assert [g.group_id for g in aes0] == ["E00001", "E00001-2", "E00001-3"]
    assert [tuple(g.tools) for g in by_level["L3"]["AES"]] == [("a", "b", "c"), ("d", "e")]  # parsed: aes = AES
    assert by_level["L3"]["AES"][1].matched == frozenset({"e:4:0"})  # e's foo joined d's AES report at L0
    assert by_level["L3"]["foo"][0].matched == frozenset({"d:3:0"})  # and the other way round
    (aes4,) = by_level["L4"]["AES"]
    assert aes4.tools == ["a", "b", "c", "d", "e"] and aes4.matched == frozenset({"e:4:0"})
    ks = [max(g.k for g in by_level[lv]["AES"]) for lv in LEVELS]
    assert ks == sorted(ks) and ks[0] == 2 and ks[-1] == 5  # cumulative
