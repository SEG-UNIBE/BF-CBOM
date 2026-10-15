"""securin-xbom worker: semgrep rule scanner with native CycloneDX 1.6 CBOM output (Apache-2.0).

Pilot verdict (2026-10-08): pass on Java and Python (fail on Go, C, JS). Runs strictly offline through
`xbom_offline.py` (bundled rules only, see there); cdxgen is deliberately not installed (xbom's
dependency layer is broken with cdxgen 12 and would only add library components).
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from common.models import JobInstruction, Trace
from common.utils import clone_repo
from common.worker import build_handle_instruction, run_worker

NAME = os.getenv("WORKER_NAME") or os.path.basename(os.path.dirname(__file__))
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "1800"))
PYTHON = os.getenv("XBOM_PYTHON", "/opt/xbom/bin/python")
BIN_DIR = os.getenv("XBOM_BIN_DIR", os.path.dirname(PYTHON))  # xbom finds semgrep on PATH only
LAUNCHER = Path(__file__).with_name("xbom_offline.py")
EXTRA_ARGS = os.getenv("XBOM_ARGS", "").split()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def xbom_version() -> str | None:
    try:
        out = subprocess.run(
            [PYTHON, "-c", "import importlib.metadata as m; print(m.version('xbom'), m.version('semgrep'))"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        parts = out.stdout.split()
        return f"xbom {parts[0]} semgrep {parts[1]}" if len(parts) == 2 else None
    except Exception as e:  # pragma: no cover - only when the venv is missing
        logger.warning("could not determine xbom version: %s", e)
        return None


def _offline_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
    closed = "http://127.0.0.1:9"  # belt and braces: any registry fetch fails immediately
    env.update(
        {
            "PATH": f"{BIN_DIR}:{env.get('PATH', '')}",
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "SEMGREP_SEND_METRICS": "off",
            "SEMGREP_ENABLE_VERSION_CHECK": "0",
            "DO_NOT_TRACK": "1",
            "HTTPS_PROXY": closed,
            "HTTP_PROXY": closed,
            "https_proxy": closed,
            "http_proxy": closed,
            "ALL_PROXY": closed,
        }
    )
    env.pop("NO_PROXY", None)
    env.pop("no_proxy", None)
    return env


def run_xbom(repo_path: Path, out_dir: Path, home: Path, trace: Trace, *, timeout: float) -> Path:
    cmd = [PYTHON, str(LAUNCHER), "scan", str(repo_path), "--bom-types", "cbom", "-o", str(out_dir), *EXTRA_ARGS]
    trace.add(f"xbom cmd: {' '.join(cmd)} (offline, bundled semgrep rules)")
    try:
        proc = subprocess.run(
            cmd, cwd=str(out_dir), capture_output=True, text=True, timeout=timeout, check=False, env=_offline_env(home)
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("xbom_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("xbom_not_found") from exc
    tail = "\n".join(((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-8:])
    out_file = out_dir / "cbom.cdx.json"
    if not out_file.exists() or out_file.stat().st_size == 0:
        raise RuntimeError(f"xbom_no_output rc={proc.returncode}: {tail[:800]}")
    if proc.returncode != 0:
        trace.add(f"xbom exit code {proc.returncode} (output written): {tail[:300]}")
    return out_file


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="xbom-") as tmp:
        repo_dir = Path(tmp) / "repo"
        out_dir = Path(tmp) / "out"
        home = Path(tmp) / "home"
        out_dir.mkdir()
        (home / ".config").mkdir(parents=True)
        (home / ".cache").mkdir(parents=True)
        cloned = clone_repo(
            instr.repo_info.git_url,
            branch=instr.repo_info.branch,
            target_dir=str(repo_dir),
            commit=instr.repo_info.commit,
        )
        if not cloned:
            trace.add("git clone failed")
            raise RuntimeError("git_clone_failed")
        out_file = run_xbom(Path(cloned), out_dir, home, trace, timeout=max(10.0, TIMEOUT_SEC - 20.0))
        raw = out_file.read_text(encoding="utf-8")
    cbom = json.loads(raw)  # fail early on malformed output
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return raw, time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    version = xbom_version()
    logger.info("tool version: %s", version)
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=version)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
