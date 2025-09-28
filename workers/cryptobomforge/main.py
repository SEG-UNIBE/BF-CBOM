import json
import logging
import os
import subprocess
import tempfile

from common.models import JobInstruction, Trace
from common.utils import clone_repo
from common.worker import build_handle_instruction, run_worker

# Worker name and timeout settings
NAME = os.path.basename(os.path.dirname(__file__))
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "6"))  # default 6s

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


class CryptobomForgeClient:
    def __init__(self, *, timeout_sec: int = TIMEOUT_SEC):
        self.timeout_sec = timeout_sec

    def _ensure_context_region(self, sarif_path: str) -> bool:
        """
        Best-effort: load a SARIF file and ensure that for any
        physicalLocation.region, a sibling physicalLocation.contextRegion
        exists. Some downstream tooling expects contextRegion and may
        KeyError when it's absent. Returns True if file was modified.
        """
        try:
            with open(sarif_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Failed to read SARIF %s: %s", sarif_path, e)
            return False

        modified = False

        def ensure_region_snippet(region_obj: dict) -> bool:
            changed_local = False
            if isinstance(region_obj, dict):
                if not isinstance(region_obj.get("snippet"), dict):
                    # Provide an empty snippet to satisfy consumers expecting it
                    region_obj["snippet"] = {"text": ""}
                    changed_local = True
            return changed_local

        def patch_physical_location(phys: dict) -> bool:
            if not isinstance(phys, dict):
                return False
            region = phys.get("region")
            # Only add when region exists and contextRegion is missing
            changed = False
            if region:
                if "contextRegion" not in phys:
                    try:
                        # Add sibling contextRegion at physicalLocation level
                        phys["contextRegion"] = dict(region)
                        changed = True
                    except Exception:
                        pass
                # Some tools (incorrectly) expect region.contextRegion; add defensively
                if isinstance(region, dict) and "contextRegion" not in region:
                    try:
                        region["contextRegion"] = dict(region)
                        changed = True
                    except Exception:
                        pass
                # Ensure snippet exists on both region and contextRegion
                if isinstance(region, dict):
                    if ensure_region_snippet(region):
                        changed = True
                    cr = phys.get("contextRegion")
                    if isinstance(cr, dict) and ensure_region_snippet(cr):
                        changed = True
            return changed

        try:
            for run in data.get("runs") or []:
                results = run.get("results") or []
                for res in results:
                    # Primary locations
                    for loc in res.get("locations") or []:
                        phys = (loc or {}).get("physicalLocation")
                        if patch_physical_location(phys):
                            modified = True
                    # Related locations sometimes also contain physicalLocation
                    for rloc in res.get("relatedLocations") or []:
                        phys = (rloc or {}).get("physicalLocation")
                        if patch_physical_location(phys):
                            modified = True
        except Exception as e:
            logger.warning("Error normalizing SARIF %s: %s", sarif_path, e)
            modified = False

        if modified:
            try:
                with open(sarif_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                logger.info("Patched SARIF to add contextRegion: %s", sarif_path)
            except Exception as e:
                logger.warning("Failed to write patched SARIF %s: %s", sarif_path, e)
                modified = False

        return modified

    def run_codeql_scan(self, repo_path: str, language: str | None) -> tuple[bool, str | None]:
        """
        Backwards-compat wrapper: execute CodeQL steps while collecting a trace.
        """
        trace = Trace()
        ok = self._run_codeql_scan(repo_path, language, trace)
        return ok, trace.text()

    # New modular entry (preferred inside this class)
    def _run_codeql_scan(self, repo_path: str, language: str | None, trace: Trace) -> bool:
        logger.info("CodeQL scan requested: repo_path=%r, language=%r", repo_path, language)
        if not language:
            trace.add("codeql: missing main_language")
            return False

        lang = (language or "").lower()
        db_path = os.path.join(repo_path, f"codeql-db-{lang}")
        if not self._codeql_create_db(repo_path, db_path, lang, trace):
            return False

        if lang in ["java", "cpp", "csharp", "go"] and os.path.exists(db_path):
            if not self._codeql_finalize_db(db_path, lang, trace):
                trace.add(f"codeql: finalize failed for {lang}; aborting analyze")
                return False

        ram_flags = self._resolve_ram_flags(trace)
        return self._codeql_analyze_db(repo_path, db_path, lang, ram_flags, trace)

    # ----- CodeQL helpers -----
    def _codeql_create_db(self, repo_path: str, db_path: str, lang: str, trace: Trace) -> bool:
        cmd = [
            "codeql",
            "database",
            "create",
            db_path,
            "--language",
            lang,
            "--source-root",
            repo_path,
        ]
        logger.info("Running CodeQL create for %s: %s", lang, " ".join(cmd))
        try:
            cp = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=self.timeout_sec,
            )
        except subprocess.CalledProcessError as e:
            stdout = e.stdout.decode("utf-8", "replace") if e.stdout else ""
            stderr = e.stderr.decode("utf-8", "replace") if e.stderr else ""
            trace.add_exc(f"codeql create failed for {lang}", e, stdout, stderr)
            return False
        except Exception as e:
            trace.add_exc(f"codeql create unexpected for {lang}", e)
            return False
        if not os.path.exists(db_path):
            trace.add(f"codeql create: db missing at {db_path}")
            return False
        logger.info("CodeQL DB created: %s (contents: %s)", db_path, os.listdir(db_path))
        logger.debug("codeql create stdout: %s", cp.stdout.decode("utf-8", "replace"))
        return True

    def _codeql_finalize_db(self, db_path: str, lang: str, trace: Trace) -> bool:
        cmd = ["codeql", "database", "finalize", db_path]
        logger.info("Running CodeQL finalize for %s: %s", lang, " ".join(cmd))
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=self.timeout_sec,
            )
            return True
        except subprocess.CalledProcessError as e:
            stdout = e.stdout.decode("utf-8", "replace") if e.stdout else ""
            stderr = e.stderr.decode("utf-8", "replace") if e.stderr else ""
            trace.add_exc(f"codeql finalize failed for {lang}", e, stdout, stderr)
            return False
        except Exception as e:
            trace.add_exc(f"codeql finalize unexpected for {lang}", e)
            return False

    def _resolve_ram_flags(self, trace: Trace) -> list[str]:
        """Return safe RAM flags for CodeQL analyze.

        Use a direct --ram budget to avoid mismatches between resolved
        -J/--off-heap flags and the analyze command's default total RAM
        (commonly 2048 MB). The budget can be overridden via CODEQL_RAM_MB.

        Defaults to 2048 MB to play nicely with constrained runners.
        """
        try:
            ram_mb = int(os.getenv("CODEQL_RAM_MB", "4096"))
        except Exception:
            ram_mb = 4096
        # Ensure a sensible lower bound and avoid zero/negative values
        if ram_mb < 512:
            ram_mb = 512
        logger.info("Using CodeQL RAM budget: --ram=%s", ram_mb)
        return [f"--ram={ram_mb}"]

    def _codeql_analyze_db(
        self,
        repo_path: str,
        db_path: str,
        lang: str,
        ram_flags: list[str],
        trace: Trace,
    ) -> bool:
        out = os.path.join(repo_path, f"codeql-{lang}-results.sarif")
        cmd = [
            "codeql",
            "database",
            "analyze",
            db_path,
            "--format",
            "sarifv2.1.0",
            "--sarif-add-snippets",
            "--output",
            out,
        ] + list(ram_flags)
        logger.info("Running CodeQL analyze for %s: %s", lang, " ".join(cmd))
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=self.timeout_sec,
            )
            return True
        except subprocess.CalledProcessError as e:
            stdout = e.stdout.decode("utf-8", "replace") if e.stdout else ""
            stderr = e.stderr.decode("utf-8", "replace") if e.stderr else ""
            trace.add_exc(f"codeql analyze failed for {lang}", e, stdout, stderr)
            return False
        except Exception as e:
            trace.add_exc(f"codeql analyze unexpected for {lang}", e)
            return False

    def generate_cbom(
        self,
        git_url: str,
        branch: str = "main",
        main_language: str | None = None,
        trace: Trace | None = None,
    ) -> str:
        """Clone -> CodeQL -> cryptobom with progressive error trace accumulation.

        Returns raw CBOM JSON text on success; raises on failure so the caller/wrapper can classify.
        """
        trace = trace or Trace()
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = clone_repo(git_url, branch=branch, target_dir=tmpdir)
            if not repo_path:
                trace.add("git clone failed")
                raise RuntimeError("git_clone_failed")

            ok = self._run_codeql_scan(repo_path, main_language, trace)
            if not ok:
                logger.warning("CodeQL scan failed for language: %s", main_language)
                raise RuntimeError("codeql_scan_failed")

            sarif_files = [os.path.join(repo_path, f) for f in os.listdir(repo_path) if f.endswith(".sarif")]
            if not sarif_files:
                trace.add("no SARIF files found for cryptobom input")
                raise RuntimeError("no_sarif_found")
            logger.info("SARIF files found: %s", sarif_files)

            results_count = self._sarif_results_count(sarif_files[0], trace)
            if results_count == 0:
                trace.add("empty SARIF: no CodeQL results found")
                raise RuntimeError("empty_sarif_no_results")

            # Normalize SARIF to avoid downstream KeyError on 'contextRegion'
            self._normalize_sarif_files(sarif_files, trace)

            output_path = os.path.join(tmpdir, "cbom.json")
            if not self._run_cryptobom(sarif_files[0], output_path, trace):
                raise RuntimeError("cryptobom_failed")

            try:
                with open(output_path, encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                trace.add_exc("read cbom output failed", e)
                raise

    # --- helpers ---
    def _sarif_results_count(self, sarif_path: str, trace: Trace) -> int | None:
        try:
            with open(sarif_path, encoding="utf-8") as _sf:
                _sdata = json.load(_sf)
            _runs = _sdata.get("runs") or []
            _cnt = 0
            for _r in _runs:
                _cnt += len(_r.get("results") or [])
            logger.info("First SARIF run count: %d run(s), %d result(s)", len(_runs), _cnt)
            return _cnt
        except Exception as e:
            logger.debug("Failed to read SARIF stats: %s", e)
            trace.add(f"sarif stat read failed: {e}")
            return None

    def _normalize_sarif_files(self, sarif_files: list[str], trace: Trace) -> None:
        try:
            patched = 0
            for s in sarif_files:
                if self._ensure_context_region(s):
                    patched += 1
            if patched:
                logger.info("Patched %d SARIF file(s) with contextRegion", patched)
        except Exception as e:
            logger.warning("SARIF normalization step failed: %s", e)
            trace.add(f"sarif normalize failed: {e}")

    def _run_cryptobom(self, cryptobom_input: str, output_path: str, trace: Trace) -> bool:
        timeout_secs = max(1, self.timeout_sec - 5)
        cmd = ["cryptobom", "generate", cryptobom_input, "--output-file", output_path]
        logger.info("Running cryptobom: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=timeout_secs,
            )
            logger.info("cryptobom CLI stdout:\n%s", result.stdout.decode("utf-8", "replace"))
            logger.info("cryptobom CLI stderr:\n%s", result.stderr.decode("utf-8", "replace"))
        except subprocess.CalledProcessError as e:
            stdout = getattr(e, "stdout", b"").decode("utf-8", "replace") if hasattr(e, "stdout") else ""
            stderr = getattr(e, "stderr", b"").decode("utf-8", "replace") if hasattr(e, "stderr") else ""
            trace.add_exc(f"cryptobom CLI failed (rc={e.returncode})", e, stdout, stderr)
            return False
        except Exception as e:
            trace.add_exc("cryptobom unexpected error", e)
            return False

        if not os.path.exists(output_path):
            trace.add("cryptobom CLI did not produce output file")
            return False
        return True


def _produce(instr: JobInstruction, trace: Trace) -> str:
    logger.info("JobInstruction received: %s", instr.to_dict())
    return CryptobomForgeClient(timeout_sec=TIMEOUT_SEC).generate_cbom(
        git_url=instr.repo_info.git_url,
        branch=instr.repo_info.branch,
        main_language=instr.repo_info.main_language,
        trace=trace,
    )


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    # Prove codeql is callable and log its version and path
    try:
        import shutil

        codeql_path = shutil.which("codeql")
        if codeql_path:
            logger.info("CodeQL CLI found at: %s", codeql_path)
            result = subprocess.run(
                ["codeql", "version"],
                capture_output=True,
                timeout=5,
            )
            logger.info(
                "CodeQL version stdout: %s",
                result.stdout.decode("utf-8", "replace").strip(),
            )
            logger.info(
                "CodeQL version stderr: %s",
                result.stderr.decode("utf-8", "replace").strip(),
            )
        else:
            logger.warning("CodeQL CLI not found in PATH!")
    except Exception as e:
        logger.error("Error checking CodeQL CLI: %s", e)

    # Delegate the queue/timeout loop to the shared runner
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
