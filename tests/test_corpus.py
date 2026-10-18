"""Corpus builder exclusions, first-party grep scope and pruning (common/corpus.py; refinements 11 and 13)."""

import json

from common import corpus


def test_reviewed_exclusions_have_reasons():
    ex = corpus.load_exclusions()
    assert ex["repos"] and all(reason for reason in ex["repos"].values())
    assert "seg-unibe/bf-cbom" in ex["repos"] and ex["patterns"]


def test_exclusion_reason_in_order_of_precedence():
    ex = {"repos": {"ibm/cbomkit": "generator"}, "patterns": [("benchmark", "benchmarks of any kind")]}
    tailored = {"ciphersift": frozenset({"apache/kafka"})}

    def reason(name):
        return corpus.exclusion_reason(name, patterns=["cbom"], exclusions=ex, tailored=tailored)

    assert reason("apache/kafka") == "tailored_on of ciphersift (tools.yaml)"
    assert reason("IBM/cbomkit") == "generator"
    assert reason("acme/crypto-benchmark") == "benchmarks of any kind (benchmark)"
    assert reason("acme/my-cbom-tool") == "name pattern" and reason("acme/webapp") is None


def test_grep_pathspecs_cover_first_party_source_only():
    specs = corpus.grep_pathspecs("Java")
    assert specs[0] == "*.java" and ":(exclude,glob)**/test/**" in specs and ":(exclude,glob)**/vendor/**" in specs
    assert corpus.grep_pathspecs("Python")[0] == "*.py"


def test_prune_corpus_drops_repos_with_timeouts(tmp_path):
    exp = tmp_path / "e0"
    runs = [
        ("a__x@000000000000", "t1", "timeout"),
        ("a__x@000000000000", "t2", "ok"),
        ("b__y@000000000000", "t1", "ok"),
        ("c__z@000000000000", "t3", "error"),
    ]
    for repo, tool, status in runs:
        d = exp / repo / "original" / tool
        d.mkdir(parents=True)
        (d / "run1.meta.json").write_text(json.dumps({"status": status, "worker": tool}))
    corpus_in = {"repos": [{"full_name": "a/x"}, {"full_name": "b/y"}, {"full_name": "c/z"}]}
    out = corpus.prune_corpus(corpus_in, exp)
    assert [r["full_name"] for r in out["repos"]] == ["b/y", "c/z"]
    assert out["pruned"] == [{"full_name": "a/x", "reason": "timed out in e0", "runs": ["t1:timeout"]}]
    assert out["pruned_from"] == "e0"
    both = corpus.prune_corpus(corpus_in, exp, statuses=("timeout", "error"))
    assert [r["full_name"] for r in both["repos"]] == ["b/y"]
