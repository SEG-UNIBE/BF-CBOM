"""Voter groups and tailored repos from common/tools.yaml (common/tools_registry.py)."""

import yaml

from common import tools_registry as reg


def test_voter_groups_separate_llms_by_default_and_merge_on_request():
    tools = {
        "cbomkit": {"voter_group": "cbomkit"},
        "cbomkit-jar": {"voter_group": "cbomkit"},
        "deepseek": {"voter_group": "deepseek", "merge_group": "llm"},
        "claude": {"voter_group": "claude", "merge_group": "llm"},
        "xgrep": {},
    }
    assert reg.voter_groups(tools) == {
        "cbomkit": "cbomkit", "cbomkit-jar": "cbomkit", "deepseek": "deepseek", "claude": "claude", "xgrep": "xgrep",
    }  # fmt: skip
    assert (
        reg.voter_groups(tools, merge_llm=True)["deepseek"]
        == "llm"
        == reg.voter_groups(tools, merge_llm=True)["claude"]
    )


def test_registry_llm_workers_are_separate_voters_with_a_merge_group():
    tools = reg.load_tools()
    llms = [t for t, i in tools.items() if i.get("engine") == "llm"]
    assert llms and all(tools[t]["voter_group"] == t and tools[t]["merge_group"] == "llm" for t in llms)


def test_rule_tools_are_separate_voters_merged_only_by_engine_family():
    # their rule sets share no rule (analysis/README.md "Voter groups"); the shared engine is a sensitivity check
    rule_tools = ("securin-xbom", "cipherradar", "scanoss")
    declared, merged = reg.voter_groups(), reg.voter_groups(engine_family=True)
    assert [declared[t] for t in rule_tools] == list(rule_tools)
    assert {merged[t] for t in rule_tools} == {"semgrep"} and merged["cbomkit"] == declared["cbomkit"] == "cbomkit"


def test_tailored_repos_from_a_corpus_yaml_and_a_fixture_directory(tmp_path):
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "pilot.yaml").write_text(
        yaml.safe_dump({"repos": [{"full_name": "a/b"}, {"full_name": "c/d"}]}), encoding="utf-8"
    )
    for lang in ("java", "python"):
        (tmp_path / "corpus" / "micro" / lang).mkdir(parents=True)
    (tmp_path / "corpus" / "micro" / "ground_truth.yaml").write_text("{}", encoding="utf-8")
    tools = {
        "tuned": {"tailored_on": ["corpus/micro", "corpus/pilot.yaml", "corpus/missing.yaml"]},
        "plain": {},
    }
    assert reg.tailored_repos("tuned", tools, root=tmp_path) == {
        "a/b",
        "c/d",
        "bfcbom/micro-java",
        "bfcbom/micro-python",
    }
    assert reg.tailored_repos("plain", tools, root=tmp_path) == frozenset()
    assert reg.tailored_map(tools, root=tmp_path) == {"tuned": reg.tailored_repos("tuned", tools, root=tmp_path)}


def test_ciphersift_is_tailored_on_the_pilot_corpus_and_the_micro_projects():
    repos = reg.tailored_repos("ciphersift")
    assert "bfcbom/micro-java" in repos and "apache/kafka" in repos
    assert "curl/curl" in repos  # the full pilot list (pilot-2026-10-08-all.yaml), not only the paper-language one
    assert "authlib/authlib" not in repos  # ICPC is held out
