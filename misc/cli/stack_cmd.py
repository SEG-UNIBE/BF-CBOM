"""The stack board (`bf-cbom stack board`, `make board`; `make up-prod` opens it once the stack is up): a live view of
what the running stack does, read from Redis only, so it runs in the runner like every command.

- Header: the logo on a wide terminal, the version and commit, the UI's address, whether Redis, the git server and
  the UI answer, the host's RAM and CPUs.
- Experiments: every running or stalled experiment (the status hash `exp:<name>` the runner writes) and every
  running UI inspection, with a bar, the results stored, the wave, the failures and the remaining time
  (`progress.forecast`: per tool its jobs left times its mean duration so far, from what the runner records in
  `exp:<name>:tools`; the slowest tool ends the batch; none for inspections). The columns keep their width while a
  run moves: it depends on the totals and the number of waves, never on the current values.
- Workers: one row per worker service (`BOARD_SERVICES`, every container of the stack as make lists it, so a crashed
  worker shows as down), from its heartbeat (`common/worker.py:publish_heartbeat`): busy / idle / down, the job and
  its origin (`jobs:routing`), for how long, the container's RAM against its limit, the jobs queued and the last
  result.

Ctrl-C closes the board; the stack keeps running. Off a terminal, or with `--once`, it prints one snapshot.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import redis
import requests
import typer
from rich.console import Console, Group
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from common.experiment import GITSERVER_URL
from common.jobs import EXPERIMENT_ROUTE_PREFIX, JOB_ROUTING_KEY
from common.memory import host_memory
from coordinator.redis_io import count_done_jobs, list_inspections
from misc.cli.progress import clock, count, forecast, remaining

stack_app = typer.Typer(
    help="The running stack at a glance",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://coordinator:8501").rstrip("/")
UI_ADDRESS = "http://localhost:8501"  # the coordinator's port as compose publishes it on the host
LOGO = Path(__file__).resolve().parents[2] / "docs" / "logo.txt"
LOGO_WIDTH, LOGO_HEIGHT = 130, 15
HEARTBEAT_NAMES = {"misc-treesimilarity": "treesimilartiy"}  # its worker names itself after its folder
ACTIVE = ("running", "stalled")
RECENT_SEC = 600  # a finished experiment stays on the board this long
FOOTER = "Ctrl-C closes the board · the stack keeps running · make board · make logs · make down"
LONGEST_CLOCK = clock(99 * 3600)  # the widest clock the experiments table makes room for (`running`, `left`)


@dataclass
class Snapshot:
    now: float
    version: str = ""
    commit: str = ""
    redis_ok: bool = False
    git_ok: bool = False
    ui_ok: bool = False
    host: dict = field(default_factory=dict)
    cpus: int | None = None
    workers: list[dict] = field(default_factory=list)
    experiments: list[dict] = field(default_factory=list)


def worker_names(services: list[str]) -> list[str]:
    """The worker names (heartbeat and queue) of the stack's services: `worker-<name>`, `misc-<name>`."""
    out = []
    for s in services:
        if s.startswith(("worker-", "misc-")):
            out.append(HEARTBEAT_NAMES.get(s, s.split("-", 1)[1]))
    return out


def _probe(url: str) -> bool:
    try:
        return requests.get(url, timeout=1).ok
    except requests.RequestException:
        return False


def _epoch(iso: str | None) -> float | None:
    """`now_iso()`'s UTC timestamps as epoch seconds."""
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else None
    except ValueError:
        return None


