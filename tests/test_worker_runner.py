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
    assert res.started_at and res.finished_at and res.started_at <= res.finished_at  # UTC ISO, set by the parent


def test_trace_fields_usage_reaches_the_result_on_success_and_on_error():
    from common.models import Trace

    def produce_ok(instr, trace: Trace):
        trace.fields["usage"] = {"requests": 2, "input_tokens": 10}
        return '{"ok": true}'

    def produce_fail(instr, trace: Trace):
        trace.fields["usage"] = {"requests": 1, "requests_failed": 1}
        raise RuntimeError("api down")

    assert build_handle_instruction("t", produce_ok)(INSTR).usage == {"requests": 2, "input_tokens": 10}
    failed = build_handle_instruction("t", produce_fail)(INSTR)
    assert failed.status == "error" and failed.usage == {"requests": 1, "requests_failed": 1}
    assert JobResult.from_json(failed.to_json()).usage == failed.usage  # survives the pipe to the parent


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
    run_job_with_timeout(
        lambda i: (time.sleep(2.2), _ok(i))[1], INSTR, 10, worker_name="t", on_tick=lambda: ticks.append(1)
    )
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


def test_build_handle_instruction_keeps_trace_as_notes_on_success():
    def _ok_with_note(instr, trace):
        trace.add("fallback: reran without --evidence")
        return '{"components": []}'

    res = build_handle_instruction("t", _ok_with_note)(INSTR)
    assert res.status == "ok"
    assert res.notes == "fallback: reran without --evidence"
    assert res.error is None


def test_job_instruction_carries_run_index():
    instr = JobInstruction(job_id="j", tool="t", repo_info=RI, run_index=3)
    assert JobInstruction.from_json(instr.to_json()).run_index == 3
    legacy = '{"job_id":"j","tool":"t","repo_info":' + RI.to_json() + "}"
    assert JobInstruction.from_json(legacy).run_index is None


def test_worker_gives_up_on_redis_after_the_grace_period(monkeypatch):
    """A worker that cannot reach Redis for REDIS_GIVE_UP_SEC exits non-zero, so the container's restart policy
    brings a fresh process instead of a zombie without heartbeat (common/worker.py:redis_error)."""
    import logging

    import pytest

    from common import worker

    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    log = logging.getLogger("test-worker")
    err = RuntimeError("Temporary failure in name resolution")
    since = worker.redis_error(log, err, None, now=100.0)
    assert since == 100.0
    assert worker.redis_error(log, err, since, now=100.0 + worker.REDIS_GIVE_UP_SEC - 1) == 100.0
    with pytest.raises(SystemExit) as exit_info:
        worker.redis_error(log, err, since, now=100.0 + worker.REDIS_GIVE_UP_SEC)
    assert exit_info.value.code == 1
