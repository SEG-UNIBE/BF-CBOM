"""SCANOSS crypto-finder worker: opengrep rules plus call-graph reachability, native CycloneDX output (GPL-2.0-only).

crypto-finder (https://github.com/scanoss/crypto-finder) takes its detection rules from the SCANOSS API, which needs
a key. Without one the documented offline mode applies: `--no-remote-rules --rules-dir <rules>` with the public open
rule set (https://github.com/scanoss/open-crypto-rules, GPL-3.0), frozen in the image at a pinned commit. It has
rules for Go, C, JavaScript and Rust, none for Java or Python. Binary, opengrep version and rules commit are pinned in
docker/Dockerfile.worker-scanoss.

Command: `crypto-finder scan --no-remote-rules --rules-dir <rules> --format cyclonedx --output out.json --timeout
<worker timeout> <repo>`, otherwise the tool's defaults (test sources and vendor dirs excluded, call graph built,
per-line deduplication). Its own 10 m timeout would cut large scans short, so it gets the worker's. A committed
`scanoss.json` in the repo root sets project skip patterns and cannot be switched off; the run notes record it.
"""

import json
import logging
import math
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
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "1800"))
BINARY = os.getenv("CRYPTO_FINDER_BIN", "crypto-finder")
RULES_DIR = os.getenv("SCANOSS_RULES_DIR", "/opt/scanoss-rules/semgrep-rules")
# set by the Dockerfile, for the version string
OPENGREP_VERSION = os.getenv("OPENGREP_VERSION", "")
RULES_COMMIT = os.getenv("OPEN_RULES_COMMIT", "")
EXTRA_ARGS = os.getenv("SCANOSS_ARGS", "").split()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def build_command(repo_path: Path, out_file: Path, *, timeout_sec: float) -> list[str]:
    """The scan command: offline with the frozen open rules. The repo URL is never passed (it may carry credentials).
    The tool's own timeout lies a minute beyond the worker's, so a long scan ends as a worker timeout."""
    minutes = max(1, math.ceil((timeout_sec + 60) / 60))
    return [
        BINARY, "scan", "--no-remote-rules", "--rules-dir", RULES_DIR, "--format", "cyclonedx",
        "--output", str(out_file), "--timeout", f"{minutes}m", *EXTRA_ARGS, str(repo_path),
    ]  # fmt: skip


def format_version(output: str | None, opengrep: str | None = None, rules_commit: str | None = None) -> str | None:
    """`crypto-finder 0.43.0 (opengrep 1.22.0, open-crypto-rules@55d46c2365c1)` from `crypto-finder version`."""
    m = re.search(r"\b(\d+\.\d+\.\d+)\b", output or "")
    if not m:
        return None
    parts = []
    if opengrep:
        parts.append(f"opengrep {opengrep}")
    if rules_commit:
        parts.append(f"open-crypto-rules@{rules_commit[:12]}")
    extra = ", ".join(parts)
    return f"crypto-finder {m.group(1)}" + (f" ({extra})" if extra else "")


def scanoss_version() -> str | None:
    try:
        out = subprocess.run([BINARY, "version"], capture_output=True, text=True, timeout=30)
        text = (out.stdout or "") + (out.stderr or "")
    except Exception as e:  # pragma: no cover - only when the binary is missing
        logger.warning("could not determine crypto-finder version: %s", e)
        text = ""
    return format_version(text, OPENGREP_VERSION, RULES_COMMIT)


def run_scanoss(repo_path: Path, out_file: Path, trace: Trace, *, timeout: float) -> None:
    if (repo_path / "scanoss.json").exists():
        trace.add("repo has a scanoss.json: its project skip patterns apply (the tool reads it from the scan root)")
    cmd = build_command(repo_path, out_file, timeout_sec=timeout)
    trace.add(f"crypto-finder cmd: {' '.join(cmd)}")
    try:
        # cwd outside the clone; project settings come from the scan root (docs/CONFIGURATION.md)
        proc = subprocess.run(
            cmd, cwd=str(out_file.parent), capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("scanoss_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("scanoss_not_found") from exc
    tail = "\n".join(((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-8:])
    # rc 0 = report written; findings never change it (no --fail-on-findings)
    if proc.returncode != 0:
        raise RuntimeError(f"scanoss_failed rc={proc.returncode}: {tail[:800]}")
    if not out_file.exists() or out_file.stat().st_size == 0:
        raise RuntimeError(f"scanoss_no_output: {tail[:800]}")
    if tail:
        trace.add(f"crypto-finder: {tail[:800]}")


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="scanoss-") as tmp:
        work = Path(tmp)
        repo_dir = work / "repo"
        out_file = work / "cbom.json"
        cloned = clone_repo(
            instr.repo_info.git_url,
            branch=instr.repo_info.branch,
            target_dir=str(repo_dir),
            commit=instr.repo_info.commit,
        )
        if not cloned:
            trace.add("git clone failed")
            raise RuntimeError("git_clone_failed")
        run_scanoss(Path(cloned), out_file, trace, timeout=max(60.0, TIMEOUT_SEC - 30.0))
        raw = out_file.read_text(encoding="utf-8")
    cbom = json.loads(raw)  # fail early on malformed output
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return raw, time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    tool_version = scanoss_version()
    logger.info("crypto-finder version: %s", tool_version)
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=tool_version)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
