"""Corpus builder: stratified GitHub sampling, crypto pre-filter and commit pinning (Phase 1.5).

Produces the `repos:` list consumed by `common.experiment.load_spec` via `corpus: <file>`.
Runs inside the `runner` container; needs network access to GitHub (a token raises the
search rate limit from 10 to 30 requests per minute).
"""

from __future__ import annotations

import logging
import random
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

import requests

from .utils import resolve_remote_commit

logger = logging.getLogger(__name__)

# Import/usage patterns that make a repo interesting for CBOM generation (grep on a shallow clone).
CRYPTO_PATTERNS: dict[str, str] = {
    "java": r"javax\.crypto|java\.security\.(MessageDigest|KeyPairGenerator|Signature|SecureRandom|KeyFactory|KeyStore)"
    r"|org\.bouncycastle|javax\.net\.ssl",
    "python": r"^\s*(from|import)\s+(cryptography|hashlib|hmac|ssl|Crypto|Cryptodome|nacl|OpenSSL|ecdsa|rsa|bcrypt"
    r"|passlib|jwt)\b",
}
# Repos that are themselves CBOM tooling or benchmarks would bias the comparison.
DEFAULT_EXCLUDE_PATTERNS = [r"cbom", r"benchmark", r"sonar-cryptography", r"cryptobom", r"crypto-finder", r"cryptoscan"]


@dataclass
class CorpusParams:
    languages: list[str] = field(default_factory=lambda: ["Java", "Python"])
    per_language: int = 100
    star_bins: list[tuple[int, int]] = field(
        default_factory=lambda: [(50, 300), (300, 2000), (2000, 15000), (15000, 1_000_000)]
    )
    size_bins_kb: list[tuple[int, int]] = field(default_factory=lambda: [(300, 3000), (3000, 30000), (30000, 300000)])
    pushed_within_days: int = 365
    crypto_filter: bool = True
    exclude_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_PATTERNS))
    seed: int = 20261015


def _headers(token: str | None) -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "bf-cbom-corpus"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def search_cell(
    language: str,
    stars: tuple[int, int],
    size_kb: tuple[int, int],
    pushed_since: str,
    token: str | None,
    *,
    per_page: int = 100,
) -> list[dict]:
    """One GitHub search query for a (language, stars, size) cell; retries on rate limiting."""
    q = (
        f"language:{language} stars:{stars[0]}..{stars[1]} size:{size_kb[0]}..{size_kb[1]} "
        f"pushed:>={pushed_since} archived:false fork:false"
    )
    url = "https://api.github.com/search/repositories"
    for attempt in range(6):
        resp = requests.get(url, params={"q": q, "per_page": per_page}, headers=_headers(token), timeout=30)
        if resp.status_code == 200:
            return [
                {
                    "full_name": it["full_name"],
                    "git_url": it["clone_url"],
                    "branch": it.get("default_branch") or "main",
                    "language": it.get("language"),
                    "stars": it.get("stargazers_count"),
                    "size_kb": it.get("size"),
                    "pushed_at": it.get("pushed_at"),
                }
                for it in resp.json().get("items", [])
            ]
        if resp.status_code in (403, 429):
            wait = int(resp.headers.get("Retry-After") or 0) or max(
                5, int(resp.headers.get("X-RateLimit-Reset", "0") or 0) - int(time.time())
            )
            wait = min(max(wait, 5), 120)
            logger.warning("GitHub search rate limited; sleeping %ss (attempt %d)", wait, attempt + 1)
            time.sleep(wait)
            continue
        raise RuntimeError(f"GitHub search failed ({resp.status_code}): {resp.text[:200]}")
    raise RuntimeError("GitHub search: giving up after repeated rate limiting")


def has_crypto_usage(git_url: str, branch: str, language: str, *, timeout: float = 300) -> bool | None:
    """Shallow-clone and grep for crypto APIs. None when the clone fails."""
    pattern = CRYPTO_PATTERNS.get(language.lower())
    if not pattern:
        return True
    with tempfile.TemporaryDirectory(prefix="corpus-") as tmp:
        clone = subprocess.run(
            ["git", "clone", "--quiet", "--depth", "1", "-b", branch, git_url, tmp],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"},
            check=False,
        )
        if clone.returncode != 0:
            return None
        grep = subprocess.run(
            [
                "git",
                "-C",
                tmp,
                "grep",
                "-l",
                "-I",
                "-E",
                pattern,
                "--",
                f"*.{'java' if language.lower() == 'java' else 'py'}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return grep.returncode == 0 and bool(grep.stdout.strip())


def _excluded(full_name: str, patterns: list[str]) -> bool:
    return any(re.search(p, full_name, flags=re.IGNORECASE) for p in patterns)


def build_corpus(params: CorpusParams, token: str | None, *, log=print) -> dict:
    """Stratified sample per language over stars x size cells, filtered and pinned to commits."""
    rng = random.Random(params.seed)
    pushed_since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - params.pushed_within_days * 86400))
    cells = [(s, z) for s in params.star_bins for z in params.size_bins_kb]
    quota = max(1, -(-params.per_language // len(cells)))  # ceil
    chosen: list[dict] = []
    stats = {"searched": 0, "excluded": 0, "no_crypto": 0, "clone_failed": 0, "unresolved": 0}
    for language in params.languages:
        picked_for_lang: list[dict] = []
        for stars, size in cells:
            candidates = search_cell(language, stars, size, pushed_since, token)
            stats["searched"] += len(candidates)
            rng.shuffle(candidates)
            taken = 0
            for cand in candidates:
                if taken >= quota or len(picked_for_lang) >= params.per_language:
                    break
                if _excluded(cand["full_name"], params.exclude_patterns):
                    stats["excluded"] += 1
                    continue
                if params.crypto_filter:
                    hit = has_crypto_usage(cand["git_url"], cand["branch"], language)
                    if hit is None:
                        stats["clone_failed"] += 1
                        continue
                    if not hit:
                        stats["no_crypto"] += 1
                        continue
                commit = resolve_remote_commit(cand["git_url"], cand["branch"])
                if not commit:
                    stats["unresolved"] += 1
                    continue
                cand["commit"] = commit
                cand["stratum"] = {"stars": list(stars), "size_kb": list(size)}
                picked_for_lang.append(cand)
                taken += 1
                log(f"  + {cand['full_name']} ({language}, {cand['stars']}★, {cand['size_kb']} KB) {commit[:12]}")
            time.sleep(2.5 if token else 6.5)  # stay under the search rate limit
        log(f"{language}: {len(picked_for_lang)} repos")
        chosen.extend(picked_for_lang)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "params": {
            "languages": params.languages,
            "per_language": params.per_language,
            "star_bins": [list(b) for b in params.star_bins],
            "size_bins_kb": [list(b) for b in params.size_bins_kb],
            "pushed_since": pushed_since,
            "crypto_filter": params.crypto_filter,
            "exclude_patterns": params.exclude_patterns,
            "seed": params.seed,
        },
        "stats": stats,
        "repos": chosen,
    }
