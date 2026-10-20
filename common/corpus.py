"""Corpus builder: stratified GitHub sampling, crypto pre-filter and commit pinning (Phase 1.5).

Produces the `repos:` list consumed by `common.experiment.load_spec` via `corpus: <file>`.
Runs inside the `runner` container; needs network access to GitHub (a token raises the
search rate limit from 10 to 30 requests per minute).

Exclusions (CLAUDE.md refinement 11): name patterns (CBOM tooling, benchmarks), the reviewed list
`corpus/exclusions.yaml` (the generators' own repos and test suites, with a reason each), and the repos a tool was
tuned on (`tailored_on` in common/tools.yaml, refinement 13). The crypto pre-filter greps first-party source only
(no test, vendor, example or documentation paths). Every exclusion is recorded in the output with its reason.
`prune_corpus` drops repos where a tool timed out in an earlier experiment (E0), with the reason.

Repo size cap: the git server serves full clones from a container limited to GITSERVER_MEMORY (Makefile), so the
corpus never samples repos above CORPUS_MAX_REPO_SIZE_MB (default GITSERVER_MEMORY / SIZE_CAP_RATIO; GitHub's `size`
is the packed repository size in KB). The cap is recorded in the corpus `params` and the search cells are clamped to
it; a candidate above it is listed as excluded with the reason.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

from .tools_registry import REPO_ROOT, tailored_map
from .utils import resolve_remote_commit

EXCLUSIONS_FILE = REPO_ROOT / "corpus" / "exclusions.yaml"
# Paths the crypto pre-filter ignores: the scope is first-party source (refinement 11; some tools skip tests too)
NON_FIRST_PARTY = (
    "test", "tests", "testing", "spec", "vendor", "third_party", "thirdparty", "node_modules", "example", "examples",
    "sample", "samples", "demo", "docs", "doc", "benchmark", "benchmarks",
)  # fmt: skip

logger = logging.getLogger(__name__)

# Import/usage patterns that make a repo interesting for CBOM generation (grep on a shallow clone).
CRYPTO_PATTERNS: dict[str, str] = {
    "java": r"javax\.crypto|java\.security\.(MessageDigest|KeyPairGenerator|Signature|SecureRandom|KeyFactory|KeyStore)"
    r"|org\.bouncycastle|javax\.net\.ssl",
    "python": r"^\s*(from|import)\s+(cryptography|hashlib|hmac|ssl|Crypto|Cryptodome|nacl|OpenSSL|ecdsa|rsa|bcrypt"
    r"|passlib|jwt)\b",
    # an import path in quotes (single or grouped imports); crypto/rand alone is not enough (IDs, nonces)
    "go": r'"crypto/(aes|cipher|des|ecdh|ecdsa|ed25519|elliptic|hkdf|hmac|md5|mlkem|pbkdf2|rc4|rsa|sha1|sha256|sha3'
    r'|sha512|tls|x509)"|"golang\.org/x/crypto/',
}
SOURCE_EXTENSIONS = {"java": "java", "python": "py", "go": "go"}
# per language, first-party exclusions beyond NON_FIRST_PARTY directories (Go keeps tests next to the code)
EXTRA_EXCLUDES = {"go": ["**/*_test.go", "**/testdata/**"]}
# Repos that are themselves CBOM tooling or benchmarks would bias the comparison.
DEFAULT_EXCLUDE_PATTERNS = [r"cbom", r"benchmark", r"sonar-cryptography", r"cryptobom", r"crypto-finder", r"cryptoscan"]
# Rule of thumb: a repo whose packed size is below GITSERVER_MEMORY / SIZE_CAP_RATIO clones through the git server
# (pack-objects holds the object list plus bounded delta windows, Dockerfile.gitserver).
SIZE_CAP_RATIO = 4
_SIZE_UNITS = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def parse_size_mb(text: str | None) -> int | None:
    """A Docker memory string (`4g`, `512m`, `1024k`, bytes without a suffix) in MB; None when empty or malformed."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgb]?)i?b?\s*", (text or "").lower())
    if not m:
        return None
    return int(float(m.group(1)) * _SIZE_UNITS[m.group(2)] / 1024**2)


