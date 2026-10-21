"""The stack board (misc/cli/stack_cmd.py) on a fake Redis: workers from their heartbeats, queues, experiments and the
remaining time; and the heartbeat it reads (common/worker.py)."""

import json
import time

import fakeredis
from rich.console import Console
from typer.testing import CliRunner

from common.jobs import JOB_ROUTING_KEY
from common.worker import publish_heartbeat
from misc.cli import stack_cmd
from misc.cli.cli import app

NOW = 1_800_000_000.0
SERVICES = ["redis", "coordinator", "runner", "worker-cbomkit", "worker-cdxgen", "worker-deepseek", "misc-pyqun"]


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _beat(r, name, state, **fields):
    r.set(f"worker:{name}:alive", json.dumps({"state": state, "ts": NOW, **fields}))


def _world():
    r = fakeredis.FakeRedis(decode_responses=True)
    _beat(r, "cbomkit", "busy", job_id="j1", mem_mb=15000.0, mem_limit_mb=16384.0,
          job={"repo": "apache/kafka@771b957", "run": 1, "started": NOW - 125},
          last={"repo": "jwtk/jjwt@1a2b3c4", "status": "ok", "duration_sec": 41.0})  # fmt: skip
    _beat(r, "cdxgen", "idle", mem_mb=300.0, mem_limit_mb=16384.0,
          last={"repo": "keycloak/keycloak@3c4d5e6", "status": "timeout", "duration_sec": 1800.0})  # fmt: skip
    _beat(r, "pyqun", "idle")
    _beat(r, "extra", "idle")  # a worker outside BOARD_SERVICES still shows
    r.rpush("jobs:cbomkit", "a", "b", "c")
    r.hset(JOB_ROUTING_KEY, "j1", "exp:e1-pilot-corpus")
    r.hset("exp:e1-pilot-corpus", mapping={"status": "running", "expected_jobs": "720", "skipped_jobs": "20",
           "stored_jobs": "392", "failed_jobs": "3", "wave": "1/1", "started_at": _iso(NOW - 3600)})  # fmt: skip
    r.hset("exp:e1-pilot-corpus:pending", "j1", "{}")
    r.hset("exp:old", mapping={"status": "completed", "finished_at": _iso(NOW - 86400)})
    return r


def _text(snap, history, width=160, height=0) -> str:
    console = Console(record=True, width=width, color_system=None)
    console.print(stack_cmd.render(snap, history, width, height))
    return console.export_text()


def test_board_shows_workers_queues_and_experiments_with_their_remaining_time():
    r = _world()
    snap = stack_cmd.snapshot(r, SERVICES, probe=lambda url: True, now=NOW)
    workers = {w["name"]: w for w in snap.workers}
    assert list(workers) == ["cbomkit", "cdxgen", "deepseek", "pyqun", "extra"]
    assert workers["cbomkit"]["origin"] == "e1-pilot-corpus" and workers["cbomkit"]["queued"] == 3
    assert workers["deepseek"]["beat"] is None
    (exp,) = snap.experiments  # exp:old finished a day ago, exp:<name>:pending is no status hash
    assert (exp["name"], exp["done"], exp["total"], exp["failed"]) == ("e1-pilot-corpus", 412, 720, 3)

    history = {}
    stack_cmd.observe(history, snap)
    text = _text(snap, history)
    assert "apache/kafka@771b957 run1 · e1-pilot-corpus" in text and "0:02:05" in text  # the job and how long
    assert "14.6/16.0" in text and "ok 0:00:41 jwtk/jjwt@1a2b3c4" in text and "timeout 0:30:00" in text
    (line,) = [line.rstrip() for line in text.splitlines() if line.startswith("e1-pilot-corpus")]
    assert "412/720" in line and "1/1" in line and "1:00:00" in line and line.endswith("–")  # no rate yet
    assert "1 busy · 3 idle · 1 down" in text and "redis ✓" in text and stack_cmd.FOOTER in text
    rows = [line.split()[0] for line in text.splitlines() if line.split() and line.split()[0] in workers]
    assert rows == ["cbomkit", "cdxgen", "extra", "pyqun", "deepseek"]  # busy, idle, down
    assert "rrxxnnuu" not in text and "rrxxnnuu" in _text(snap, history, width=140, height=50)  # the logo if it fits
    assert "rrxxnnuu" not in _text(snap, history, width=140, height=20)

    r.hset("exp:e1-pilot-corpus", "stored_jobs", "398")  # 6 more in a minute: 302 left at 0.1/s
    later = stack_cmd.snapshot(r, SERVICES, probe=lambda url: True, now=NOW + 60)
    stack_cmd.observe(history, later)
    assert "0:50:20" in _text(later, history)


def test_board_without_experiments_or_redis():
    r = fakeredis.FakeRedis(decode_responses=True)
    snap = stack_cmd.snapshot(r, SERVICES, probe=lambda url: False, now=NOW)
    text = _text(snap, {})
    assert "no experiment running" in text and "0 busy · 0 idle · 4 down" in text and "git server ✗" in text

    server = fakeredis.FakeServer()
    server.connected = False
    down = stack_cmd.snapshot(fakeredis.FakeRedis(server=server, decode_responses=True), SERVICES,
                              probe=lambda url: False, now=NOW)  # fmt: skip
    assert "Redis does not answer" in _text(down, {}) and "redis ✗" in _text(down, {})


def test_board_once_prints_one_snapshot(monkeypatch):
    r = _world()
    monkeypatch.setattr(stack_cmd.redis, "Redis", lambda **_: r)
    monkeypatch.setattr(stack_cmd, "_probe", lambda url: True)
    monkeypatch.setenv("BOARD_SERVICES", ",".join(SERVICES))
    result = CliRunner().invoke(app, ["stack", "board", "--once"])
    assert result.exit_code == 0, result.output
    assert "Workers" in result.output and "e1-pilot-corpus" in result.output and "deepseek" in result.output


def test_heartbeat_carries_job_memory_and_last_result():
    r = fakeredis.FakeRedis(decode_responses=True)
    job = {"repo": "o/r@abcdef0", "run": 2, "started": NOW}
    last = {"repo": "o/q", "status": "error", "duration_sec": 3.0, "finished_at": NOW}
    publish_heartbeat(r, "t", "busy", "j9", job=job, last=last)
    beat = json.loads(r.get("worker:t:alive"))
    assert (beat["state"], beat["job_id"], beat["job"], beat["last"]) == ("busy", "j9", job, last)
    assert {"mem_mb", "mem_limit_mb", "ts"} <= set(beat)  # None off cgroup v2 (a laptop), numbers in a container
    publish_heartbeat(r, "t", "idle")  # the UI's workers and misc/pyqun call it like this
    assert json.loads(r.get("worker:t:alive"))["job"] is None
