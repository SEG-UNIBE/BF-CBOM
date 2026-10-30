"""Corpus builder exclusions, first-party grep scope and pruning (common/corpus.py; refinements 11 and 13)."""

import json
import re
from datetime import date, timedelta

import pytest

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
    go = corpus.grep_pathspecs("Go")
    assert go[0] == "*.go" and ":(exclude,glob)**/*_test.go" in go and ":(exclude,glob)**/testdata/**" in go


def test_go_crypto_pattern_matches_crypto_imports_not_rand_alone():
    pattern = re.compile(corpus.CRYPTO_PATTERNS["go"])
    assert pattern.search('\t"crypto/aes"') and pattern.search('import "crypto/tls"')
    assert pattern.search('\tcrand "crypto/rand"\n\t"golang.org/x/crypto/pbkdf2"')
    assert not pattern.search('\t"crypto/rand"') and not pattern.search('"github.com/acme/crypto/aes"')
    assert corpus.CorpusParams().languages == ["Java", "Python", "Go"]


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


def test_repo_size_cap_is_fixed_unless_set():
    assert corpus.repo_size_cap_mb({}) == corpus.DEFAULT_MAX_REPO_SIZE_MB == 1536
    assert corpus.repo_size_cap_mb({"GITSERVER_MEMORY": "4g"}) == 1536  # the git server's memory does not move it
    assert corpus.repo_size_cap_mb({"CORPUS_MAX_REPO_SIZE_MB": "300"}) == 300
    assert corpus.repo_size_cap_mb({"CORPUS_MAX_REPO_SIZE_MB": "0"}) is None


def test_size_cells_are_clamped_to_the_cap():
    bins = [(300, 3000), (3000, 30000), (30000, 300000)]
    assert corpus.clamp_size_bins(bins, None) == bins
    assert corpus.clamp_size_bins(bins, 1024) == bins  # the top cell ends at 293 MB, below the cap
    assert corpus.clamp_size_bins(bins, 200) == [(300, 3000), (3000, 30000), (30000, 204800)]
    assert corpus.clamp_size_bins(bins, 20) == [(300, 3000), (3000, 20480)]


def _params(**kw) -> corpus.CorpusParams:
    base = dict(languages=["Java"], per_cell=2, star_bins=[(1, None)], size_bins_kb=[(300, None)], crypto_filter=False,
                max_repo_size_mb=1024)  # fmt: skip
    return corpus.CorpusParams(**{**base, **kw})


def _build(monkeypatch, params, population, **kw):
    monkeypatch.setattr(corpus, "search_cell", lambda *a, **k: (list(population), len(population), 1))
    monkeypatch.setattr(corpus, "resolve_remote_commit", lambda url, branch: "f" * 40)
    monkeypatch.setattr(corpus.time, "sleep", lambda s: None)
    return corpus.build_corpus(
        params, None, log=lambda m: None, exclusions={"repos": {}, "patterns": []}, tailored={}, **kw
    )


def test_build_corpus_excludes_repos_above_the_cap(monkeypatch):
    big = {"full_name": "big/repo", "git_url": "u", "branch": "main", "size_kb": 2 * 1024 * 1024, "stars": 1}
    small = {"full_name": "small/repo", "git_url": "u", "branch": "main", "size_kb": 500, "stars": 1}
    out = _build(monkeypatch, _params(size_bins_kb=[(300, 3_000_000)]), [big, small])
    assert [r["full_name"] for r in out["repos"]] == ["small/repo"]
    assert out["excluded"][0]["reason"] == "repo size 2048 MB > cap 1024 MB"
    assert out["params"]["max_repo_size_mb"] == 1024 and out["params"]["size_bins_kb"] == [[300, 1024 * 1024]]


def test_bins_are_half_open_configurable_and_the_quota_is_per_cell():
    assert corpus.parse_bins("50-300,300-2000,2000-") == [(50, 300), (300, 2000), (2000, None)]
    assert corpus.bin_query(50, 300) == "50..299" and corpus.bin_query(2000, None) == ">=2000"
    assert corpus.cell_query("Java", (50, 300), (300, None), "2025-10-29") == (
        "language:Java stars:50..299 size:>=300 pushed:>=2025-10-29 archived:false fork:false"
    )
    for bad in ("", "50-300,400-", "50-300,300-2000,2000"):
        with pytest.raises(ValueError):
            corpus.parse_bins(bad)
    p = corpus.CorpusParams(max_repo_size_mb=None)
    assert (p.cells_per_language, p.per_cell, p.per_language) == (9, 2, 18)  # 3 x 3 cells x 2 = 18 per language
    assert corpus.CorpusParams(per_cell=3, max_repo_size_mb=None).per_language == 27
    assert corpus.clamp_size_bins([(300, 3000), (3000, None)], 20) == [
        (300, 3000),
        (3000, 20480),
    ]  # the open bin ends at the cap


