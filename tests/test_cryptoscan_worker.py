"""CryptoScan worker: panic bisection with `--exclude` and the recorded workaround (refinement 15).

The tool is not installed in the test environment; a fake executable panics (rc 2, Go stack trace, no output) when a
scanned file contains the marker PANIC and otherwise writes a CBOM naming the files it scanned, honouring --exclude.
"""

import importlib.util
import json
import sys
import time
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
        "patterns = args[args.index('--exclude') + 1].split(',') if '--exclude' in args else []\n"
        "def excluded(rel):\n"
        "    for p in patterns:\n"
        "        if p.endswith('/**') and (rel + '/').startswith(p[:-2]): return True\n"
        "        if '/' in p and rel == p: return True\n"
        "        if '/' not in p and os.path.basename(rel) == p: return True\n"
        "    return False\n"
        "kept = []\n"
        "for d, _, files in os.walk(root):\n"
        "    for f in files:\n"
        "        rel = os.path.relpath(os.path.join(d, f), root).replace(os.sep, '/')\n"
        "        if rel.startswith('.git/') or excluded(rel): continue\n"
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


def test_panic_is_bisected_to_the_file_and_excluded_in_the_rerun(tmp_path: Path, fake_cryptoscan):
    repo = _repo(
        tmp_path,
        {
            "README.md": "docs",
            "a/x.go": "clean",
            "a/b/bad.go": "x := \u00e9 PANIC",
            "a/b/ok.go": "clean",
            "c/ok.go": "clean",
            "c/d/e.go": "clean",
        },
    )
    out, trace = tmp_path / "cbom.json", Trace()
    excluded = cs.run_cryptoscan(repo, out, trace, timeout=60)
    assert excluded == ["a/b/bad.go"]
    cbom = json.loads(out.read_text())
    assert [c["name"] for c in cbom["components"]] == ["README.md", "a/b/ok.go", "a/x.go", "c/d/e.go", "c/ok.go"]
    notes = trace.text()
    assert "workaround: cryptoscan --exclude a/b/bad.go (Go panic in patterns.MatchWithContext" in notes
    assert "cryptoscan panicked: panic: runtime error: slice bounds out of range" in notes
    scans = (tmp_path / "scans.log").read_text().splitlines()
    assert len(scans) < 12  # bisection, not a scan per file


def test_several_culprits_including_a_top_level_file(tmp_path: Path, fake_cryptoscan):
    repo = _repo(
        tmp_path,
        {"BAD.md": "PANIC", "a/x.go": "clean", "a/bad.go": "PANIC", "z/ok.go": "clean", "z/y/bad2.go": "PANIC"},
    )
    out, trace = tmp_path / "cbom.json", Trace()
    assert cs.run_cryptoscan(repo, out, trace, timeout=60) == ["BAD.md", "a/bad.go", "z/y/bad2.go"]
    assert [c["name"] for c in json.loads(out.read_text())["components"]] == ["a/x.go", "z/ok.go"]


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


def test_bisection_respects_the_deadline(tmp_path: Path, fake_cryptoscan):
    repo = _repo(tmp_path, {"a/bad.go": "PANIC", "b/ok.go": "clean"})
    with pytest.raises(TimeoutError, match="cryptoscan_timeout"):
        cs.find_crashing_files(repo, tmp_path / "cbom.json", Trace(), deadline=time.monotonic() + 1)
