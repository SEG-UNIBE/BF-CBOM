"""misc/cli/progress.py: a step that fails is marked on the display and named with its item and reason."""

import re

import pytest
import typer
from rich.progress import Progress

from misc.cli import progress


def test_a_failed_step_prints_what_failed_where_and_why(capsys):
    with pytest.raises(ValueError), progress.session():  # off a terminal: plain lines
        list(progress.track(["x"], "normalise"))
        for x in progress.track(["a", "b", "c"], "match", item=str):
            if x == "b":
                raise ValueError("no b")
    err = capsys.readouterr().err
    assert "normalise: 1 in" in err  # the finished step's line
    assert re.search(r"✗ match failed after \d+\.\d s at 1/3 \(b\): ValueError: no b", err)
    with pytest.raises(typer.Exit), progress.session(), progress.step("ladder"):
        raise typer.Exit(0)  # a deliberate exit is no failure
    assert "✗" not in capsys.readouterr().err


def test_the_display_crosses_the_unfinished_lines_and_keeps_the_finished(monkeypatch, capsys):
    monkeypatch.setattr(progress, "_live", Progress())  # a display that is never drawn
    with pytest.raises(RuntimeError), progress.session():
        list(progress.track(["x"], "normalise"))
        for x in progress.track(["a", "b"], "match", item=str):
            if x == "b":
                with progress.step("compare"):
                    raise RuntimeError("boom")
    lines = {t.description: t for t in progress._live.tasks}
    assert lines["normalise"].finished and not lines["normalise"].fields.get("failed")
    match, compare = lines["match"], lines["compare"]
    assert match.fields["failed"] and match.fields["item"] == "RuntimeError at b" and not match.finished
    assert compare.fields["item"] == "RuntimeError" and compare.total == 1  # the pulsing bar stopped
    assert progress._StateColumn().render(match).plain == "✗"
    assert progress._CountColumn().render(match).plain == "1/2"  # no remaining time on a failed line
    assert str(progress._ItemColumn().render(match).style) == "red"
    err = capsys.readouterr().err
    assert "✗ match failed" in err and "✗ compare failed" in err and "RuntimeError: boom" in err


def test_a_long_track_moves_its_line_in_steps_and_ends_on_the_count(monkeypatch):
    monkeypatch.setattr(progress, "_live", Progress())
    seen = []
    for i, _ in enumerate(progress.track(range(25), "rows", every=10)):
        if i in (5, 15):
            seen.append(progress._live.tasks[0].fields["done"])
    (task,) = progress._live.tasks
    assert seen == [0, 10] and task.fields["done"] == 25 and task.finished


def test_a_big_table_is_written_on_a_counted_line_of_its_own(tmp_path, capsys):
    from misc.cli.analysis_cmd import BIG_TABLE_ROWS, _writer

    counts: dict[str, int] = {}
    write = _writer(tmp_path, counts)
    write("small.csv", [{"a": 1}], ["a"])
    write("big.csv", [{"a": i} for i in range(BIG_TABLE_ROWS)], ["a"])
    out = capsys.readouterr()
    assert "small.csv" in out.out and "big.csv" not in out.out  # the big one's line says its rows instead
    assert f"big.csv: {progress.count(BIG_TABLE_ROWS)} in" in out.err
    assert counts == {"small.csv": 1, "big.csv": BIG_TABLE_ROWS}
    assert len((tmp_path / "big.csv").read_text().splitlines()) == BIG_TABLE_ROWS + 1
