"""The stack board (misc/cli/stack_cmd.py) on a fake Redis: workers from their heartbeats, queues, experiments and the
remaining time (misc/cli/progress.py:forecast); and the heartbeat it reads (common/worker.py)."""

import json
import time

import fakeredis
from rich.console import Console
from typer.testing import CliRunner

from common.jobs import JOB_ROUTING_KEY
from common.worker import publish_heartbeat, replica_key
from misc.cli import progress, stack_cmd
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
    r.hset(JOB_ROUTING_KEY, "j1", "exp:corpus-pilot-small")
    r.hset("exp:corpus-pilot-small", mapping={"status": "running", "expected_jobs": "720", "skipped_jobs": "20",
           "stored_jobs": "392", "failed_jobs": "3", "wave": "1/1", "started_at": _iso(NOW - 3600),
           "tool_jobs": json.dumps({"cbomkit": 10, "cdxgen": 5}),
           "waves": json.dumps([["cbomkit", "cdxgen"]])})  # fmt: skip
    r.hset("exp:corpus-pilot-small:tools", mapping={"cbomkit:n": 4, "cbomkit:sec": 2400.0, "cdxgen:n": 4,
           "cdxgen:sec": 400.0})  # fmt: skip
    r.hset("exp:corpus-pilot-small:pending", "j1", "{}")
    r.hset("exp:old", mapping={"status": "completed", "finished_at": _iso(NOW - 86400)})
    return r


def _text(snap, width=160, height=0) -> str:
    console = Console(record=True, width=width, color_system=None)
    console.print(stack_cmd.render(snap, width, height))
    return console.export_text()


def test_board_shows_workers_queues_and_experiments_with_their_remaining_time():
    r = _world()
    snap = stack_cmd.snapshot(r, SERVICES, probe=lambda url: True, now=NOW)
    workers = {w["name"]: w for w in snap.workers}
    assert list(workers) == ["cbomkit", "cdxgen", "deepseek", "pyqun", "extra"]
    assert workers["cbomkit"]["origin"] == "corpus-pilot-small" and workers["cbomkit"]["queued"] == 3
    assert workers["deepseek"]["beat"] is None
    (exp,) = snap.experiments  # exp:old finished a day ago, exp:<name>:pending is no status hash
    assert (exp["name"], exp["done"], exp["total"], exp["failed"]) == ("corpus-pilot-small", 412, 720, 3)

    text = _text(snap)
    assert "apache/kafka@771b957 run1 · corpus-pilot-small" in text and "0:02:05" in text  # the job and how long
    assert "14.6/16.0" in text and "ok 0:00:41 jwtk/jjwt@1a2b3c4" in text and "timeout 0:30:00" in text
    (line,) = [line.rstrip() for line in text.splitlines() if line.startswith("corpus-pilot-small")]
    # cbomkit: 6 left x 10 min, less the 2:05 its job has run; cdxgen: 1 x 100 s; the slowest ends the batch
    assert "412/720" in line and "1/1" in line and "1:00:00" in line and line.endswith("0:57:55")
    assert "1 busy · 3 idle · 1 down" in text and "redis ✓" in text and stack_cmd.FOOTER in text
    rows = [line.split()[0] for line in text.splitlines() if line.split() and line.split()[0] in workers]
    assert rows == ["cbomkit", "cdxgen", "extra", "pyqun", "deepseek"]  # busy, idle, down
    assert "rrxxnnuu" not in text and "rrxxnnuu" in _text(snap, width=140, height=50)  # the logo if it fits
    assert "rrxxnnuu" not in _text(snap, width=140, height=20)


def test_forecast_takes_the_slowest_tool_per_wave_and_adds_the_waves():
    mean = {"cbomkit": 600.0, "xgrep": 10.0}
    assert progress.forecast({"cbomkit": 3, "xgrep": 50}, mean, {"cbomkit": [100.0]}, [["cbomkit", "xgrep"]]) == (
        1700.0, False)  # fmt: skip
    # a later wave starts when the first is done; its tool without a result yet makes the time a lower bound
    fc = progress.forecast({"cbomkit": 3, "xgrep": 50, "cdxgen": 4}, mean, {}, [["cbomkit", "xgrep"], ["cdxgen"]])
    assert fc == (1800.0, True) and progress.remaining(fc) == "≥ 0:30:00"
    assert progress.forecast({"cbomkit": 1}, mean, {"cbomkit": [900.0]}, [["cbomkit"]])[0] == 0.0  # overdue: now
    assert progress.remaining(progress.forecast({"cdxgen": 4}, mean, {}, [["cdxgen"]])) == "–"  # nothing known


