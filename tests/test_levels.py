"""The L0-L4 normalisation ladder (analysis/levels.py) on tiny hand-written CBOMs."""

from analysis.levels import (
    LEVELS,
    PARAMETER_LEVELS,
    algorithm_rows,
    algorithms_at,
    clusters_at,
    identity,
    ladder,
    level_matches,
)
from analysis.normalize import load_adapter, normalize_cbom
from analysis.normalize.specs import spec_files
from analysis.normalize.vocab import load_vocabulary


def _records(tool: str, *names: str, family_field: str | None = None, version: str = "1.6"):
    comps = []
    for n in names:
        cp = {"assetType": "algorithm"}
        if family_field:
            cp["algorithmProperties"] = {"algorithmFamily": family_field}
        comps.append({"type": "cryptographic-asset", "name": n, "cryptoProperties": cp})
    return normalize_cbom({"bomFormat": "CycloneDX", "specVersion": version, "components": comps}, tool).records


def test_identity_per_level():
    (rec,) = _records("t", "AES-256-GCM")
    assert [identity(rec, lv) for lv in LEVELS] == ["AES-256-GCM", "aes256gcm", "aes256gcm", "AES", "AES"]
    (des,) = _records("t", "DESede")
    assert identity(des, "L1") == "desede" and identity(des, "L2") == "3DES"
    (cr,) = _records("t", "RSA-ECB/OAEPWithSHA-256AndMGF1Padding", family_field="RSASSA-PKCS1", version="1.7")
    assert identity(cr, "L2") == "RSASSA-PKCS1"  # 1.7 algorithmFamily is standard vocabulary


def test_ladder_counts_confirmations_by_other_voter_groups():
    records = {
        ("s", "a"): _records("a", "SHA-256", "AES-256-GCM"),
        ("s", "b"): _records("b", "sha256", "AES"),
        ("s", "c"): _records("c", "SHA-256", "AES-256-GCM"),  # same voter group as a: never confirms a
    }
    rows = {(r.level, r.tool): r for r in ladder(records, {"a": "g1", "b": "g2", "c": "g1"}, dims=("algorithm",))}
    assert [(rows[(lv, "a")].confirmed, rows[(lv, "a")].algorithms) for lv in LEVELS] == [
        (0, 2), (1, 2), (1, 2), (2, 2), (2, 2)]  # fmt: skip
    assert rows[("L3", "all")].share == 1.0


def test_ladder_excludes_tailored_pairs_on_both_sides():
    records = {
        ("s", "a"): _records("a", "SHA-256"),
        ("s", "b"): _records("b", "SHA-256"),
    }
    voters = {"a": "g1", "b": "g2"}
    rows = {(r.level, r.tool): r for r in ladder(records, voters, exclude={("s", "b")}, dims=("algorithm",))}
    assert ("L3", "b") not in rows  # b was tuned on s: not scored there ...
    assert (rows[("L3", "a")].algorithms, rows[("L3", "a")].confirmed) == (1, 0)  # ... and it confirms nobody


def test_tool_labels_resolve_at_l4_and_no_spec_makes_judgements(tmp_path):
    (tmp_path / "t.yaml").write_text(
        "spec: t\ninherits: _cyclonedx\nsources: [base]\n"
        "dictionary:\n  name:\n    Cert Check: {protocol: TLS, interpretive: true}\n    weird-rand: {family: PRNG}\n",
        encoding="utf-8",
    )
    spec = load_adapter("t", str(tmp_path))
    bom = {"bomFormat": "CycloneDX", "specVersion": "1.6", "components": [
        {"type": "cryptographic-asset", "name": "weird-rand", "cryptoProperties": {"assetType": "algorithm"}},
        {"type": "cryptographic-asset", "name": "Cert Check", "cryptoProperties": {"assetType": "algorithm"}},
    ]}  # fmt: skip
    rand, cert = normalize_cbom(bom, "t", spec=spec).records
    # a tool's own label is tool-specific: unknown at L2/L3, resolved at L4
    assert rand.via["family"] == "dictionary"
    assert [identity(rand, lv) for lv in ("L2", "L3", "L4")] == ["weirdrand", "weirdrand", "PRNG"]
    # the engine still tags a judgement entry (`interpretive`), but no shipped spec may contain one (CLAUDE.md Rules):
    # what a tool reports is the finding, not what we think it meant
    assert cert.via["protocol"] == "interpretive" and identity(cert, "L4") is None
    import yaml

    for path in spec_files():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for dim, table in (data.get("dictionary") or {}).items():
            for key, entry in (table or {}).items():
                assert not (isinstance(entry, dict) and entry.get("interpretive")), f"{path.name}: {dim}/{key}"


