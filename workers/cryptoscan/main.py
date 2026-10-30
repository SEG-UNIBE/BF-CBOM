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


def base_command(repo_path: Path, out_file: Path) -> list[str]:
    return [BINARY, "scan", str(repo_path), "--format", "cbom", "--output", str(out_file), *BASE_ARGS, *EXTRA_ARGS]


def run_cryptoscan(repo_path: Path, out_file: Path, trace: Trace, *, timeout: float) -> None:
    """Run CryptoScan once, as released. 1.4.0 panicked in `patterns.MatchWithContext` on some files, which ended the
    whole scan (1.5.0's notes say such lines no longer end it); a run that still panics fails with the panic head in
    the error. No crash workaround: 1.4.0's `--exclude` matched a file by its basename only, so it could not leave out
    exactly the file it crashed on (docs/findings.md "Interventions")."""
    cmd = base_command(repo_path, out_file)
    trace.add(f"cryptoscan cmd: {' '.join(cmd)}")
    proc = _scan(cmd, repo_path, timeout)
    output = _output(proc)
    # cryptoscan exits non-zero when findings exceed a severity threshold; only a missing output is an error
    if not out_file.exists() or out_file.stat().st_size == 0:
        kind = "cryptoscan_panic" if panicked(output) else "cryptoscan_no_output"
        raise RuntimeError(f"{kind} rc={proc.returncode}: {error_excerpt(output)[:1200]}")
    if proc.returncode != 0:
        tail = "\n".join(output.strip().splitlines()[-8:])
        trace.add(f"cryptoscan exit code {proc.returncode} (output written): {tail[:300]}")


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
        run_cryptoscan(Path(cloned), out_file, trace, timeout=max(10.0, TIMEOUT_SEC - 20.0))
        raw = out_file.read_text(encoding="utf-8")
    cbom = json.loads(raw)  # fail early on malformed output
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
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
