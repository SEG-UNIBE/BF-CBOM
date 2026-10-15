"""Tests for the subprocess job runner in common/worker.py (no Redis needed)."""

import os
import subprocess
import time

from common.models import JobInstruction, JobResult, RepoInfo
from common.worker import build_handle_instruction, run_job_with_timeout

RI = RepoInfo(full_name="o/r", git_url="https://github.com/o/r.git", branch="main", size_kb=1)
INSTR = JobInstruction(job_id="job-0001", tool="t", repo_info=RI)


def _ok(instr: JobInstruction) -> JobResult:
    return JobResult(job_id=instr.job_id, status="ok", repo_info=instr.repo_info, json='{"x":1}', worker="t")


def _hang(instr: JobInstruction) -> JobResult:
    subprocess.Popen(["sleep", "120"])  # grandchild must die with the job
    time.sleep(120)
    return _ok(instr)


def _crash(instr: JobInstruction) -> JobResult:
    os._exit(3)


def _raise(instr: JobInstruction) -> JobResult:
    raise ValueError("boom")


def _sleeping_grandchildren() -> set[str]:
    out = subprocess.run(["pgrep", "-f", "^sleep 120$"], capture_output=True, text=True).stdout
    return set(out.split())


def test_result_is_returned_from_subprocess():
    res = run_job_with_timeout(_ok, INSTR, 5, worker_name="t")
    assert (res.status, res.json) == ("ok", '{"x":1}')


def test_timeout_kills_job_and_its_children():
    before = _sleeping_grandchildren()
    t0 = time.monotonic()
    res = run_job_with_timeout(_hang, INSTR, 1.5, worker_name="t")
    assert res.status == "timeout"
    assert time.monotonic() - t0 < 10  # not the 120s the job wanted
    time.sleep(0.5)
    assert _sleeping_grandchildren() <= before


def test_crashing_job_process_becomes_error_result():
    res = run_job_with_timeout(_crash, INSTR, 5, worker_name="t")
    assert res.status == "error"
    assert "exited with code 3" in (res.error or "")


def test_exception_in_handler_becomes_error_result():
    res = run_job_with_timeout(_raise, INSTR, 5, worker_name="t")
    assert res.status == "error"
    assert "boom" in (res.error or "")


def test_on_tick_is_called_while_running():
    ticks = []
    run_job_with_timeout(lambda i: (time.sleep(2.2), _ok(i))[1], INSTR, 10, worker_name="t", on_tick=lambda: ticks.append(1))
    assert len(ticks) >= 2


def test_build_handle_instruction_maps_outcomes():
    handle = build_handle_instruction("t", lambda instr, trace: '{"components": []}')
    assert handle(INSTR).status == "ok"
    handle = build_handle_instruction("t", lambda instr, trace: "not json")
    assert handle(INSTR).status == "error"

    def _timeout(instr, trace):
        trace.add("tool took too long")
        raise TimeoutError("tool_timeout")

    res = build_handle_instruction("t", _timeout)(INSTR)
    assert res.status == "timeout"
    assert "tool took too long" in res.error
