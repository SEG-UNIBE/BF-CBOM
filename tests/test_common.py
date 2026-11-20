"""Unit tests for the pure-Python helpers in common/ (no Redis, no Docker)."""

import json
from pathlib import Path

import pytest

from common.cbom_analysis import (
    analyze_cbom_json,
    create_component_match_instruction,
    harmonize_value,
    load_components,
    summarize_component_types,
)
from common.cbom_filters import filter_cbom_components_include_only, is_included_component_type
from common.models import JobInstruction, JobResult, RepoInfo
from common.utils import normalize_json, repo_dict_to_info

FIXTURES = Path(__file__).parent


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ----- normalize_json -----


@pytest.mark.parametrize(
    ("payload", "expected_err"),
    [("", "empty_json"), ("   ", "empty_json"), (None, "empty_json"), ("{not json", "invalid_json")],
)
def test_normalize_json_rejects_bad_input(payload, expected_err):
    text, err = normalize_json(payload)
    assert text == "{}"
    assert err == expected_err


def test_normalize_json_compacts_and_keeps_unicode():
    text, err = normalize_json('{ "a" : "ü",\n "b": [1, 2] }')
    assert err is None
    assert text == '{"a":"ü","b":[1,2]}'


# ----- models round-trip -----


def test_job_models_round_trip():
    ri = RepoInfo(full_name="o/r", git_url="https://github.com/o/r.git", branch="main", size_kb=12, stars=3)
    instr = JobInstruction(job_id="j1", tool="cdxgen", repo_info=ri)
    assert JobInstruction.from_json(instr.to_json()) == instr
    res = JobResult(job_id="j1", status="ok", repo_info=ri, json="{}", duration_sec=1.5, worker="cdxgen")
    assert JobResult.from_json(res.to_json()) == res


def test_repo_dict_to_info_defaults():
    info = repo_dict_to_info({"full_name": "o/r", "size": "42", "stargazers_count": "7", "language": "Python"})
    assert info.git_url == "https://github.com/o/r.git"
    assert info.branch == "main"
    assert (info.size_kb, info.stars, info.main_language) == (42, 7, "Python")


# ----- component filters / analysis -----


@pytest.mark.parametrize("value", ["cryptographic-asset", "Crypto-Asset", " crypto assets "])
def test_included_component_types(value):
    assert is_included_component_type(value)


@pytest.mark.parametrize("value", ["library", "application", None, ""])
def test_excluded_component_types(value):
    assert not is_included_component_type(value)


def test_filter_keeps_only_requested_types():
    raw = json.dumps({"components": [{"type": "library", "name": "x"}, {"type": "cryptographic-asset", "name": "AES"}]})
    out = json.loads(filter_cbom_components_include_only(raw, {"cryptographic-asset"}))
    assert [c["name"] for c in out["components"]] == ["AES"]


def test_analyze_cbom_counts_fixture():
    total, types, combos, details = analyze_cbom_json(_fixture("authlib_cbomkit.json"), "cbomkit")
    assert total == 26
    assert types == {"cryptographic-asset": 26}
    assert sum(combos.values()) == 26
    # names are reported without the "@uuid" suffix
    assert all("@" not in name for (_, _, name) in details)


def test_analyze_cbom_handles_wrapped_and_invalid_input():
    assert analyze_cbom_json("not json", "x")[0] == 0
    wrapped = json.dumps({"bom": {"components": [{"type": "library", "name": "a"}]}})
    assert analyze_cbom_json(wrapped, "x")[0] == 1
    assert len(load_components(wrapped)) == 1


def test_summarize_component_types_groups_by_worker():
    rows = [
        {
            "repo": "o/r",
            "worker": "w1",
            "total_components": 2,
            "types": {"cryptographic-asset": 2},
            "type_asset_name_counts": {("cryptographic-asset", "algorithm", "AES"): 2},
        },
        {
            "repo": "o/r",
            "worker": "w2",
            "total_components": 1,
            "types": {"cryptographic-asset": 1},
            "type_asset_name_counts": {("cryptographic-asset", "algorithm", "AES"): 1},
        },
    ]
    table = summarize_component_types(rows, ["w1", "w2"])
    assert table["o/r"] == [
        {"component.type": "cryptographic-asset", "asset.type": "algorithm", "name": "AES", "w1": 2, "w2": 1}
    ]


# ----- matching input -----


def test_harmonize_value_strips_ids_and_case():
    assert harmonize_value("HMAC-SHA256@123e4567") == "hmacsha256"
    assert harmonize_value({"k": ["A-B", 1]}) == {"k": ["ab", 1]}


def test_create_component_match_instruction_requires_two_cboms():
    one = {"cbomkit": _fixture("authlib_cbomkit.json")}
    assert create_component_match_instruction({"full_name": "authlib/authlib"}, "insp", one) is None


def test_create_component_match_instruction_minimizes_components():
    cboms = {"cbomkit": _fixture("authlib_cbomkit.json"), "manual": _fixture("authlib_cbomkit_manuallygenerated.json")}
    instr = create_component_match_instruction({"full_name": "authlib/authlib"}, "insp", cboms, exclude_types=True)
    assert instr is not None
    assert [e.tool for e in instr.CbomJsons] == ["cbomkit", "manual"]
    comp = json.loads(instr.CbomJsons[0].components_as_json[0])
    assert set(comp) <= {"type", "name", "cryptoProperties"}
    assert comp["type"] == "cryptographicasset"  # harmonized
    assert len(instr.CbomJsons[0].components_as_json) == 26
