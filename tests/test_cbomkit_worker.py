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
