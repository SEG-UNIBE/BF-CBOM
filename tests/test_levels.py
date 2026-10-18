"""The L0-L4 normalisation ladder (analysis/levels.py) on tiny hand-written CBOMs."""

from analysis.levels import LEVELS, PARAMETER_LEVELS, algorithm_rows, algorithms_at, identity, ladder
from analysis.normalize import load_adapter, normalize_cbom
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
    assert [identity(rec, lv) for lv in LEVELS] == ["AES-256-GCM", "aes256gcm", "aes256gcm", "AES", "AES", "AES"]
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
        (0, 2), (1, 2), (1, 2), (2, 2), (2, 2), (2, 2)]  # fmt: skip
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


def test_interpretive_entries_count_at_l4_only(tmp_path):
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
    assert rand.via["family"] == "dictionary" and cert.via["protocol"] == "interpretive"
    # a tool idiom is tool-specific: unknown at L2/L3, resolved at L4lit and L4
    assert [identity(rand, lv) for lv in ("L2", "L3", "L4lit", "L4")] == ["weirdrand", "weirdrand", "PRNG", "PRNG"]
    # a judgement entry: resolved at L4 only (here it sets no family, so the identity stays the folded name)
    assert identity(cert, "L4lit") == identity(cert, "L3") == "certcheck"


def test_parameter_ladder_admits_values_by_source():
    standard = _records("a", "AES", family_field="AES")  # no parameters in standard fields
    (parsed,) = _records("b", "AES-256-GCM")
    (also,) = _records("c", "AES-256-GCM")
    records = {("s", "a"): standard, ("s", "b"): [parsed], ("s", "c"): [also]}
    rows = {(r.dimension, r.level, r.tool): r for r in ladder(records, {"a": "g1", "b": "g2", "c": "g3"})}
    assert ("key_size", "L0", "b") not in rows and ("key_size", "L2", "b") in rows
    assert rows[("key_size", "L2", "b")].algorithms == 0  # parsed from the name: not a standard field
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
    l3 = {(r["algorithm"], r["implied"]) for r in rows if r["level"] == "L3"}
    assert l3 == {("HMAC", False), ("SHA-256", True)}
    (pbkdf,) = _records("a", "PBKDF2WithHmacSHA1")
    assert vocab.implied(pbkdf.values) == ["SHA-1", "HMAC"]
