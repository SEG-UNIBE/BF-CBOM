"""Human-readable experiment overviews for the CLI: what `experiment run` and `collect` print when they finish, and
what `experiment status` and `experiment list` show.

Everything comes from the files on disk (the run meta files, `_experiment.yaml`, `_run-log.json`); live progress for
`status` comes from Redis. Data for scripts stays in those files and in the analysis CSVs: this module only renders.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import typer

from common.experiment import ExperimentError, expected_jobs, iter_results, load_run_log, load_spec, spec_path

# statuses in display order; anything else a worker reports is appended after these
STATUS_ORDER = ("ok", "error", "timeout", "cancelled", "unsupported")
NOT_OK = ("error", "timeout", "cancelled")
MAX_PROBLEMS = 15
ERROR_WIDTH = 90


@dataclass
class ToolRow:
    tool: str
    expected: int | None = None
    counts: Counter = field(default_factory=Counter)
    durations: list[float] = field(default_factory=list)
    peak_mb: float | None = None
    limit_mb: float | None = None
    oom_kills: int = 0
    requests: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    unpriced: int = 0

    @property
    def stored(self) -> int:
        return sum(self.counts.values())

    @property
    def missing(self) -> int | None:
        return None if self.expected is None else max(self.expected - self.stored, 0)


@dataclass
class Problem:
    status: str
    tool: str
    repo: str
    variant: str
    run: int
    text: str


@dataclass
class Overview:
    name: str
    expected: int | None
    tools: list[ToolRow]
    problems: list[Problem]
    sessions: list[dict]
    spec_file: str

    @property
    def stored(self) -> int:
        return sum(t.stored for t in self.tools)

    @property
    def counts(self) -> Counter:
        total = Counter()
        for t in self.tools:
            total.update(t.counts)
        return total

    @property
    def missing(self) -> int | None:
        return None if self.expected is None else max(self.expected - self.stored, 0)


def build(exp_dir: Path) -> Overview:
    """Collect the overview of one experiment folder."""
    try:
        spec = load_spec(spec_path(exp_dir), base_dir=exp_dir)
    except (ExperimentError, OSError, ValueError):
        spec = None
    rows: dict[str, ToolRow] = {}
    if spec is not None:
        per_tool = expected_jobs(spec, exp_dir) // max(len(spec.tools), 1)
        rows = {t: ToolRow(t, expected=per_tool) for t in spec.tools}
    problems: list[Problem] = []
    for meta, _ in iter_results(exp_dir, spec) if spec is not None else ():  # unreadable spec copy: nothing to read
        tool = meta.get("worker") or "?"
        row = rows.setdefault(tool, ToolRow(tool))
        status = meta.get("status") or "?"
        row.counts[status] += 1
        if status not in ("unsupported",) and isinstance(meta.get("duration_sec"), int | float):
            row.durations.append(float(meta["duration_sec"]))
        if isinstance(meta.get("peak_memory_mb"), int | float):
            row.peak_mb = max(row.peak_mb or 0.0, float(meta["peak_memory_mb"]))
        if isinstance(meta.get("memory_limit_mb"), int | float):
            row.limit_mb = float(meta["memory_limit_mb"])
        row.oom_kills += int(meta.get("oom_kills") or 0)
        _add_usage(row, meta)
        if status in NOT_OK or status not in STATUS_ORDER:
            repo = (meta.get("repo_info") or {}).get("full_name") or "?"
            problems.append(
                Problem(status, tool, repo, meta.get("variant") or "original", int(meta.get("run_index") or 1),
                        _problem_text(meta))
            )  # fmt: skip
    problems.sort(key=lambda p: (p.tool, p.repo, p.variant, p.run))
    return Overview(
        name=exp_dir.name,
        expected=expected_jobs(spec, exp_dir) if spec is not None else None,
        tools=list(rows.values()),
        problems=problems,
        sessions=load_run_log(exp_dir),
        spec_file=f"experiments/{exp_dir.name}.yaml",
    )


def _add_usage(row: ToolRow, meta: dict) -> None:
    usage = meta.get("usage")
    if not isinstance(usage, dict):
        return
    row.requests += int(usage.get("requests") or 0)
    row.input_tokens += int(usage.get("input_tokens") or 0)
    row.cached_input_tokens += int(usage.get("cached_input_tokens") or 0)
    row.output_tokens += int(usage.get("output_tokens") or 0)
    try:
        from analysis.costs import cost_usd

        cost = cost_usd(usage, started_at=meta.get("started_at"))["cost_usd"]
    except Exception:  # pricing is a convenience here; the analysis report has the real cost columns
        cost = None
    if cost is None:
        row.unpriced += 1
    else:
        row.cost_usd = (row.cost_usd or 0.0) + cost


def _problem_text(meta: dict) -> str:
    from analysis.tables import error_line

    if meta.get("status") == "timeout":
        line = error_line(meta.get("error"))
        dur = meta.get("duration_sec")
        return line or (f"timed out after {duration(dur)}" if isinstance(dur, int | float) else "timed out")
    return error_line(meta.get("error")) or error_line(meta.get("notes")) or "(no error text)"


# ---- formatting ----


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    s = round(seconds)
    if s < 60:
        return f"{s} s"
    m = round(s / 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def _gib(mb: float | None) -> str:
    return "–" if mb is None else f"{mb / 1024:.1f} GiB"


def _tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def _ts(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def when(value) -> str:
    t = _ts(value)
    return t.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if t else "–"


def _style(text: str, status: str | None = None, *, bold: bool = False, dim: bool = False) -> str:
    colour = {"ok": typer.colors.GREEN, "timeout": typer.colors.YELLOW, "unsupported": None}.get(status or "")
    if status in ("error", "cancelled", "?"):
        colour = typer.colors.RED
    dim = dim or status == "unsupported"
    if colour is None and not bold and not dim:
        return text  # no escape codes on plain cells: log files stay readable
    return typer.style(text, fg=colour, bold=bold or None, dim=dim or None)


def _table(header: list[str], rows: list[list[tuple[str, str | None]]], right: set[int]) -> list[str]:
    """Aligned plain-text table; cells are (text, status for colour). Widths come from the plain text, so ANSI
    colours never break the alignment."""
    widths = [len(h) for h in header]
    for row in rows:
        for i, (text, _) in enumerate(row):
            widths[i] = max(widths[i], len(text))

    def pad(i: int, text: str) -> str:
        return text.rjust(widths[i]) if i in right else text.ljust(widths[i])

    out = [_style("  ".join(pad(i, h) for i, h in enumerate(header)).rstrip(), bold=True)]
    for row in rows:
        out.append("  ".join(_style(pad(i, t), s) for i, (t, s) in enumerate(row)).rstrip())
    return out


def _statuses(ov: Overview) -> list[str]:
    seen = set(ov.counts)
    return [s for s in STATUS_ORDER if s in seen or s in ("ok", "error", "timeout")] + sorted(seen - set(STATUS_ORDER))


def _counts_line(counts: Counter, statuses: list[str]) -> str:
    return "   ".join(_style(f"{s} {counts.get(s, 0)}", s if counts.get(s) else None) for s in statuses)


def render(ov: Overview, *, outcome: str | None = None, session: dict | None = None, spec_file: str | None = None,
           live: dict | None = None) -> list[str]:  # fmt: skip
    """The overview as terminal lines. `outcome` and `session` describe the run that just ended; `live` is the
    Redis progress for `experiment status`."""
    lines: list[str] = []
    stored = f"{ov.stored} of {ov.expected} runs stored" if ov.expected is not None else f"{ov.stored} runs stored"
    state = outcome or (live or {}).get("status") or ("complete" if ov.missing == 0 else None)
    title = f"{ov.name}: {state}, {stored}" if state else f"{ov.name}: {stored}"
    rule = "─" * max(len(title), 60)
    lines += ["", _style(rule, dim=True), _style(title, bold=True), _style(rule, dim=True)]

    label = 14
    if session:
        start, end = _ts(session.get("started_at")), _ts(session.get("finished_at") or session.get("updated_at"))
        parts = []
        if start and end:
            parts.append(duration((end - start).total_seconds()))
        parts.append(f"{session.get('results_stored', 0)} results stored")
        host = session.get("host") or {}
        if session.get("ram_peak_used_mb") and host.get("ram_total_mb"):
            parts.append(f"VM RAM peak {_gib(session['ram_peak_used_mb'])} of {_gib(host['ram_total_mb'])}")
        budget = session.get("memory_budget_gb")
        waves = session.get("waves") or []
        if budget:
            parts.append(f"budget {budget:g} GiB in {len(waves)} wave(s)")
        lines.append(f"{'this session':<{label}}" + ", ".join(parts))
    elif ov.sessions:
        last = ov.sessions[-1]
        lines.append(f"{'last session':<{label}}{last.get('command', '?')} {last.get('status', '?')}, "
                     f"finished {when(last.get('finished_at') or last.get('updated_at'))}")  # fmt: skip
    if live:
        lines += _live_lines(live, label)

    statuses = _statuses(ov)
    lines.append(f"{'all runs':<{label}}" + _counts_line(ov.counts, statuses)
                 + (f"   missing {ov.missing}" if ov.missing else ""))  # fmt: skip
    lines.append("")

    # columns without any value (memory on runs from before 2026-10-18, nothing missing) are left out
    show_missing = any(t.missing for t in ov.tools)
    show_memory = any(t.peak_mb is not None for t in ov.tools)
    header = ["tool", "runs", *statuses] + (["missing"] if show_missing else []) + ["median", "max"]
    header += ["peak RAM", "OOM"] if show_memory else []
    rows = []
    for t in sorted(ov.tools, key=lambda r: r.tool):
        cells = [(t.tool, None), (str(t.stored), None)]
        cells += [(str(t.counts.get(s, 0)), s if t.counts.get(s) else None) for s in statuses]
        if show_missing:
            cells.append((str(t.missing) if t.missing else "–", "timeout" if t.missing else None))
        cells.append((duration(statistics.median(t.durations)) if t.durations else "–", None))
        cells.append((duration(max(t.durations)) if t.durations else "–", None))
        if show_memory:
            peak = _gib(t.peak_mb)
            if t.peak_mb is not None and t.limit_mb:
                peak = f"{t.peak_mb / 1024:.1f} / {t.limit_mb / 1024:.0f} GiB"
            cells.append((peak, None))
            cells.append((str(t.oom_kills) if t.oom_kills else "–", "error" if t.oom_kills else None))
        rows.append(cells)
    lines += _table(header, rows, right=set(range(1, len(header))))

    llm = [t for t in ov.tools if t.requests]
    if llm:
        lines.append("")
        for t in sorted(llm, key=lambda r: r.tool):
            cost = "" if t.cost_usd is None else f", about ${t.cost_usd:.2f}"
            if t.unpriced:
                cost += f" ({t.unpriced} run(s) without a price)"
            lines.append(
                f"{'LLM usage' if t is llm[0] else '':<{label}}{t.tool}: {t.requests} requests, "
                f"{_tokens(t.input_tokens)} input ({_tokens(t.cached_input_tokens)} cached) / "
                f"{_tokens(t.output_tokens)} output tokens{cost}"
            )

    if ov.problems:
        lines += ["", _style(f"not ok ({len(ov.problems)}), with the most telling error line:", bold=True)]
        tool_w = max(len(p.tool) for p in ov.problems)
        repo_w = max(len(_where(p)) for p in ov.problems)
        for p in ov.problems[:MAX_PROBLEMS]:
            text = p.text if len(p.text) <= ERROR_WIDTH else p.text[: ERROR_WIDTH - 1] + "…"
            lines.append(f"  {_style(f'{p.status:<8}', p.status)} {p.tool:<{tool_w}}  {_where(p):<{repo_w}}  {text}")
        if len(ov.problems) > MAX_PROBLEMS:
            lines.append(f"  … and {len(ov.problems) - MAX_PROBLEMS} more (runs.csv of the analysis report lists all)")

    lines += ["", *_next_steps(ov, spec_file or ov.spec_file, live, label)]
    return lines


def _where(p: Problem) -> str:
    where = p.repo + ("" if p.variant == "original" else f"@{p.variant}")
    return where + (f" run{p.run}" if p.run > 1 else "")


def _live_lines(live: dict, label: int) -> list[str]:
    lines = []
    wave = live.get("wave")
    queued, running = live.get("queued", 0), live.get("running") or []
    progress = f"{queued} queued, {len(running)} running" + (f", wave {wave}" if wave else "")
    if live.get("updated_at"):
        progress += f", last update {when(live['updated_at'])}"
    lines.append(f"{'in Redis':<{label}}{progress}")
    now = datetime.now(UTC)
    for job in running:
        since = _ts(job.get("started_at"))
        age = f" ({duration((now - since).total_seconds())})" if since else ""
        started = when(job.get("started_at"))
        lines.append(f"{'':<{label}}  running {job['tool']:<15} {job['repo']}  since {started}{age}")
    return lines


def _next_steps(ov: Overview, spec_file: str, live: dict | None, label: int) -> list[str]:
    steps = []
    if live and live.get("status") == "running":
        steps.append((f"make experiment-status NAME={ov.name}", "check again later"))
    else:
        failed = sum(ov.counts.get(s, 0) for s in NOT_OK)
        if failed:
            steps.append((f"make experiment EXP={spec_file} ARGS=--retry-failed", f"redo the {failed} run(s) not ok"))
        if ov.missing:
            steps.append((f"make experiment EXP={spec_file}", f"resume the {ov.missing} missing run(s)"))
        steps.append((f"make experiment-export NAME={ov.name}", "analysis report + zip into experiments/results/"))
    width = max(len(cmd) for cmd, _ in steps)
    return [
        f"{'next' if i == 0 else '':<{label}}{_style(cmd.ljust(width), bold=True)}   {why}"
        for i, (cmd, why) in enumerate(steps)
    ]


def list_rows(overviews: list[Overview]) -> list[str]:
    """One line per experiment for `experiment list`."""
    header = ["experiment", "stored", "expected", "ok", "not ok", "unsupported", "last session"]
    rows = []
    for ov in overviews:
        c = ov.counts
        not_ok = sum(n for s, n in c.items() if s not in ("ok", "unsupported"))
        last = ov.sessions[-1] if ov.sessions else {}
        session = f"{when(last.get('finished_at') or last.get('updated_at'))} {last.get('status', '')}".strip()
        rows.append(
            [
                (ov.name, None),
                (str(ov.stored), None),
                ("–" if ov.expected is None else str(ov.expected), None),
                (str(c.get("ok", 0)), "ok" if c.get("ok") else None),
                (str(not_ok), "error" if not_ok else None),
                (str(c.get("unsupported", 0)), None),
                (session if last else "–", None),
            ]
        )
    return _table(header, rows, right={1, 2, 3, 4, 5})
