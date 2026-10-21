"""Live progress for the long CLI commands (`analysis report`, `experiment export`), so that a slow step reads as
working, not stuck.

On a terminal every step gets a line on stderr: a spinner while it runs (a check mark once done), its name, a bar,
`done/total` and the remaining time where the items are known in advance, the elapsed time and the item being worked
on. The display redraws ten times a second from its own thread, so the spinner and the clock keep moving while one
large item takes long, and what the steps print scrolls above it. The finished lines stay, which shows where the time
went. Off a terminal (a pipe, a log file, the tests) each step prints one plain line when it ends.
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

_live: Progress | None = None  # the outermost session's display; None outside a session and off a terminal


def _count(n: int) -> str:
    return f"{n:,}".replace(",", "'")


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m // 60}:{m % 60:02d}:{s:02d}"


class _CountColumn(ProgressColumn):
    """`done/total` and, while it runs, the remaining time; empty for a step without a count."""

    def render(self, task: Task) -> Text:
        if task.fields.get("of") is None:
            return Text("")
        text = f"{_count(task.fields['done'])}/{_count(task.fields['of'])}"
        if not task.finished and task.time_remaining is not None:
            text += f" eta {_clock(task.time_remaining)}"
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
        BarColumn(bar_width=20),
        _CountColumn(table_column=fixed),
        TimeElapsedColumn(table_column=fixed),
        TextColumn("{task.fields[item]}", style="dim", markup=False, table_column=Column(no_wrap=True, ratio=1)),
        console=console,
        expand=True,
        refresh_per_second=10,
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


def track(items: Iterable, name: str, *, total: int | None = None, item: Callable | None = None) -> Iterator:
    """Yield `items` (sized, or with `total`), counting them on the step's line; `item(x)` names the one in work."""
    of = len(items) if total is None else total  # type: ignore[arg-type]
    start = time.monotonic()
    task = _live.add_task(name, total=of or 1, done=0, of=of, item="") if _live else None
    done = 0
    for x in items:
        if _live is not None:
            _live.update(task, item=item(x) if item else "")
        yield x
        done += 1
        if _live is not None:
            _live.update(task, advance=1, done=done)
    _finish(task, name, start, done)


@contextmanager
def step(name: str) -> Iterator[None]:
    """A step without a count: a pulsing bar and the clock."""
    start = time.monotonic()
    task = _live.add_task(name, total=None, of=None, item="") if _live else None
    yield
    _finish(task, name, start)


def _finish(task, name: str, start: float, done: int | None = None) -> None:
    if _live is not None:
        _live.update(task, total=1, completed=1, item="")
        return
    count = "" if done is None else f"{_count(done)} in "
    typer.echo(f"{name}: {count}{time.monotonic() - start:.1f} s", err=True)
