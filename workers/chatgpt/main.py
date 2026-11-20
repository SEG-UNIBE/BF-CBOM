"""ChatGPT (OpenAI) worker: provider adapter over common/llm_cbom.py (OpenAI chat completions API)."""

import logging
import os

from common.config import OPENAI_API_KEY
from common.llm_cbom import OpenAICompatProvider
from common.llm_worker import API_TIMEOUT_SEC, run_llm_worker

NAME = os.getenv("WORKER_NAME") or os.path.basename(os.path.dirname(__file__))
MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
BASE_URL = os.getenv("OPENAI_BASE_URL") or None  # default: api.openai.com

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def make_provider() -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name=NAME, model=MODEL, api_key=OPENAI_API_KEY, base_url=BASE_URL, timeout=API_TIMEOUT_SEC
    )


if __name__ == "__main__":
    run_llm_worker(NAME, make_provider, tool_version=f"chatgpt {MODEL}", api_key=OPENAI_API_KEY)
