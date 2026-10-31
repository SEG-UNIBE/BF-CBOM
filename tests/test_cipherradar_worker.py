"""CipherRadar worker: crash-trigger prefilter, crashes as results (no cradar binary needed)."""

import importlib.util
import subprocess
import time
from pathlib import Path

import pytest

from common.models import Trace

_SPEC = importlib.util.spec_from_file_location(
    "cipherradar_main", Path(__file__).resolve().parents[1] / "workers" / "cipherradar" / "main.py"
)
cw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cw)


def test_prefilter_lists_only_files_with_the_crash_trigger(tmp_path: Path):
    src = tmp_path / "src" / "main" / "java" / "x"
    src.mkdir(parents=True)
    (src / "Crash.java").write_text("class A { Object o = new ECGenParameterSpec(curveName); }\n")
    (src / "CrashArg.java").write_text("class B { void f() { g(new ECGenParameterSpec( name , 1)); } }\n")
    (src / "Literal.java").write_text('class C { Object o = new ECGenParameterSpec("secp256r1"); }\n')
    (src / "Call.java").write_text("class D { Object o = new ECGenParameterSpec(getName()); }\n")
    (tmp_path / "notes.txt").write_text("new ECGenParameterSpec(curveName)\n")
    assert cw.prefilter_crashers(tmp_path) == [
        "src/main/java/x/Crash.java",
        "src/main/java/x/CrashArg.java",
    ]


def _crashing_java_repo(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "Crash.java").write_text("class A { Object o = new ECGenParameterSpec(curveName); }\n")
    crash = "scanning src/Crash.java\npanic: runtime error: invalid memory address or nil pointer dereference\n"
    monkeypatch.setattr(cw.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, "", crash))
    return repo


def test_a_crash_fails_the_run_and_nothing_is_excluded_by_default(tmp_path: Path, monkeypatch):
    repo = _crashing_java_repo(tmp_path, monkeypatch)
    assert cw.EXCLUDE_CRASHERS is False
    with pytest.raises(RuntimeError, match="cradar_no_output rc=2"):
        cw.run_cradar(repo, tmp_path / "out.json", tmp_path, Trace(), deadline=time.monotonic() + 60)
    assert not (repo / ".cradarignore").exists()


def test_the_switch_excludes_the_files_with_the_crash_trigger(tmp_path: Path, monkeypatch):
    repo = _crashing_java_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(cw, "EXCLUDE_CRASHERS", True)
    with pytest.raises(RuntimeError, match="cradar_no_output"):  # the fake crashes on every attempt
        cw.run_cradar(repo, tmp_path / "out.json", tmp_path, Trace(), deadline=time.monotonic() + 60)
    assert (repo / ".cradarignore").read_text() == "src/Crash.java\n"
