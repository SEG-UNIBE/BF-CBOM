"""Scoring without ground truth (analysis/score.py) and run-to-run stability (analysis/stability.py)."""

from analysis import score as sc
from analysis.matchers import ExactMatcher
from analysis.normalize import NormRecord
from analysis.stability import stability_rows

FAMILIES = {"AES": ("AES", "AES"), "RSA": ("RSA", "RSA"), "SHA-256": ("SHA-2", "SHA-256"), "MD5": ("MD5", "MD5")}


def _rec(i, tool, name, *, file=None, line=None, **values):
    """A normalised record as the normaliser builds it: a known name has its family (so the L4 identity exists),
    parameter values come from the parsed name (admitted from L3 on)."""
    vals = {"asset_type": "algorithm", **values}
    if name in FAMILIES and "family" not in vals:
        vals["family"], vals["algorithm"] = FAMILIES[name]
    via, src = ({k: "parse" for k in values}, {k: "name" for k in values})
    return NormRecord(f"{tool}:{i}:0", tool, i, 0, name=name, values=vals, via=via, src=src, file=file, line=line)


VOTERS = {"a": "ga", "b": "gb", "c": "gc", "a2": "ga"}
LOW = {"min_repos": 1, "min_items": 1}  # two tiny snapshots: rank everything


def _observations(tailored=None):
    """Two snapshots. s1: AES by a, b, c (a and b at the same site), RSA by a alone, SHA-256 by b and c; a2 (same
    voter group as a) reports AES. s2: AES by a and b with different key sizes; MD5 by c alone."""
    snaps = {
        "s1": [
            _rec(0, "a", "AES", file="x.py", line=10, key_size=256),
            _rec(1, "b", "AES", file="x.py", line=11, key_size=256),
            _rec(2, "c", "AES", file="y.py", line=5, key_size=128),
            _rec(3, "a", "RSA", file="x.py", line=20),
            _rec(4, "b", "SHA-256"),
            _rec(5, "c", "SHA-256"),
            _rec(6, "a2", "AES"),
        ],
        "s2": [
            _rec(0, "a", "AES", key_size=256),
            _rec(1, "b", "AES", key_size=128),
            _rec(2, "c", "MD5"),
        ],
    }
    matcher = ExactMatcher(level="L3", voter_of=VOTERS)
    groups = {s: matcher.match(recs) for s, recs in snaps.items()}
    ran = {"s1": {"a", "b", "c", "a2"}, "s2": {"a", "b", "c"}}
    return sc.observe(
        groups, level="L3", voter_of=VOTERS, tools_ran=ran, tailored=tailored, repo_of={"s1": "o/s1", "s2": "o/s2"}
    )


def test_confirmation_exclusivity_and_ranking():
    obs = _observations()
    algo = sc.confirmation(obs, "algorithm")
    assert algo["a"] == (3, 2, 1)  # AES x2 confirmed, RSA exclusive
    assert algo["b"] == (3, 3, 0) and algo["c"] == (3, 2, 1)  # c: AES and SHA-256 confirmed, MD5 alone
    assert algo["a2"] == (1, 1, 0)  # confirmed by b and c, not by a (same voter group)
    rates = sc.repo_rates(obs, "algorithm")
    assert rates["a"] == [0.5, 1.0] and rates["a2"] == [1.0] and sc.macro(rates["a"]) == 0.75
    assert sc.ranking(rates, algo, **LOW)[:2] == ["a2", "b"]  # 100 % each, a2 first by name
    assert sc.ranking(rates, algo) == []  # two snapshots: nobody has the support for a rank (3 repos, 10 items)
    key = sc.confirmation(obs, "key_size")
    assert key["a"] == (2, 1, 0) and key["c"] == (1, 0, 0)  # c's 128 in s1 disagrees but is not exclusive
    loc = sc.confirmation(obs, "location")
    assert loc["a"] == (2, 1, 1) and loc["c"] == (1, 0, 0)  # a+b share x.py:10-11; c's y.py site is alone
    assert loc["b"][1] == 1 and sc.confirmation(obs, "mode") == {t: (0, 0, 0) for t in obs.tools}


