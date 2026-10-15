"""Shared worker body for the LLM provider workers (deepseek, chatgpt, claude).

Each `workers/<provider>/main.py` only builds an `LLMProvider`; everything else (clone at the pinned
commit, file selection, prompting, CBOM assembly, timeouts, provenance) is identical, so differences
between providers come from the model alone.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable

from .llm_cbom import LLM_MAX_CHUNKS, LLMProvider, generate_cbom
from .models import JobInstruction, Trace
from .utils import clone_repo
from .worker import build_handle_instruction, run_worker

TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "900"))
# Per-request budget; the whole job is bounded by TIMEOUT_SEC (the runner kills it) and LLM_MAX_CHUNKS.
API_TIMEOUT_SEC = float(os.getenv("LLM_API_TIMEOUT_SEC", "180"))


# Values the env templates ship with; treated as "no key" so an unconfigured worker exits instead of failing jobs.
_PLACEHOLDER_KEYS = {"", "your_secret", "changeme", "todo", "xxx", "none"}


def has_api_key(value: str | None) -> bool:
    return bool(value) and value.strip().strip("'\"").lower() not in _PLACEHOLDER_KEYS


def run_llm_worker(
    name: str, make_provider: Callable[[], LLMProvider], *, tool_version: str, api_key: str | None
) -> None:
    """Start the queue loop for one LLM worker. Exits early with a clear message when the key is missing."""
    log = logging.getLogger(name)
    if not has_api_key(api_key):
        log.error("%s: API key not configured (see docker/env/worker-%s.env); refusing to start", name, name)
        raise SystemExit(1)
    provider = make_provider()

    def _produce(instr: JobInstruction, trace: Trace) -> str:
        start = time.monotonic()
        deadline = start + max(30.0, TIMEOUT_SEC - API_TIMEOUT_SEC - 15.0)  # headroom to assemble + push
        with tempfile.TemporaryDirectory(prefix=f"{name}-") as tmp:
            repo_path = clone_repo(
                instr.repo_info.git_url, branch=instr.repo_info.branch, target_dir=tmp, commit=instr.repo_info.commit
            )
            if not repo_path:
                trace.add("git clone failed")
                raise RuntimeError("git_clone_failed")
            cbom, stats = generate_cbom(
                provider,
                repo_path,
                repo_info=instr.repo_info,
                language=instr.repo_info.main_language,
                trace=trace,
                max_chunks=LLM_MAX_CHUNKS,
                deadline=deadline,
            )
        log.info(
            "%s: %d assets, %d requests for %d chunks, %d+%d tokens",
            instr.repo_info.full_name,
            len(cbom["components"]),
            stats.chunks_sent,
            stats.chunks_total,
            stats.prompt_tokens,
            stats.completion_tokens,
        )
        return json.dumps(cbom, ensure_ascii=False)

    run_worker(name, build_handle_instruction(name, _produce), default_timeout=TIMEOUT_SEC, tool_version=tool_version)
