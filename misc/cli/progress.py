"""Live progress for the long CLI commands (`experiment run` / `collect`, `analysis report`, `experiment export`,
`corpus build`), so that a slow step reads as working, not stuck.

On a terminal every step gets a line on stderr: a spinner while it runs (a check mark once done), its name, a bar,
`done/total` and the remaining time where the items are known in advance, the elapsed time and the item being worked
on. The display redraws ten times a second from its own thread, so the spinner and the clock keep moving while one
large item takes long, and what the commands print scrolls above it. The finished lines stay, which shows where the
time went. `track` and `step` are steps that end by themselves; `line` is a line the caller keeps up to date (a
batch's tools). When an exception leaves a session, the lines it left unfinished get a red cross with the reason, and
one line per unfinished step says above them what failed, where and why. Off a terminal (a pipe, a log file, the
tests) a step prints one plain line when it ends or fails and a `line` shows nothing.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

import typer
from rich.console import Console
from rich.file_proxy import FileProxy
from rich.progress import BarColumn, Progress, ProgressColumn, SpinnerColumn, Task, TextColumn, TimeElapsedColumn
from rich.table import Column
from rich.text import Text

ETA_WINDOW_SEC = 1800  # the remaining time comes from the rate over this window (a batch's results come unevenly)

_live: Progress | None = None  # the outermost session's display; None outside a session and off a terminal
_open: dict[str, list] = {}  # the steps running: name -> [start, the item in work, "done/total" or ""] (`_fail`)
_depth = 0  # the sessions open, nested ones included


def count(n: int) -> str:
    return f"{n:,}".replace(",", "'")


def clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m // 60}:{m % 60:02d}:{s:02d}"


def forecast(
    left: dict[str, int],
    mean: dict[str, float],
    running: dict[str, list[float]],
    waves: list[list[str]],
    *,
    slots: dict[str, int] | None = None,
) -> tuple[float | None, bool]:
    """The remaining time of a batch whose tools work their own queues in parallel: per tool, its jobs left times its
    mean duration so far, less what its running jobs have taken (each at most one mean), shared by its containers
    (`slots`, the replicas; default 1) that still have a job (at most its jobs left); the slowest tool ends a wave and
    the waves follow each other. The queues hold the largest repositories first (`plan_jobs`), so early on the mean
    comes from them and the forecast is too high. Returns (seconds, whether it is a lower bound: a tool with jobs left
    and no result yet counts as 0), or (None, True) when no tool with jobs left has a result yet."""
    total, lower, known = 0.0, False, False
    for wave in waves:
        worst = 0.0
        for t in wave:
            if left.get(t, 0) <= 0:
                continue
            if t not in mean:
                lower = True
                continue
            known = True
            work = left[t] * mean[t] - sum(min(s, mean[t]) for s in running.get(t, ()))
            share = max(min((slots or {}).get(t, 1), left[t]), 1)
            worst = max(worst, max(work, 0.0) / share)
        total += worst
    return (total, lower) if known else (None, True)


def remaining(fc: tuple[float | None, bool]) -> str:
    """A forecast as text: `1:42:10`, `≥ 0:12:00` while a tool has no result yet, `–` unknown."""
    secs, lower = fc
    if secs is None:
        return "–"
    return ("≥ " if lower else "") + clock(secs)


class _CountColumn(ProgressColumn):
    """`done/total` and, while it runs, the remaining time; empty for a step without a count."""

    def render(self, task: Task) -> Text:
        if task.fields.get("of") is None:
            return Text("")
        text = f"{count(task.fields['done'])}/{count(task.fields['of'])}"
        if task.finished or task.fields.get("failed"):
            return Text(text)
        if "eta" in task.fields:  # the caller's forecast (`update(eta=...)`), else rich's rate
            return Text(f"{text} eta {task.fields['eta']}" if task.fields["eta"] else text)
        if task.time_remaining is not None:
            text += f" eta {clock(task.time_remaining)}"
        return Text(text)


class _StateColumn(SpinnerColumn):
    """The spinner while a line runs, a check mark once done, a red cross when it failed (`_fail`)."""

    def render(self, task: Task):
        return Text("✗", style="red") if task.fields.get("failed") else super().render(task)


class _ItemColumn(ProgressColumn):
    """The item in work, dim; on a failed line the reason and where, in red."""

    def render(self, task: Task) -> Text:
        return Text(task.fields["item"], style="red" if task.fields.get("failed") else "dim")


class _Proxy(FileProxy):
    """rich's stdout/stderr proxy, which prints above the display, with the real stream's encoding and tty: rich's
    reports neither (`io.TextIOBase` defaults), so `typer.echo` took it for misconfigured and wrote past it."""

    @property
    def encoding(self):
        return self.rich_proxied_file.encoding

    def isatty(self) -> bool:
        return self.rich_proxied_file.isatty()


@contextmanager
def session() -> Iterator[None]:
    """Show the steps run inside the block; a nested session (the export around its report) joins the outer one."""
    global _live
    console = Console(stderr=True)
    if _live is not None or not console.is_terminal:
        with _failing():
            yield
        return
    fixed = Column(no_wrap=True)
    _live = Progress(  # the item's name takes what the other columns leave and is cut first on a narrow terminal
        _StateColumn(finished_text="[green]✓"),
        TextColumn("{task.description}", markup=False, table_column=fixed),
        BarColumn(bar_width=20, finished_style="green"),
        _CountColumn(table_column=fixed),
        TimeElapsedColumn(table_column=fixed),
        _ItemColumn(table_column=Column(no_wrap=True, ratio=1)),
        console=console,
        expand=True,
        refresh_per_second=10,
        speed_estimate_period=ETA_WINDOW_SEC,
        redirect_stdout=False,
        redirect_stderr=False,
    )
    streams = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Proxy(console, sys.stdout), _Proxy(console, sys.stderr)
    try:
        with _live, _failing():  # the failed lines are marked before the display stops
            yield
    finally:
        sys.stdout, sys.stderr = streams
        _live = None


def live() -> bool:
    """Whether a display is showing (a session on a terminal)."""
    return _live is not None


def line(name: str, *, total: int | None = None, done: int = 0, start: bool = True):
    """A line the caller keeps up to date with `update` (None, showing nothing, off a terminal): `total` counts,
    None gives a pulsing bar; a line added with `start=False` keeps its clock still until `begin`."""
    if _live is None:
        return None
    bar = None if total is None else max(total, 1)
    return _live.add_task(name, total=bar, completed=done, start=start, done=done, of=total, item="")


def update(task, *, done: int | None = None, item: str | None = None, eta: str | None = None) -> None:
    """Move a line to `done`, name what it works on now and/or give its remaining time (`forecast`, `remaining`)."""
    if _live is None or task is None:
        return
    fields: dict = {} if done is None else {"completed": done, "done": done}
    if item is not None:
        fields["item"] = item
    if eta is not None:
        fields["eta"] = eta
    _live.update(task, **fields)


def begin(task) -> None:
    """Start the clock of a line added with `start=False`."""
    if _live is not None and task is not None:
        _live.start_task(task)


def end(task) -> None:
    """Mark a line done although its count fell short of the total (a check mark, the clock stops)."""
    if _live is not None and task is not None:
        _live.update(task, total=1, completed=1)


def track(
    items: Iterable, name: str, *, total: int | None = None, item: Callable | None = None, every: int = 1
) -> Iterator:
    """Yield `items` (sized, or with `total`), counting them on the step's line; `item(x)` names the one in work.
    `every` > 1 moves the line on every that many items only (a table's rows: a display update costs microseconds)."""
    of = len(items) if total is None else total  # type: ignore[arg-type]
    start = time.monotonic()
    task = line(name, total=of)
    done = 0
    _open[name] = [start, "", f"0/{count(of)}"]
    for x in items:
        if not done % every:
            label = item(x) if item else ""
            _open[name][1:] = [label, f"{count(done)}/{count(of)}"]
            update(task, item=label)
        yield x
        done += 1
        if not done % every or done == of:
            update(task, done=done)
    _finish(task, name, start, done)


@contextmanager
def step(name: str) -> Iterator[None]:
    """A step without a count: a pulsing bar and the clock."""
    start = time.monotonic()
    task = line(name)
    _open[name] = [start, "", ""]
    yield
    _finish(task, name, start)


def _finish(task, name: str, start: float, done: int | None = None) -> None:
    _open.pop(name, None)
    if _live is not None:
        update(task, item="")
        end(task)
        return
    counted = "" if done is None else f"{count(done)} in "
    typer.echo(f"{name}: {counted}{time.monotonic() - start:.1f} s", err=True)


def reason(exc: BaseException) -> str:
    """Why a step failed, in one line: `interrupted` (Ctrl-C), `exit code N`, else the exception's type and text."""
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted"
    if isinstance(exc, typer.Exit):
        return f"exit code {exc.exit_code}"
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _fail(exc: BaseException) -> None:
    """The steps `exc` left unfinished: one red line each (what, after how long, at which item, why) and, on the
    display, a red cross on every unfinished line with the reason where its item was."""
    why = reason(exc)
    for name, (start, item, counted) in _open.items():
        at = (f" at {counted}" if counted else "") + (f" ({item})" if item else "")
        typer.secho(f"✗ {name} failed after {time.monotonic() - start:.1f} s{at}: {why}", fg=typer.colors.RED, err=True)
    _open.clear()
    if _live is None:
        return
    short = why.split(":", 1)[0]
    for task in _live.tasks:
        if task.finished or task.fields.get("failed"):
            continue
        item = task.fields.get("item", "")
        fields = {"failed": True, "item": f"{short} at {item}" if item else short}
        if task.total is None:
            fields["total"] = 1  # a step's pulsing bar stops
        _live.update(task.id, **fields)
        _live.stop_task(task.id)


@contextmanager
def _failing() -> Iterator[None]:
    """Mark what an exception leaving the session left unfinished (`_fail`); an exit with code 0 is no failure. The
    outermost session starts with no steps open (one left by a caller outside any session, a test, is dropped)."""
    global _depth
    if not _depth:
        _open.clear()
    _depth += 1
    try:
        yield
    except BaseException as exc:
        if not (isinstance(exc, typer.Exit) and exc.exit_code == 0):
            _fail(exc)
        raise
    finally:
        _depth -= 1