def test_generic_reports_confirm_through_attachment_once_per_unit():
    """SHA-2 (generic) next to SHA-256 and SHA-512: each specific item is confirmed once, the generic one too, and no
    item is counted twice although the generic record sits in three clusters."""
    recs = [_rec(0, "a", "SHA-256"), _rec(1, "b", "SHA-512", family="SHA-2", algorithm="SHA-512"),
            _rec(2, "c", "SHA-2", family="SHA-2", algorithm="SHA-2")]  # fmt: skip
    groups = {"s": ExactMatcher(level="L4", voter_of={}).match(recs)}
    obs = sc.observe(groups, level="L4", voter_of={}, tools_ran={"s": {"a", "b", "c"}})
    assert sc.confirmation(obs, "algorithm") == {"a": (1, 1, 0), "b": (1, 1, 0), "c": (1, 1, 0)}


def test_tailored_pairs_neither_count_nor_confirm():
    obs = _observations(tailored={"c": frozenset({"o/s1"})})
    algo = sc.confirmation(obs, "algorithm")
    assert algo["c"] == (1, 0, 1)  # only s2 (MD5, alone) counts for c
    assert algo["b"] == (3, 2, 1)  # SHA-256 in s1 lost its confirmer


def test_kendall_tau_and_leave_one_out():
    assert sc.kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    assert sc.kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == -1.0
    assert sc.kendall_tau(["a", "b", "c", "d"], ["a", "c", "b", "d"]) == 0.667
    assert sc.kendall_tau(["a"], ["a"]) is None
    rows = {r["left_out"]: r for r in sc.leave_one_out(_observations(), "algorithm", **LOW)}
    assert set(rows) == {"ga", "gb", "gc"} and all("a" not in r["ranking"] for r in [rows["ga"]])
    assert all(-1.0 <= r["kendall_tau"] <= 1.0 for r in rows.values())


def test_bootstrap_ci_brackets_the_point_estimate():
    obs = _observations()
    cis = sc.bootstrap_ci(obs, "algorithm", samples=50)
    point = sc.macro(sc.repo_rates(obs, "algorithm")["a"])
    assert cis["a"][0] <= round(point, 3) <= cis["a"][1] and cis["b"] == (1.0, 1.0)


def test_dawid_skene_recovers_a_reliable_and_an_unreliable_voter():
    """Three voters over 40 items of which 20 are real: `good` reports the real ones and nothing else, `ok` misses a
    few and adds a few, `noisy` reports half of everything at random. EM should rank good > ok > noisy on
    sensitivity+specificity and give the real items higher posteriors."""
    import random

    rng = random.Random(1)
    real = [f"alg{i}" for i in range(20)]  # lists, not sets: set order changes with the hash seed per process
    fake = [f"alg{i}" for i in range(20, 40)]
    reports = {
        "good": set(real),
        "ok": set(real[:16]) | set(fake[:3]),
        "noisy": {k for k in real + fake if rng.random() < 0.5},
    }
    voter_of = {t: t for t in reports}
    obs = sc.Observations(level="L3", voter_of=voter_of)
    obs.snapshots, obs.tools, obs.ran = {"s"}, set(reports), {"s": set(reports)}
    for key in real + fake:  # one cluster per algorithm, holding the voters that report it
        obs.sets[("s", "algorithm")].append({t: {key} for t, keys in reports.items() if key in keys})
    ds = sc.dawid_skene(obs)
    quality = {v: ds["sensitivity"][v] + ds["specificity"][v] for v in reports}
    assert quality["good"] > quality["ok"] > quality["noisy"]
    post_real = sum(ds["posterior"][("s", k)] for k in real) / 20
    post_fake = sum(ds["posterior"].get(("s", k), 0.0) for k in fake) / 20
    assert post_real > 0.9 and post_fake < 0.3 and 0 < ds["iterations"] <= 50


def test_score_rows_cover_every_dimension_with_items():
    rows, loo = sc.score_rows(_observations(), samples=20)
    dims = {r["dimension"] for r in rows}
    assert dims == {"algorithm", "key_size", "location"} and {r["dimension"] for r in loo} == dims
    a = next(r for r in rows if r["tool"] == "a" and r["dimension"] == "algorithm")
    assert (a["repos"], a["items"], a["rate"], a["rate_pooled"], a["exclusivity"]) == (2, 3, 0.75, 0.667, 0.333)
    assert a["rank"] is None and a["ci_low"] is None  # two repositories: below the support for a rank and a CI
    assert a["ds_support"] is not None and a["ds_sensitivity"] is not None
    assert all(r["ds_support"] is None for r in rows if r["dimension"] != "algorithm")
    ranked, _ = sc.score_rows(_observations(), samples=20, **LOW)
    a = next(r for r in ranked if r["tool"] == "a" and r["dimension"] == "algorithm")
    assert a["rank"] >= 1 and a["ci_low"] is not None