def repo_size_cap_mb(env: dict | None = None) -> int | None:
    """The corpus size cap in MB: CORPUS_MAX_REPO_SIZE_MB when set, else GITSERVER_MEMORY / SIZE_CAP_RATIO."""
    env = os.environ if env is None else env
    explicit = (env.get("CORPUS_MAX_REPO_SIZE_MB") or "").strip()
    if explicit:
        return int(float(explicit))
    gitserver = parse_size_mb(env.get("GITSERVER_MEMORY"))
    return gitserver // SIZE_CAP_RATIO if gitserver else None


def clamp_size_bins(bins: list[tuple[int, int]], cap_mb: int | None) -> list[tuple[int, int]]:
    """The size cells (KB) cut at the cap: cells above it disappear, the cell containing it ends at the cap."""
    if not cap_mb:
        return list(bins)
    cap_kb = cap_mb * 1024
    return [(lo, min(hi, cap_kb)) for lo, hi in bins if lo < cap_kb]


@dataclass
class CorpusParams:
    languages: list[str] = field(default_factory=lambda: ["Java", "Python", "Go"])  # the paper's (2026-10-20)
    per_language: int = 100
    star_bins: list[tuple[int, int]] = field(
        default_factory=lambda: [(50, 300), (300, 2000), (2000, 15000), (15000, 1_000_000)]
    )
    size_bins_kb: list[tuple[int, int]] = field(default_factory=lambda: [(300, 3000), (3000, 30000), (30000, 300000)])
    pushed_within_days: int = 365
    crypto_filter: bool = True
    exclude_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_PATTERNS))
    seed: int = 20261015
    max_repo_size_mb: int | None = field(default_factory=repo_size_cap_mb)  # see the module docstring


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
            ["git", "-C", tmp, "grep", "-l", "-I", "-E", pattern, "--", *grep_pathspecs(language)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return grep.returncode == 0 and bool(grep.stdout.strip())


def _excluded(full_name: str, patterns: list[str]) -> bool:
    return any(re.search(p, full_name, flags=re.IGNORECASE) for p in patterns)


def load_exclusions(path: Path = EXCLUSIONS_FILE) -> dict:
    """{"repos": {full name (lower): reason}, "patterns": [(regex, reason)]} from corpus/exclusions.yaml."""
    if not Path(path).exists():
        return {"repos": {}, "patterns": []}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {
        "repos": {str(r["full_name"]).lower(): str(r.get("reason") or "excluded") for r in data.get("repos") or []},
        "patterns": [(str(p["regex"]), str(p.get("reason") or "excluded")) for p in data.get("patterns") or []],
    }


def exclusion_reason(
    full_name: str,
    *,
    patterns: list[str],
    exclusions: dict | None = None,
    tailored: dict[str, frozenset[str]] | None = None,
) -> str | None:
    """Why a repo may not enter the corpus, or None: a tool was tuned on it (refinement 13), the reviewed list, a
    reviewed pattern, or the builder's default name patterns."""
    for tool, repos in (tailored or {}).items():
        if full_name in repos:
            return f"tailored_on of {tool} (tools.yaml)"
    exclusions = exclusions or {"repos": {}, "patterns": []}
    if full_name.lower() in exclusions["repos"]:
        return exclusions["repos"][full_name.lower()]
    for regex, reason in exclusions["patterns"]:
        if re.search(regex, full_name, flags=re.IGNORECASE):
            return f"{reason} ({regex})"
    if _excluded(full_name, patterns):
        return "name pattern"
    return None


def grep_pathspecs(language: str) -> list[str]:
    """`git grep` pathspecs for first-party source of `language`: its extensions, minus NON_FIRST_PARTY directories
    (and Go's test files and testdata)."""
    ext = SOURCE_EXTENSIONS[language.lower()]
    excludes = [f"**/{d}/**" for d in NON_FIRST_PARTY] + EXTRA_EXCLUDES.get(language.lower(), [])
    return [f"*.{ext}", *(f":(exclude,glob){x}" for x in excludes)]


def build_corpus(
    params: CorpusParams,
    token: str | None,
    *,
    log=print,
    exclusions: dict | None = None,
    tailored: dict[str, frozenset[str]] | None = None,
) -> dict:
    """Stratified sample per language over stars x size cells, filtered and pinned to commits. Exclusions default to
    corpus/exclusions.yaml and the registry's `tailored_on`; every excluded candidate is listed with its reason."""
    exclusions = load_exclusions() if exclusions is None else exclusions
    tailored = tailored_map() if tailored is None else tailored
    rng = random.Random(params.seed)
    pushed_since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - params.pushed_within_days * 86400))
    cap_mb = params.max_repo_size_mb
    size_bins = clamp_size_bins(params.size_bins_kb, cap_mb)
    if cap_mb:
        log(f"repo size cap {cap_mb} MB (git server memory / {SIZE_CAP_RATIO}); size cells (KB): {size_bins}")
    cells = [(s, z) for s in params.star_bins for z in size_bins]
    quota = max(1, -(-params.per_language // len(cells)))  # ceil
    chosen: list[dict] = []
    excluded: list[dict] = []
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
                reason = exclusion_reason(
                    cand["full_name"], patterns=params.exclude_patterns, exclusions=exclusions, tailored=tailored
                )
                if reason:
                    stats["excluded"] += 1
                    excluded.append({"full_name": cand["full_name"], "reason": reason})
                    continue
                if cap_mb and (cand.get("size_kb") or 0) > cap_mb * 1024:
                    stats["excluded"] += 1
                    excluded.append(
                        {
                            "full_name": cand["full_name"],
                            "reason": f"repo size {cand['size_kb'] // 1024} MB > cap {cap_mb} MB (GITSERVER_MEMORY)",
                        }
                    )
                    continue
                if params.crypto_filter:
                    hit = has_crypto_usage(cand["git_url"], cand["branch"], language)
                    if hit is None:
                        stats["clone_failed"] += 1
                        excluded.append({"full_name": cand["full_name"], "reason": "clone failed"})
                        continue
                    if not hit:
                        stats["no_crypto"] += 1
                        excluded.append({"full_name": cand["full_name"], "reason": "no first-party crypto usage"})
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
            "size_bins_kb": [list(b) for b in size_bins],
            "max_repo_size_mb": cap_mb,
            "pushed_since": pushed_since,
            "crypto_filter": params.crypto_filter,
            "exclude_patterns": params.exclude_patterns,
            "seed": params.seed,
        },
        "stats": stats,
        "excluded": excluded,
        "repos": chosen,
    }


def prune_corpus(corpus: dict, exp_dir: Path, *, statuses: tuple[str, ...] = ("timeout",)) -> dict:
    """The corpus without the repos where some tool's run in `exp_dir` ended in one of `statuses` (E0 timeouts,
    refinement 11); the dropped repos are listed under `pruned` with the tools and statuses."""
    hits: dict[str, set[str]] = {}
    for meta_path in sorted(Path(exp_dir).glob("*/*/*/run*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("status") in statuses:
            repo = meta_path.parents[2].name.split("@", 1)[0].replace("__", "/", 1)
            hits.setdefault(repo, set()).add(f"{meta.get('worker') or meta_path.parent.name}:{meta.get('status')}")
    kept = [r for r in corpus.get("repos") or [] if r.get("full_name") not in hits]
    reason = f"timed out in {Path(exp_dir).name}"
    pruned = [
        {"full_name": r["full_name"], "reason": reason, "runs": sorted(hits[r["full_name"]])}
        for r in corpus.get("repos") or []
        if r.get("full_name") in hits
    ]
    return {**corpus, "pruned_from": Path(exp_dir).name, "pruned": pruned, "repos": kept}
