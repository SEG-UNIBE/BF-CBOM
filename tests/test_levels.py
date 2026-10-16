"""The L0-L4 normalisation ladder (analysis/levels.py) on tiny hand-written CBOMs."""

from analysis.levels import LEVELS, identity, ladder
from analysis.normalize import normalize_cbom


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
    rows = {(r.level, r.tool): r for r in ladder(records, {"a": "g1", "b": "g2", "c": "g1"})}
    assert [(rows[(lv, "a")].confirmed, rows[(lv, "a")].algorithms) for lv in LEVELS] == [
        (0, 2), (1, 2), (1, 2), (2, 2), (2, 2)]  # fmt: skip
    assert rows[("L3", "all")].share == 1.0
