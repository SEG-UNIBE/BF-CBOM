"""CSNP CryptoScan worker: pattern-based scanner with native CycloneDX 1.6 CBOM output (Apache-2.0)."""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from common.models import JobInstruction, Trace
from common.utils import clone_repo
from common.worker import build_handle_instruction, run_worker

NAME = os.getenv("WORKER_NAME") or os.path.basename(os.path.dirname(__file__))
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "600"))
BINARY = os.getenv("CRYPTOSCAN_BIN", "cryptoscan")
# without it CryptoScan leaves classical algorithms (AES, SHA-2, HMAC, PBKDF2) out of the CBOM
BASE_ARGS = ["--include-quantum-safe"]
EXTRA_ARGS = os.getenv("CRYPTOSCAN_ARGS", "").split()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def cryptoscan_version() -> str | None:
    try:
        out = subprocess.run([BINARY, "version"], capture_output=True, text=True, timeout=30)
        text = (out.stdout or "") + (out.stderr or "")
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", text)
        return m.group(1) if m else (text.strip().splitlines() or [None])[0]
    except Exception as e:  # pragma: no cover - only when the binary is missing
        logger.warning("could not determine cryptoscan version: %s", e)
        return None


PANIC_RE = re.compile(r"^(panic: .*|goroutine \d+ \[.*\]:|fatal error: .*)$", re.MULTILINE)
MAX_BISECT_RUNS = int(os.getenv("CRYPTOSCAN_MAX_BISECT_RUNS", "200"))