class _FakeGitHub:
    """A search API over a fixed population: `created:A..B` and `page` are honoured, at most 1000 results per query."""

    def __init__(self, n: int, start=date(2015, 1, 1), per_day=1):
        self.repos = [
            {"full_name": f"o/r{i}", "clone_url": f"u{i}", "default_branch": "main", "language": "Java",
             "stargazers_count": 10, "size_kb": 500, "size": 500, "pushed_at": "2026-01-01T00:00:00Z",
             "created_at": (start + timedelta(days=i // per_day)).isoformat(), "topics": ["crypto", "a"]}
            for i in range(n)
        ]  # fmt: skip
        self.calls: list[tuple[str, int, int]] = []

    def __call__(self, q, token, *, page=1, per_page=100):
        self.calls.append((q, page, per_page))
        m = re.search(r"created:(\S+)\.\.(\S+)", q)
        pool = self.repos
        if m:
            lo, hi = m.group(1), m.group(2)
            pool = [r for r in pool if lo <= r["created_at"][:10] <= hi]
        start = (page - 1) * per_page
        return pool[start : min(start + per_page, corpus.SEARCH_LIMIT)], len(pool)


def test_search_cell_enumerates_the_whole_population_by_pages_and_date_splits(monkeypatch):
    gh = _FakeGitHub(2_500, per_day=4)  # 625 days, 2500 repos: above the API's 1000, so the dates are split
    monkeypatch.setattr(corpus, "_search", gh)
    monkeypatch.setattr(corpus.time, "sleep", lambda s: None)
    repos, total, n_requests = corpus.search_cell("Java", (1, None), (300, None), "2025-01-01", None, pause=0)
    assert total == 2_500 and len(repos) == 2_500 and len({r["full_name"] for r in repos}) == 2_500
    assert repos[0]["topics"] == ["a", "crypto"] and repos[0]["created_at"].startswith("2015-")
    assert n_requests == len(gh.calls) and all(
        len(gh(q, None, page=p, per_page=pp)[0]) <= 100 for q, p, pp in gh.calls[:3]
    )
    assert max(p for _, p, _ in gh.calls) <= 10  # never past the limit of 1000 per query
    small = _FakeGitHub(150)
    monkeypatch.setattr(corpus, "_search", small)
    repos, total, n_requests = corpus.search_cell("Java", (1, None), (300, None), "2025-01-01", None, pause=0)
    assert (total, len(repos), n_requests) == (150, 150, 3)  # two pages plus the count query


def test_the_draw_is_uniform_over_the_cell_reproducible_and_recorded(monkeypatch):
    population = [
        {"full_name": f"o/r{i}", "git_url": "u", "branch": "main", "size_kb": 500, "stars": 5, "topics": ["x"]}
        for i in range(40)
    ]
    out = _build(monkeypatch, _params(per_cell=3, seed=7), population)
    again = _build(monkeypatch, _params(per_cell=3, seed=7), list(reversed(population)))  # the API's order varies
    other = _build(monkeypatch, _params(per_cell=3, seed=8), population)
    names = [r["full_name"] for r in out["repos"]]
    assert len(names) == 3 and names == [r["full_name"] for r in again["repos"]]
    assert names != [r["full_name"] for r in other["repos"]]
    (cell,) = out["cells"]
    assert cell == {"language": "Java", "stars": [1, None], "size_kb": [300, 1024 * 1024], "population": 40,
                    "enumerated": 40, "tried": 3, "sampled": 3, "quota": 3}  # fmt: skip
    assert out["params"]["per_cell"] == 3 and out["params"]["per_language"] == 3 and out["params"]["search_date"]
    assert out["repos"][0]["stratum"] == {"stars": [1, None], "size_kb": [300, 1024 * 1024]}
    assert out["repos"][0]["topics"] == ["x"] and "crypto_apis" not in out["repos"][0]  # no crypto filter here
    assert out["stats"]["requests"] == 1


def test_the_crypto_prefilter_records_the_api_families_as_a_covariate(monkeypatch):
    population = [{"full_name": "o/a", "git_url": "ua", "branch": "main", "size_kb": 500, "stars": 5},
                  {"full_name": "o/b", "git_url": "ub", "branch": "main", "size_kb": 500, "stars": 5}]  # fmt: skip
    families = {"ua": ["JCA", "BouncyCastle"], "ub": []}  # o/a matches two families, o/b none

    def fake_usage(git_url, branch, language):
        return families[git_url]

    monkeypatch.setattr(corpus, "has_crypto_usage", fake_usage)
    out = _build(monkeypatch, _params(per_cell=2, crypto_filter=True), population)
    assert [r["full_name"] for r in out["repos"]] == ["o/a"] and out["repos"][0]["crypto_apis"] == [
        "JCA",
        "BouncyCastle",
    ]
    assert out["excluded"] == [{"full_name": "o/b", "reason": "no first-party crypto usage"}]
    assert set(corpus.CRYPTO_APIS) == {"java", "python", "go"} and "pyca" in corpus.CRYPTO_APIS["python"]
    assert re.search(corpus.CRYPTO_APIS["python"]["pyca"], "from cryptography.hazmat import x", flags=re.M)
    assert not re.search(corpus.CRYPTO_APIS["python"]["pyca"], "import cryptography_tools", flags=re.M)


def test_corpus_covariates_reach_the_experiment_record():
    from common.experiment import RepoSpec, _as_repo_spec, spec_to_dict

    item = {"full_name": "o/a", "git_url": "u", "branch": "main", "commit": "f" * 40, "language": "Java", "stars": 5,
            "size_kb": 500, "stratum": {"stars": [50, 300], "size_kb": [300, 3000]}, "crypto_apis": ["JCA"],
            "topics": ["crypto"], "created_at": "2020-01-01T00:00:00Z"}  # fmt: skip
    spec = _as_repo_spec(item)
    assert isinstance(spec, RepoSpec) and spec.stratum == {"stars": [50, 300], "size_kb": [300, 3000]}
    assert spec.crypto_apis == ["JCA"] and spec.topics == ["crypto"]
    assert _as_repo_spec({"full_name": "o/b"}).stratum is None and _as_repo_spec({"full_name": "o/b"}).topics is None
    from common.experiment import ExperimentSpec

    d = spec_to_dict(ExperimentSpec(name="x", repos=[spec], tools=["t"]))
    assert d["repos"][0]["stratum"] == item["stratum"] and d["repos"][0]["topics"] == ["crypto"]


def test_github_check_reports_the_quota_and_names_a_rejected_token(monkeypatch):
    class Resp:
        def __init__(self, code, data=None):
            self.status_code, self._data, self.text = code, data or {}, "Bad credentials"

        def json(self):
            return self._data

    quota = {"resources": {"search": {"remaining": 30, "limit": 30}, "core": {"remaining": 4999, "limit": 5000}}}
    monkeypatch.setattr(corpus.requests, "get", lambda *a, **k: Resp(200, quota))
    line = corpus.github_check("t0ken")
    assert "authenticated" in line and "search 30 of 30" in line and "t0ken" not in line
    monkeypatch.setattr(corpus.requests, "get", lambda *a, **k: Resp(401))
    with pytest.raises(RuntimeError, match="rejected the token"):
        corpus.github_check("t0ken")


def test_a_frame_is_reused_without_searching_nests_the_draws_and_caches_crypto_checks(monkeypatch):
    population = [{"full_name": f"o/r{i:02d}", "git_url": f"u{i}", "branch": "main", "size_kb": 500, "stars": 60}
                  for i in range(20)]  # fmt: skip
    clones: list[str] = []
    monkeypatch.setattr(corpus, "has_crypto_usage", lambda url, branch, lang: clones.append(url) or ["JCA"])
    frame: dict = {}
    small = _build(monkeypatch, _params(per_cell=1, crypto_filter=True), population, frame=frame)
    cell = frame["cells"][0]
    assert cell["population"] == 20 and [r["full_name"] for r in cell["repos"]] == sorted(
        r["full_name"] for r in population
    )
    assert "commit" not in cell["repos"][0] and len(frame["crypto_checks"]) == len(clones) == 1
    monkeypatch.setattr(corpus, "search_cell", lambda *a, **k: pytest.fail("a reused frame must not search"))
    big = corpus.build_corpus(_params(per_cell=3, crypto_filter=True), None, log=lambda m: None,
                              exclusions={"repos": {}, "patterns": []}, tailored={}, frame=frame)  # fmt: skip
    names = [r["full_name"] for r in big["repos"]]
    assert len(names) == 3 and names[0] == small["repos"][0]["full_name"]  # the small draw sits inside the big one
    assert len(clones) == 3 and big["params"]["search_date"] == frame["search_date"]  # the first check was reused
    old = {**frame, "search_date": "2020-01-01", "cells": frame["cells"]}
    with pytest.raises(RuntimeError, match="has no cell"):
        corpus.build_corpus(_params(star_bins=[(1, 50), (50, None)]), None, log=lambda m: None,
                            exclusions={"repos": {}, "patterns": []}, tailored={}, frame=old)  # fmt: skip


def test_a_frame_survives_writing_and_reading(tmp_path):
    frame = {"search_date": "2026-10-30", "pushed_since": "2025-10-30",
             "cells": [{"language": "Go", "stars": [50, 300], "size_kb": [300, 3000], "query": "q", "population": 1,
                        "repos": [{"full_name": "a/b", "pushed_at": "2026-05-01T12:00:00Z", "topics": ["x"]}]}],
             "crypto_checks": {"a/b": {"commit": "0" * 40, "apis": ["stdlib"]}}}  # fmt: skip
    path = tmp_path / "corpus" / "c-frame.yaml"
    corpus.write_frame(frame, path)
    assert corpus.read_frame(path) == frame
