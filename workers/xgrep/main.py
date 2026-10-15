"""Mondoo xgrep worker: tree-sitter + data-flow scanner with native CycloneDX 1.6 CBOM output.

Licence caveat: xgrep is free of charge but proprietary (LicenseRef-Mondoo-Proprietary), and its
terms reportedly forbid publishing benchmark results. The worker is part of the experiment flow so
the data exists; whether xgrep appears in the paper (named, anonymised, or not at all) is decided
later, pending Mondoo's answer to the permission request of 2026-10-08. The image is built locally
from the public npm package and is never pushed (see docker-bake.hcl).

Pilot verdict (2026-10-08): pass on Java, Python, Go and JS. Command as the pilot ran it:
`xgrep sbom --no-telemetry --include cbom -o out.json <repo>` with XGREP_UPDATE_CHECK=0, DO_NOT_TRACK=1.
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
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "2700"))  # the pilot used a 45 min limit
BINARY = os.getenv("XGREP_BIN", "xgrep")
EXTRA_ARGS = os.getenv("XGREP_ARGS", "").split()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def _env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({"HOME": str(home), "XGREP_UPDATE_CHECK": "0", "DO_NOT_TRACK": "1", "XDG_CONFIG_HOME": str(home)})
    return env


def xgrep_version() -> str | None:
    try:
        with tempfile.TemporaryDirectory(prefix="xgrep-home-") as home:
            out = subprocess.run([BINARY, "version"], capture_output=True, text=True, timeout=60, env=_env(Path(home)))
        text = (out.stdout or "") + (out.stderr or "")
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", text)
        return m.group(1) if m else (text.strip().splitlines() or [None])[0]
    except Exception as e:  # pragma: no cover - only when the binary is missing
        logger.warning("could not determine xgrep version: %s", e)
        return None


def run_xgrep(repo_path: Path, out_file: Path, home: Path, trace: Trace, *, timeout: float) -> None:
    cmd = [BINARY, "sbom", "--no-telemetry", "--include", "cbom", "-o", str(out_file), *EXTRA_ARGS, str(repo_path)]
    trace.add(f"xgrep cmd: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd, cwd=str(repo_path), capture_output=True, text=True, timeout=timeout, check=False, env=_env(home)
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("xgrep_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("xgrep_not_found") from exc
    tail = "\n".join(((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-8:])
    if not out_file.exists() or out_file.stat().st_size == 0:
        raise RuntimeError(f"xgrep_no_output rc={proc.returncode}: {tail[:800]}")
    if proc.returncode != 0:
        trace.add(f"xgrep exit code {proc.returncode} (output written): {tail[:300]}")


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="xgrep-") as tmp:
        repo_dir = Path(tmp) / "repo"
        out_file = Path(tmp) / "cbom.json"
        home = Path(tmp) / "home"
        home.mkdir()
        cloned = clone_repo(
            instr.repo_info.git_url,
            branch=instr.repo_info.branch,
            target_dir=str(repo_dir),
            commit=instr.repo_info.commit,
        )
        if not cloned:
            trace.add("git clone failed")
            raise RuntimeError("git_clone_failed")
        run_xgrep(Path(cloned), out_file, home, trace, timeout=max(10.0, TIMEOUT_SEC - 20.0))
        raw = out_file.read_text(encoding="utf-8")
    cbom = json.loads(raw)  # fail early on malformed output
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return raw, time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    version = xgrep_version()
    logger.info("xgrep version: %s", version)
    tool_version = os.getenv("TOOL_VERSION") or (f"xgrep {version}" if version else None)
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=tool_version)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
