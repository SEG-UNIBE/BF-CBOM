import logging
import os
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

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


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

    def _run_cdxgen(self, repo_dir: Path, out_file: Path, trace: Trace) -> None:
        # Revert to the previously working form: absolute output path and repo path
        cmd = [
            self.binary,
            "--json-pretty",
            "--include-crypto",
            "-o",
            str(out_file),
            str(repo_dir),
        ]
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

    def generate_cbom(self, git_url: str, branch: str = "main", trace: Trace | None = None) -> str:
        trace = trace or Trace()
        work_root = Path("/tmp") / f"cdxgen-{int(time.time() * 1000)}"
        repo_dir = work_root / "repo"
        out_file = work_root / "bom.json"
        self._ensure_clean_dir(work_root)
        try:
            cloned_path = clone_repo(git_url, branch=branch, target_dir=str(repo_dir))
            if not cloned_path:
                trace.add("git clone failed")
                raise RuntimeError("git_clone_failed")
            self._run_cdxgen(Path(cloned_path), out_file, trace)
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
    )


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
