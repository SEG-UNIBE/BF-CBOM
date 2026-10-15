import os
import re

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
# Queue consumers poll with a bounded BLPOP (returns None on timeout) and loop.
# The socket timeout must exceed the BLPOP timeout: redis-py >= 8 defaults it to 5s,
# which breaks unbounded or longer blocking pops.
BLPOP_TIMEOUT_SEC = 10
REDIS_SOCKET_TIMEOUT_SEC = 30

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_CACHE_TTL_SEC = int(os.getenv("GITHUB_CACHE_TTL_SEC", "86400"))  # default 1 day
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def _parse_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    # split on commas or whitespace, preserve order and drop empties
    out = []
    seen = set()
    for p in re.split(r"[\s,]+", raw):
        p = p.strip()
        if p and p not in seen:
            out.append(p)
            seen.add(p)
    return out


# Usage:
AVAILABLE_LANGUAGES = _parse_list(os.getenv("AVAILABLE_LANGUAGES"))

AVAILABLE_WORKERS = _parse_list(os.getenv("AVAILABLE_WORKERS"))
