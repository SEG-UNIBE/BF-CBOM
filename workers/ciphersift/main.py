"""CipherSift worker: tree-sitter + YAML rule scanner for Java, Python and Go with native CycloneDX 1.7 output.

CipherSift (Apache-2.0, https://github.com/SEG-UNIBE/ciphersift) is a clean-room reimplementation of the approach
of Crypsy (Näther & Hirsch, "Hidden Ciphers and Where to Find Them", arXiv 2608.04857), which scans Go and Ruby;
CipherSift covers Java, Python and Go and their dependency manifests. The release is pinned in
docker/Dockerfile.worker-ciphersift.

Benchmark caveat: part of its rules and defaults were chosen after evaluations on the BF-CBOM micro projects and
pilot corpus ("benchmark-informed" in its DECISIONS.md), so those are no held-out data for it (`tailored_on` in
common/tools.yaml).

Command: `java --enable-native-access=ALL-UNNAMED -jar ciphersift.jar scan <repo> -o out.json --quiet` with the
tool's defaults (CycloneDX 1.7, tests scanned and marked, no weakness indicators). Locations are already relative
to the scan root and `.git` is never followed, so the CBOM is stored unchanged.
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
JAVA = str(Path(os.getenv("JAVA_HOME", "/opt/java/openjdk")) / "bin" / "java")
JAR = os.getenv("CIPHERSIFT_JAR", "/opt/ciphersift/ciphersift.jar")
RELEASE = os.getenv("CIPHERSIFT_RELEASE", "")  # set by the Dockerfile: the pinned release tag
# the JVM default heap is a quarter of the container limit (the scan is the only process); an OOM ends the JVM
# instead of leaving it running half-dead
JAVA_OPTS = os.getenv("CIPHERSIFT_JAVA_OPTS", "-XX:MaxRAMPercentage=75 -XX:+ExitOnOutOfMemoryError").split()
EXTRA_ARGS = os.getenv("CIPHERSIFT_ARGS", "").split()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def java_command() -> list[str]:
    return [JAVA, *JAVA_OPTS, "--enable-native-access=ALL-UNNAMED", "-jar", JAR]


def build_command(repo_path: Path, out_file: Path) -> list[str]:
    """The scan command. The repo URL is never passed: it may carry credentials."""
    return [*java_command(), "scan", str(repo_path), "-o", str(out_file), "--quiet", *EXTRA_ARGS]


def format_version(output: str | None, release: str | None = None) -> str | None:
    """`ciphersift 1.1.1 (release v1.1.1)`: the jar's own version line plus the release tag it was built from."""
    line = next((s.strip() for s in (output or "").splitlines() if s.strip()), "")
    if not line:
        return f"ciphersift {release}" if release else None
    if not line.startswith("ciphersift"):
        line = f"ciphersift {line}"
    return f"{line} (release {release})" if release else line


def ciphersift_version() -> str | None:
    try:
        out = subprocess.run([*java_command(), "--version"], capture_output=True, text=True, timeout=60)
        text = out.stdout
    except Exception as e:  # pragma: no cover - only when the JRE or jar is missing
        logger.warning("could not determine ciphersift version: %s", e)
        text = ""
    return format_version(text, RELEASE)


def run_ciphersift(repo_path: Path, out_file: Path, trace: Trace, *, timeout: float) -> None:
    cmd = build_command(repo_path, out_file)
    trace.add(f"ciphersift cmd: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("ciphersift_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("ciphersift_not_found") from exc
    tail = "\n".join(((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-8:])
    # exit codes (README): 0 = CBOM written (unparsable files skipped with a warning), anything else = failed
    if proc.returncode != 0:
        raise RuntimeError(f"ciphersift_failed rc={proc.returncode}: {tail[:800]}")
    if not out_file.exists() or out_file.stat().st_size == 0:
        raise RuntimeError(f"ciphersift_no_output: {tail[:800]}")
    if tail:
        trace.add(f"ciphersift: {tail[:800]}")


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ciphersift-") as tmp:
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
        run_ciphersift(Path(cloned), out_file, trace, timeout=max(10.0, TIMEOUT_SEC - 20.0))
        cbom = json.loads(out_file.read_text(encoding="utf-8"))
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return json.dumps(cbom, ensure_ascii=False), time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    tool_version = ciphersift_version()
    logger.info("ciphersift version: %s", tool_version)
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=tool_version)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
