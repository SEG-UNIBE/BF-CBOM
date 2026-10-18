"""The CLI's human-readable experiment overview (misc/cli/overview.py): what `experiment run` prints at the end."""

import json

import click
import yaml

from common import experiment as ex
from misc.cli import overview


def _meta(exp_dir, repo, tool, status, run=1, **fields):
    snap = exp_dir / f"{repo.replace('/', '__')}@abcdef123456" / "original" / tool
    snap.mkdir(parents=True, exist_ok=True)
    meta = {"worker": tool, "status": status, "run_index": run, "variant": "original",
            "repo_info": {"full_name": repo}, **fields}  # fmt: skip
    (snap / f"run{run}.meta.json").write_text(json.dumps(meta))


def _experiment(tmp_path):
    exp_dir = tmp_path / "e-view"
    exp_dir.mkdir()
    spec = {"name": "e-view", "tools": ["t1", "t2", "llm"], "repetitions": 1,
            "repos": [{"full_name": "o/a", "git_url": "x"}, {"full_name": "o/b", "git_url": "x"}]}  # fmt: skip
    ex.spec_path(exp_dir).write_text(yaml.safe_dump(spec))
    gib = 1024.0
    _meta(exp_dir, "o/a", "t1", "ok", duration_sec=30, peak_memory_mb=3 * gib, memory_limit_mb=4 * gib)
    _meta(exp_dir, "o/b", "t1", "error", duration_sec=5, oom_kills=1,
          error="t1 cmd: scan .\nTraceback (most recent call last):\n  File x\nRuntimeError: tool crashed")  # fmt: skip
    _meta(exp_dir, "o/a", "t2", "timeout", duration_sec=1800, error=None)
    _meta(exp_dir, "o/b", "t2", "unsupported")
    usage = {"requests": 3, "input_tokens": 1_000_000, "cached_input_tokens": 0, "output_tokens": 100_000,
             "models": {"gpt-5": 3}}  # fmt: skip
    _meta(exp_dir, "o/a", "llm", "ok", duration_sec=60, usage=usage, started_at="2026-10-18T10:00:00Z")
    return exp_dir


def test_overview_counts_problems_usage_and_next_steps(tmp_path):
    ov = overview.build(_experiment(tmp_path))
    assert (ov.expected, ov.stored, ov.missing) == (6, 5, 1)
    assert dict(ov.counts) == {"ok": 2, "error": 1, "timeout": 1, "unsupported": 1}
    assert [(p.status, p.tool, p.text) for p in ov.problems] == [
        ("error", "t1", "RuntimeError: tool crashed"),
        ("timeout", "t2", "timed out after 30 min"),
    ]
    session = {"started_at": "2026-10-18T10:00:00Z", "finished_at": "2026-10-18T11:05:00Z", "results_stored": 5,
               "host": {"ram_total_mb": 64 * 1024}, "ram_peak_used_mb": 40 * 1024, "memory_budget_gb": 52,
               "waves": [["t1"], ["t2"]]}  # fmt: skip
    text = "\n".join(click.unstyle(line) for line in overview.render(
        ov, outcome="completed", session=session, spec_file="experiments/e-view.yaml"))  # fmt: skip
    assert "e-view: completed, 5 of 6 runs stored" in text
    assert "1 h 05 min, 5 results stored, VM RAM peak 40.0 GiB of 64.0 GiB, budget 52 GiB in 2 wave(s)" in text
    assert "ok 2   error 1   timeout 1   unsupported 1   missing 1" in text
    t1 = next(line for line in text.splitlines() if line.startswith("t1 "))
    assert t1.split() == ["t1", "2", "1", "1", "0", "0", "–", "18", "s", "30", "s", "3.0", "/", "4", "GiB", "1"]
    assert "llm: 3 requests, 1.0M input (0 cached) / 100k output tokens, about $2.25" in text
    assert "o/b  RuntimeError: tool crashed" in text
    assert "make experiment EXP=experiments/e-view.yaml ARGS=--retry-failed   redo the 2 run(s) not ok" in text
    assert "make experiment EXP=experiments/e-view.yaml" in text and "resume the 1 missing run(s)" in text
    assert "make experiment-export NAME=e-view" in text


def test_plain_cells_carry_no_escape_codes(tmp_path):
    lines = overview.render(overview.build(_experiment(tmp_path)))
    t2 = next(line for line in lines if line.startswith("t2 "))
    assert t2.startswith("t2    ") and "\x1b" in t2  # coloured counts only
    assert not overview._style("plain").startswith("\x1b")


def test_live_progress_for_status(tmp_path):
    ov = overview.build(_experiment(tmp_path))
    live = {"status": "running", "wave": "1/2", "queued": 4,
            "running": [{"tool": "t1", "repo": "o/b", "started_at": "2026-10-18T10:00:00Z"}]}  # fmt: skip
    text = "\n".join(click.unstyle(line) for line in overview.render(ov, live=live))
    assert "e-view: running, 5 of 6 runs stored" in text and "4 queued, 1 running, wave 1/2" in text
    assert "running t1" in text and "make experiment-status NAME=e-view" in text and "--retry-failed" not in text


def test_list_rows(tmp_path):
    rows = [click.unstyle(r) for r in overview.list_rows([overview.build(_experiment(tmp_path))])]
    assert rows[0].split()[:3] == ["experiment", "stored", "expected"]
    assert rows[1].split()[:7] == ["e-view", "5", "6", "2", "2", "1", "–"]
