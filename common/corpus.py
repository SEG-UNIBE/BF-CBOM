"""Corpus builder: stratified GitHub sampling, crypto pre-filter and commit pinning (Phase 1.5).

Produces the `repos:` list consumed by `common.experiment.load_spec` via `corpus: <file>`.
Runs inside the `runner` container; needs network access to GitHub (a token raises the
search rate limit from 10 to 30 requests per minute).

Sampling design (2026-10-29): the cells are language x star bin x size bin, every cell gets the same quota
(`per_cell`), so the sample covers the design space evenly (a comparison of tools across strata, not an estimate of
the GitHub population). Every cell's population is enumerated completely before the draw: the search is paginated
to the API's limit of 1000 results, and a cell above it is split by creation date until every part fits, so the draw
(`random.Random(seed)` over the full, name-sorted population) is uniform over the cell. The frame is recorded in the
corpus `params` (search date, bins, every cell with its population and quota), each sampled repo keeps its `stratum`,
and two covariates are recorded but never selected on: `crypto_apis` (the API families the pre-filter matched) and
`topics` (the repository's GitHub topics). No topic targeting: the crypto-use pre-filter is the one content criterion.

Exclusions (CLAUDE.md refinement 11): name patterns (CBOM tooling, benchmarks), the reviewed list
`experiments/corpus/exclusions.yaml` (the generators' own repos and test suites, with a reason each), and the repos
a tool was tuned on (`tailored_on` in common/tools.yaml, refinement 13). The crypto pre-filter greps first-party
source only (no test, vendor, example or documentation paths). Every exclusion is recorded with its reason.
`prune_corpus` drops repos where a tool timed out in an earlier experiment (the stability run), with the reason.

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
from datetime import date, timedelta
from pathlib import Path

import requests
import yaml

from .tools_registry import REPO_ROOT, tailored_map
from .utils import resolve_remote_commit

EXCLUSIONS_FILE = REPO_ROOT / "experiments" / "corpus" / "exclusions.yaml"
# Paths the crypto pre-filter ignores: the scope is first-party source (refinement 11; some tools skip tests too)
NON_FIRST_PARTY = (
    "test", "tests", "testing", "spec", "vendor", "third_party", "thirdparty", "node_modules", "example", "examples",
    "sample", "samples", "demo", "docs", "doc", "benchmark", "benchmarks",
)  # fmt: skip

logger = logging.getLogger(__name__)

# Import/usage patterns that make a repo interesting for CBOM generation (grep on a shallow clone), per API family:
# the families a repo matches are recorded as the covariate `crypto_apis` (never selected on).
CRYPTO_APIS: dict[str, dict[str, str]] = {
    "java": {
        "JCA": r"javax\.crypto|java\.security\.(MessageDigest|KeyPairGenerator|Signature|SecureRandom|KeyFactory"
        r"|KeyStore)",
        "BouncyCastle": r"org\.bouncycastle",
        "JSSE": r"javax\.net\.ssl",
    },
    "python": {
        "pyca": r"^\s*(from|import)\s+cryptography\b",
        "hashlib": r"^\s*(from|import)\s+(hashlib|hmac)\b",
        "ssl": r"^\s*(from|import)\s+ssl\b",
        "pycryptodome": r"^\s*(from|import)\s+(Crypto|Cryptodome)\b",
        "nacl": r"^\s*(from|import)\s+nacl\b",
        "pyOpenSSL": r"^\s*(from|import)\s+OpenSSL\b",
        "other": r"^\s*(from|import)\s+(ecdsa|rsa|bcrypt|passlib|jwt)\b",
    },
    "go": {
        # an import path in quotes (single or grouped imports); crypto/rand alone is not enough (IDs, nonces)
        "stdlib": r'"crypto/(aes|cipher|des|ecdh|ecdsa|ed25519|elliptic|hkdf|hmac|md5|mlkem|pbkdf2|rc4|rsa|sha1|sha256'
        r'|sha3|sha512|tls|x509)"',
        "x/crypto": r'"golang\.org/x/crypto/',
    },
}
CRYPTO_PATTERNS: dict[str, str] = {lang: "|".join(fams.values()) for lang, fams in CRYPTO_APIS.items()}
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


Bin = tuple[int, int | None]  # [lo, hi): hi None = open (up to the size cap for sizes)


def parse_bins(text: str) -> list[Bin]:
    """`"50-300,300-2000,2000-"` -> [(50, 300), (300, 2000), (2000, None)]: bins are [lo, hi), the last may be open."""
    bins: list[Bin] = []
    for part in (p.strip() for p in text.split(",") if p.strip()):
        lo, dash, hi = part.partition("-")
        if not dash or not lo.strip().isdigit() or (hi.strip() and not hi.strip().isdigit()):
            raise ValueError(f"a bin is `lo-hi` or `lo-`: {part!r}")
        bins.append((int(lo), int(hi) if hi.strip() else None))
    if not bins:
        raise ValueError("no bins")
    for (_, hi), (lo, _) in zip(bins, bins[1:], strict=False):
        if hi is None or lo != hi:
            raise ValueError(f"bins must be adjacent: {text!r}")
    return bins


def clamp_size_bins(bins: list[Bin], cap_mb: int | None) -> list[Bin]:
    """The size cells (KB) cut at the cap: cells above it disappear, the cell containing it (or the open last one)
    ends at the cap."""
    if not cap_mb:
        return list(bins)
    cap_kb = cap_mb * 1024
    return [(lo, cap_kb if hi is None else min(hi, cap_kb)) for lo, hi in bins if lo < cap_kb]


def bin_query(lo: int, hi: int | None) -> str:
    """The GitHub range qualifier of a [lo, hi) bin: `lo..hi-1`, or `>=lo` when open."""
    return f">={lo}" if hi is None else f"{lo}..{hi - 1}"


def bin_text(lo: int, hi: int | None) -> str:
    return f"{lo}-" if hi is None else f"{lo}-{hi}"


@dataclass
class CorpusParams:
    languages: list[str] = field(default_factory=lambda: ["Java", "Python", "Go"])  # the paper's (2026-10-20)
    per_cell: int = 2  # repos drawn per (language, stars, size) cell; 3 x 3 cells x 2 = 18 per language
    star_bins: list[Bin] = field(default_factory=lambda: [(50, 300), (300, 2000), (2000, None)])
    size_bins_kb: list[Bin] = field(default_factory=lambda: [(300, 3000), (3000, 30000), (30000, None)])
    pushed_within_days: int = 365
    crypto_filter: bool = True
    exclude_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_PATTERNS))
    seed: int = 20261015
    max_repo_size_mb: int | None = field(default_factory=repo_size_cap_mb)  # see the module docstring

    @property
    def cells_per_language(self) -> int:
        return len(self.star_bins) * len(clamp_size_bins(self.size_bins_kb, self.max_repo_size_mb))

    @property
    def per_language(self) -> int:
        return self.cells_per_language * self.per_cell


def _headers(token: str | None) -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "bf-cbom-corpus"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


SEARCH_URL = "https://api.github.com/search/repositories"
SEARCH_LIMIT = 1000  # GitHub returns at most 1000 results per query
GITHUB_EPOCH = date(2008, 1, 1)


def _repo(it: dict) -> dict:
    return {
        "full_name": it["full_name"],
        "git_url": it["clone_url"],
        "branch": it.get("default_branch") or "main",
        "language": it.get("language"),
        "stars": it.get("stargazers_count"),
        "size_kb": it.get("size"),
        "pushed_at": it.get("pushed_at"),
        "created_at": it.get("created_at"),
        "topics": sorted(it.get("topics") or []),  # a covariate, never a selection criterion
    }


def _search(q: str, token: str | None, *, page: int = 1, per_page: int = 100) -> tuple[list[dict], int]:
    """One search request: the page's items and the query's total count; retries on rate limiting."""
    for attempt in range(6):
        resp = requests.get(
            SEARCH_URL, params={"q": q, "per_page": per_page, "page": page}, headers=_headers(token), timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("items", []), int(data.get("total_count") or 0)
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


def cell_query(language: str, stars: Bin, size_kb: Bin, pushed_since: str) -> str:
    return (
        f"language:{language} stars:{bin_query(*stars)} size:{bin_query(*size_kb)} "
        f"pushed:>={pushed_since} archived:false fork:false"
    )


def _enumerate(q: str, token: str | None, created: tuple[date, date], *, per_page: int, pause: float,
               log=lambda m: None) -> tuple[list[dict], int]:  # fmt: skip
    """Every repository matching `q` created within `created` (inclusive): paginated up to the API's limit; above it
    the date range is halved and both halves enumerated, so the population is complete. Returns the repos and the
    number of requests."""
    lo, hi = created
    query = f"{q} created:{lo.isoformat()}..{hi.isoformat()}"
    items, total = _search(query, token, page=1, per_page=per_page)
    requests_made = 1
    if total > SEARCH_LIMIT and lo < hi:
        mid = lo + (hi - lo) // 2
        time.sleep(pause)
        left, n1 = _enumerate(q, token, (lo, mid), per_page=per_page, pause=pause, log=log)
        time.sleep(pause)
        right, n2 = _enumerate(q, token, (mid + timedelta(days=1), hi), per_page=per_page, pause=pause, log=log)
        return left + right, requests_made + n1 + n2
    if total > SEARCH_LIMIT:
        log(f"  ! {total} repos created on {lo}: the API returns {SEARCH_LIMIT} at most, the population is truncated")
    repos = [_repo(it) for it in items]
    page = 1
    while len(repos) < min(total, SEARCH_LIMIT) and page * per_page < SEARCH_LIMIT:
        page += 1
        time.sleep(pause)
        items, _ = _search(query, token, page=page, per_page=per_page)
        requests_made += 1
        if not items:
            break
        repos += [_repo(it) for it in items]
    return repos, requests_made


def search_cell(
    language: str,
    stars: Bin,
    size_kb: Bin,
    pushed_since: str,
    token: str | None,
    *,
    per_page: int = 100,
    pause: float = 2.1,
    log=lambda m: None,
) -> tuple[list[dict], int, int]:
    """The complete population of a (language, stars, size) cell: every matching repository (deduplicated by full
    name), the cell's total count as the API states it, and the requests it took. `pause` is the wait between
    requests (30 per minute with a token)."""
    q = cell_query(language, stars, size_kb, pushed_since)
    repos, requests_made = _enumerate(q, token, (GITHUB_EPOCH, date.today()), per_page=per_page, pause=pause, log=log)
    _, total = _search(q, token, page=1, per_page=1)  # the cell's own count, next to the enumeration
    seen: dict[str, dict] = {}
    for r in repos:
        seen.setdefault(r["full_name"], r)
    return list(seen.values()), total, requests_made + 1


def has_crypto_usage(git_url: str, branch: str, language: str, *, timeout: float = 300) -> list[str] | None:
    """Shallow-clone and grep first-party source for the crypto API families of `language` (CRYPTO_APIS). Returns the
    families matched (empty: no first-party crypto usage, the repo is not sampled; the list is the covariate
    `crypto_apis`), or None when the clone fails. A language without patterns matches `["unchecked"]`."""
    families = CRYPTO_APIS.get(language.lower())
    if not families:
        return ["unchecked"]
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
        matched = []
        for family, pattern in families.items():
            grep = subprocess.run(
                ["git", "-C", tmp, "grep", "-l", "-I", "-E", pattern, "--", *grep_pathspecs(language)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if grep.returncode == 0 and grep.stdout.strip():
                matched.append(family)
        return matched


def _excluded(full_name: str, patterns: list[str]) -> bool:
    return any(re.search(p, full_name, flags=re.IGNORECASE) for p in patterns)


def load_exclusions(path: Path = EXCLUSIONS_FILE) -> dict:
    """{"repos": {full name (lower): reason}, "patterns": [(regex, reason)]} from experiments/corpus/exclusions.yaml."""
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
    status=lambda language, picked, now: None,
    exclusions: dict | None = None,
    tailored: dict[str, frozenset[str]] | None = None,
) -> dict:
    """Stratified sample per language over stars x size cells, `per_cell` repos each, filtered and pinned to
    commits. Every cell's population is enumerated and recorded, the draw is a seeded shuffle of the name-sorted
    population, and the first `per_cell` candidates that pass the exclusions, the size cap and the crypto pre-filter
    are taken (so the sample is uniform over the eligible repos of the cell). Exclusions default to
    experiments/corpus/exclusions.yaml and the registry's `tailored_on`; every excluded candidate is listed
    with its reason.
    `status(language, picked, now)` follows the search (the CLI's progress line): `now` is the cell searched or the
    repo checked, None once the language is done."""
    exclusions = load_exclusions() if exclusions is None else exclusions
    tailored = tailored_map() if tailored is None else tailored
    rng = random.Random(params.seed)
    pushed_since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - params.pushed_within_days * 86400))
    cap_mb = params.max_repo_size_mb
    size_bins = clamp_size_bins(params.size_bins_kb, cap_mb)
    if cap_mb:
        log(f"repo size cap {cap_mb} MB (git server memory / {SIZE_CAP_RATIO}); size cells (KB): {size_bins}")
    cells = [(s, z) for s in params.star_bins for z in size_bins]
    quota = params.per_cell
    log(
        f"{len(params.star_bins)} star bins x {len(size_bins)} size bins = {len(cells)} cells x {quota} repos = "
        f"{len(cells) * quota} per language, {len(cells) * quota * len(params.languages)} in all"
    )
    pause = 2.1 if token else 6.5  # 30 (10) search requests per minute
    chosen: list[dict] = []
    excluded: list[dict] = []
    frame: list[dict] = []
    stats = {"searched": 0, "requests": 0, "excluded": 0, "no_crypto": 0, "clone_failed": 0, "unresolved": 0}
    for language in params.languages:
        picked_for_lang: list[dict] = []
        for stars, size in cells:
            label = f"{bin_text(*stars)} stars, {bin_text(*size)} KB"
            status(language, len(picked_for_lang), f"enumerating {label}")
            candidates, population, n_requests = search_cell(
                language, stars, size, pushed_since, token, pause=pause, log=log
            )
            stats["searched"] += len(candidates)
            stats["requests"] += n_requests
            candidates.sort(key=lambda c: c["full_name"])  # the API's order varies; the seed decides the draw
            rng.shuffle(candidates)
            taken = tried = 0
            for cand in candidates:
                if taken >= quota:
                    break
                tried += 1
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
                    status(language, len(picked_for_lang), f"{cand['full_name']}: cloning for the crypto check")
                    apis = has_crypto_usage(cand["git_url"], cand["branch"], language)
                    if apis is None:
                        stats["clone_failed"] += 1
                        excluded.append({"full_name": cand["full_name"], "reason": "clone failed"})
                        continue
                    if not apis:
                        stats["no_crypto"] += 1
                        excluded.append({"full_name": cand["full_name"], "reason": "no first-party crypto usage"})
                        continue
                    cand["crypto_apis"] = apis
                commit = resolve_remote_commit(cand["git_url"], cand["branch"])
                if not commit:
                    stats["unresolved"] += 1
                    continue
                cand["commit"] = commit
                cand["stratum"] = {"stars": list(stars), "size_kb": list(size)}
                picked_for_lang.append(cand)
                taken += 1
                log(f"  + {cand['full_name']} ({language}, {cand['stars']}★, {cand['size_kb']} KB) {commit[:12]}")
            frame.append(
                {
                    "language": language,
                    "stars": list(stars),
                    "size_kb": list(size),
                    "population": population,
                    "enumerated": len(candidates),
                    "tried": tried,
                    "sampled": taken,
                    "quota": quota,
                }
            )
            if taken < quota:
                log(f"  ! {language} {label}: {taken} of {quota} (population {population}, {tried} tried)")
            time.sleep(pause)
        status(language, len(picked_for_lang), None)
        log(f"{language}: {len(picked_for_lang)} repos")
        chosen.extend(picked_for_lang)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "params": {
            "languages": params.languages,
            "per_cell": quota,
            "per_language": len(cells) * quota,
            "star_bins": [list(b) for b in params.star_bins],
            "size_bins_kb": [list(b) for b in size_bins],
            "max_repo_size_mb": cap_mb,
            "pushed_since": pushed_since,
            "search_date": time.strftime("%Y-%m-%d", time.gmtime()),
            "crypto_filter": params.crypto_filter,
            "exclude_patterns": params.exclude_patterns,
            "seed": params.seed,
        },
        "cells": frame,
        "stats": stats,
        "excluded": excluded,
        "repos": chosen,
    }


def prune_corpus(corpus: dict, exp_dir: Path, *, statuses: tuple[str, ...] = ("timeout",)) -> dict:
    """The corpus without the repos where some tool's run in `exp_dir` ended in one of `statuses` (the stability
    run's timeouts, refinement 11); the dropped repos are listed under `pruned` with the tools and statuses."""
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
