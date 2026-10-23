"""
Shared logger configuration for the coordinator Streamlit application.
Import this module to get the configured logger across all pages.
"""

import logging
import multiprocessing
import os

import streamlit as st


def _init_logging():
    """Initialize and configure the coordinator logger."""
    # Configure root logger for container logs
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger("coordinator")
    logger.setLevel(level)
    return logger


# Initialize the logger once at module level
logger = _init_logging()

# Log startup information in the main process only: a spawned worker of the analysis report (misc/cli/parallel.py)
# re-imports the CLI's main module, and its banner would land on the terminal past the progress display (the worker's
# process name is set before that import, its parent-process handle only after it)
if multiprocessing.current_process().name == "MainProcess":
    print("[coordinator] Logger initialized")
    logger.info("Coordinator logger initialized (version=%s)", getattr(st, "__version__", "?"))