def test_parameter_ladder_admits_values_by_source():
    standard = _records("a", "AES", family_field="AES")  # no parameters in standard fields
    (parsed,) = _records("b", "AES-256-GCM")
    (also,) = _records("c", "AES-256-GCM")
    records = {("s", "a"): standard, ("s", "b"): [parsed], ("s", "c"): [also]}
    rows = {(r.dimension, r.level, r.tool): r for r in ladder(records, {"a": "g1", "b": "g2", "c": "g3"})}
    assert ("key_size", "L0", "b") not in rows and ("key_size", "L2", "b") in rows
    l2 = rows[("key_size", "L2", "b")]
    assert (l2.algorithms, l2.confirmed) == (1, 0)  # the pair exists (fixed unit), parsed values are not admitted
    assert (rows[("key_size", "L3", "b")].algorithms, rows[("key_size", "L3", "b")].confirmed) == (1, 1)
    assert (rows[("mode", "L4", "all")].algorithms, rows[("mode", "L4", "all")].confirmed) == (2, 2)
    assert set(PARAMETER_LEVELS) == {r.level for r in rows.values() if r.dimension != "algorithm"}


def test_closure_adds_implied_algorithms_without_pooling():
    (hmac,) = _records("a", "HmacSHA256")
    vocab = load_vocabulary()
    assert vocab.implied(hmac.values) == ["SHA-256"]
    assert set(algorithms_at([hmac], "L3", vocab)) == {"HMAC"}
    assert set(algorithms_at([hmac], "L3", vocab, closure=True)) == {"HMAC", "SHA-256"}
    assert set(algorithms_at([hmac], "L2", vocab, closure=True)) == {"hmacsha256"}  # nothing parsed at L2
    rows = algorithm_rows({("s", "original", "a"): [hmac]}, {"a": "a"}, run=1, closure=True)
    assert {(r["algorithm"], r["implied"]) for r in rows} == {("HMAC", False), ("SHA-256", True)}
    assert all(r["k_L0"] == 1 and r["k_L4"] == 1 for r in rows)
    (pbkdf,) = _records("a", "PBKDF2WithHmacSHA1")
    assert vocab.implied(pbkdf.values) == ["SHA-1", "HMAC"]


def test_ladder_is_cumulative_on_the_fixed_l4_unit():
    """The unit is the L4 identity; the relation is cumulative: a match at a lower level is never lost."""
    # a: two spellings of one algorithm; b: one. The unit stays one item, confirmed from L1 on (aes = AES)
    records = {("s", "a"): _records("a", "AES-256-GCM", "aes"), ("s", "b"): _records("b", "AES")}
    rows = {(r.level, r.tool): r for r in ladder(records, {"a": "g1", "b": "g2"}, dims=("algorithm",))}
    assert [(rows[(lv, "a")].algorithms, rows[(lv, "a")].confirmed) for lv in LEVELS] == [(1, 0)] + [(1, 1)] * 4
    # the same raw string, resolved differently: a's "foo" has algorithmFamily AES, b's RSA
    records = {
        ("s", "a"): _records("a", "foo", family_field="AES"),
        ("s", "b"): _records("b", "foo", family_field="RSA"),
    }
    assert [identity(r, "L4") for r in records[("s", "a")] + records[("s", "b")]] == ["AES", "RSA"]
    rows = {(r.level, r.tool): r for r in ladder(records, {"a": "g1", "b": "g2"}, dims=("algorithm",))}
    assert all(rows[(lv, t)].share == 1.0 for lv in LEVELS for t in ("a", "b"))  # verbatim equal: agree everywhere
    assert all(rows[(lv, "all")].multi_unit_clusters == 1 for lv in LEVELS)  # the `foo` cluster spans AES and RSA
    items = level_matches(records[("s", "a")] + records[("s", "b")])
    assert set(items) == {"AES", "RSA"} and items["AES"].k_at("L0", {"a": "a", "b": "b"}) == 2
    # a name nobody understands has no unit: it is no item and matches nothing
    records = {("s", "a"): _records("a", "AES", "parseCsvMap"), ("s", "b"): _records("b", "parseCsvMap")}
    rows = {(r.level, r.tool): r for r in ladder(records, {"a": "g1", "b": "g2"}, dims=("algorithm",))}
    assert (rows[("L4", "a")].algorithms, rows[("L4", "a")].confirmed) == (1, 0) and rows[("L4", "b")].algorithms == 0


