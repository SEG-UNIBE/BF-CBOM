"""CipherRadar (cradar) worker: opengrep rules + tree-sitter scanner, CycloneDX 1.7 output.

Pilot verdict (2026-10-08): pass on Python, Go and JavaScript; borderline on Java because the Java
pass crashes (nil pointer in constant propagation) on `new ECGenParameterSpec(<identifier>)`
(jjwt, Keycloak). The pilot worked around it by listing the crashing files in a `.cradarignore`;
this worker does the same automatically: on a crash it reads the failing file from the output,
appends it to `.cradarignore` in the clone and retries (bounded), and records the ignored files in
the result notes. Command as the pilot ran it:
`cradar --verbose scan <repo> -o out.json --validate --no-baseline --passes 1,2`.
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
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "1800"))
BINARY = os.getenv("CRADAR_BIN", "cradar")
EXTRA_ARGS = os.getenv("CRADAR_ARGS", "").split()
MAX_IGNORE_RETRIES = int(os.getenv("CRADAR_MAX_IGNORE_RETRIES", "20"))
# Go panics print the goroutine stack; the scanned file shows up in the "scanning"/"processing" lines
# that --verbose emits before the crash, so the last mentioned source file is the culprit.
_FILE_RE = re.compile(r"(?:scanning|processing|parsing|file)\s+[\"']?([^\s\"']+\.[A-Za-z0-9]+)", re.IGNORECASE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def cradar_version() -> str | None:
    try:
        out = subprocess.run([BINARY, "version"], capture_output=True, text=True, timeout=60)
        text = (out.stdout or "") + (out.stderr or "")
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", text)
        return m.group(1) if m else (text.strip().splitlines() or [None])[0]
    except Exception as e:  # pragma: no cover - only when the binary is missing
        logger.warning("could not determine cradar version: %s", e)
        return None


def _crashed_file(output: str, repo_path: Path) -> str | None:
    """Best-effort: the last source file mentioned before a Go panic, relative to the repo."""
    if "panic:" not in output and "runtime error" not in output:
        return None
    candidates = _FILE_RE.findall(output)
    for cand in reversed(candidates):
        p = Path(cand)
        rel = cand
        if p.is_absolute():
            try:
                rel = str(p.resolve().relative_to(repo_path.resolve()))
            except ValueError:
                continue
        if (repo_path / rel).is_file():
            return rel
    return None


def run_cradar(repo_path: Path, out_file: Path, home: Path, trace: Trace, *, deadline: float) -> list[str]:
    """Run cradar, retrying with a growing .cradarignore on crashes. Returns the ignored files."""
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "SEMGREP_SEND_METRICS": "off",
            "OPENGREP_SEND_METRICS": "off",
            "DO_NOT_TRACK": "1",
        }
    )
    ignored: list[str] = []
    cmd = [BINARY, "--verbose", "scan", str(repo_path), "-o", str(out_file), "--validate", "--no-baseline"]
    cmd += ["--passes", "1,2", *EXTRA_ARGS]
    trace.add(f"cradar cmd: {' '.join(cmd)}")
    for attempt in range(MAX_IGNORE_RETRIES + 1):
        remaining = deadline - time.monotonic()
        if remaining < 5:
            raise TimeoutError("cradar_timeout")
        out_file.unlink(missing_ok=True)
        try:
            proc = subprocess.run(
                cmd, cwd=str(repo_path), capture_output=True, text=True, timeout=remaining, check=False, env=env
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("cradar_timeout") from exc
        except FileNotFoundError as exc:
            raise RuntimeError("cradar_not_found") from exc
        output = (proc.stderr or "") + (proc.stdout or "")
        if out_file.exists() and out_file.stat().st_size > 0:
            # rc 3 = CBOM written but schema validation failed (pilot: Keycloak); keep the output, note it
            if proc.returncode != 0:
                tail = "\n".join(output.strip().splitlines()[-5:])
                trace.add(f"cradar exit code {proc.returncode} (output written): {tail[:300]}")
            return ignored
        culprit = _crashed_file(output, repo_path)
        if culprit is None or culprit in ignored:
            tail = "\n".join(output.strip().splitlines()[-10:])
            raise RuntimeError(f"cradar_no_output rc={proc.returncode}: {tail[:800]}")
        ignored.append(culprit)
        with (repo_path / ".cradarignore").open("a", encoding="utf-8") as fh:
            fh.write(culprit + "\n")
        trace.add(f"cradar crashed on {culprit}; added to .cradarignore and retrying ({attempt + 1})")
    raise RuntimeError(f"cradar_crash_loop: still crashing after ignoring {len(ignored)} files")


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    deadline = start + max(30.0, TIMEOUT_SEC - 20.0)
    with tempfile.TemporaryDirectory(prefix="cradar-") as tmp:
        repo_dir = Path(tmp) / "repo"
        out_file = Path(tmp) / "cbom.json"
        home = Path(tmp) / "home"
        for sub in (".config", ".cache", ".local/share"):
            (home / sub).mkdir(parents=True, exist_ok=True)
        cloned = clone_repo(
            instr.repo_info.git_url,
            branch=instr.repo_info.branch,
            target_dir=str(repo_dir),
            commit=instr.repo_info.commit,
        )
        if not cloned:
            trace.add("git clone failed")
            raise RuntimeError("git_clone_failed")
        ignored = run_cradar(Path(cloned), out_file, home, trace, deadline=deadline)
        raw = out_file.read_text(encoding="utf-8")
    if ignored:
        trace.add(f"files excluded via .cradarignore after crashes: {', '.join(ignored)}")
    cbom = json.loads(raw)  # fail early on malformed output
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return raw, time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    version = cradar_version()
    logger.info("cradar version: %s", version)
    # the image sets TOOL_VERSION to the pinned source commit; the binary's own `version` is the fallback
    tool_version = os.getenv("TOOL_VERSION") or (f"cradar {version}" if version else None)
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=tool_version)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
