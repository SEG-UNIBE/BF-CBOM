"""Pure helpers of workers/cbomkit/main.py (loaded by path: workers are scripts, not a package)."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("websocket")

_spec = importlib.util.spec_from_file_location(
    "cbomkit_worker_main", Path(__file__).resolve().parents[1] / "workers" / "cbomkit" / "main.py"
)
cbomkit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cbomkit)


def test_parse_progress_recognises_cbomkit_messages():
    assert cbomkit.parse_progress('{"type":"LABEL","message":"Finished"}') == ("LABEL", "Finished")
    assert cbomkit.parse_progress('{"type":"ERROR","message":"Git clone failed: Revision not found: master"}') == (
        "ERROR",
        "Git clone failed: Revision not found: master",
    )
    assert cbomkit.parse_progress('{"type":"BRANCH","message":"main"}') == ("BRANCH", "main")


def test_parse_progress_tolerates_non_progress_payloads():
    assert cbomkit.parse_progress("not json") == ("", "")
    assert cbomkit.parse_progress("[1, 2]") == ("", "")
    assert cbomkit.parse_progress('{"message": "no type"}') == ("", "no type")


def test_a_timeout_restarts_the_backend_and_the_restart_wait_comes_off_the_scan_budget(monkeypatch):
    # a job's timeout kills only the worker's process group: the backend's scan would go on beside the next job's
    calls = []

    class FakeClient:
        def wait_restarted(self, max_wait):
            calls.append(("wait", max_wait))
            monkeypatch.setattr(cbomkit.time, "monotonic", lambda: 1000.0 + 30.0)  # the restart took 30 s

        def generate_cbom(self, git_url, branch, commit, budget):
            calls.append(("scan", round(budget)))
            return None, budget, "ws_not_finished"

    monkeypatch.setattr(cbomkit.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(cbomkit, "CbomKitClient", FakeClient)
    monkeypatch.setattr(cbomkit, "stop_backend", lambda: calls.append(("stop",)))
    monkeypatch.setattr(cbomkit, "TIMEOUT_SEC", 3600)
    repo = type("Repo", (), {"full_name": "o/r", "git_url": "https://gitserver/o/r.git", "branch": "main",
                             "commit": "a" * 40})()  # fmt: skip
    instr = type("Instr", (), {"repo_info": repo})()
    with pytest.raises(TimeoutError, match="ws_not_finished"):
        cbomkit._produce(instr, cbomkit.Trace())
    assert calls == [("wait", cbomkit.RESTART_WAIT_SEC), ("scan", 3600 - 5 - 30), ("stop",)]
