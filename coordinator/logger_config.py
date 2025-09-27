"""
Shared logger configuration for the coordinator Streamlit application.
Import this module to get the configured logger across all pages.
"""

import logging
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

# Log startup information
print("[coordinator] Logger initialized")
logger.info("Coordinator logger initialized (version=%s)", getattr(st, "__version__", "?"))
