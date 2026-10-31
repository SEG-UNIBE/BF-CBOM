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

# cdxgen's code-level crypto analysis (the evidence step) runs for the project type given with -t. Without one, 12.x
# fell back to Java; 13.x picks the language from the packages the BOM lists and skips the step when it lists none
# ("Pass -t to choose the language"), so a Java repo without Maven packages got no analysis at all (micro-java,
# bar-micro of 2026-10-31). Java and Go repos therefore get their -t, the README's own CBOM call (`cbom -t java` =
# `cdxgen -t java --include-crypto`). JavaScript/TypeScript must run without -t: its source-level crypto inventory
# (the only one with file/line) runs only in the default all-types mode. Python is not in cdxgen's `languages`: its
# README claims no Python crypto (common/tools.yaml).
# Go (golem) type-checks through go/packages: an import resolves only when the repo's go.sum pins it, and golem's
# `go list` then downloads the module itself, also with --no-install-deps (needs network). cdxgen's `cbom` command
# finds no Go asset (it never runs golem) and --profile research adds none (checked 2026-10-22, docs/findings.md).
# The image's GOPATH (/opt/app-root/go) cannot be created under cap_drop ALL (root without DAC_OVERRIDE, /opt not
# writable), so `go list` failed and every Go module with a dependency yielded no asset until 2026-10-22: each job
# gets its own GOPATH in its work dir (`go_env`), writable (-modcacherw) so the cleanup can delete it.
_PROJECT_TYPE = {"java": "java", "go": "go"}

# cdxgen's own words when its code analysis was skipped or failed (13.5.0): the evidence step without a language
# ("Skipping the evidence analysis since the BOM lists no package in a language it supports. Pass -t to choose the
# language."), atom crashing ("Failure: Failed to enhance CPG: ...", "WARN: atom exited with status 1; the analysis
# may be incomplete.", "atom reported a failure and produced no reachables slice for python.", micro-python,
# 2026-10-31). The run records them as tool warnings; the JVM's deprecation WARNINGs and the purl messages every run
# prints are not among them.
WARNING_LINE = re.compile(r"^(Skipping the evidence analysis|WARN: atom exited|atom reported a failure|Failure: )")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def project_type_for(main_language: str | None) -> str | None:
    """The -t value for a repo's main language, or None (all project types, cdxgen's default)."""
    return _PROJECT_TYPE.get((main_language or "").strip().lower())


def analysis_warnings(output: str, *, most: int = 5) -> list[str]:
    """The distinct lines of cdxgen's output that say its code analysis was skipped or failed, in order, at most
    `most`."""
    out: list[str] = []
    for line in _ANSI.sub("", output).splitlines():
        line = line.strip()[:300]
        if line and WARNING_LINE.match(line) and line not in out:
            out.append(line)
    return out[:most]


def go_env(work_root: Path) -> dict[str, str]:
    """The job's own Go module cache: under the job's work dir, read-write so it is deleted with it."""
    return {"GOPATH": str(work_root / "go"), "GOFLAGS": "-modcacherw"}


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
        self,
        repo_dir: Path,
        out_file: Path,
        trace: Trace,
        *,
        evidence: bool = True,
        project_type: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        cmd = build_command(repo_dir, out_file, evidence=evidence, project_type=project_type, binary=self.binary)
        logger.info("Running cdxgen: %s", " ".join(cmd))
        trace.add(f"cdxgen cmd: {' '.join(cmd)}")
        if extra_env:
            trace.add("cdxgen env: " + " ".join(f"{k}={v}" for k, v in extra_env.items()))
        try:
            env = {**os.environ, **(extra_env or {})}
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
        for line in analysis_warnings(f"{proc.stdout or ''}\n{proc.stderr or ''}"):
            trace.add(f"tool warning: {line}")

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
        env = go_env(work_root)
        try:
            cloned_path = clone_repo(git_url, branch=branch, target_dir=str(repo_dir), commit=commit)
            if not cloned_path:
                trace.add("git clone failed")
                raise RuntimeError("git_clone_failed")
            try:
                self._run_cdxgen(Path(cloned_path), out_file, trace, project_type=project_type, extra_env=env)
            except RuntimeError as exc:
                if not str(exc).startswith("cdxgen_no_output rc=0"):
                    raise
                # cdxgen 11.x exits 0 without writing a BOM in --evidence mode on some Maven
                # projects. Rerun without evidence so the job still yields a CBOM; the fallback is
                # recorded in the result notes (no occurrences in this output).
                logger.warning("cdxgen wrote no output with --evidence; retrying without it")
                trace.add("fallback: --evidence produced no output, reran without --evidence")
                self._run_cdxgen(
                    Path(cloned_path), out_file, trace, evidence=False, project_type=project_type, extra_env=env
                )
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