def test_consensus_restriction_drops_items_outside_the_set():
    obs = _observations()
    obs.restrict("s1", "a", {"AES"})  # RSA was not in a's consensus over repeated runs
    assert sc.confirmation(obs, "algorithm")["a"] == (2, 2, 0)


def test_stability_jaccard_and_consensus():
    runs = {
        ("s", "original", "t"): {
            1: [_rec(0, "t", "AES", family="AES"), _rec(1, "t", "RSA", family="RSA")],
            2: [
                _rec(0, "t", "AES", family="AES"),
                _rec(1, "t", "RSA", family="RSA"),
                _rec(2, "t", "MD5", family="MD5"),
            ],  # fmt: skip
            3: [_rec(0, "t", "AES", family="AES")],
        },
        ("s", "original", "once"): {1: [_rec(0, "once", "AES", family="AES")]},
    }
    rows, consensus = stability_rows(runs, level="L3")
    (row,) = rows
    assert row["tool"] == "t" and row["runs"] == 3 and row["records_mean"] == 2.0
    assert row["jaccard_min"] == round(1 / 3, 3) and row["consensus"] == ["AES", "RSA"] and row["unstable"] == ["MD5"]
    assert consensus == {("s", "original", "t"): {"AES", "RSA"}}  # a single run has no consensus row; L4 identities


def test_observers_are_confirmed_but_confirm_nobody_and_change_no_voter_number():
    """An observer (tools.yaml `role: observer`) is scored against the voters, but the voters' scores, ranks, LOO and
    Dawid-Skene are the same with and without it; with `--observers-vote` it is a voter like any other."""
    from common.tools_registry import VoterMap

    voters = VoterMap(VOTERS)
    snaps = {
        "s1": [_rec(0, "a", "AES"), _rec(1, "b", "AES"), _rec(2, "a", "RSA"), _rec(3, "c", "MD5")],
        "s2": [_rec(0, "a", "AES"), _rec(1, "c", "SHA-256")],
    }
    seen = {"s1": [_rec(9, "o", "AES"), _rec(8, "o", "RSA"), _rec(7, "o", "SHA-256")], "s2": [_rec(9, "o", "SHA-256")]}

    def scored(voter_of, with_observer):
        matcher = ExactMatcher(level="L3", voter_of=voter_of)
        recs = {s: snaps[s] + (seen[s] if with_observer else []) for s in snaps}
        ran = {s: {"a", "b", "c"} | ({"o"} if with_observer else set()) for s in snaps}
        obs = sc.observe({s: matcher.match(r) for s, r in recs.items()}, level="L3", voter_of=voter_of, tools_ran=ran)
        rows, loo = sc.score_rows(obs, samples=20, **LOW)
        return {(r["dimension"], r["tool"]): r for r in rows}, loo

    base, base_loo = scored(voters, False)
    observed = VoterMap(VOTERS, observers={"o"})
    rows, loo = scored(observed, True)
    for key, row in base.items():  # every voter number is unchanged by the observer's records
        assert rows[key] == row, key
    assert loo == base_loo
    o = rows[("algorithm", "o")]
    assert (o["items"], o["confirmed"], o["role"], o["rank"], o["ds_support"]) == (4, 3, "observer", None, None)
    # a's RSA and c's SHA-256 (s2) are reported by o alone; as an observer it confirms nobody
    assert (rows[("algorithm", "a")]["confirmed"], rows[("algorithm", "c")]["confirmed"]) == (1, 0)
    voting, _ = scored(VoterMap(VOTERS), True)  # --observers-vote: o is a voter like any other
    assert (voting[("algorithm", "a")]["confirmed"], voting[("algorithm", "c")]["confirmed"]) == (2, 1)
    assert voting[("algorithm", "o")]["role"] == "voter" and voting[("algorithm", "o")]["rank"] is not None
