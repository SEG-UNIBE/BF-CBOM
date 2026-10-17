import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from common.models import JobInstruction, Trace
from common.utils import clone_repo, delete_directory
from common.worker import build_handle_instruction, run_worker

# Worker name and timeout settings
NAME = os.path.basename(os.path.dirname(__file__))
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "120"))  # default 120s for repo scan

# cdxgen's code-level crypto analysis (the evidence step, atom) runs for the project types given with -t and
# falls back to Java without one, so Python and Go repos need their -t (micro-python: 0 -> 5 algorithms,
# micro-go: 0 -> 6, 2026-10-17). JavaScript/TypeScript must run without -t: its source-level crypto
# inventory (the only one with file/line) runs only in the default all-types mode. Java: the same either way.
_PROJECT_TYPE = {"python": "python", "go": "go"}

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def project_type_for(main_language: str | None) -> str | None:
    """The -t value for a repo's main language, or None (all project types, cdxgen's default)."""
    return _PROJECT_TYPE.get((main_language or "").strip().lower())


def build_command(
    repo_dir: Path, out_file: Path, *, evidence: bool = True, project_type: str | None = None, binary: str = "cdxgen"
) -> list[str]:
    cmd = [
        binary,
        "--json-pretty",
        "--include-crypto",
        # never run pip/npm/maven for arbitrary repos (also off for reproducibility)
        "--no-install-deps",
        "-o",
        str(out_file),
        str(repo_dir),
    ]
    if evidence:
        # file/line occurrences for components and the code-level crypto analysis; off by default in cdxgen
        cmd.insert(3, "--evidence")
    if project_type:
        cmd[1:1] = ["-t", project_type]
    return cmd


class CdxgenClient:
    def __init__(self, *, binary: str = "cdxgen", timeout_sec: int = TIMEOUT_SEC):
        self.binary = binary
        self.timeout_sec = timeout_sec

    def _ensure_clean_dir(self, path: Path):
        try:
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise RuntimeError(f"failed_prepare_workdir: {e}") from e

    def _run_cdxgen(
        self, repo_dir: Path, out_file: Path, trace: Trace, *, evidence: bool = True, project_type: str | None = None
    ) -> None:
        cmd = build_command(repo_dir, out_file, evidence=evidence, project_type=project_type, binary=self.binary)
        logger.info("Running cdxgen: %s", " ".join(cmd))
        trace.add(f"cdxgen cmd: {' '.join(cmd)}")
        try:
            env = os.environ.copy()
            # Enable debug if user provided; otherwise leave unset
            proc = subprocess.run(
                cmd,
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=(self.timeout_sec - 5 if self.timeout_sec > 10 else self.timeout_sec),
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            trace.add("cdxgen timeout during run")
            raise TimeoutError("cdxgen_timeout") from exc
        except FileNotFoundError as exc:
            trace.add("cdxgen binary not found in PATH")
            raise RuntimeError("cdxgen_not_found") from exc

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            trace.add(f"cdxgen_failed rc={proc.returncode}: {stderr or stdout}")
            raise RuntimeError(f"cdxgen_failed rc={proc.returncode}: {stderr or stdout}")

        if not out_file.exists() or out_file.stat().st_size == 0:
            # Try common alternate output locations
            for alt in (repo_dir / "bom.json", repo_dir / "bom.cdx.json"):
                try:
                    if alt.exists() and alt.stat().st_size > 0:
                        alt.replace(out_file)
                        break
                except Exception:
                    pass
        if not out_file.exists() or out_file.stat().st_size == 0:
            # Assemble richer diagnostics to bubble up into JobResult.error
            rc = proc.returncode
            cmd_str = " ".join(cmd)
            stderr_lines = (proc.stderr or "").strip().splitlines()
            stdout_lines = (proc.stdout or "").strip().splitlines()
            stderr_tail = " | ".join(stderr_lines[-10:]) if stderr_lines else ""
            stdout_tail = " | ".join(stdout_lines[-10:]) if stdout_lines else ""
            trace.add(
                f"cdxgen_no_output rc={rc} cmd='{cmd_str}' cwd='{repo_dir}'\n"
                f"stderr_tail: {stderr_tail or '(empty)'}\n"
                f"stdout_tail: {stdout_tail or '(empty)'}"
            )
            raise RuntimeError(
                f"cdxgen_no_output rc={rc} cmd='{cmd_str}' cwd='{repo_dir}'\n"
                f"stderr_tail: {stderr_tail or '(empty)'}\n"
                f"stdout_tail: {stdout_tail or '(empty)'}"
            )

    def generate_cbom(
        self,
        git_url: str,
        branch: str = "main",
        trace: Trace | None = None,
        commit: str | None = None,
        main_language: str | None = None,
    ) -> str:
        trace = trace or Trace()
        project_type = project_type_for(main_language)
        work_root = Path("/tmp") / f"cdxgen-{int(time.time() * 1000)}"
        repo_dir = work_root / "repo"
        out_file = work_root / "bom.json"
        self._ensure_clean_dir(work_root)
        try:
            cloned_path = clone_repo(git_url, branch=branch, target_dir=str(repo_dir), commit=commit)
            if not cloned_path:
                trace.add("git clone failed")
                raise RuntimeError("git_clone_failed")
            try:
                self._run_cdxgen(Path(cloned_path), out_file, trace, project_type=project_type)
            except RuntimeError as exc:
                if not str(exc).startswith("cdxgen_no_output rc=0"):
                    raise
                # cdxgen 11.x exits 0 without writing a BOM in --evidence mode on some Maven
                # projects. Rerun without evidence so the job still yields a CBOM; the fallback is
                # recorded in the result notes (no occurrences in this output).
                logger.warning("cdxgen wrote no output with --evidence; retrying without it")
                trace.add("fallback: --evidence produced no output, reran without --evidence")
                self._run_cdxgen(Path(cloned_path), out_file, trace, evidence=False, project_type=project_type)
            return out_file.read_text(encoding="utf-8")
        finally:
            # Best-effort cleanup
            try:
                delete_directory(str(work_root))
            except Exception:
                pass


def _produce(instr: JobInstruction, trace: Trace) -> str:
    return CdxgenClient(timeout_sec=TIMEOUT_SEC).generate_cbom(
        git_url=instr.repo_info.git_url,
        branch=instr.repo_info.branch,
        trace=trace,
        commit=instr.repo_info.commit,
        main_language=instr.repo_info.main_language,
    )


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    version = None
    try:
        ver = subprocess.run(["cdxgen", "--version"], capture_output=True, text=True, timeout=30)
        # "\x1b[1mCycloneDX Generator 11.7.0\x1b[0m\nRuntime: Node.js, ..." -> "11.7.0"
        out = re.sub(r"\x1b\[[0-9;]*m", "", (ver.stdout or "") + (ver.stderr or ""))
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", out)
        version = m.group(1) if m else (out.strip().splitlines() or [None])[0]
        logger.info("cdxgen version: %s", version)
    except Exception as e:
        logger.warning("could not determine cdxgen version: %s", e)
    run_worker(
        NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=f"cdxgen {version}" if version else None
    )


if __name__ == "__main__":
    logger.info("starting up...")
    main()
