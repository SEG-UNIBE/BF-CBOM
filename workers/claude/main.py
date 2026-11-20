"""Claude (Anthropic) worker: provider adapter over common/llm_cbom.py (Anthropic Messages API)."""

import logging
import os

from common.config import ANTHROPIC_API_KEY
from common.llm_cbom import AnthropicProvider
from common.llm_worker import API_TIMEOUT_SEC, run_llm_worker

NAME = os.getenv("WORKER_NAME") or os.path.basename(os.path.dirname(__file__))
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def make_provider() -> AnthropicProvider:
    return AnthropicProvider(name=NAME, model=MODEL, api_key=ANTHROPIC_API_KEY, timeout=API_TIMEOUT_SEC)


if __name__ == "__main__":
    run_llm_worker(NAME, make_provider, tool_version=f"claude {MODEL}", api_key=ANTHROPIC_API_KEY)