def test_forecast_shares_a_tools_work_among_its_replicas():
    mean = {"cbomkit": 600.0}
    # 7 jobs left, 3 of them running for 100, 200 and 900 s (the last counts one mean): (4200 - 900) / 3 containers
    running = {"cbomkit": [100.0, 200.0, 900.0]}
    fc = progress.forecast({"cbomkit": 7}, mean, running, [["cbomkit"]], slots={"cbomkit": 3})
    assert fc == (1100.0, False)
    # 2 jobs left on 3 containers: only 2 of them still work
    assert progress.forecast({"cbomkit": 2}, mean, {}, [["cbomkit"]], slots={"cbomkit": 3})[0] == 600.0
    assert progress.forecast({"cbomkit": 2}, mean, {}, [["cbomkit"]], slots={"cbomkit": 0})[0] == 1200.0  # down: as 1


def test_experiment_columns_keep_their_width_while_a_run_moves():
    early = {"name": "corpus-pilot-small", "status": "running", "done": 7, "total": 720, "failed": 0, "wave": "1/12",
             "started": NOW - 31, "forecast": (None, True)}  # fmt: skip
    late = {**early, "status": "stalled", "done": 712, "failed": 345, "wave": "10/12", "started": NOW - 45 * 3600,
            "forecast": (11 * 3600.0, True)}  # fmt: skip
    for width in (80, 160):
        lines = []
        for e in (early, late):
            console = Console(record=True, width=width, color_system=None)
            console.print(stack_cmd.experiments_table(stack_cmd.Snapshot(now=NOW, experiments=[e]), width))
            lines.append(console.export_text().splitlines()[:2])
        (head, first), (head_late, last) = lines
        assert head == head_late  # the same column widths
        for col in ("results", "wave", "failed", "running", "left"):  # right-aligned: ends where its header does
            end = head.index(col) + len(col)
            assert first[end - 1] != " " and last[end - 1] != " " and first[end : end + 1] in ("", " "), (width, col)
        assert "  7/720" in first and " 1/12" in first and "≥ 11:00:00" in last and "45:00:00" in last


def test_board_without_experiments_or_redis():
    r = fakeredis.FakeRedis(decode_responses=True)
    snap = stack_cmd.snapshot(r, SERVICES, probe=lambda url: False, now=NOW)
    text = _text(snap)
    assert "no experiment running" in text and "0 busy · 0 idle · 4 down" in text and "git server ✗" in text

    server = fakeredis.FakeServer()
    server.connected = False
    down = stack_cmd.snapshot(fakeredis.FakeRedis(server=server, decode_responses=True), SERVICES,
                              probe=lambda url: False, now=NOW)  # fmt: skip
    assert "Redis does not answer" in _text(down) and "redis ✗" in _text(down)


def test_board_once_prints_one_snapshot(monkeypatch):
    r = _world()
    monkeypatch.setattr(stack_cmd.redis, "Redis", lambda **_: r)
    monkeypatch.setattr(stack_cmd, "_probe", lambda url: True)
    monkeypatch.setenv("BOARD_SERVICES", ",".join(SERVICES))
    result = CliRunner().invoke(app, ["stack", "board", "--once"])
    assert result.exit_code == 0, result.output
    assert "Workers" in result.output and "corpus-pilot-small" in result.output and "deepseek" in result.output


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
    assert json.loads(r.get(replica_key("t")))["state"] == "idle"  # and this container's own key


def test_board_shows_a_tools_replicas_in_one_row():
    r = _world()
    for host, state, mem, started in (("a1", "busy", 9000.0, NOW - 300), ("b2", "busy", 7000.0, NOW - 60),
                                      ("c3", "idle", 1000.0, None)):  # fmt: skip
        job = {"repo": f"o/{host}@abcdef0", "run": 1, "started": started} if started else None
        r.set(replica_key("cbomkit", host), json.dumps({"state": state, "job_id": f"j-{host}" if job else None,
              "job": job, "mem_mb": mem, "mem_limit_mb": 16384.0, "ts": NOW}))  # fmt: skip
        if job:
            r.hset(JOB_ROUTING_KEY, f"j-{host}", "exp:corpus-pilot-small")
    snap = stack_cmd.snapshot(r, SERVICES, probe=lambda url: True, now=NOW)
    (w,) = [w for w in snap.workers if w["name"] == "cbomkit"]
    assert len(w["replicas"]) == 3 and w["beat"]["job"]["repo"] == "o/a1@abcdef0"  # the longest-running job
    text = _text(snap)
    (row,) = [line for line in text.splitlines() if line.startswith("cbomkit")]
    assert "busy 2/3" in row and "+1 running" in row and "0:05:00" in row and "16.6/48.0" in row
    # the experiment's forecast: cbomkit's 6 jobs left, less what its 2 running jobs did, on 3 containers
    (exp,) = snap.experiments
    assert exp["forecast"] == ((6 * 600 - 300 - 60) / 3, False)
