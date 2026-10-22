"""Live progress for the long CLI commands (`experiment run` / `collect`, `analysis report`, `experiment export`,
`corpus build`), so that a slow step reads as working, not stuck.

On a terminal every step gets a line on stderr: a spinner while it runs (a check mark once done), its name, a bar,
`done/total` and the remaining time where the items are known in advance, the elapsed time and the item being worked
on. The display redraws ten times a second from its own thread, so the spinner and the clock keep moving while one
large item takes long, and what the commands print scrolls above it. The finished lines stay, which shows where the
time went. `track` and `step` are steps that end by themselves; `line` is a line the caller keeps up to date (a
batch's tools). Off a terminal (a pipe, a log file, the tests) a step prints one plain line when it ends and a `line`
shows nothing.
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


def count(n: int) -> str:
    return f"{n:,}".replace(",", "'")


def clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m // 60}:{m % 60:02d}:{s:02d}"


def forecast(
    left: dict[str, int], mean: dict[str, float], running: dict[str, float], waves: list[list[str]]
) -> tuple[float | None, str | None, bool]:
    """The remaining time of a batch whose tools work their own queues in parallel, one job at a time: per tool, its
    jobs left times its mean duration so far, less what its running job has taken (at most one mean); the slowest
    tool ends a wave and the waves follow each other. Returns (seconds, the slowest tool, whether it is a lower bound:
    a tool with jobs left and no result yet counts as 0), or (None, None, True) when no tool with jobs left has a
    result yet."""
    total, slowest, lower, known = 0.0, None, False, False
    for wave in waves:
        worst, worst_tool = 0.0, None
        for t in wave:
            if left.get(t, 0) <= 0:
                continue
            if t not in mean:
                lower = True
                continue
            known = True
            secs = max(left[t] * mean[t] - min(running.get(t, 0.0), mean[t]), 0.0)
            if worst_tool is None or secs > worst:
                worst, worst_tool = secs, t
        total += worst
        slowest = slowest or worst_tool  # the one holding up the first wave with work left
    return (total, slowest, lower) if known else (None, None, True)


def remaining(fc: tuple[float | None, str | None, bool], *, tool: bool = True) -> str:
    """A forecast as text: `1:42:10 (cbomkit)`, `≥ 0:12:00 (xgrep)` while a tool has no result yet, `–` unknown."""
    secs, slowest, lower = fc
    if secs is None:
        return "–"
    text = ("≥ " if lower else "") + clock(secs)
    return f"{text} ({slowest})" if tool and slowest else text


class _CountColumn(ProgressColumn):
    """`done/total` and, while it runs, the remaining time; empty for a step without a count."""

    def render(self, task: Task) -> Text:
        if task.fields.get("of") is None:
            return Text("")
        text = f"{count(task.fields['done'])}/{count(task.fields['of'])}"
        if task.finished:
            return Text(text)
        if "eta" in task.fields:  # the caller's forecast (`update(eta=...)`), else rich's rate
            return Text(f"{text} eta {task.fields['eta']}" if task.fields["eta"] else text)
        if task.time_remaining is not None:
            text += f" eta {clock(task.time_remaining)}"
        return Text(text)


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
        yield
        return
    fixed = Column(no_wrap=True)
    _live = Progress(  # the item's name takes what the other columns leave and is cut first on a narrow terminal
        SpinnerColumn(finished_text="[green]✓"),
        TextColumn("{task.description}", markup=False, table_column=fixed),
        BarColumn(bar_width=20, finished_style="green"),
        _CountColumn(table_column=fixed),
        TimeElapsedColumn(table_column=fixed),
        TextColumn("{task.fields[item]}", style="dim", markup=False, table_column=Column(no_wrap=True, ratio=1)),
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
        with _live:
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


def track(items: Iterable, name: str, *, total: int | None = None, item: Callable | None = None) -> Iterator:
    """Yield `items` (sized, or with `total`), counting them on the step's line; `item(x)` names the one in work."""
    of = len(items) if total is None else total  # type: ignore[arg-type]
    start = time.monotonic()
    task = line(name, total=of)
    done = 0
    for x in items:
        update(task, item=item(x) if item else "")
        yield x
        done += 1
        update(task, done=done)
    _finish(task, name, start, done)


@contextmanager
def step(name: str) -> Iterator[None]:
    """A step without a count: a pulsing bar and the clock."""
    start = time.monotonic()
    task = line(name)
    yield
    _finish(task, name, start)


def _finish(task, name: str, start: float, done: int | None = None) -> None:
    if _live is not None:
        update(task, item="")
        end(task)
        return
    counted = "" if done is None else f"{count(done)} in "
    typer.echo(f"{name}: {counted}{time.monotonic() - start:.1f} s", err=True)
