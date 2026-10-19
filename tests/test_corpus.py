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


def test_repo_size_cap_follows_the_git_server_memory():
    assert corpus.parse_size_mb("4g") == 4096 and corpus.parse_size_mb("512m") == 512
    assert corpus.parse_size_mb("2GiB") == 2048 and corpus.parse_size_mb("1048576") == 1
    assert corpus.parse_size_mb("") is None and corpus.parse_size_mb("lots") is None
    assert corpus.repo_size_cap_mb({"GITSERVER_MEMORY": "4g"}) == 1024
    assert corpus.repo_size_cap_mb({"GITSERVER_MEMORY": "4g", "CORPUS_MAX_REPO_SIZE_MB": "300"}) == 300
    assert corpus.repo_size_cap_mb({}) is None


def test_size_cells_are_clamped_to_the_cap():
    bins = [(300, 3000), (3000, 30000), (30000, 300000)]
    assert corpus.clamp_size_bins(bins, None) == bins
    assert corpus.clamp_size_bins(bins, 1024) == bins  # the top cell ends at 293 MB, below the cap
    assert corpus.clamp_size_bins(bins, 200) == [(300, 3000), (3000, 30000), (30000, 204800)]
    assert corpus.clamp_size_bins(bins, 20) == [(300, 3000), (3000, 20480)]


def test_build_corpus_excludes_repos_above_the_cap(monkeypatch):
    big = {"full_name": "big/repo", "git_url": "u", "branch": "main", "size_kb": 2 * 1024 * 1024, "stars": 1}
    small = {"full_name": "small/repo", "git_url": "u", "branch": "main", "size_kb": 500, "stars": 1}
    monkeypatch.setattr(corpus, "search_cell", lambda *a, **k: [big, small])
    monkeypatch.setattr(corpus, "resolve_remote_commit", lambda url, branch: "f" * 40)
    monkeypatch.setattr(corpus.time, "sleep", lambda s: None)
    params = corpus.CorpusParams(
        languages=["Java"],
        per_language=2,
        star_bins=[(1, 10)],
        size_bins_kb=[(300, 3_000_000)],
        crypto_filter=False,
        max_repo_size_mb=1024,
    )
    out = corpus.build_corpus(params, None, log=lambda m: None, exclusions={"repos": {}, "patterns": []}, tailored={})
    assert [r["full_name"] for r in out["repos"]] == ["small/repo"]
    assert out["excluded"][0]["reason"] == "repo size 2048 MB > cap 1024 MB (GITSERVER_MEMORY)"
    assert out["params"]["max_repo_size_mb"] == 1024 and out["params"]["size_bins_kb"] == [[300, 1024 * 1024]]
