"""common/jobs.py: language scope and the status keys the UI derives from job hashes."""

from common.jobs import normalize_language, tool_supports
from common.utils import get_status_help, get_status_keys_order, status_text
from coordinator.redis_io import TERMINAL_JOB_STATUSES, job_status_key
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