def _scan(cmd: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("cryptoscan_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("cryptoscan_not_found") from exc


def _output(proc: subprocess.CompletedProcess) -> str:
    return (proc.stderr or "") + (proc.stdout or "")


def panicked(output: str) -> bool:
    return bool(PANIC_RE.search(output))


def error_excerpt(output: str, *, tail_lines: int = 8) -> str:
    """The Go panic head (the panic message and the first goroutine line) plus the last lines of the output, so the
    stored error names the failure, not only the deepest stack frames."""
    lines = output.strip().splitlines()
    head = [ln for ln in lines if PANIC_RE.match(ln)][:2]
    tail = lines[-tail_lines:]
    return "\n".join(dict.fromkeys(head + tail))  # keep order, drop duplicates


def exclude_pattern(repo_path: Path, path: Path) -> str:
    """A `--exclude` pattern for one entry: `<rel>/**` for a directory, the path from the scan root for a file."""
    rel = path.relative_to(repo_path).as_posix()
    return f"{rel}/**" if path.is_dir() else rel


def base_command(repo_path: Path, out_file: Path, excludes: list[str] = ()) -> list[str]:
    cmd = [BINARY, "scan", str(repo_path), "--format", "cbom", "--output", str(out_file), *BASE_ARGS, *EXTRA_ARGS]
    if excludes:
        cmd += ["--exclude", ",".join(excludes)]
    return cmd


def find_crashing_files(repo_path: Path, out_file: Path, trace: Trace, *, deadline: float) -> list[str]:
    """The files whose content makes CryptoScan panic, found by bisection with `--exclude` (the panic itself does not
    name the file). "Scanning only a set of entries" = excluding every other candidate entry; a set that still panics
    is split in halves, a panicking directory is opened, a panicking single file is a culprit. Directories and
    nested files are excluded by their path from the scan root; a top-level file can only be excluded by name (which
    also hides deeper files of that name), so the top-level files are bisected first, with every directory excluded,
    and are never excluded while the directories are bisected. Returns the culprits as paths from the scan root."""
    culprits: list[str] = []
    runs = 0

    def scan_only(keep: list[Path], others: list[Path]) -> bool:
        nonlocal runs
        runs += 1
        if runs > MAX_BISECT_RUNS:
            raise RuntimeError(f"cryptoscan_bisect_limit: {MAX_BISECT_RUNS} scans without isolating the panic")
        remaining = deadline - time.monotonic()
        if remaining < 5:
            raise TimeoutError("cryptoscan_timeout")
        out_file.unlink(missing_ok=True)
        excludes = [exclude_pattern(repo_path, p) for p in others] + culprits
        proc = _scan(base_command(repo_path, out_file, excludes), repo_path, remaining)
        return panicked(_output(proc))

    def bisect(candidates: list[Path], excluded: list[Path]) -> None:
        """`candidates` are known (or assumed) to panic when scanned with `excluded` left out."""
        if len(candidates) == 1:
            entry = candidates[0]
            if entry.is_dir():
                children = sorted(p for p in entry.iterdir() if p.name != ".git")
                if children:
                    bisect(children, excluded)
                return
            culprits.append(entry.relative_to(repo_path).as_posix())
            trace.add(f"cryptoscan panics on {culprits[-1]}")
            return
        half = len(candidates) // 2
        for part, rest in ((candidates[:half], candidates[half:]), (candidates[half:], candidates[:half])):
            if scan_only(part, excluded + rest):
                bisect(part, excluded + rest)

    top = sorted(p for p in repo_path.iterdir() if p.name != ".git")
    top_files, top_dirs = [p for p in top if not p.is_dir()], [p for p in top if p.is_dir()]
    if top_files and scan_only(top_files, top_dirs):
        bisect(top_files, top_dirs)
    if top_dirs:
        bisect(top_dirs, [])
    trace.add(f"cryptoscan bisection: {runs} scans, {len(culprits)} crashing file(s)")
    return culprits


def run_cryptoscan(repo_path: Path, out_file: Path, trace: Trace, *, timeout: float) -> list[str]:
    """Run CryptoScan; on a Go panic, find the crashing files, exclude exactly those and rerun (refinement 15: a
    minimal workaround that lets the tool produce output, recorded in the run notes). Returns the excluded files."""
    deadline = time.monotonic() + timeout
    cmd = base_command(repo_path, out_file)
    trace.add(f"cryptoscan cmd: {' '.join(cmd)}")
    proc = _scan(cmd, repo_path, timeout)
    output = _output(proc)
    excluded: list[str] = []
    if (not out_file.exists() or out_file.stat().st_size == 0) and panicked(output):
        trace.add(f"cryptoscan panicked: {error_excerpt(output, tail_lines=2)[:300]}")
        excluded = find_crashing_files(repo_path, out_file, trace, deadline=deadline)
        if excluded:
            cmd = base_command(repo_path, out_file, excluded)
            trace.add(f"cryptoscan cmd (retry): {' '.join(cmd)}")
            out_file.unlink(missing_ok=True)
            proc = _scan(cmd, repo_path, max(5.0, deadline - time.monotonic()))
            output = _output(proc)
    # cryptoscan exits non-zero when findings exceed a severity threshold; only a missing output is an error
    if not out_file.exists() or out_file.stat().st_size == 0:
        raise RuntimeError(f"cryptoscan_no_output rc={proc.returncode}: {error_excerpt(output)[:1200]}")
    if proc.returncode != 0:
        tail = "\n".join(output.strip().splitlines()[-8:])
        trace.add(f"cryptoscan exit code {proc.returncode} (output written): {tail[:300]}")
    if excluded:
        trace.add(
            f"workaround: cryptoscan --exclude {','.join(excluded)} (Go panic in patterns.MatchWithContext on these "
            "files; the tool is unchanged)"
        )
    return excluded


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="cryptoscan-") as tmp:
        repo_dir = Path(tmp) / "repo"
        out_file = Path(tmp) / "cbom.json"
        cloned = clone_repo(
            instr.repo_info.git_url,
            branch=instr.repo_info.branch,
            target_dir=str(repo_dir),
            commit=instr.repo_info.commit,
        )
        if not cloned:
            trace.add("git clone failed")
            raise RuntimeError("git_clone_failed")
        excluded = run_cryptoscan(Path(cloned), out_file, trace, timeout=max(10.0, TIMEOUT_SEC - 20.0))
        raw = out_file.read_text(encoding="utf-8")
    cbom = json.loads(raw)  # fail early on malformed output
    logger.info(
        "%s: %d components%s",
        instr.repo_info.full_name,
        len(cbom.get("components") or []),
        f" ({len(excluded)} crashing files excluded)" if excluded else "",
    )
    return raw, time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    version = cryptoscan_version()
    logger.info("cryptoscan version: %s", version)
    run_worker(
        NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=f"cryptoscan {version}" if version else None
    )


if __name__ == "__main__":
    logger.info("starting up...")
    main()
