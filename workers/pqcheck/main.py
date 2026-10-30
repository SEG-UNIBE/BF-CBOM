"""pqcheck worker: tree-sitter (and go/types) scanner with native CycloneDX 1.6 CBOM output (Apache-2.0).

Pilot verdict (2026-10-08): pass on Python and Go. Command as the pilot ran it:
`pqcheck scan <repo> --policy cryptoct-advisory --format cbom -o out.json`.
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
BINARY = os.getenv("PQCHECK_BIN", "pqcheck")
POLICY = os.getenv("PQCHECK_POLICY", "cryptoct-advisory")
EXTRA_ARGS = os.getenv("PQCHECK_ARGS", "").split()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def pqcheck_version() -> str | None:
    try:
        out = subprocess.run([BINARY, "version"], capture_output=True, text=True, timeout=60)
        text = (out.stdout or "") + (out.stderr or "")
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", text)
        return m.group(1) if m else (text.strip().splitlines() or [None])[0]
    except Exception as e:  # pragma: no cover - only when the binary is missing
        logger.warning("could not determine pqcheck version: %s", e)
        return None


def run_pqcheck(repo_path: Path, out_file: Path, trace: Trace, *, timeout: float) -> None:
    cmd = [BINARY, "scan", str(repo_path), "--policy", POLICY, "--format", "cbom", "-o", str(out_file), *EXTRA_ARGS]
    trace.add(f"pqcheck cmd: {' '.join(cmd)}")
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}  # never call an LLM
    try:
        proc = subprocess.run(
            cmd, cwd=str(repo_path), capture_output=True, text=True, timeout=timeout, check=False, env=env
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("pqcheck_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("pqcheck_not_found") from exc
    tail = "\n".join(((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-8:])
    # pqcheck exits non-zero when the policy flags findings; only a missing output is an error
    if not out_file.exists() or out_file.stat().st_size == 0:
        raise RuntimeError(f"pqcheck_no_output rc={proc.returncode}: {tail[:800]}")
    if proc.returncode != 0:
        trace.add(f"pqcheck exit code {proc.returncode} (output written): {tail[:300]}")


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="pqcheck-") as tmp:
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
        run_pqcheck(Path(cloned), out_file, trace, timeout=max(10.0, TIMEOUT_SEC - 20.0))
        raw = out_file.read_text(encoding="utf-8")
    cbom = json.loads(raw)  # fail early on malformed output
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return raw, time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    version = pqcheck_version()
    logger.info("pqcheck version: %s", version)
    # the image's TOOL_VERSION names the pinned source commit (a release and later commits share one version string)
    tool_version = os.getenv("TOOL_VERSION") or (f"pqcheck {version}" if version else None)
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=tool_version)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
