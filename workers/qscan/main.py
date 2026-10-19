"""qScan worker: quantakrypto/pqc-tools (Apache-2.0, npm @quantakrypto/qscan), TypeScript pattern detectors with a
CycloneDX 1.6 CBOM (`qscan <dir> --format cbom -o <file>`).

Observer (common/tools.yaml `role`, decided 2026-10-19): it failed the pilot's inclusion bar, its data is harvested,
it does not vote. Offline: its optional LLM triage (`--triage`) and supply-chain audit (`--audit`) are never enabled,
and LLM keys are removed from its environment. Exit code 1 means "findings at or above the severity threshold", not an
error. A `quantakrypto.config.json` committed in a scanned repo is honoured as the tool ships (recorded in the notes).
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
BINARY = os.getenv("QSCAN_BIN", "qscan")
EXTRA_ARGS = os.getenv("QSCAN_ARGS", "").split()
LLM_KEYS = ("QK_LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
CONFIG_FILE = "quantakrypto.config.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def qscan_version() -> str | None:
    try:
        out = subprocess.run([BINARY, "--version"], capture_output=True, text=True, timeout=60)
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", (out.stdout or "") + (out.stderr or ""))
        return m.group(1) if m else None
    except Exception as e:  # pragma: no cover - only when the binary is missing
        logger.warning("could not determine the qscan version: %s", e)
        return None


def run_qscan(repo_path: Path, out_file: Path, trace: Trace, *, timeout: float) -> None:
    cmd = [BINARY, str(repo_path), "--format", "cbom", "-o", str(out_file), *EXTRA_ARGS]
    trace.add(f"qscan cmd: {' '.join(cmd)}")
    if (repo_path / CONFIG_FILE).exists():
        trace.add(f"qscan: the repo has its own {CONFIG_FILE} (honoured, as the tool ships)")
    env = {k: v for k, v in os.environ.items() if k not in LLM_KEYS}  # never call an LLM
    try:
        proc = subprocess.run(
            cmd, cwd=str(out_file.parent), capture_output=True, text=True, timeout=timeout, check=False, env=env
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("qscan_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("qscan_not_found") from exc
    tail = "\n".join(((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-8:])
    # 0 = no findings at the threshold, 1 = findings; 2 = usage or I/O error
    if proc.returncode not in (0, 1) or not out_file.exists() or out_file.stat().st_size == 0:
        raise RuntimeError(f"qscan_no_output rc={proc.returncode}: {tail[:800]}")


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="qscan-") as tmp:
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
        run_qscan(Path(cloned), out_file, trace, timeout=max(10.0, TIMEOUT_SEC - 20.0))
        raw = out_file.read_text(encoding="utf-8")
    cbom = json.loads(raw)  # fail early on malformed output
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return raw, time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    version = qscan_version()
    logger.info("qscan version: %s", version)
    run_worker(
        NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=f"qscan {version}" if version else None
    )


if __name__ == "__main__":
    logger.info("starting up...")
    main()
