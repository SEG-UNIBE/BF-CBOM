import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import click
import redis
import typer

from common.models import InspectionConfig, RepoRef
from coordinator.redis_io import (
    cancel_inspection,
    collect_results_once,
    create_inspection,
    get_insp_meta,
    get_insp_repos,
    get_insp_workers,
    list_inspections,
    now_iso,
    pair_key,
    start_inspection,
)

app = typer.Typer(
    add_completion=False,
    help="BF-CBOM CLI: create and run inspections",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

export_app = typer.Typer(
    help="Export inspection artifacts",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(export_app, name="export")


EXPORT_DEST_OPTION = typer.Option(..., "--dest", "-d", help="Directory to write the ZIP")


_STATUS_COLORS = {
    "completed": typer.colors.GREEN,
    "running": typer.colors.CYAN,
    "pending": typer.colors.BLUE,
    "failed": typer.colors.RED,
    "cancelled": typer.colors.YELLOW,
}


def _style_status(status: str) -> str:
    color = _STATUS_COLORS.get((status or "").lower(), typer.colors.WHITE)
    return typer.style((status or "").ljust(9), fg=color, bold=True)


def _style_count(value: int, color: str, width: int = 3, align_left: bool = False) -> str:
    text = str(value)
    text = text.ljust(width) if align_left else text.rjust(width)
    return typer.style(text, fg=color, bold=True)


def _connect_redis(host: str, port: int) -> redis.Redis:
    r = redis.Redis(host=host, port=port, decode_responses=True)
    try:
        r.ping()
    except redis.exceptions.RedisError as e:
        typer.echo(f"Error: cannot connect to Redis at {host}:{port}: {e}", err=True)
        raise typer.Exit(2) from e
    return r


def _read_config_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as f:
        return f.read()


def _summarize_inspection(r: redis.Redis, insp_id: str) -> dict:
    meta = get_insp_meta(r, insp_id)
    jobs = r.lrange(f"insp:{insp_id}:jobs", 0, -1) or []
    total = len(jobs)
    counts = {"completed": 0, "failed": 0, "cancelled": 0, "pending": 0}
    for j in jobs:
        st = r.hget(f"insp:{insp_id}:job:{j}", "status") or ""
        if st in counts:
            counts[st] += 1
        else:
            counts["pending"] += 1
    done = counts["completed"] + counts["failed"] + counts["cancelled"]
    return {
        "insp_id": insp_id,
        "name": meta.get("name", insp_id),
        "status": meta.get("status", ""),
        "counts": counts,
        "done": done,
        "total": total,
    }


def _resolve_insp_id(r: redis.Redis, insp_id: str) -> str:
    """Allow prefix matching for inspection identifiers when unique."""

    if not insp_id:
        raise ValueError("Inspection ID is required")

    if r.exists(f"insp:{insp_id}"):
        return insp_id

    inspections = r.smembers("inspecs") or []
    matches = [bid for bid in inspections if bid.startswith(insp_id)]

    if not matches:
        raise ValueError(f"No inspection found matching '{insp_id}'")
    if len(matches) > 1:
        sample = ", ".join(sorted(matches)[:5])
        raise ValueError(
            f"Inspection prefix '{insp_id}' is ambiguous "
            f"({len(matches)} matches: {sample}{'…' if len(matches) > 5 else ''})"
        )

    return matches[0]


def _clear_screen() -> None:
    # ANSI clear + home
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def _hide_cursor() -> None:
    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()


def _show_cursor() -> None:
    sys.stdout.write("\x1b[?25h")
    sys.stdout.flush()


def _render_lines(lines: list[str]) -> None:
    output = "\n".join(lines)
    _clear_screen()
    sys.stdout.write(output)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _build_cboms_zip_bytes(r: redis.Redis, insp_id: str) -> tuple[bytes, int]:
    """Collect successful CBOM payloads for an inspection and package them into a ZIP."""

    repos = get_insp_repos(r, insp_id) or []
    workers = get_insp_workers(r, insp_id) or []

    mem = io.BytesIO()
    written = 0
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for repo in repos:
            full = repo.get("full_name")
            safe_repo = (full or "").replace("/", "_")
            for worker in workers:
                job_key = pair_key(full or "", worker)
                job_id = r.hget(f"insp:{insp_id}:job_index", job_key)
                if not job_id:
                    continue
                raw = r.hget(f"insp:{insp_id}:job:{job_id}", "result_json")
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                if payload.get("status") != "ok":
                    continue
                content = payload.get("json") or "{}"
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and isinstance(parsed.get("bom"), dict):
                        parsed = parsed["bom"]
                    content = json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)
                except Exception:
                    # Keep original content if it is not valid JSON
                    pass
                archive_path = f"{insp_id}/{worker}/{safe_repo}_{worker}.json"
                zf.writestr(archive_path, content)
                written += 1
    mem.seek(0)
    return mem.read(), written


@app.command()
def watch(
    insp_id: str | None = typer.Option(None, "--insp-id", "-b", help="Follow a specific inspection"),
    redis_host: str = typer.Option("localhost", envvar="REDIS_HOST"),
    redis_port: int = typer.Option(6379, envvar="REDIS_PORT"),
    interval: float = typer.Option(2.0, help="Refresh interval seconds"),
):
    """Interactive-like watch view. Ctrl-C to exit."""
    r = _connect_redis(redis_host, redis_port)
    cursor_hidden = False
    try:
        _hide_cursor()
        cursor_hidden = True
        while True:
            lines: list[str] = []
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            if insp_id:
                summary = _summarize_inspection(r, insp_id)
                title = typer.style("BF-CBOM Watch", fg=typer.colors.BRIGHT_BLUE, bold=True)
                timestamp = typer.style(now, fg=typer.colors.BRIGHT_BLACK)
                lines.append(f"{title} · {timestamp}")
                name = typer.style(summary["name"], fg=typer.colors.WHITE, bold=True)
                insp_short = typer.style(insp_id[:8], fg=typer.colors.BRIGHT_CYAN, bold=True)
                status = _style_status(summary["status"])
                lines.append(f"{name} · {insp_short} · status={status}")
                c = summary["counts"]
                done = typer.style(str(summary["done"]), fg=typer.colors.BRIGHT_GREEN, bold=True)
                total = typer.style(str(summary["total"]), fg=typer.colors.BRIGHT_BLACK, bold=True)
                completed = _style_count(c["completed"], typer.colors.GREEN)
                failed = _style_count(c["failed"], typer.colors.RED)
                cancelled = _style_count(c["cancelled"], typer.colors.YELLOW)
                pending = _style_count(c["pending"], typer.colors.BLUE)
                lines.append(
                    f"done {done}/{total} · "
                    f"completed={completed} failed={failed} cancelled={cancelled} pending={pending}"
                )
            else:
                inspections = list_inspections(r)
                title = typer.style("BF-CBOM Watch", fg=typer.colors.BRIGHT_BLUE, bold=True)
                timestamp = typer.style(now, fg=typer.colors.BRIGHT_BLACK)
                insp_count = typer.style(str(len(inspections)), fg=typer.colors.BRIGHT_CYAN, bold=True)
                lines.append(f"{title} · {timestamp} · {insp_count} inspections")
                header_text = (
                    "id       name                          status    done/total   completed  failed  cancelled"
                )
                lines.append(typer.style(header_text, fg=typer.colors.WHITE, bold=True))
                lines.append(typer.style("-" * 90, fg=typer.colors.BRIGHT_BLACK))
                for bid, meta in inspections[:50]:
                    s = _summarize_inspection(r, bid)
                    nm = (meta.get("name", bid) or "").strip()
                    nm = (nm[:28] + "…") if len(nm) > 29 else nm.ljust(29)
                    nm_display = typer.style(nm, fg=typer.colors.WHITE)
                    status = _style_status(s["status"])
                    done_text = str(s["done"]).rjust(3)
                    total_text = str(s["total"]).ljust(3)
                    done = typer.style(done_text, fg=typer.colors.BRIGHT_GREEN, bold=True)
                    total = typer.style(total_text, fg=typer.colors.BRIGHT_BLACK, bold=True)
                    completed = _style_count(s["counts"]["completed"], typer.colors.GREEN)
                    failed = _style_count(s["counts"]["failed"], typer.colors.RED)
                    cancelled = _style_count(s["counts"]["cancelled"], typer.colors.YELLOW)
                    lines.append(
                        f"{typer.style(bid[:8], fg=typer.colors.BRIGHT_CYAN, bold=True)}  "
                        f"{nm_display}  {status}  "
                        f"{done}/{total}      {completed}       {failed}    {cancelled}"
                    )
                lines.append("")
                lines.append("Use --insp-id to follow one. Ctrl-C to exit.")
            _render_lines(lines)
            time.sleep(max(0.2, interval))
    except KeyboardInterrupt:
        return
    finally:
        if cursor_hidden:
            _show_cursor()


@app.command()
def run(
    config: str = typer.Option(..., "--config", "-c", help="Path to config JSON or '-' for stdin"),
    name: str | None = typer.Option(None, "--name", "-n", help="Override inspection name from config"),
    redis_host: str = typer.Option("localhost", envvar="REDIS_HOST", help="Redis host"),
    redis_port: int = typer.Option(6379, envvar="REDIS_PORT", help="Redis port"),
    wait: bool = typer.Option(False, "--wait", help="Wait for completion"),
    poll_interval: float = typer.Option(2.0, help="Polling interval when waiting (sec)"),
    timeout: int | None = typer.Option(None, help="Max seconds to wait before exiting"),
):
    """Create an inspection from config, start it, and optionally wait."""

    cfg_text = _read_config_text(config)
    try:
        cfg = InspectionConfig.from_json(cfg_text)
    except Exception as e:
        typer.echo(f"Error: invalid CLI config: {e}", err=True)
        raise typer.Exit(2) from e

    workers = list(cfg.workers or [])
    repos_refs = list(cfg.repos or [])
    if not workers:
        typer.echo("Error: config must include non-empty 'workers'", err=True)
        raise typer.Exit(2)
    if not repos_refs:
        typer.echo("Error: config must include non-empty 'repos'", err=True)
        raise typer.Exit(2)

    insp_name = name or (cfg.name or f"cli-{int(time.time())}")
    params = {"source": "cli", "schema_version": str(cfg.schema_version or "1")}

    r = _connect_redis(redis_host, redis_port)
    repos_payload = [
        {
            "full_name": rr.full_name,
            "git_url": rr.git_url,
            **({"branch": rr.branch} if rr.branch else {}),
        }
        for rr in repos_refs
    ]
    insp_id = create_inspection(r, name=insp_name, params=params, repos=repos_payload, workers=workers)
    typer.echo(insp_id)
    issued = start_inspection(r, insp_id)
    typer.secho(f"Issued jobs: {issued}", fg=typer.colors.BLUE, err=True)

    if not wait:
        return

    start_ts = time.time()
    while True:
        done, total = collect_results_once(r, insp_id)
        summary = _summarize_inspection(r, insp_id)
        typer.secho(
            f"Progress: {summary['done']}/{summary['total']} "
            f"(completed={summary['counts']['completed']} failed={summary['counts']['failed']})",
            fg=typer.colors.BLUE,
            err=True,
        )
        if total and done >= total:
            # Mark completed for consistency with GUI page behavior
            r.hset(
                f"insp:{insp_id}",
                mapping={"status": "completed", "finished_at": now_iso()},
            )
            break
        if timeout is not None and (time.time() - start_ts) > timeout:
            typer.secho("Timeout waiting for completion", fg=typer.colors.RED, err=True)
            raise typer.Exit(124)
        time.sleep(max(0.1, poll_interval))

    final = _summarize_inspection(r, insp_id)
    failed = final["counts"]["failed"]
    typer.echo(json.dumps(final, ensure_ascii=False))
    raise typer.Exit(1 if failed else 0)


@app.command()
def status(
    insp_id: str = typer.Argument(..., help="Inspection ID (supports unique prefix)"),
    redis_host: str = typer.Option("localhost", envvar="REDIS_HOST", help="Redis host"),
    redis_port: int = typer.Option(6379, envvar="REDIS_PORT", help="Redis port"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON summary"),
):
    """Show current status of an inspection."""
    r = _connect_redis(redis_host, redis_port)
    try:
        resolved_id = _resolve_insp_id(r, insp_id)
    except ValueError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(1) from err

    if resolved_id != insp_id:
        typer.echo(f"Resolved inspection ID '{insp_id}' -> '{resolved_id}'", err=True)

    summary = _summarize_inspection(r, resolved_id)
    if json_out:
        typer.echo(json.dumps(summary, ensure_ascii=False))
    else:
        typer.echo(
            f"{summary['name']} · {resolved_id[:8]} · status={summary['status']} · "
            f"completed={summary['counts']['completed']} "
            f"failed={summary['counts']['failed']} "
            f"cancelled={summary['counts']['cancelled']} "
            f"total={summary['total']}"
        )


@app.command()
def cancel(
    insp_id: str = typer.Argument(..., help="Inspection ID (supports unique prefix)"),
    redis_host: str = typer.Option("localhost", envvar="REDIS_HOST", help="Redis host"),
    redis_port: int = typer.Option(6379, envvar="REDIS_PORT", help="Redis port"),
):
    """Cancel a running inspection by removing queued jobs and marking them cancelled."""

    r = _connect_redis(redis_host, redis_port)
    try:
        resolved_id = _resolve_insp_id(r, insp_id)
    except ValueError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(1) from err

    if resolved_id != insp_id:
        typer.echo(f"Resolved inspection ID '{insp_id}' -> '{resolved_id}'", err=True)

    if not get_insp_meta(r, resolved_id):
        typer.echo(f"No such inspection: {insp_id}", err=True)
        raise typer.Exit(1)

    cancelled = cancel_inspection(r, resolved_id)
    typer.echo(f"Cancelled {cancelled} pending job(s) for {resolved_id[:8]}")


@export_app.callback(invoke_without_command=True)
def export_default(
    ctx: typer.Context,
    out: str | None = typer.Option(
        None,
        "--out",
        "-o",
        help="Output file when exporting configs; defaults to stdout",
    ),
    redis_host: str = typer.Option("localhost", envvar="REDIS_HOST", help="Redis host"),
    redis_port: int = typer.Option(6379, envvar="REDIS_PORT", help="Redis port"),
) -> None:
    """Backward-compatible entry point: `cli export` defaults to config export."""

    if ctx.invoked_subcommand is not None:
        return

    remaining = list(ctx.args)
    subcommands = set(ctx.command.commands.keys())

    if not remaining:
        typer.echo(ctx.get_help())
        raise typer.Exit(1)

    candidate = remaining.pop(0)

    if candidate in subcommands:
        if remaining and remaining[0] in ("-h", "--help"):
            remaining.pop(0)
            cmd = ctx.command.commands[candidate]
            with click.Context(cmd, info_name=candidate, parent=ctx) as sub_ctx:
                typer.echo(cmd.get_help(sub_ctx))
            raise typer.Exit()

        typer.echo(
            f"Missing INSP_ID argument for `cli export {candidate}`.\n"
            f"Usage: uv run misc/cli/cli.py export {candidate} <INSP_ID> [options]",
            err=True,
        )
        raise typer.Exit(1)

    insp_id = candidate

    if remaining:
        typer.echo(
            "Unexpected extra arguments: " + " ".join(remaining),
            err=True,
        )
        raise typer.Exit(1)

    ctx.invoke(
        export_config,
        insp_id=insp_id,
        out=out,
        redis_host=redis_host,
        redis_port=redis_port,
    )


@export_app.command("config")
def export_config(
    insp_id: str = typer.Argument(..., help="Inspection ID"),
    out: str | None = typer.Option(
        None,
        "--out",
        "-o",
        help="Write JSON to this file instead of stdout",
    ),
    redis_host: str = typer.Option("localhost", envvar="REDIS_HOST", help="Redis host"),
    redis_port: int = typer.Option(6379, envvar="REDIS_PORT", help="Redis port"),
) -> None:
    """Export a minimal config snapshot for an inspection."""

    r = _connect_redis(redis_host, redis_port)
    try:
        resolved_id = _resolve_insp_id(r, insp_id)
    except ValueError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(1) from err

    if resolved_id != insp_id:
        typer.echo(f"Resolved inspection ID '{insp_id}' -> '{resolved_id}'", err=True)

    meta = get_insp_meta(r, resolved_id)
    if not meta:
        typer.echo(f"No such inspection: {insp_id}", err=True)
        raise typer.Exit(1)

    workers = get_insp_workers(r, resolved_id)
    repos = get_insp_repos(r, resolved_id)

    repo_refs: list[RepoRef] = []
    for data in repos:
        full = data.get("full_name")
        git_url = data.get("clone_url") or data.get("git_url") or (f"https://github.com/{full}.git" if full else "")
        branch = data.get("default_branch") or data.get("branch")
        repo_refs.append(RepoRef(full_name=full or "", git_url=git_url or "", branch=branch))

    cfg_obj = InspectionConfig(
        schema_version="1",
        name=meta.get("name", resolved_id) or "",
        workers=workers,
        repos=repo_refs,
    )
    text = cfg_obj.to_json(indent=2)

    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        typer.echo(str(path))
    else:
        typer.echo(text)


@export_app.command("cboms")
def export_cboms(
    insp_id: str = typer.Argument(..., help="Inspection ID"),
    dest: Path = EXPORT_DEST_OPTION,
    filename: str | None = typer.Option(
        None,
        "--filename",
        "-f",
        help="Optional ZIP filename (defaults to cboms_<insp>.zip)",
    ),
    redis_host: str = typer.Option("localhost", envvar="REDIS_HOST", help="Redis host"),
    redis_port: int = typer.Option(6379, envvar="REDIS_PORT", help="Redis port"),
) -> None:
    """Export a ZIP containing successful CBOMs for an inspection."""

    r = _connect_redis(redis_host, redis_port)
    try:
        resolved_id = _resolve_insp_id(r, insp_id)
    except ValueError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(1) from err

    if resolved_id != insp_id:
        typer.echo(f"Resolved inspection ID '{insp_id}' -> '{resolved_id}'", err=True)

    meta = get_insp_meta(r, resolved_id)
    if not meta:
        typer.echo(f"No such inspection: {insp_id}", err=True)
        raise typer.Exit(1)

    dest = dest.expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    zip_name = filename or f"cboms_{resolved_id[:8]}.zip"
    out_path = dest / zip_name

    data, count = _build_cboms_zip_bytes(r, resolved_id)
    out_path.write_bytes(data)

    if count == 0:
        typer.echo(f"Created empty archive (no successful CBOMs) at {out_path}", err=True)
    else:
        typer.echo(f"Wrote {count} CBOM(s) for {resolved_id[:8]} to {out_path}")


@app.command()
def banner(
    redis_host: str = typer.Option(os.getenv("REDIS_HOST", "localhost"), "--redis-host"),
    redis_port: int = typer.Option(int(os.getenv("REDIS_PORT", "6379")), "--redis-port"),
) -> None:
    """Print usage hints and Redis connectivity, then exit."""
    ok = False
    try:
        r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        r.ping()
        ok = True
    except Exception:
        ok = False

    status = "ok" if ok else "unreachable"
    # Read ASCII art and print it verbatim, preserving any ANSI colors
    # embedded in the file. If there are no ANSI escapes, it will use the
    # terminal's default color.
    try:
        art_text = Path("./docs/logo.txt").read_text(encoding="utf-8")
    except OSError:
        art_text = ""

    if art_text:
        # Always preserve colors exactly as encoded in the file.
        # If the file has no ANSI codes, it will render with the terminal's default color.
        sys.stdout.write(art_text)
        if not art_text.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()

    typer.echo("")
    theme_color = typer.colors.BRIGHT_RED
    typer.secho("BF-CBOM CLI", fg=theme_color, bold=True)
    typer.echo(
        typer.style(
            "Create inspections, monitor their progress, and export artifacts.",
            fg=typer.colors.WHITE,
        )
    )

    status_color = typer.colors.GREEN if ok else typer.colors.RED
    typer.echo("")
    typer.secho(
        f"Redis {status}",
        fg=status_color,
        bold=True,
        nl=False,
    )
    typer.echo(typer.style(f"  {redis_host}:{redis_port}", fg=typer.colors.WHITE))

    typer.echo("")
    # Helper to emphasize the interesting part after the CLI path
    def _echo_uv(args: str) -> None:
        left = typer.style("uv run misc/cli/cli.py", fg=typer.colors.BRIGHT_BLACK)
        right = typer.style(args, bold=True)
        typer.echo(f"  {left} {right}")

    def _echo_piped(prefix: str, args: str) -> None:
        pre = typer.style(prefix, fg=typer.colors.BRIGHT_BLACK)
        left = typer.style("uv run misc/cli/cli.py", fg=typer.colors.BRIGHT_BLACK)
        right = typer.style(args, bold=True)
        typer.echo(f"  {pre} {left} {right}")

    typer.secho("Examples\n", fg=theme_color, bold=True)
    # 1) Watch
    typer.echo(typer.style("  # Watch live progress of existing inspection", fg=theme_color))
    _echo_uv("watch --insp-id <INSP_ID>")
    typer.echo("")
    # 2) Check status
    typer.echo(typer.style("  # Check status of a distinct inspection", fg=theme_color))
    _echo_uv("status <INSP_ID> --json")
    typer.echo("")
    # 3) Download all CBOMs
    typer.echo(typer.style("  # Download all CBOMs as zip", fg=theme_color))
    _echo_uv("export cboms <INSP_ID> --dest ./downloads")
    typer.echo("")
    # 4) Export config
    typer.echo(typer.style("  # Export a config for an existing inspection", fg=theme_color))
    _echo_uv("export config <INSP_ID> -o insp.json")
    typer.echo("")
    # 5) Run again with config (two styles)
    typer.echo(typer.style("  # Run again with config", fg=theme_color))
    _echo_uv("run -c insp.json --wait")
    _echo_piped("cat insp.json |", "run -c - --wait")
    typer.echo("")
    # 6) Cancel running inspection
    typer.echo(typer.style("  # Cancel a running inspection", fg=theme_color))
    _echo_uv("cancel <INSP_ID>")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("--help")
    app()