def snapshot(r: redis.Redis, services: list[str], *, probe=None, now: float | None = None) -> Snapshot:
    """What the board shows, read once."""
    probe = probe or _probe
    snap = Snapshot(now=time.time() if now is None else now)
    snap.version = os.getenv("BF_CBOM_VERSION", "")
    snap.commit = os.getenv("BF_CBOM_COMMIT", "")
    snap.host, snap.cpus = host_memory(), os.cpu_count()
    snap.git_ok, snap.ui_ok = probe(f"{GITSERVER_URL}/healthz"), probe(f"{COORDINATOR_URL}/_stcore/health")
    try:
        r.ping()
    except redis.exceptions.RedisError:
        return snap
    snap.redis_ok = True

    names = worker_names(services)
    seen = {k.split(":")[1] for k in r.scan_iter(match="worker:*:alive", count=500)}
    names += sorted(seen - set(names))
    pipe = r.pipeline()
    for n in names:
        pipe.get(f"worker:{n}:alive")
        pipe.llen(f"jobs:{n}")
    raw = pipe.execute()
    beats = {n: _json(raw[2 * i]) for i, n in enumerate(names)}
    queued = {n: raw[2 * i + 1] for i, n in enumerate(names)}
    jobs = [b["job_id"] for b in beats.values() if b and b.get("state") == "busy" and b.get("job_id")]
    targets = dict(zip(jobs, r.hmget(JOB_ROUTING_KEY, jobs), strict=True)) if jobs else {}
    for n in names:
        beat = beats[n]
        target = targets.get(beat.get("job_id")) if beat else None
        snap.workers.append(
            {"name": n, "beat": beat, "queued": queued[n], "origin": _origin(r, target) if target else None}
        )

    for key in sorted(r.scan_iter(match="exp:*", count=500)):
        if key.count(":") != 1 or r.type(key) != "hash":
            continue
        meta = r.hgetall(key)
        finished = _epoch(meta.get("finished_at"))
        if meta.get("status") not in ACTIVE and not (finished and snap.now - finished < RECENT_SEC):
            continue
        name = key.split(":", 1)[1]
        snap.experiments.append(
            {
                "name": name,
                "status": meta.get("status", ""),
                "done": _int(meta.get("skipped_jobs")) + _int(meta.get("stored_jobs")),
                "total": _int(meta.get("expected_jobs")),
                "failed": _int(meta.get("failed_jobs")),
                "wave": meta.get("wave", ""),
                "started": _epoch(meta.get("started_at")),
                "forecast": _forecast(meta, r.hgetall(f"{key}:tools"), snap, name),
            }
        )
    for insp_id, meta in list_inspections(r):
        if meta.get("status") == "running":
            done, total = count_done_jobs(r, insp_id)
            snap.experiments.append(
                {
                    "name": f"inspection {meta.get('name') or insp_id[:8]}",
                    "status": "running",
                    "done": done,
                    "total": total,
                    "failed": 0,
                    "wave": meta.get("wave", ""),
                    "started": _epoch(meta.get("started_at")),
                }
            )
    return snap


def _forecast(meta: dict, tools: dict, snap: Snapshot, name: str) -> tuple:
    """The experiment's remaining time from this session's jobs per tool (`tool_jobs`), the results and seconds the
    runner stored per tool (`<tool>:n`, `<tool>:sec`) and how long each of its tools' running job has taken (the
    heartbeats)."""
    jobs = _json(meta.get("tool_jobs")) or {}
    n = {t: _int(tools.get(f"{t}:n")) for t in jobs}
    left = {t: c - n[t] for t, c in jobs.items()}
    mean = {t: float(tools.get(f"{t}:sec") or 0) / n[t] for t in jobs if n[t]}
    running = {}
    for w in snap.workers:
        info = (w["beat"] or {}).get("job") or {}
        if w["origin"] == name and info.get("started"):
            running[w["name"]] = max(snap.now - info["started"], 0.0)
    waves = _json(meta.get("waves")) or [sorted(jobs)]
    return forecast(left, mean, running, waves if isinstance(waves, list) else [sorted(jobs)])


def _origin(r: redis.Redis, target: str) -> str:
    if target.startswith(EXPERIMENT_ROUTE_PREFIX):
        return target[len(EXPERIMENT_ROUTE_PREFIX) :]
    return f"inspection {r.hget(f'insp:{target}', 'name') or target[:8]}"


