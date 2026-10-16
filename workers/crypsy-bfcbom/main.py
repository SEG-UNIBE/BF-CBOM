"""Crypsy (BF-CBOM adaptation) worker: tree-sitter + rule-registry scanner with native CycloneDX 1.7 output.

Crypsy 0.2.0 is the scanner of Näther & Hirsch ("Hidden Ciphers and Where to Find Them", arXiv
2608.04857, submitted to ICICS 2026), driven by the rule registry Crypistry; the authors' version scans
Go and Ruby. This worker runs the adaptation 0.2.0+bfcbom.1, which adds Java and Python discovery,
extends Go and fixes engine bugs; it was written by Claude agents for a colleague and is published by us
on GitHub (repo and commit pinned in docker/Dockerfile.worker-crypsy-bfcbom). Report it as "Crypsy
(BF-CBOM adaptation)", not as "Crypsy".

Benchmark caveat: the adaptation's last fixes were chosen from its failures on the BF-CBOM micro projects
and pilot corpus, so those are no held-out data for it (`tailored_on` in common/tools.yaml).

Command, as the tool's scripts/scan.sh runs it (neutral cwd and `-I`, so files in the scanned tree can
never shadow the tool's own modules):
`python -I -m crypsy scan <repo> --rule-registry <crypistry> -o out.json --exclude .git/*`.
Adapter: crypsy writes absolute locations under the scan root; they are made repo-relative (recorded in
the result notes). Nothing else is changed.
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from common.cbom_adapters import repo_root_prefixes, strip_location_prefixes
from common.models import JobInstruction, Trace
from common.utils import clone_repo
from common.worker import build_handle_instruction, run_worker

NAME = os.getenv("WORKER_NAME") or os.path.basename(os.path.dirname(__file__))
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "1800"))  # Keycloak (~10k files) took ~85 s on one core
CRYPSY_HOME = Path(os.getenv("CRYPSY_HOME", "/opt/crypsy"))
PYTHON = os.getenv("CRYPSY_PYTHON", str(CRYPSY_HOME / "venv" / "bin" / "python"))
REGISTRY = Path(os.getenv("CRYPSY_REGISTRY", str(CRYPSY_HOME / "crypistry")))
SOURCE_COMMIT = os.getenv("CRYPSY_COMMIT", "")  # set by the Dockerfile: the pinned commit of the tool's repo
# crypsy walks every file under the target, git metadata included (the other tools skip it themselves)
EXCLUDES = (".git/*", "*/.git/*")
EXTRA_ARGS = os.getenv("CRYPSY_ARGS", "").split()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def build_command(
    repo_path: Path, out_file: Path, *, target_name: str | None = None, commit: str | None = None
) -> list[str]:
    """The scan command. The repo URL is deliberately not passed: it may carry credentials."""
    cmd = [PYTHON, "-I", "-m", "crypsy", "scan", str(repo_path), "--rule-registry", str(REGISTRY), "-o", str(out_file)]
    for pattern in EXCLUDES:
        cmd += ["--exclude", pattern]
    if target_name:
        cmd += ["--target-name", target_name]
    if commit:
        cmd += ["--git-commit", commit]
    return cmd + EXTRA_ARGS


def registry_version(registry: Path = REGISTRY) -> str | None:
    try:
        return json.loads((registry / "index.json").read_text(encoding="utf-8")).get("kb_version")
    except (OSError, ValueError):
        return None


def format_version(version: str | None, kb: str | None = None, commit: str | None = None) -> str | None:
    """`crypsy 0.2.0+bfcbom.1 (crypistry kb 0.9.2, 1a2b3c4d5e6f)`: package, rule registry, source commit."""
    if not version:
        return None
    extra = [f"crypistry kb {kb}"] if kb else []
    if commit:
        extra.append(commit[:12])
    return f"crypsy {version}" + (f" ({', '.join(extra)})" if extra else "")


def crypsy_version() -> str | None:
    try:
        out = subprocess.run(
            [PYTHON, "-I", "-c", "import importlib.metadata as m; print(m.version('crypsy'))"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        version = out.stdout.strip()
    except Exception as e:  # pragma: no cover - only when the venv is missing
        logger.warning("could not determine crypsy version: %s", e)
        return None
    return format_version(version, registry_version(), SOURCE_COMMIT)


def run_crypsy(
    repo_path: Path,
    out_file: Path,
    workdir: Path,
    trace: Trace,
    *,
    timeout: float,
    target_name: str | None = None,
    commit: str | None = None,
) -> None:
    cmd = build_command(repo_path, out_file, target_name=target_name, commit=commit)
    trace.add(f"crypsy cmd: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("crypsy_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("crypsy_not_found") from exc
    tail = "\n".join(((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-8:])
    # the tool's contract: exit code 0 = CBOM written, any other code (an uncaught exception) = failed
    if proc.returncode != 0:
        raise RuntimeError(f"crypsy_failed rc={proc.returncode}: {tail[:800]}")
    if not out_file.exists() or out_file.stat().st_size == 0:
        raise RuntimeError(f"crypsy_no_output: {tail[:800]}")
    summary = [s.strip() for s in (proc.stdout or "").splitlines() if s.strip().startswith(("Files", "Components"))]
    if summary:
        trace.add("crypsy: " + "; ".join(summary))


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="crypsy-") as tmp:
        work = Path(tmp)
        repo_dir = work / "repo"
        out_file = work / "cbom.json"
        cwd = work / "cwd"  # neutral working directory, outside the scanned tree
        cwd.mkdir()
        cloned = clone_repo(
            instr.repo_info.git_url,
            branch=instr.repo_info.branch,
            target_dir=str(repo_dir),
            commit=instr.repo_info.commit,
        )
        if not cloned:
            trace.add("git clone failed")
            raise RuntimeError("git_clone_failed")
        repo_path = Path(cloned)
        run_crypsy(
            repo_path,
            out_file,
            cwd,
            trace,
            timeout=max(10.0, TIMEOUT_SEC - 20.0),
            target_name=instr.repo_info.full_name,
            commit=instr.repo_info.commit,
        )
        cbom = json.loads(out_file.read_text(encoding="utf-8"))
        fixed = strip_location_prefixes(cbom, repo_root_prefixes(repo_path))
        if fixed:
            trace.add(f"adapter: {fixed} occurrence locations made repo-relative (crypsy reports absolute paths)")
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return json.dumps(cbom, ensure_ascii=False), time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    tool_version = crypsy_version()
    logger.info("crypsy version: %s", tool_version)
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=tool_version)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
