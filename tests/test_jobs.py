"""common/jobs.py: language scope, routing, and the status keys the UI derives from job hashes."""

import json

import fakeredis
import pytest
import redis

from common import worker
from common.jobs import JOB_ROUTING_KEY, job_is_routed, normalize_language, tool_supports
from common.models import JobInstruction, RepoInfo
from common.utils import get_status_help, get_status_keys_order, status_text
from coordinator.redis_io import TERMINAL_JOB_STATUSES, delete_inspection, job_status_key
from coordinator.utils import derive_status_key_from_payload, summarize_result_cell

REG = {"cbomkit": {"languages": ["java", "python"]}, "cryptoscan": {"languages": ["java", "c", "cpp"]}, "llm": {}}


def test_normalize_language_aliases():
    assert normalize_language("Java") == "java"
    assert normalize_language("C++") == "cpp"
    assert normalize_language("C#") == "csharp"
    assert normalize_language(None) == ""


def test_tool_supports_uses_registry_scope_and_is_permissive_without_data():
    assert tool_supports("cbomkit", "Java", REG)
    assert not tool_supports("cbomkit", "Go", REG)
    assert tool_supports("cryptoscan", "C++", REG)
    assert tool_supports("llm", "Go", REG)  # no languages listed -> run
    assert tool_supports("cbomkit", None, REG)  # unknown repo language -> run
    assert tool_supports("not-registered", "Go", REG)


def test_job_status_keys_cover_every_state():
    assert job_status_key({"status": "held"}) == "held"
    assert job_status_key({"status": "fired"}) == "queued"
    assert job_status_key({}) == "queued"
    assert job_status_key({"status": "running"}) == "running"
    assert job_status_key({"status": "completed"}) == "completed"
    assert job_status_key({"status": "failed", "worker_status": "timeout"}) == "timeout"
    assert job_status_key({"status": "failed", "worker_status": "error"}) == "failed"
    assert job_status_key({"status": "cancelled"}) == "cancelled"
    assert job_status_key({"status": "unsupported"}) == "unsupported"
    assert "unsupported" in TERMINAL_JOB_STATUSES and "running" not in TERMINAL_JOB_STATUSES
    keys = set(get_status_keys_order())
    assert keys == {"completed", "failed", "timeout", "cancelled", "unsupported", "running", "queued", "held"}
    assert all(status_text(k, "label") and get_status_help(k) for k in keys)
    assert derive_status_key_from_payload({"status": "unsupported"}) == "unsupported"
    assert derive_status_key_from_payload(None) == "queued"


def test_result_cells_render_live_states():
    assert summarize_result_cell({"status": "held"}).startswith("⏸️")
    assert summarize_result_cell({"status": "fired"}) == "💤"
    assert summarize_result_cell({"status": "running", "started_at": "2000-01-01T00:00:00Z"}).startswith("⚙️ ")
    assert "Unsupported" in summarize_result_cell({"status": "unsupported"})


def _instr(job_id):
    ri = RepoInfo(full_name="o/r", git_url="https://github.com/o/r.git", branch="main", size_kb=1)
    return JobInstruction(job_id=job_id, tool="t", repo_info=ri).to_json()


def test_a_worker_skips_a_job_no_longer_routed_and_a_redis_blip_drops_nothing(monkeypatch):
    class Down:
        def hexists(self, *_):
            raise redis.exceptions.ConnectionError("down")

    class Stop(Exception):
        pass

    def run(handle, instr, *args, **kwargs):
        ran.append(instr.job_id)
        raise Stop

    r = fakeredis.FakeRedis(decode_responses=True)
    r.hset(JOB_ROUTING_KEY, "routed", "exp:e")
    r.rpush("jobs:t", _instr("cancelled"), _instr("routed"))  # cancelled: its routing entry went, the job stayed
    assert job_is_routed(r, "routed") and not job_is_routed(r, "cancelled")
    assert job_is_routed(Down(), "cancelled")
    ran = []
    monkeypatch.setattr(worker.redis, "Redis", lambda **_: r)
    monkeypatch.setattr(worker, "run_job_with_timeout", run)
    with pytest.raises(Stop):
        worker.run_worker("t", lambda instr: None)
    assert ran == ["routed"] and not r.llen("jobs:t")


def test_deleting_an_inspection_takes_its_queued_jobs_out_of_the_worker_queues():
    r = fakeredis.FakeRedis(decode_responses=True)
    r.hset("insp:i", "workers_json", json.dumps(["t"]))
    r.rpush("insp:i:jobs", "a", "b")
    r.hset(JOB_ROUTING_KEY, mapping={"a": "i", "b": "i", "c": "other"})
    r.rpush("jobs:t", _instr("a"), _instr("c"))
    assert delete_inspection(r, "i") == 2
    assert [json.loads(x)["job_id"] for x in r.lrange("jobs:t", 0, -1)] == ["c"]
    assert r.hgetall(JOB_ROUTING_KEY) == {"c": "other"}


def test_tool_supports_narrows_to_a_specs_tool_languages():
    reg = {"cbomkit": {"languages": ["java", "python", "go"]}, "xgrep": {}}
    assert tool_supports("cbomkit", "Python", reg) and not tool_supports("cbomkit", "Python", reg, only=["go"])
    assert tool_supports("cbomkit", "Go", reg, only=["Go"]) and tool_supports("cbomkit", None, reg, only=["go"])
    assert tool_supports("xgrep", "Go", reg, only=["go"]) and not tool_supports("xgrep", "Java", reg, only=["go"])
    assert not tool_supports("cbomkit", "Rust", reg, only=["rust"])  # the declared support still bounds it
