"""qelens worker: qelens/cbom (ex keylens, MPL-2.0), a Rust/tree-sitter scanner for Python and JavaScript with
CycloneDX output (`cbom scan --path <dir> --format cyclonedx`, written to stdout; progress goes to stderr).

Disqualified by the bar exam (2026-10-23): integrated, run on bar-micro only. Pilot verdict
(2026-10-08): borderline on Java (import lines), fail elsewhere.
"""

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
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "900"))
BINARY = os.getenv("QELENS_BIN", "cbom")
EXTRA_ARGS = os.getenv("QELENS_ARGS", "").split()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def qelens_version() -> str | None:
    try:
        out = subprocess.run([BINARY, "--version"], capture_output=True, text=True, timeout=60)
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", (out.stdout or "") + (out.stderr or ""))
        return m.group(1) if m else None
    except Exception as e:  # pragma: no cover - only when the binary is missing
        logger.warning("could not determine the qelens version: %s", e)
        return None


def run_qelens(repo_path: Path, out_file: Path, trace: Trace, *, timeout: float) -> None:
    """Scan `repo_path`; the CBOM arrives on stdout and is written to `out_file` unchanged."""
    cmd = [BINARY, "scan", "--path", str(repo_path), "--format", "cyclonedx", *EXTRA_ARGS]
    trace.add(f"qelens cmd: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd, cwd=str(repo_path.parent), capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("qelens_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("qelens_not_found") from exc
    tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        raise RuntimeError(f"qelens_no_output rc={proc.returncode}: {tail[:800]}")
    out_file.write_text(proc.stdout, encoding="utf-8")


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="qelens-") as tmp:
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
        run_qelens(Path(cloned), out_file, trace, timeout=max(10.0, TIMEOUT_SEC - 20.0))
        raw = out_file.read_text(encoding="utf-8")
    cbom = json.loads(raw)  # fail early on malformed output
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return raw, time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    version = qelens_version()
    logger.info("qelens version: %s", version)
    run_worker(
        NAME,
        handle_instruction,
        default_timeout=TIMEOUT_SEC,
        tool_version=f"qelens cbom {version}" if version else None,
    )


if __name__ == "__main__":
    logger.info("starting up...")
    main()
