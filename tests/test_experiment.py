"""Tests for common/experiment.py and the commit-pinned clone path (local git repos, no network)."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from common import experiment as ex
from common.models import JobResult, RepoInfo
from common.utils import clone_repo, repo_dict_to_info, resolve_remote_commit


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[Path, str, str]:
    """A small 'upstream' repo with two commits on branch main; returns (path, sha1, sha2)."""
    src = tmp_path / "upstream"
    src.mkdir()
    _git("init", "-q", "-b", "main", cwd=src)
    _git("config", "user.email", "t@example.com", cwd=src)
    _git("config", "user.name", "t", cwd=src)
    (src / "a.py").write_text("import hashlib\n")
    _git("add", ".", cwd=src)
    _git("commit", "-q", "-m", "one", cwd=src)
    sha1 = _git("rev-parse", "HEAD", cwd=src)
    (src / "a.py").write_text("import hashlib\nimport hmac\n")
    _git("commit", "-q", "-am", "two", cwd=src)
    sha2 = _git("rev-parse", "HEAD", cwd=src)
    return src, sha1, sha2


# ----- spec parsing -----


def test_spec_parsing_and_corpus_merge(tmp_path: Path):
    (tmp_path / "corpus.yaml").write_text(yaml.safe_dump({"repos": [{"full_name": "a/b", "language": "Java"}]}))
    spec_file = tmp_path / "e.yaml"
    spec_file.write_text(
        yaml.safe_dump(
            {
                "name": "e1",
                "tools": ["cdxgen"],
                "repetitions": 2,
                "corpus": "corpus.yaml",
                "repos": [{"full_name": "c/d", "variants": ["original", "m-1"]}],
            }
        )
    )
    spec = ex.load_spec(spec_file)
    assert [r.full_name for r in spec.repos] == ["a/b", "c/d"]
    assert spec.repos[0].source_url == "https://github.com/a/b.git"
    assert spec.expected_jobs == (1 + 2) * 1 * 2


@pytest.mark.parametrize(
    "data",
    [
        {"name": "bad name!", "tools": ["x"], "repos": [{"full_name": "a/b"}]},
        {"name": "ok", "tools": [], "repos": [{"full_name": "a/b"}]},
        {"name": "ok", "tools": ["x"], "repos": [{"full_name": "nope"}]},
        {"name": "ok", "tools": ["x"], "repos": [{"full_name": "a/b"}, {"full_name": "a/b"}]},
        {"name": "ok", "tools": ["x"], "repos": [{"full_name": "a/b", "commit": "abc"}]},
        {"name": "ok", "tools": ["x"], "repos": [{"full_name": "a/b", "variants": ["bad/slash"]}]},
    ],
)
def test_spec_rejects_invalid(data):
    with pytest.raises(ex.ExperimentError):
        ex.spec_from_dict(data)


# ----- paths and branches -----


def test_layout_helpers():
    sha = "a" * 40
    cbom, meta = ex.result_paths(Path("/r/e"), "o/n", sha, "original", "cdxgen", 2)
    assert cbom == Path("/r/e/o__n@aaaaaaaaaaaa/original/cdxgen/run2.json")
    assert meta.name == "run2.meta.json"
    assert ex.variant_branch("original", sha) == f"bfcbom/{sha}"
    assert ex.variant_branch("m-1", sha) == "bfcbom/m-1"
    assert ex.mirror_url("o/n", sha, "http://gitserver") == "http://gitserver/o/n--aaaaaaaaaaaa.git"


# ----- clone by commit + resolve -----


def test_resolve_remote_commit_and_clone_exact_commit(upstream, tmp_path: Path):
    src, sha1, sha2 = upstream
    assert resolve_remote_commit(str(src), "main") == sha2
    assert resolve_remote_commit(str(src), "nope") is None
    assert resolve_remote_commit(str(src), sha1) == sha1  # full SHA passes through

    target = tmp_path / "co"
    path = clone_repo(str(src), branch="main", target_dir=str(target), commit=sha1)
    assert path and _git("rev-parse", "HEAD", cwd=target) == sha1
    assert (target / "a.py").read_text() == "import hashlib\n"

    # without commit: shallow clone of the branch head
    path = clone_repo(str(src), branch="main", target_dir=str(target))
    assert path and _git("rev-parse", "HEAD", cwd=target) == sha2
    assert clone_repo(str(src), branch="missing", target_dir=str(target)) is None


def test_repo_dict_to_info_prefers_explicit_branch_and_keeps_commit():
    info = repo_dict_to_info({"full_name": "o/r", "branch": "bfcbom/x", "default_branch": "main", "commit": "c" * 40})
    assert info.branch == "bfcbom/x"
    assert info.commit == "c" * 40
    assert repo_dict_to_info({"full_name": "o/r", "default_branch": "dev"}).branch == "dev"


# ----- mirroring, lock, planning, persistence -----


def test_mirror_lock_plan_and_write(upstream, tmp_path: Path):
    src, sha1, sha2 = upstream
    mirrors = tmp_path / "mirrors"
    results = tmp_path / "results"
    repos = [{"full_name": "own/repo", "git_url": str(src)}]
    spec = ex.spec_from_dict({"name": "e", "tools": ["t1", "t2"], "repetitions": 2, "repos": repos})
    exp_dir = ex.experiment_dir("e", results)

    snaps = ex.resolve_snapshots(spec, exp_dir, mirror_dir=mirrors, server_url="http://gs", log=lambda m: None)
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap.commit == sha2 and snap.variant_commit == sha2
    assert snap.scan_url == f"http://gs/own/repo--{sha2[:12]}.git" and snap.scan_branch == f"bfcbom/{sha2}"
    bare = mirrors / "own" / "repo.git"
    assert _git("rev-parse", f"refs/heads/bfcbom/{sha2}", cwd=bare) == sha2
    scan = mirrors / "own" / f"repo--{sha2[:12]}.git"
    assert _git("symbolic-ref", "HEAD", cwd=scan) == f"refs/heads/bfcbom/{sha2}"
    assert _git("rev-parse", "HEAD", cwd=scan) == sha2
    assert (scan / "objects" / "info" / "alternates").exists()
    assert _git("config", "uploadpack.allowAnySHA1InWant", cwd=bare) == "true"
    assert ex.load_lock(exp_dir)["own/repo"]["commit"] == sha2

    # a worker can fetch exactly that commit from the mirror
    co = clone_repo(str(scan), branch=snap.scan_branch, target_dir=str(tmp_path / "wt"), commit=sha2)
    assert co and _git("rev-parse", "HEAD", cwd=co) == sha2

    # a mutant variant is a branch in the mirror
    _git("update-ref", "refs/heads/bfcbom/m-1", sha1, cwd=bare)
    spec.repos[0].variants = ["original", "m-1"]
    snaps = ex.resolve_snapshots(spec, exp_dir, mirror_dir=mirrors, server_url="http://gs", log=lambda m: None)
    assert [(s.variant, s.variant_commit) for s in snaps] == [("original", sha2), ("m-1", sha1)]
    m1 = mirrors / "own" / f"repo--{sha1[:12]}.git"
    assert _git("symbolic-ref", "HEAD", cwd=m1) == "refs/heads/bfcbom/m-1"
    assert _git("rev-parse", "HEAD", cwd=m1) == sha1
    with pytest.raises(ex.ExperimentError):
        ex.variant_commit(spec.repos[0], "m-missing", sha2, mirror_dir=mirrors)

    # the lock wins over upstream moving on
    (src / "b.py").write_text("x = 1\n")
    _git("add", ".", cwd=src)
    _git("commit", "-q", "-m", "three", cwd=src)
    snaps = ex.resolve_snapshots(spec, exp_dir, mirror_dir=mirrors, server_url="http://gs", log=lambda m: None)
    assert snaps[0].commit == sha2

    ids = iter(f"job-{i}" for i in range(100))
    jobs, skipped = ex.plan_jobs(spec, snaps, exp_dir, new_id=lambda: next(ids))
    assert (len(jobs), skipped) == (2 * 2 * 2, 0)
    instr = jobs[0].instruction()
    assert instr.repo_info == RepoInfo(
        full_name="own/repo",
        git_url=f"http://gs/own/repo--{sha2[:12]}.git",
        branch=f"bfcbom/{sha2}",
        size_kb=0,
        commit=sha2,
    )
    assert instr.run_index == 1

    res = JobResult(
        job_id=jobs[0].job_id,
        status="ok",
        repo_info=instr.repo_info,
        json='{"bom": {"components": [{"name": "AES"}]}}',
        duration_sec=1.0,
        worker="t1",
        tool_version="t1 9.9",
        image_ref="bf-cbom/worker-t1:1",
        run_index=1,
    )
    cbom_path = ex.write_result(exp_dir, jobs[0], res)
    assert json.loads(cbom_path.read_text()) == {"components": [{"name": "AES"}]}
    meta = json.loads(cbom_path.with_name("run1.meta.json").read_text())
    assert meta["tool_version"] == "t1 9.9" and meta["variant"] == "original" and "json" not in meta

    jobs2, skipped2 = ex.plan_jobs(spec, snaps, exp_dir, new_id=lambda: next(ids))
    assert (len(jobs2), skipped2) == (7, 1)  # resumable
    failed = JobResult(
        job_id=jobs[1].job_id, status="error", repo_info=instr.repo_info, json="{}", worker="t1", error="x"
    )
    ex.write_result(exp_dir, jobs[1], failed)
    assert ex.plan_jobs(spec, snaps, exp_dir, new_id=lambda: next(ids))[1] == 2
    jobs3, skipped3 = ex.plan_jobs(spec, snaps, exp_dir, new_id=lambda: next(ids), retry_failed=True)
    assert (len(jobs3), skipped3) == (7, 1)  # the failed run is redone, the ok one kept
    assert not jobs[1].paths(exp_dir)[1].exists()
    assert ex.summarize(exp_dir) == {"name": "e", "stored_runs": 1, "per_tool": {"t1": {"ok": 1}}}


# ----- fixtures published from plain directories -----


def test_publish_fixture_is_deterministic_and_resolvable(tmp_path: Path):
    src = tmp_path / "micro"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "a.py").write_text("import hashlib\n")
    mirror = tmp_path / "mirror"
    spec = ex.RepoSpec(full_name="bfcbom/micro-x", path=str(src))
    sha1 = ex.publish_fixture(spec, mirror_dir=mirror)
    sha_again = ex.publish_fixture(spec, mirror_dir=mirror)
    assert sha1 == sha_again and len(sha1) == 40
    assert spec.source_url == f"fixture:{src}"
    # same content from another directory (e.g. another machine) -> same SHA (author, date, message are fixed)
    other = tmp_path / "copy"
    other.mkdir()
    (other / "pkg").mkdir()
    (other / "pkg" / "a.py").write_text("import hashlib\n")
    assert ex.publish_fixture(ex.RepoSpec(full_name="bfcbom/micro-x", path=str(other)), mirror_dir=mirror) == sha1
    # changed content -> new SHA, published into the existing mirror
    (src / "pkg" / "a.py").write_text("import hmac\n")
    sha2 = ex.publish_fixture(spec, mirror_dir=mirror)
    assert sha2 != sha1
    bare = ex.mirror_path("bfcbom/micro-x", mirror)
    assert _git("rev-parse", "refs/heads/main", cwd=bare) == sha2
    assert _git("cat-file", "-t", sha1, cwd=bare) == "commit"

    # an experiment spec with `path:` relative to the spec file
    spec_file = tmp_path / "exp" / "e.yaml"
    spec_file.parent.mkdir()
    spec_file.write_text(
        yaml.safe_dump(
            {"name": "e-fix", "tools": ["cdxgen"], "repos": [{"full_name": "bfcbom/micro-x", "path": "../micro"}]}
        )
    )
    loaded = ex.load_spec(spec_file)
    assert loaded.repos[0].path == str(src.resolve()) and loaded.repos[0].is_fixture
    exp_dir = tmp_path / "results" / "e-fix"
    snaps = ex.resolve_snapshots(loaded, exp_dir, mirror_dir=mirror, server_url="http://gs", log=lambda m: None)
    assert [s.commit for s in snaps] == [sha2]
    assert snaps[0].scan_url == f"http://gs/bfcbom/micro-x--{sha2[:12]}.git"
    scan = ex.scan_repo_path("bfcbom/micro-x", sha2, mirror)
    assert _git("rev-parse", "HEAD", cwd=scan) == sha2
    # the lock keeps the commit even if the fixture changes again
    (src / "pkg" / "a.py").write_text("import ssl\n")
    snaps2 = ex.resolve_snapshots(loaded, exp_dir, mirror_dir=mirror, server_url="http://gs", log=lambda m: None)
    assert snaps2[0].commit == sha2


def test_spec_rejects_path_with_commit_or_url():
    with pytest.raises(ex.ExperimentError):
        ex.spec_from_dict(
            {"name": "ok", "tools": ["x"], "repos": [{"full_name": "a/b", "path": "p", "commit": "a" * 40}]}
        )


# ----- memory budget / waves -----

MEM = {"cbomkit": 8.0, "cbomkit-jar": 8.0, "cryptobomforge": 6.0, "cdxgen": 4.0, "pqcheck": 4.0, "deepseek": 1.0}
TOOLS = ["cbomkit", "cdxgen", "cryptobomforge", "deepseek", "pqcheck", "cbomkit-jar"]


def test_plan_waves_no_budget_is_one_wave_in_spec_order():
    assert ex.plan_waves(TOOLS, budget_gb=0, memory_gb=MEM) == [TOOLS]
    assert ex.plan_waves([], budget_gb=14, memory_gb=MEM) == []


def test_plan_waves_packs_first_fit_decreasing_within_budget():
    waves = ex.plan_waves(TOOLS, budget_gb=14, memory_gb=MEM)
    assert all(sum(MEM[t] for t in w) <= 14 for w in waves)
    assert sorted(t for w in waves for t in w) == sorted(TOOLS)  # every tool exactly once
    assert waves[0] == ["cbomkit", "cryptobomforge"]  # 8 + 6, kept in spec order inside the wave
    assert waves[1] == ["cdxgen", "deepseek", "cbomkit-jar"]  # 8 + 4 + 1
    assert waves[2] == ["pqcheck"]


def test_plan_waves_oversized_tool_gets_its_own_wave_and_pinned_tools_go_first():
    assert ex.plan_waves(["cbomkit", "deepseek"], budget_gb=4, memory_gb=MEM) == [["cbomkit"], ["deepseek"]]
    # jobs already in the queues (`first`) are collected in wave 1 even when that exceeds the budget
    waves = ex.plan_waves(TOOLS, budget_gb=14, memory_gb=MEM, first=["cbomkit-jar", "cbomkit"])
    assert waves[0] == ["cbomkit", "cbomkit-jar"]
    assert sorted(t for w in waves for t in w) == sorted(TOOLS)


def test_tools_yaml_memory_matches_compose_limits():
    """`memory_gb` in common/tools.yaml is what the runner budgets with; it must equal the compose limit."""
    from common.tools_registry import load_tools, tool_memory_gb

    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    registry = load_tools(root / "common" / "tools.yaml")
    checked = 0
    for name, entry in registry.items():
        if entry.get("status") != "integrated":
            continue
        service = compose["services"][f"worker-{name}"]
        limit = service["deploy"]["resources"]["limits"]["memory"].lower()
        gb = float(limit[:-1]) if limit.endswith("g") else float(limit[:-1]) / 1024
        assert tool_memory_gb(name, registry) == gb, f"{name}: tools.yaml {entry.get('memory_gb')} vs compose {limit}"
        checked += 1
    assert checked >= 10
    assert tool_memory_gb("no-such-tool", registry) == 1.0


def test_mark_unsupported_records_meta_and_plan_skips_it(tmp_path: Path):
    spec = ex.spec_from_dict(
        {"name": "scope", "tools": ["cbomkit", "cryptoscan"], "repetitions": 2, "repos": [{"full_name": "o/r"}]}
    )
    snap = ex.Snapshot(
        full_name="o/r",
        source_url="u",
        branch="main",
        commit="a" * 40,
        variant="original",
        variant_commit="a" * 40,
        scan_url="s",
        scan_branch="b",
        language="Go",
    )
    registry = {"cbomkit": {"languages": ["java", "python"]}, "cryptoscan": {"languages": ["go"]}}
    exp_dir = tmp_path / "scope"
    recorded = ex.mark_unsupported(spec, [snap], exp_dir, registry=registry)
    assert recorded == [("cbomkit", "o/r")]
    metas = sorted(exp_dir.glob("*/*/cbomkit/run*.meta.json"))
    assert len(metas) == 2
    assert json.loads(metas[0].read_text())["status"] == "unsupported"
    assert ex.mark_unsupported(spec, [snap], exp_dir, registry=registry) == []  # idempotent
    ids = iter(f"id{i}" for i in range(10))
    jobs, skipped = ex.plan_jobs(spec, [snap], exp_dir, new_id=lambda: next(ids))
    assert {j.tool for j in jobs} == {"cryptoscan"} and skipped == 2
    jobs, _ = ex.plan_jobs(spec, [snap], exp_dir, new_id=lambda: next(ids), retry_failed=True)
    assert {j.tool for j in jobs} == {"cryptoscan"}  # unsupported runs are never retried
    assert ex.summarize(exp_dir)["per_tool"] == {"cbomkit": {"unsupported": 2}}
