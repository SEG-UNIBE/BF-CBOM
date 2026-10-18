"""The batch runner end to end on a fake Redis (misc/cli/experiment_cmd.py): a fake worker thread drains the job
queues like common/worker.py would, the runner enqueues, collects, stores, resumes, retries and exports."""

import json
import subprocess
import threading
import time
import zipfile

import fakeredis
import pytest
import yaml
from typer.testing import CliRunner

from common import experiment as ex
from common.models import JobInstruction, JobResult
from common.worker import publish_heartbeat
from misc.cli import experiment_cmd

TOOLS = ["t1", "t2"]


class FakeWorkers(threading.Thread):
    """BLPOPs `jobs:<tool>` for every tool, answers with an ok CBOM (t2 fails on the second repo), publishes
    heartbeats, pushes JobResults to `results:<tool>` as a worker does."""

    def __init__(self, r):
        super().__init__(daemon=True)
        self.r, self.stop, self.seen = r, threading.Event(), []

    def run(self):
        while not self.stop.is_set():
            for tool in TOOLS:
                publish_heartbeat(self.r, tool, "idle")
            item = self.r.blpop([f"jobs:{t}" for t in TOOLS], timeout=1)
            if item is None:
                continue
            queue, raw = item
            tool = queue.split(":", 1)[1]
            instr = JobInstruction.from_json(raw)
            self.seen.append((tool, instr.repo_info.full_name))
            failing = tool == "t2" and instr.repo_info.full_name.endswith("/second")
            result = JobResult(
                job_id=instr.job_id,
                status="error" if failing else "ok",
                repo_info=instr.repo_info,
                json="{}"
                if failing
                else json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": []}),
                duration_sec=0.01,
                size_bytes=2,
                worker=tool,
                tool_version=f"{tool} 1.0",
                run_index=instr.run_index,
                error="boom" if failing else None,
            )
            self.r.rpush(f"results:{tool}", result.to_json())


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two local upstream repos, a results dir, a mirror dir, a fake Redis and the runner pointed at all of them."""
    repos = {}
    for name in ("first", "second"):
        src = tmp_path / "upstream" / name
        src.mkdir(parents=True)
        (src / "a.py").write_text("import hashlib\n")
        _git("init", "-q", "-b", "main", cwd=src)
        _git("-c", "user.name=t", "-c", "user.email=t@t", "add", ".", cwd=src)
        _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=src)
        repos[name] = src
    spec = tmp_path / "e.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "name": "e-fake",
                "tools": TOOLS,
                "repetitions": 1,
                "repos": [{"full_name": f"o/{n}", "git_url": str(p), "language": "Python"} for n, p in repos.items()],
            }
        )
    )
    results, mirrors = tmp_path / "results", tmp_path / "mirrors"
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(experiment_cmd, "_redis", lambda host, port: r)
    monkeypatch.setattr(experiment_cmd, "experiment_dir", lambda name: results / name)
    monkeypatch.setattr(ex, "RESULTS_DIR", results)
    monkeypatch.setattr(experiment_cmd, "RESULTS_DIR", results)
    real_resolve = experiment_cmd.resolve_snapshots
    monkeypatch.setattr(
        experiment_cmd,
        "resolve_snapshots",
        lambda spec, exp_dir, **kw: real_resolve(spec, exp_dir, mirror_dir=mirrors, server_url="http://gs", **kw),
    )
    monkeypatch.setenv("BF_CBOM_COMMIT", "abc123def456")
    monkeypatch.setenv("EXPERIMENT_IMAGE_IDS", "bf-cbom/worker-t1:1.0.3=sha256:" + "1" * 64)
    workers = FakeWorkers(r)
    workers.start()
    yield {"spec": spec, "results": results, "redis": r, "workers": workers}
    workers.stop.set()
    workers.join(timeout=3)


def _run(args):
    return CliRunner().invoke(experiment_cmd.experiment_app, args, catch_exceptions=False)


def test_run_stores_results_resumes_retries_and_exports(world, tmp_path):
    spec, results = world["spec"], world["results"]
    t0 = time.monotonic()
    result = _run(["run", str(spec), "--idle-timeout", "20", "--memory-budget-gb", "1"])
    assert result.exit_code == 0, result.output
    exp = results / "e-fake"
    metas = sorted(p.relative_to(exp).as_posix() for p in exp.glob("*/*/*/run1.meta.json"))
    assert len(metas) == 4 and (exp / "_experiment.yaml").exists() and (exp / "lock.json").exists()
    statuses = {(p.parents[2].name.split("@")[0], p.parent.name): json.loads(p.read_text())["status"]
                for p in exp.glob("*/*/*/run1.meta.json")}  # fmt: skip
    assert statuses[("o__second", "t2")] == "error" and sum(s == "ok" for s in statuses.values()) == 3
    meta = json.loads(next(exp.glob("o__first@*/original/t1/run1.meta.json")).read_text())
    assert meta["enqueued_at"] and meta["tool_version"] == "t1 1.0" and meta["variant"] == "original"
    (session,) = ex.load_run_log(exp)
    assert session["status"] == "completed" and session["results_stored"] == 4 and session["command"] == "run"
    assert session["runner_commit"] == "abc123def456" and session["images"] == {"t1": "sha256:" + "1" * 64}
    assert session["memory_budget_gb"] == 1.0 and len(session["waves"]) == 2  # two unknown tools at 1 GiB each
    assert "e-fake: completed, 4 of 4 runs stored" in result.output and "ok 3   error 1" in result.output
    assert "o/second  boom" in result.output and "ARGS=--retry-failed   redo the 1 run(s) not ok" in result.output
    assert time.monotonic() - t0 < 60

    # resume: everything on disk, nothing enqueued
    again = _run(["run", str(spec), "--idle-timeout", "5"])
    assert again.exit_code == 0 and "4 run(s) already on disk" in again.output and len(world["workers"].seen) == 4
    assert len(ex.load_run_log(exp)) == 1  # no session without work

    # retry-failed redoes only the failed run
    retry = _run(["run", str(spec), "--idle-timeout", "20", "--retry-failed"])
    assert retry.exit_code == 0 and "1 to enqueue" in retry.output and len(world["workers"].seen) == 5
    assert len(ex.load_run_log(exp)) == 2

    # export zips the layout without touching the volume
    dest = tmp_path / "out"
    exported = _run(["export", "e-fake", "--dest", str(dest), "--no-report"])
    assert exported.exit_code == 0, exported.output
    names = zipfile.ZipFile(dest / "e-fake.zip").namelist()
    assert "e-fake/_experiment.yaml" in names and "e-fake/_run-log.json" in names and "e-fake/lock.json" in names
    assert sum(n.endswith("run1.json") for n in names) == 4


def test_parse_replicas():
    assert experiment_cmd.parse_replicas("cryptoscan=3, xgrep=2,bad,one=1") == {"cryptoscan": 3, "xgrep": 2}
    assert experiment_cmd.parse_replicas(None) == {} and experiment_cmd.parse_replicas("") == {}


def test_list_shows_experiments_and_folders_in_an_older_layout(tmp_path, monkeypatch):
    results = tmp_path / "results"
    (results / "e-new").mkdir(parents=True)
    (results / "e-new" / ex.SPEC_FILE).write_text("name: e-new\n")
    (results / "e-old").mkdir()
    (results / "e-old" / "experiment.yaml").write_text("name: e-old\n")
    (results / "corpus").mkdir()
    monkeypatch.setattr(experiment_cmd, "RESULTS_DIR", results)
    out = _run(["list"]).output
    lines = out.splitlines()
    assert lines[0].split()[0] == "experiment" and lines[1].split()[0] == "e-new"
    assert "make experiment-delete NAME=e-old" in out and "corpus" not in out
