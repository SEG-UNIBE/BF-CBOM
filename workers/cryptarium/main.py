"""cryptarium worker: sgoveia/cryptarium (Apache-2.0), a Go/tree-sitter scanner with embedded YAML rule packs and
CycloneDX CBOM output (`cryptarium scan -format cbom -deterministic -output <file> <dir>`).

Disqualified by the bar exam (2026-10-23): integrated, run on bar-micro only. Its defaults are kept (test files and
fixtures are down-scored by the tool, not by us); `-deterministic` only suppresses timestamps and machine metadata.
Its CBOM has no locations (only its JSON format has them): benchmarked as shipped.
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
BINARY = os.getenv("CRYPTARIUM_BIN", "cryptarium")
EXTRA_ARGS = os.getenv("CRYPTARIUM_ARGS", "").split()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def cryptarium_version() -> str | None:
    try:
        out = subprocess.run([BINARY, "--version"], capture_output=True, text=True, timeout=60)
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", (out.stdout or "") + (out.stderr or ""))
        return m.group(1) if m else None
    except Exception as e:  # pragma: no cover - only when the binary is missing
        logger.warning("could not determine the cryptarium version: %s", e)
        return None


def run_cryptarium(repo_path: Path, out_file: Path, trace: Trace, *, timeout: float) -> None:
    cmd = [BINARY, "scan", "-format", "cbom", "-deterministic", "-output", str(out_file), *EXTRA_ARGS, str(repo_path)]
    trace.add(f"cryptarium cmd: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd, cwd=str(out_file.parent), capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("cryptarium_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("cryptarium_not_found") from exc
    tail = "\n".join(((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-8:])
    if not out_file.exists() or out_file.stat().st_size == 0:
        raise RuntimeError(f"cryptarium_no_output rc={proc.returncode}: {tail[:800]}")
    if proc.returncode != 0:
        trace.add(f"cryptarium exit code {proc.returncode} (output written): {tail[:300]}")


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="cryptarium-") as tmp:
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
        run_cryptarium(Path(cloned), out_file, trace, timeout=max(10.0, TIMEOUT_SEC - 20.0))
        raw = out_file.read_text(encoding="utf-8")
    cbom = json.loads(raw)  # fail early on malformed output
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return raw, time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    version = cryptarium_version()
    logger.info("cryptarium version: %s", version)
    run_worker(
        NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=f"cryptarium {version}" if version else None
    )


if __name__ == "__main__":
    logger.info("starting up...")
    main()
