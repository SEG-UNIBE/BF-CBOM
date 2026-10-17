"""CipherRadar worker: crash-trigger prefilter (no cradar binary needed)."""

import importlib.util
from pathlib import Path

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