def _json(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return {"state": "unknown"}


def _int(v) -> int:
    try:
        return int(v or 0)
    except ValueError:
        return 0


def eta(e: dict) -> str:
    if e["total"] and e["done"] >= e["total"]:
        return "done"
    return remaining(e["forecast"]) if e.get("forecast") else "–"


def _counted(done: int, total: int) -> str:
    """`done/total` with done padded to the total's width: ` 33/180` and `133/180` take the same room."""
    return f"{count(done):>{len(count(total))}}/{count(total)}"


def _wave(wave: str) -> str:
    """An experiment's `i/n` with i padded to n's width (an inspection's wave is a bare number)."""
    i, slash, n = wave.partition("/")
    return f"{i:>{len(n)}}/{n}" if slash else wave


def _gib(mb: float | None) -> str:
    return "?" if mb is None else f"{mb / 1024:.1f}"


def _check(ok: bool) -> Text:
    return Text("✓", style="green") if ok else Text("✗", style="bold red")


def header(snap: Snapshot, width: int, height: int) -> list:
    """The logo when it fits beside everything else (15 lines, 130 columns), then one line of facts."""
    out = []
    rows = 10 + len(snap.workers) + max(len(snap.experiments), 1)
    if width >= LOGO_WIDTH and height >= rows + LOGO_HEIGHT:
        try:
            out.append(Text.from_ansi(LOGO.read_text(encoding="utf-8").rstrip("\n")))
        except OSError:
            pass
    line = Text()
    line.append("BF-CBOM", style="bold bright_red")
    if snap.version:
        line.append(f" {snap.version}", style="bold")
    if snap.commit:
        line.append(f" · {snap.commit[:12]}", style="dim")
    line.append(" · UI ")
    line.append(UI_ADDRESS, style="underline cyan")
    for label, ok in (("redis", snap.redis_ok), ("git server", snap.git_ok), ("UI", snap.ui_ok)):
        line.append(f" · {label} ")
        line.append_text(_check(ok))
    if snap.host:
        line.append(f" · host {_gib(snap.host.get('used_mb'))}/{_gib(snap.host.get('total_mb'))} GiB")
    if snap.cpus:
        line.append(f" · {snap.cpus} CPUs")
    line.append(" · " + time.strftime("%H:%M:%S UTC", time.gmtime(snap.now)), style="dim")
    out.append(line)
    return out


def experiments_table(snap: Snapshot, width: int = 160) -> Table | Text:
    if not snap.experiments:
        return Text("no experiment running · make experiment EXP=experiments/<spec>.yaml", style="dim")
    t = Table(box=None, pad_edge=False, show_header=True, header_style="bold", expand=True)
    t.add_column("experiment", no_wrap=True, overflow="ellipsis", ratio=1, min_width=12)
    bar_width = 24 if width >= 120 else 10
    t.add_column("", no_wrap=True, width=bar_width)
    # fixed widths from what a run does not change (totals, waves, the widest clock), so no column shifts as it moves
    of = max(len(count(e["total"])) for e in snap.experiments)
    for col, w in (("results", 2 * of + 1), ("wave", max(len(_wave(e["wave"])) for e in snap.experiments)),
                   ("failed", of), ("running", len(LONGEST_CLOCK)), ("left", len(f"≥ {LONGEST_CLOCK}"))):  # fmt: skip
        t.add_column(col, justify="right", no_wrap=True, width=max(w, len(col)))
    for e in snap.experiments:
        style = "" if e["status"] in ACTIVE else "dim"
        bar = ProgressBar(total=max(e["total"], 1), completed=min(e["done"], e["total"]), width=bar_width,
                          finished_style="green")  # fmt: skip
        failed = Text(count(e["failed"]), style="red" if e["failed"] else "dim")
        name = Text(e["name"], style=style)
        if e["status"] == "stalled":
            name.append(" (stalled)", style="yellow")
        elif e["status"] not in ACTIVE:
            name.append(f" ({e['status']})", style="dim")
        running = clock(max(snap.now - e["started"], 0)) if e["started"] else ""
        t.add_row(name, bar, _counted(e["done"], e["total"]), _wave(e["wave"]), failed, running, eta(e))
    return t


def _state(beat: dict | None) -> tuple[int, Text]:
    if beat is None:
        return 2, Text("down", style="bold red")
    state = beat.get("state", "unknown")
    return (0, Text("busy", style="bold yellow")) if state == "busy" else (1, Text(state, style="green"))


def workers_table(snap: Snapshot) -> Table:
    # the fixed columns keep their width on a narrow terminal; the job and the last result are shortened instead
    t = Table(box=None, pad_edge=False, show_header=True, header_style="bold", expand=True)
    t.add_column("worker", no_wrap=True, min_width=max((len(w["name"]) for w in snap.workers), default=6))
    t.add_column("state", no_wrap=True, min_width=5)
    t.add_column("job", no_wrap=True, overflow="ellipsis", ratio=3, min_width=8)
    t.add_column("for", justify="right", no_wrap=True, min_width=8)
    t.add_column("RAM GiB", justify="right", no_wrap=True, min_width=9)
    t.add_column("queued", justify="right", no_wrap=True, min_width=6)
    t.add_column("last", no_wrap=True, overflow="ellipsis", ratio=2, min_width=8)
    rows = []
    for w in snap.workers:
        beat = w["beat"] or {}
        order, state = _state(w["beat"])
        job, since = Text(""), ""
        info = beat.get("job") if order == 0 else None
        if info:
            job.append(info.get("repo") or "")
            if info.get("run") is not None:
                job.append(f" run{info['run']}", style="dim")
            if w["origin"]:
                job.append(f" · {w['origin']}", style="cyan")
            if info.get("started"):
                since = clock(max(snap.now - info["started"], 0))
        mem, limit = beat.get("mem_mb"), beat.get("mem_limit_mb")
        ram = Text("" if mem is None else f"{_gib(mem)}/{_gib(limit)}" if limit else _gib(mem))
        if mem is not None and limit and mem / limit >= 0.9:
            ram.stylize("bold red")
        elif mem is not None and limit and mem / limit >= 0.75:
            ram.stylize("yellow")
        last = Text("")
        if beat.get("last"):
            lr = beat["last"]
            last.append(lr.get("status") or "?", style="green" if lr.get("status") == "ok" else "red")
            if lr.get("duration_sec") is not None:
                last.append(f" {clock(lr['duration_sec'])}")
            last.append(f" {lr.get('repo') or ''}", style="dim")
        queued = Text(count(w["queued"] or 0), style="" if w["queued"] else "dim")
        started = (info or {}).get("started") or 0  # busy: the longest-running job first
        rows.append(((order, started, w["name"]), [w["name"], state, job, since, ram, queued, last]))
    for _, cells in sorted(rows, key=lambda x: x[0]):
        t.add_row(*cells)
    return t


def render(snap: Snapshot, width: int, height: int = 0) -> Group:
    parts = header(snap, width, height) + [Text("")]
    if not snap.redis_ok:
        parts.append(Text("Redis does not answer: is the stack up? (make up-prod)", style="bold red"))
    else:
        busy = sum(1 for w in snap.workers if (w["beat"] or {}).get("state") == "busy")
        down = sum(1 for w in snap.workers if w["beat"] is None)
        title = Text("Workers", style="bold")
        title.append(f"  {busy} busy · {len(snap.workers) - busy - down} idle · {down} down", style="dim")
        parts += [Text("Experiments", style="bold"), experiments_table(snap, width), Text(""), title,
                  workers_table(snap)]  # fmt: skip
    parts += [Text(""), Text(FOOTER, style="dim")]
    return Group(*parts)


@stack_app.command("board")
def board(
    once: bool = typer.Option(False, "--once", help="Print one snapshot and exit (also off a terminal)"),
    interval: float = typer.Option(2.0, help="Seconds between refreshes"),
    redis_host: str = typer.Option("localhost", envvar="REDIS_HOST", help="Redis host"),
    redis_port: int = typer.Option(6379, envvar="REDIS_PORT", help="Redis port"),
):
    """A live view of the running stack: experiments, workers, queues. Ctrl-C closes it; the stack keeps running."""
    r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True, socket_timeout=5)
    services = [s for s in os.getenv("BOARD_SERVICES", "").split(",") if s]
    console = Console()
    if once or not console.is_terminal:
        console.print(render(snapshot(r, services), console.width, console.height))
        return
    try:
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while True:
                live.update(render(snapshot(r, services), console.width, console.height), refresh=True)
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    console.print(f"board closed; {FOOTER.split(' · ', 1)[1]}")