def test_generic_reports_attach_without_merging_and_bridges_do_not_merge():
    """SHA-2 (generic) next to SHA-256 and SHA-512: the generic record confirms both and is confirmed, but SHA-256 and
    SHA-512 stay two clusters; a record with different identities per level sits in two clusters that never merge."""
    records = {
        ("s", "a"): _records("a", "SHA-256"),
        ("s", "b"): _records("b", "SHA-512"),
        ("s", "c"): _records("c", "SHA-2"),
    }
    voters = {"a": "a", "b": "b", "c": "c"}
    items = level_matches(records[("s", "a")] + records[("s", "b")] + records[("s", "c")])
    assert set(items) == {"SHA-256", "SHA-512", "SHA-2"}
    l4 = {c.key: c for c in clusters_at(items, "L4")}
    assert set(l4) == {"SHA-256", "SHA-512", "SHA-2"}
    assert [r.tool for r in l4["SHA-256"].members] == ["a"] and [r.tool for r in l4["SHA-256"].attached] == ["c"]
    assert [r.tool for r in l4["SHA-512"].attached] == ["c"] and l4["SHA-512"].reason == {"c:0:0": "generic"}
    assert l4["SHA-2"].attached == [] and all(len(c.units(load_vocabulary())) == 1 for c in l4.values())
    assert items["SHA-256"].confirmed_at("L4", "a", voters) and items["SHA-2"].confirmed_at("L4", "c", voters)
    assert not items["SHA-256"].confirmed_strictly_at("L4", "a", voters)  # only through the attached generic report
    assert not items["SHA-256"].confirmed_at("L1", "a", voters)  # generic knowledge exists from L2 on
    rows = {(r.level, r.tool): r for r in ladder(records, voters, dims=("algorithm",))}
    assert (rows[("L4", "all")].confirmed, rows[("L4", "all")].confirmed_strict) == (3, 0)
    assert rows[("L4", "all")].multi_unit_clusters == 0
    # CipherSift's `hmac.new` with SHA-2 in its family field: the raw name at L0/L1, the field's SHA-2 at L2, the
    # parsed HMAC at L3, the field's SHA-2 at L4: the record sits in the HMAC and the SHA-2 cluster, which never merge
    comp = {
        "type": "cryptographic-asset",
        "name": "hmac.new",
        "cryptoProperties": {"assetType": "algorithm", "algorithmProperties": {"algorithmFamily": "SHA-2"}},
    }
    (rec,) = normalize_cbom(
        {"bomFormat": "CycloneDX", "specVersion": "1.7", "components": [comp]}, "ciphersift"
    ).records
    assert rec.name_kind is None
    assert [identity(rec, lv) for lv in LEVELS] == ["hmac.new", "hmacnew", "SHA-2", "HMAC", "SHA-2"]


def test_observers_get_ladder_rows_but_confirm_nobody():
    from common.tools_registry import VoterMap, voter_groups

    records = {("s", "a"): _records("a", "AES", "SHA-256"), ("s", "b"): _records("b", "aes")}
    observer = {("s", "o"): _records("o", "AES", "SHA-256", "MD5")}
    plain = {(r.dimension, r.level, r.tool): r.to_dict() for r in ladder(records, VoterMap({}))}
    voter_of = VoterMap({}, observers={"o"})
    rows = {(r.dimension, r.level, r.tool): r.to_dict() for r in ladder({**records, **observer}, voter_of)}
    assert {k: v for k, v in rows.items() if k[2] != "o"} == plain  # voters and the pooled `all` rows: unchanged
    o = rows[("algorithm", "L4", "o")]
    assert (o["algorithms"], o["confirmed"], o["role"]) == (3, 2, "observer")  # AES and SHA-256 confirmed by a, b
    assert rows[("algorithm", "L4", "a")]["confirmed"] == 1  # a's SHA-256: only the observer reports it, no vote
    algos = algorithm_rows(
        {("s", "original", t): recs for (_, t), recs in {**records, **observer}.items()}, voter_of, run=1
    )
    md5 = next(r for r in algos if r["algorithm"] == "MD5")
    assert (md5["role"], md5["k_L4"]) == ("observer", 0)  # only an observer reports it: no voter group
    assert voter_groups({"x": {"role": "observer"}, "y": {}}).observers == {"x"}
    assert voter_groups({"x": {"role": "observer"}}, observers_vote=True).observers == frozenset()
