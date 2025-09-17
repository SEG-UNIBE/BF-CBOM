import json
import logging
import os

from common.models import JobInstruction, Trace
from common.worker import build_handle_instruction, run_worker

# Worker name and timeout settings
NAME = os.path.basename(os.path.dirname(__file__))
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "6"))  # default 6s

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


class SkeletonClient:
    def __init__(self):
        pass

    def generate_cbom(self, git_url: str, branch: str = "main", trace: Trace | None = None) -> str:
        """
        Implement your CBOM generation here.
        Return a JSON string (valid or empty '{}').
        """
        # Minimal placeholder: empty CBOM
        return json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "components": [],
            },
            ensure_ascii=False,
        )


def _produce(instr: JobInstruction, trace: Trace) -> str:
    return SkeletonClient().generate_cbom(
        git_url=instr.repo_info.git_url,
        branch=instr.repo_info.branch,
        trace=trace,
    )


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    # Delegate the queue/timeout loop to the shared runner
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
