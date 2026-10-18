"""Memory measurement (common/memory.py), the worker's per-job resources and the runner's session log."""

import json

from common import memory
from common.models import JobInstruction, JobResult, RepoInfo
from common.worker import run_job_with_timeout

MIB = 1024 * 1024


def _cgroup(d, *, current, inactive=0, limit="max", oom=0):
    d.mkdir(exist_ok=True)
    (d / "memory.current").write_text(f"{current}\n")
    (d / "memory.stat").write_text(f"anon 1\nactive_file 2\ninactive_file {inactive}\n")
    (d / "memory.max").write_text(f"{limit}\n")
    (d / "memory.events").write_text(f"low 0\nhigh 0\nmax 3\noom 1\noom_kill {oom}\n")
    return d


def test_working_set_is_current_minus_inactive_file_like_docker_stats(tmp_path):
    cg = _cgroup(tmp_path / "cg", current=900 * MIB, inactive=100 * MIB, limit=4096 * MIB, oom=2)
    assert memory.working_set_bytes(cg) == 800 * MIB
    assert memory.limit_bytes(cg) == 4096 * MIB and memory.oom_kills(cg) == 2
    assert memory.limit_bytes(_cgroup(tmp_path / "unlimited", current=1)) is None


def test_job_sampler_keeps_the_peak_and_counts_oom_kills_during_the_job(tmp_path):
    cg = _cgroup(tmp_path / "cg", current=200 * MIB, limit=1024 * MIB, oom=1)
    sampler = memory.JobSampler(cg)
    _cgroup(cg, current=700 * MIB, limit=1024 * MIB, oom=1)
    sampler.sample()
    _cgroup(cg, current=300 * MIB, limit=1024 * MIB, oom=2)
    assert sampler.result() == {"peak_memory_mb": 700.0, "memory_limit_mb": 1024.0, "oom_kills": 1}


def test_without_cgroup_files_everything_is_none(tmp_path):
    assert memory.JobSampler(tmp_path / "missing").result() == {
        "peak_memory_mb": None,
        "memory_limit_mb": None,
        "oom_kills": None,
    }
    assert memory.host_memory(tmp_path / "meminfo") == {}


def test_host_sampler_reads_meminfo(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       20480000 kB\nMemFree:  1 kB\nMemAvailable:   10240000 kB\n")
    host = memory.HostSampler(meminfo)
    assert (host.total_mb, host.peak_used_mb) == (20000.0, 10000.0)
    meminfo.write_text("MemTotal:       20480000 kB\nMemAvailable:    2048000 kB\n")
    host.sample()
    meminfo.write_text("MemTotal:       20480000 kB\nMemAvailable:   15360000 kB\n")
    host.sample()
    assert host.peak_used_mb == 18000.0


def _ok(instr: JobInstruction) -> JobResult:
    return JobResult(job_id=instr.job_id, status="ok", repo_info=instr.repo_info, json="{}", worker="t")


def test_every_job_result_carries_the_containers_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "CGROUP_DIR", _cgroup(tmp_path / "cg", current=64 * MIB, limit=512 * MIB))
    instr = JobInstruction(
        job_id="j1", tool="t", repo_info=RepoInfo(full_name="o/r", git_url="u", branch="main", size_kb=1)
    )
    res = run_job_with_timeout(_ok, instr, 5, worker_name="t")
    assert (res.peak_memory_mb, res.memory_limit_mb, res.oom_kills) == (64.0, 512.0, 0)
    assert json.loads(res.to_json())["peak_memory_mb"] == 64.0


def test_runner_session_is_written_to_the_run_log(tmp_path, monkeypatch):
    from common.experiment import load_run_log
    from misc.cli import experiment_cmd

    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 2048000 kB\nMemAvailable: 1024000 kB\n")
    monkeypatch.setattr(memory, "MEMINFO", meminfo)
    session = experiment_cmd._Session(tmp_path, "run", memory_budget_gb=14.0, waves=[["a"], ["b"]])
    meminfo.write_text("MemTotal: 2048000 kB\nMemAvailable: 512000 kB\n")
    session.tick()
    session.finish("completed", results_stored=3)
    (entry,) = load_run_log(tmp_path)
    assert entry["status"] == "completed" and entry["results_stored"] == 3 and entry["finished_at"]
    assert entry["host"]["ram_total_mb"] == 2000.0 and entry["ram_peak_used_mb"] == 1500.0
    assert entry["memory_budget_gb"] == 14.0 and entry["waves"] == [["a"], ["b"]]
    experiment_cmd._Session(tmp_path, "collect").finish("completed")
    assert [e["command"] for e in load_run_log(tmp_path)] == ["run", "collect"]
