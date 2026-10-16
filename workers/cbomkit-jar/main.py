"""CBOMkit action-jar worker: the same sonar-cryptography engine as `workers/cbomkit`, packaged as the
standalone `CBOMkit-action.jar` (cbomkit-action v2.3.0 -> cbomkit-lib 1.3.0 -> sonar-cryptography 1.7.0),
run as a plain CLI without the server, database or git clone of the service.

Why a second CBOMkit worker: the pilot of 2026-10-08 graded this packaging, the server image is what
users run; both count as ONE voter in the agreement scoring (`voter_group: cbomkit` in
common/tools.yaml), but as separate tools everywhere else, so the difference between the packagings
is measured instead of assumed. Command as the pilot ran it:
`java -Xmx16g -Duser.home=<empty dir> -jar CBOMkit-action.jar` with GITHUB_WORKSPACE=<repo>,
CBOMKIT_OUTPUT_DIR=<dir>, CBOMKIT_LANGUAGES=java,python (or go), CBOMKIT_JAVA_REQUIRE_BUILD=false,
CBOMKIT_GENERATE_MODULE_CBOMS=false -> <dir>/cbom.json.

Known upstream quirk (pilot): a directory is only indexed as a module when it has a build file AND a
`src/` directory; Kafka-like layouts silently scan a handful of files. `CBOMKIT_ENSURE_SRC=1` applies
the pilot's workaround (an empty `src/` at the repo root); off by default so the tool is measured as is,
and the risk is recorded in the result notes either way.
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from common.cbom_adapters import strip_location_prefixes
from common.models import JobInstruction, Trace
from common.utils import clone_repo
from common.worker import build_handle_instruction, run_worker

NAME = os.getenv("WORKER_NAME") or os.path.basename(os.path.dirname(__file__))
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "2700"))  # the pilot used a 45 min limit
JAR = os.getenv("CBOMKIT_ACTION_JAR", "/cbomkit-action/CBOMkit-action.jar")
JAR_DIR = os.getenv("CBOMKIT_JAVA_JAR_DIR", "/cbomkit-action/java/scan")
HEAP = os.getenv("CBOMKIT_HEAP", "6g")  # pilot: 16g; compose memory limit must leave room for the JVM
DEFAULT_LANGUAGES = os.getenv("CBOMKIT_LANGUAGES", "java,python,go")
ENSURE_SRC = os.getenv("CBOMKIT_ENSURE_SRC", "0") == "1"
BUILD_FILES = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
_LANG = {"java": "java", "kotlin": "java", "scala": "java", "python": "python", "go": "go"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def languages_for(main_language: str | None) -> str:
    """CBOMKIT_LANGUAGES for a repo: its main language when CBOMkit supports it, else all three."""
    lang = _LANG.get((main_language or "").strip().lower())
    return lang or DEFAULT_LANGUAGES


def indexing_risk(repo_path: Path) -> bool:
    """True when the repo root has a Java build file but no src/ (the pilot's silent-skip case)."""
    return any((repo_path / f).exists() for f in BUILD_FILES) and not (repo_path / "src").is_dir()


def relativize_locations(cbom: dict, repo_path: Path) -> int:
    """The jar writes locations relative to its working directory (`repo/src/...`); make them repo-relative."""
    return strip_location_prefixes(cbom, (f"{repo_path.name}/", str(repo_path).rstrip("/") + "/"))


def run_action_jar(repo_path: Path, work: Path, languages: str, trace: Trace, *, timeout: float) -> Path:
    out_dir = work / "out"
    home = work / "home"  # empty: the jar must not pick up a real ~/.m2 or ~/.gradle
    tmp = work / "tmp"
    for d in (out_dir, home, tmp):
        d.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_WORKSPACE": str(repo_path),
            "GITHUB_OUTPUT": str(work / "gh_output"),
            "CBOMKIT_OUTPUT_DIR": str(out_dir),
            "CBOMKIT_LANGUAGES": languages,
            "CBOMKIT_JAVA_REQUIRE_BUILD": "false",
            "CBOMKIT_GENERATE_MODULE_CBOMS": "false",
            "CBOMKIT_JAVA_JAR_DIR": JAR_DIR,
            "LANGUAGE": "en_US:en",
        }
    )
    cmd = ["java", f"-Xmx{HEAP}", f"-Duser.home={home}", f"-Djava.io.tmpdir={tmp}", "-jar", JAR]
    trace.add(f"cbomkit-action cmd: {' '.join(cmd)} (CBOMKIT_LANGUAGES={languages}, source-only scan)")
    try:
        proc = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True, timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("cbomkit_action_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("java_not_found") from exc
    tail = "\n".join(((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-8:])
    cbom = out_dir / "cbom.json"
    if proc.returncode != 0 or not cbom.exists() or cbom.stat().st_size == 0:
        raise RuntimeError(f"cbomkit_action_failed rc={proc.returncode}: {tail[:800]}")
    return cbom


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="cbomkit-jar-") as tmp:
        work = Path(tmp)
        repo_dir = work / "repo"
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
        if indexing_risk(repo_path):
            if ENSURE_SRC:
                (repo_path / "src").mkdir()
                trace.add("indexing workaround applied: empty src/ created at the repo root (CBOMKIT_ENSURE_SRC=1)")
            else:
                trace.add("indexing risk: build file without src/ at the repo root; CBOMkit may index only a few files")
        languages = languages_for(instr.repo_info.main_language)
        cbom_path = run_action_jar(repo_path, work, languages, trace, timeout=max(10.0, TIMEOUT_SEC - 20.0))
        cbom = json.loads(cbom_path.read_text(encoding="utf-8"))
        fixed = relativize_locations(cbom, repo_path)
        if fixed:
            trace.add(
                f"adapter: {fixed} occurrence locations made repo-relative (jar reports them relative to its cwd)"
            )
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return json.dumps(cbom, ensure_ascii=False), time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=os.getenv("TOOL_VERSION"))


if __name__ == "__main__":
    logger.info("starting up...")
    main()
