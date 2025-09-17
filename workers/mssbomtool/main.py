import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from common.models import JobInstruction, Trace
from common.worker import build_handle_instruction, run_worker

# Worker name and timeout settings
NAME = os.path.basename(os.path.dirname(__file__))
# Overall worker timeout is managed by run_worker; keep our own subprocess timeouts well below it
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "6"))  # default 6s
# Keep a little headroom for cleanup
CMD_TIMEOUT_SEC = max(1, TIMEOUT_SEC - 1)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def _which(prog: str) -> str | None:
    try:
        return shutil.which(prog)
    except Exception:
        return None


def _run(
    cmd: list[str], cwd: Path | None = None, timeout: int | None = None
) -> subprocess.CompletedProcess:
    """
    Run a command and return the CompletedProcess. Raises on non-zero returncode.
    """
    logger.debug("running: %s (cwd=%s, timeout=%s)", " ".join(cmd), cwd, timeout)
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class MsSbomToolClient:
    """
    Thin wrapper that tries, in order:
      1) local `sbom-tool` CLI if present
      2) Docker image `ms_sbom_tool` (as suggested by upstream docs)
    It clones the repo shallowly, invokes the tool, and returns the discovered JSON SBOM as a string.
    """

    def __init__(self, docker_image: str = "ms_sbom_tool"):
        self.docker_image = docker_image
        self.cli_path = _which("sbom-tool")
        self.docker_path = _which("docker")

    # ---------- public API ----------

    def generate_cbom(self, git_url: str, branch: str = "main") -> str:
        """
        Clone target repo and run Microsoft sbom-tool against it.
        Returns a JSON string if found; otherwise '{}' string.
        """
        with tempfile.TemporaryDirectory(prefix="mssbomtool_") as tmpdir:
            tmp = Path(tmpdir)
            repo_dir = tmp / "repo"

            # 1) clone shallow for speed
            self._git_shallow_clone(git_url, branch, repo_dir)

            # 2) derive package meta
            pkg_name = self._guess_package_name(git_url, repo_dir)
            pkg_version = self._last_commit_short_sha(repo_dir) or "0.0.0"
            pkg_supplier = "unknown"

            # 3) attempt local CLI first; if missing, try docker image
            out_dir = tmp / "_out"
            out_dir.mkdir(parents=True, exist_ok=True)

            produced_paths: list[Path] = []
            try:
                if self.cli_path:
                    produced_paths = self._run_sbom_cli(
                        repo_dir, pkg_name, pkg_version, pkg_supplier
                    )
                elif self.docker_path:
                    produced_paths = self._run_sbom_docker(
                        repo_dir, pkg_name, pkg_version, pkg_supplier
                    )
                else:
                    raise RuntimeError(
                        "neither `sbom-tool` nor `docker` is available in PATH"
                    )
            except subprocess.TimeoutExpired as te:
                raise RuntimeError(
                    f"sbom-tool timed out after {CMD_TIMEOUT_SEC}s"
                ) from te
            except subprocess.CalledProcessError as cpe:
                raise RuntimeError(
                    f"sbom-tool failed: {cpe.stderr.strip() or cpe.stdout.strip()}"
                ) from cpe

            # 4) pick the most likely JSON file
            json_path = self._find_best_json(repo_dir)
            if not json_path and produced_paths:
                # fallback: inspect produced paths for json
                json_candidates = [
                    p
                    for p in produced_paths
                    if p.suffix.lower() == ".json" and p.is_file()
                ]
                json_path = json_candidates[0] if json_candidates else None

            if not json_path:
                logger.warning(
                    "[%s] sbom-tool produced no JSON output we could find", NAME
                )
                return "{}"

            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                # Normalize by dumping again to ensure we return valid JSON text
                return json.dumps(data, ensure_ascii=False)
            except Exception as e:
                logger.warning(
                    "[%s] failed to parse JSON at %s: %s", NAME, json_path, e
                )
                # Return raw bytes if it's at least text-ish
                try:
                    return json_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    return "{}"

    # ---------- helpers ----------

    def _git_shallow_clone(self, git_url: str, branch: str, dest: Path) -> None:
        # honor branch if provided; fall back to default remote HEAD if not
        cmd = ["git", "clone", "--depth", "1"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [git_url, str(dest)]
        _run(cmd, timeout=CMD_TIMEOUT_SEC)

    def _guess_package_name(self, git_url: str, repo_dir: Path) -> str:
        # Prefer repo folder name; fallback to last path segment of URL
        return repo_dir.name or Path(git_url.rstrip("/")).stem

    def _last_commit_short_sha(self, repo_dir: Path) -> str | None:
        try:
            cp = _run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_dir,
                timeout=CMD_TIMEOUT_SEC,
            )
            return cp.stdout.strip()
        except Exception:
            return None

    def _run_sbom_cli(
        self, repo_dir: Path, pkg_name: str, pkg_version: str, pkg_supplier: str
    ) -> list[Path]:
        """
        Invoke local `sbom-tool` CLI.
        The tool typically writes under `<repo>/_manifest/...`.
        """
        cmd = [
            self.cli_path or "sbom-tool",
            "generate",
            "-b",
            str(repo_dir),
            "-bc",
            str(repo_dir),
            "-pn",
            pkg_name,
            "-pv",
            pkg_version,
            "-ps",
            pkg_supplier,
        ]
        _run(cmd, cwd=repo_dir, timeout=CMD_TIMEOUT_SEC)
        return self._list_manifest_jsons(repo_dir)

    def _run_sbom_docker(
        self, repo_dir: Path, pkg_name: str, pkg_version: str, pkg_supplier: str
    ) -> list[Path]:
        """
        Invoke `sbom-tool` via Docker image `ms_sbom_tool`.
        Mount the repo at /work inside the container.
        """
        # Ensure the repo path is absolute to be mountable
        repo_abs = repo_dir.resolve()
        cmd = [
            self.docker_path or "docker",
            "run",
            "--rm",
            "-v",
            f"{repo_abs}:/work",
            "-w",
            "/work",
            self.docker_image,
            "sbom-tool",
            "generate",
            "-b",
            "/work",
            "-bc",
            "/work",
            "-pn",
            pkg_name,
            "-pv",
            pkg_version,
            "-ps",
            pkg_supplier,
        ]
        _run(cmd, timeout=CMD_TIMEOUT_SEC)
        return self._list_manifest_jsons(repo_dir)

    def _list_manifest_jsons(self, repo_dir: Path) -> list[Path]:
        candidates: list[Path] = []
        manifest_dir = repo_dir / "_manifest"
        if manifest_dir.exists():
            for p in manifest_dir.rglob("*.json"):
                candidates.append(p)
        # Also scan common alternatives just in case
        for alt in ("bom.json", "sbom.json", "cyclonedx.json", "manifest.json"):
            p = repo_dir / alt
            if p.exists():
                candidates.append(p)
        return candidates

    def _find_best_json(self, repo_dir: Path) -> Path | None:
        """
        Heuristics: prefer SPDX manifest JSON if present,
        otherwise any JSON under _manifest, then top-level bom.json.
        """
        manifest_dir = repo_dir / "_manifest"
        preferred_names = [
            "manifest.spdx.json",
            "manifest.json",
            "bom.json",
            "sbom.json",
            "cyclonedx.json",
        ]
        if manifest_dir.exists():
            for name in preferred_names:
                hit = list(manifest_dir.rglob(name))
                if hit:
                    return hit[0]
            # else return the first json under _manifest
            any_json = list(manifest_dir.rglob("*.json"))
            if any_json:
                return any_json[0]

        # Fallback to common top-level names
        for name in preferred_names:
            p = repo_dir / name
            if p.exists():
                return p

        return None


def _produce(instr: JobInstruction, trace: Trace) -> str:
    return MsSbomToolClient().generate_cbom(
        git_url=instr.repo_info.git_url,
        branch=instr.repo_info.branch,
    )


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    # Delegate the queue/timeout loop to the shared runner
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
