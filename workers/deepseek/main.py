"""DeepSeek worker: provider adapter over common/llm_cbom.py (OpenAI-compatible API)."""

import logging
import os

from common.config import DEEPSEEK_API_KEY
from common.llm_cbom import OpenAICompatProvider
from common.llm_worker import API_TIMEOUT_SEC, run_llm_worker

NAME = os.getenv("WORKER_NAME") or os.path.basename(os.path.dirname(__file__))
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")  # thinks by default; deepseek-chat until 2026-10-21
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def make_provider() -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name=NAME, model=MODEL, api_key=DEEPSEEK_API_KEY, base_url=BASE_URL, timeout=API_TIMEOUT_SEC
    )


if __name__ == "__main__":
    run_llm_worker(NAME, make_provider, tool_version=f"deepseek {MODEL}", api_key=DEEPSEEK_API_KEY)
