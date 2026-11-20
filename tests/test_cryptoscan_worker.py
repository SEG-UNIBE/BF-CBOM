"""CryptoScan worker: one run as released; a crash fails the run with the panic head in the error.

The tool is not installed in the test environment; a fake executable panics (rc 2, Go stack trace, no output) when a
scanned file contains the marker PANIC and otherwise writes a CBOM naming the files it scanned.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from common.models import Trace

_spec = importlib.util.spec_from_file_location(
    "cryptoscan_worker", Path(__file__).resolve().parents[1] / "workers" / "cryptoscan" / "main.py"
)
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

PANIC = (
    "panic: runtime error: slice bounds out of range [:300] with length 257\n\n"
    "goroutine 42 [running]:\n"
    "github.com/csnp/cryptoscan/pkg/patterns.(*Matcher).MatchWithContext(0xc000440700?, {0xc000440700, 0x6b})\n"
    "\t/home/runner/work/cryptoscan/cryptoscan/pkg/patterns/matcher.go:135 +0x94a\n"
    "github.com/csnp/cryptoscan/pkg/scanner.(*Scanner).scanFile(0xc00031c280, {0xc0003569a0, 0x64})\n"
    "\t/home/runner/work/cryptoscan/cryptoscan/pkg/scanner/scanner.go:600 +0x6cf\n"
)


@pytest.fixture
def fake_cryptoscan(tmp_path, monkeypatch):
    script = tmp_path / "fake-cryptoscan"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "root = args[1]\n"
        "out = args[args.index('--output') + 1]\n"
        "kept = []\n"
        "for d, _, files in os.walk(root):\n"
        "    for f in files:\n"
        "        rel = os.path.relpath(os.path.join(d, f), root).replace(os.sep, '/')\n"
        "        if rel.startswith('.git/'): continue\n"
        "        kept.append(rel)\n"
        "with open(os.path.join(root, '..', 'scans.log'), 'a') as fh: fh.write(' '.join(sorted(kept)) + '\\n')\n"
        "for rel in kept:\n"
        "    if 'PANIC' in open(os.path.join(root, rel), encoding='utf-8', errors='replace').read():\n"
        f"        sys.stderr.write({PANIC!r}); sys.exit(2)\n"
        "json.dump({'bomFormat': 'CycloneDX', 'components': [{'name': r} for r in sorted(kept)]}, open(out, 'w'))\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(cs, "BINARY", str(script))
    return script


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    (repo / ".git").mkdir()
    return repo


def test_a_clean_scan_writes_the_tools_cbom(tmp_path: Path, fake_cryptoscan):
    repo = _repo(tmp_path, {"README.md": "docs", "a/x.go": "clean"})
    out, trace = tmp_path / "cbom.json", Trace()
    cs.run_cryptoscan(repo, out, trace, timeout=60)
    assert [c["name"] for c in json.loads(out.read_text())["components"]] == ["README.md", "a/x.go"]
    assert "--include-quantum-safe" in trace.text() and "--exclude" not in trace.text()


def test_a_panic_fails_the_run_after_one_scan(tmp_path: Path, fake_cryptoscan):
    # the tool's crash is the result: no retry, no file excluded
    repo = _repo(tmp_path, {"a/x.go": "clean", "a/b/bad.go": "x := \u00e9 PANIC"})
    with pytest.raises(RuntimeError, match="cryptoscan_panic rc=2: panic: runtime error: slice bounds out of range"):
        cs.run_cryptoscan(repo, tmp_path / "cbom.json", Trace(), timeout=60)
    assert len((tmp_path / "scans.log").read_text().splitlines()) == 1


def test_a_failure_without_panic_is_the_tools_error(tmp_path: Path, monkeypatch):
    script = tmp_path / "failing"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nprint('Error: cannot read config', file=sys.stderr)\nsys.exit(2)\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(cs, "BINARY", str(script))
    repo = _repo(tmp_path, {"a.go": "clean"})
    with pytest.raises(RuntimeError, match="cryptoscan_no_output rc=2: Error: cannot read config"):
        cs.run_cryptoscan(repo, tmp_path / "cbom.json", Trace(), timeout=30)


def test_error_excerpt_keeps_the_panic_head_and_the_tail():
    text = cs.error_excerpt(PANIC + "\n".join(f"frame {i}" for i in range(20)), tail_lines=3)
    lines = text.splitlines()
    assert lines[0].startswith("panic: runtime error") and lines[1] == "goroutine 42 [running]:"
    assert lines[-3:] == ["frame 17", "frame 18", "frame 19"]
    assert cs.panicked(PANIC) and not cs.panicked("Error: no patterns\n")
