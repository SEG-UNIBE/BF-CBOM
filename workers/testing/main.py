import hashlib
import json
import logging
import os
import time

from common.cbom_analysis import COMMON_CRYPTO_ASSET_TYPES
from common.models import JobInstruction, Trace
from common.worker import build_handle_instruction, run_worker

# Worker name and timeout settings
NAME = os.path.basename(os.path.dirname(__file__))
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "6"))  # default 6s

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


class TestingClient:
    def __init__(self):
        pass

    def generate_cbom_synth(
        self,
        *,
        repo_full_name: str,
        git_url: str,
        branch: str,
        timeout_sec: int,
        repo_size_kb: int | None,
    ) -> str:
        """
        Synthetic CBOM generator for testing the pipeline.
        - Deterministically returns timeout/error/ok based on repo name hash.
        - For OK cases, returns a CBOM whose size is ~10-60% of repo size (KB),
          capped to keep the UI responsive, and sleeps a deterministic duration
          under the timeout to simulate generation time.

        Behavior mapping (deterministic by repo_full_name):
          h % 13 == 0 -> simulate timeout (sleep > timeout_sec)
          h % 11 == 0 -> raise an exception (error)
          else        -> return CBOM JSON (sometimes larger)
        """
        # Hash the repo for stable behavior
        h = int(hashlib.md5((repo_full_name or "").encode("utf-8")).hexdigest(), 16)

        # Timeout path: sleep beyond runner timeout (run_worker detects and reports timeout)
        if h % 13 == 0:
            time.sleep(timeout_sec + 2)
            # This return won't usually be observed, as the runner already timed out
            return "{}"

        # Error path: raise to be caught by runner (or caller) as an error
        if h % 11 == 0:
            raise RuntimeError("testing_simulated_error")

        # OK path: build a CBOM sized relative to repo size (KB)
        repo_kb = int(repo_size_kb or 0)
        if repo_kb <= 0:
            repo_kb = 500  # fallback if missing

        # 10–60% ratio, deterministic from hash
        ratio = 0.10 + ((h >> 8) % 51) / 100.0  # 0.10..0.60
        target_kb = int(repo_kb * ratio)

        # Clamp to a sensible window
        min_kb = int(os.getenv("TESTING_MIN_CBOM_KB", "10"))
        max_kb = int(os.getenv("TESTING_MAX_CBOM_KB", "512"))
        target_kb = max(min_kb, min(target_kb, max_kb))
        target_bytes = target_kb * 1024

        # Simulate generation time deterministically; keep below timeout
        base_frac = 0.20 + ((h >> 4) % 66) / 100.0  # 0.20..0.86
        sleep_sec = max(0.05, min(timeout_sec - 0.25, timeout_sec * base_frac))
        time.sleep(sleep_sec)

        # Build components with adjustable notes to approach target size
        base_comp_count = 5 + (h % 36)  # 5..40
        components = []

        # First build with empty notes to estimate base size
        for i in range(base_comp_count):
            # Deterministic per-component asset type selection
            atype = COMMON_CRYPTO_ASSET_TYPES[(h + i * 31) % len(COMMON_CRYPTO_ASSET_TYPES)]
            comp = {
                "type": "crypto-asset",
                "name": f"component-{i}",
                "cryptoProperties": {
                    "assetType": atype,
                    "confidence": 0.9,
                },
                "notes": "",  # will be filled
            }
            components.append(comp)

        cbom = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "components": components,
            "metadata": {
                "generator": "testing-synth",
                "source": {
                    "git_url": git_url,
                    "branch": branch,
                    "full_name": repo_full_name,
                },
                "testing": {
                    "target_kb": target_kb,
                    "sleep_sec": round(sleep_sec, 3),
                },
            },
        }

        base_json = json.dumps(cbom, ensure_ascii=False)
        base_size = len(base_json.encode("utf-8"))

        # If base already exceeds target, reduce components
        if base_size > target_bytes:
            # Try with fewer components
            keep = max(0, int(base_comp_count * 0.3))
            cbom["components"] = cbom["components"][:keep]
            base_json = json.dumps(cbom, ensure_ascii=False)
            base_size = len(base_json.encode("utf-8"))

        remaining = max(0, target_bytes - base_size)
        comp_count = len(cbom["components"]) or 1
        # Distribute remaining across notes fields
        per_comp = remaining // comp_count if comp_count else 0
        remainder = remaining - per_comp * comp_count

        # Use a simple ASCII lorem to match bytes == chars
        lorem = (
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            "Phasellus volutpat sapien eu scelerisque ultrices. "
            "Sed maximus urna metus id arcu. "
        )

        for i, comp in enumerate(cbom["components"]):
            want = per_comp + (1 if i < remainder else 0)
            if want <= 0:
                comp["notes"] = ""
                continue
            # Build a notes string close to desired bytes
            repeats = (want // len(lorem)) + 1
            s = (lorem * repeats)[:want]
            comp["notes"] = s

        # Final JSON and any fine-tune padding
        final_json = json.dumps(cbom, ensure_ascii=False)
        cur_bytes = len(final_json.encode("utf-8"))
        if cur_bytes < target_bytes:
            pad = "x" * min(target_bytes - cur_bytes, 32 * 1024)  # cap extra pad to 32KB
            cbom["metadata"]["testing"]["padding"] = len(pad)
            cbom["metadata"]["testing"]["actual_kb_before_pad"] = round(cur_bytes / 1024, 1)
            cbom["metadata"]["testing"]["target_bytes"] = target_bytes
            cbom["metadata"]["testing"]["repo_kb"] = repo_kb
            cbom["metadata"]["testing"]["ratio"] = round(ratio, 3)
            cbom["metadata"]["testing"]["comp_count"] = comp_count
            cbom["metadata"]["testing"]["base_size_bytes"] = base_size
            cbom["metadata"]["testing"]["notes_per_comp"] = per_comp
            cbom["metadata"]["testing"]["remainder"] = remainder
            cbom["metadata"]["testing"]["sleep_sec"] = round(sleep_sec, 3)
            cbom["metadata"]["pad"] = pad
        else:
            cbom["metadata"]["testing"]["actual_kb"] = round(cur_bytes / 1024, 1)
            cbom["metadata"]["testing"]["target_bytes"] = target_bytes
            cbom["metadata"]["testing"]["repo_kb"] = repo_kb
            cbom["metadata"]["testing"]["ratio"] = round(ratio, 3)
            cbom["metadata"]["testing"]["comp_count"] = comp_count
            cbom["metadata"]["testing"]["base_size_bytes"] = base_size
            cbom["metadata"]["testing"]["notes_per_comp"] = per_comp
            cbom["metadata"]["testing"]["remainder"] = remainder
            cbom["metadata"]["testing"]["sleep_sec"] = round(sleep_sec, 3)

        return json.dumps(cbom, ensure_ascii=False)


def _produce(instr: JobInstruction, trace: Trace) -> str:
    return TestingClient().generate_cbom_synth(
        repo_full_name=instr.repo_info.full_name,
        git_url=instr.repo_info.git_url,
        branch=instr.repo_info.branch,
        timeout_sec=TIMEOUT_SEC,
        repo_size_kb=instr.repo_info.size_kb,
    )


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    # Delegate the queue/timeout loop to the shared runner
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
