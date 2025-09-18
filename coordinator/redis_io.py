import datetime as dt
import json
import os
import uuid

import redis
import requests
import streamlit as st

from common.config import GITHUB_CACHE_TTL_SEC, GITHUB_TOKEN
from common.models import Benchmark, CbomJson, ComponentMatchJobInstruction, RepoInfo


# ----- Job instruction helper -----
def create_job_instruction(repo: dict, worker: str, job_id: str):
    """Construct a JobInstruction object using RepoInfo."""
    import logging

    from common.models import JobInstruction, RepoInfo

    logger = logging.getLogger("create_job_instruction")
    full = repo.get("full_name")
    repo_info = RepoInfo(
        full_name=full,
        git_url=repo.get("clone_url") or repo.get("git_url") or (f"https://github.com/{full}.git" if full else ""),
        branch=repo.get("default_branch") or repo.get("branch") or "main",
        size_kb=int(repo.get("size", 0) or 0),
        main_language=repo.get("language"),
        stars=repo.get("stargazers_count"),
    )
    job_instr = JobInstruction(job_id=job_id, tool=worker, repo_info=repo_info)
    logger.info(
        "New JobInstruction created: %r \t (%r on %r)",
        job_instr.job_id,
        job_instr.tool,
        job_instr.repo_info.full_name,
    )
    return job_instr


def _coerce_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return default


def _repo_dict_to_info(repo: dict) -> RepoInfo:
    full = (repo.get("full_name") or "").strip()
    git_url = (
        repo.get("clone_url")
        or repo.get("git_url")
        or (f"https://github.com/{full}.git" if full else "")
    )
    branch = repo.get("default_branch") or repo.get("branch") or "main"
    size_val = repo.get("size")
    if size_val in (None, ""):
        size_val = repo.get("size_kb")
    size_kb = _coerce_int(size_val, default=0)
    stars_raw = repo.get("stargazers_count")
    stars = _coerce_int(stars_raw, default=0) if stars_raw is not None else None
    return RepoInfo(
        full_name=full,
        git_url=git_url,
        branch=branch,
        size_kb=size_kb,
        main_language=repo.get("language"),
        stars=stars,
    )


def create_component_match_instruction(
    repo: dict,
    bench_id: str,
    cboms_by_worker: dict[str, str],
) -> ComponentMatchJobInstruction | None:
    """Construct a ComponentMatchJobInstruction from repo snapshot and worker CBOMs.

    Returns None if fewer than two CBOM payloads are available.
    """

    entries: list[CbomJson] = []
    for worker, payload in cboms_by_worker.items():
        if not payload:
            continue
        entries.append(CbomJson(tool=worker, json=payload))

    if len(entries) < 2:
        return None

    repo_info = _repo_dict_to_info(repo)
    job_id = str(uuid.uuid4())
    return ComponentMatchJobInstruction(
        job_id=job_id,
        benchmark_id=bench_id,
        repo_info=repo_info,
        CbomJsons=entries,
    )


def _collect_repo_cboms(
    r: redis.Redis,
    bench_id: str,
    repo_full_name: str,
    workers: list[str],
) -> dict[str, str]:
    job_idx = r.hgetall(f"bench:{bench_id}:job_index") or {}
    cboms: dict[str, str] = {}

    for worker in workers:
        job_id = job_idx.get(pair_key(repo_full_name, worker))
        if not job_id:
            continue
        job_meta = r.hgetall(f"bench:{bench_id}:job:{job_id}") or {}
        if job_meta.get("status") != "completed":
            continue
        raw = job_meta.get("result_json")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        cbom_text = payload.get("json")
        if not cbom_text:
            continue
        cboms[worker] = cbom_text

    return cboms


def prepare_component_match_instruction(
    r: redis.Redis,
    bench_id: str,
    repo: dict,
) -> ComponentMatchJobInstruction | None:
    workers = get_bench_workers(r, bench_id)
    full_name = (repo.get("full_name") or "").strip()
    if not full_name:
        return None
    cbom_map = _collect_repo_cboms(r, bench_id, full_name, workers)
    return create_component_match_instruction(repo, bench_id, cbom_map)


def enqueue_component_match_instruction(
    r: redis.Redis,
    instruction: ComponentMatchJobInstruction,
    queue_name: str,
) -> None:
    """Push a component match instruction onto the worker queue."""

    r.rpush(queue_name, instruction.to_json())


def build_component_match_jobs(
    r: redis.Redis,
    bench_id: str,
) -> tuple[list[ComponentMatchJobInstruction], dict[str, str]]:
    """Return component match instructions + repo mapping for a benchmark."""

    repos = get_bench_repos(r, bench_id)

    instructions: list[ComponentMatchJobInstruction] = []
    repo_map: dict[str, str] = {}

    for repo in repos:
        instruction = prepare_component_match_instruction(r, bench_id, repo)
        if not instruction:
            continue

        instructions.append(instruction)
        repo_map[instruction.job_id] = instruction.repo_info.full_name or (repo.get("full_name") or "")

    return instructions, repo_map


# ----- GitHub enrichment helpers (module-level, cache-aware) -----


def _cache_ttl_seconds() -> int:
    return int(GITHUB_CACHE_TTL_SEC)


def _github_headers(token: str | None) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_repo_meta(r: redis.Redis, fullname: str, token: str | None) -> dict | None:
    """Fetch /repos metadata with Redis caching (stars, default_branch, etc)."""
    try:
        key = f"gh:repo:{fullname}:meta"
        cached = r.get(key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass
        if not token:
            return None
        url = f"https://api.github.com/repos/{fullname}"
        resp = requests.get(url, headers=_github_headers(token), timeout=10)
        if resp.status_code == 200:
            data = resp.json() or {}
            try:
                r.set(key, json.dumps(data), ex=_cache_ttl_seconds())
            except Exception:
                pass
            return data
    except Exception:
        pass
    return None


def fetch_default_branch(r: redis.Redis, fullname: str, token: str | None) -> str | None:
    meta = fetch_repo_meta(r, fullname, token)
    if isinstance(meta, dict):
        return meta.get("default_branch")
    return None


def fetch_top_language(r: redis.Redis, fullname: str, token: str | None) -> str | None:
    """Return the dominant language by bytes using /languages with caching."""
    try:
        key = f"gh:repo:{fullname}:languages"
        cached = r.get(key)
        if cached:
            try:
                data = json.loads(cached) or {}
                if isinstance(data, dict) and data:
                    return max(data.items(), key=lambda kv: kv[1])[0]
            except Exception:
                pass
        if not token:
            return None
        url = f"https://api.github.com/repos/{fullname}/languages"
        resp = requests.get(url, headers=_github_headers(token), timeout=10)
        if resp.status_code == 200:
            data = resp.json() or {}
            try:
                r.set(key, json.dumps(data), ex=_cache_ttl_seconds())
            except Exception:
                pass
            if isinstance(data, dict) and data:
                return max(data.items(), key=lambda kv: kv[1])[0]
    except Exception:
        pass
    return None


# ----- Core helpers -----


def now_iso() -> str:
    """Return current UTC time in ISO-8601 format with Z suffix (no micros)."""
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@st.cache_resource(show_spinner=False)
def get_redis() -> redis.Redis:
    """Return a cached Redis client, halting Streamlit app on connection error."""
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    r = redis.Redis(host=host, port=port, decode_responses=True)
    try:
        r.ping()
    except redis.exceptions.RedisError as e:
        st.error(f"Cannot connect to Redis at {host}:{port}: {e}")
        st.stop()
    return r


# ----- Benchmark schema helpers -----


def list_benchmarks(r: redis.Redis) -> list[tuple[str, dict[str, str]]]:
    """Return list of (bench_id, meta) sorted newest-first by created/started timestamp.

    Falls back to a minimal timestamp when parsing fails so newer items float to top.
    """
    ids = list(r.smembers("benches"))
    items: list[tuple[str, dict[str, str], str]] = []
    for bid in ids:
        meta = r.hgetall(f"bench:{bid}") or {}
        created = meta.get("created_at") or meta.get("started_at") or ""
        items.append((bid, meta, created))

    def _parse_iso(s: str):
        if not s:
            return dt.datetime.min.replace(tzinfo=None)
        try:
            s2 = s.replace("Z", "+00:00")
            return dt.datetime.fromisoformat(s2)
        except Exception:
            return dt.datetime.min.replace(tzinfo=None)

    items.sort(key=lambda t: _parse_iso(t[2]), reverse=True)
    return [(bid, meta) for (bid, meta, _) in items]


def get_bench_meta(r: redis.Redis, bench_id: str) -> dict[str, str]:
    """Return benchmark metadata hash as a plain dict (may be empty)."""
    return r.hgetall(f"bench:{bench_id}") or {}


def get_bench_repos(r: redis.Redis, bench_id: str) -> list[dict]:
    """Return the list of repository dicts snapshot for a benchmark."""
    items = r.lrange(f"bench:{bench_id}:repos", 0, -1) or []
    return [json.loads(x) for x in items]


def get_bench_workers(r: redis.Redis, bench_id: str) -> list[str]:
    """Return the list of worker names associated with a benchmark."""
    meta = get_bench_meta(r, bench_id)
    try:
        return json.loads(meta.get("workers_json", "[]"))
    except Exception:
        return []


def pair_key(repo_full_name: str, worker: str) -> str:
    """Return a stable join key for repo/worker pairs used in Redis indices."""
    return f"{repo_full_name}|{worker}"


def create_benchmark(r: redis.Redis, name: str, params: dict, repos: list[dict], workers: list[str]) -> str:
    """Create a benchmark record with a snapshot of repos and selected workers."""
    bench_id = str(uuid.uuid4())

    bench = Benchmark(
        bench_id=bench_id,
        name=name,
        status="created",
        params=params,
        workers=workers,
        created_at=now_iso(),
        repo_count=len(repos),
        worker_count=len(workers),
        expected_jobs=len(repos) * len(workers),
    )

    pipe = r.pipeline()
    pipe.sadd("benches", bench_id)
    # Store legacy fields for existing readers and a full JSON for type-safe consumers
    pipe.hset(
        f"bench:{bench_id}",
        mapping={
            "name": bench.name,
            "status": bench.status,
            "params_json": json.dumps(bench.params or {}),
            "workers_json": json.dumps(bench.workers or []),
            "created_at": bench.created_at or "",
            "repo_count": str(bench.repo_count or 0),
            "worker_count": str(bench.worker_count or 0),
            "expected_jobs": str(bench.expected_jobs or 0),
            "meta_json": bench.to_json(),
        },
    )
    for repo in repos:
        pipe.rpush(f"bench:{bench_id}:repos", json.dumps(repo))
    pipe.execute()
    return bench_id


def start_benchmark(r: redis.Redis, bench_id: str) -> int:
    """Queue jobs to workers for the benchmark; return number of issued jobs."""
    meta = get_bench_meta(r, bench_id)
    status = meta.get("status", "created")
    if status == "running":
        return 0

    repos = get_bench_repos(r, bench_id)
    workers = get_bench_workers(r, bench_id)
    repos_key = f"bench:{bench_id}:repos"

    issued = 0
    pipe = r.pipeline()
    # Optional token for higher rate limits when resolving default branches
    gh_token = GITHUB_TOKEN

    for idx, repo in enumerate(repos):
        repo = dict(repo)
        fullname = repo.get("full_name")
        branch = repo.get("default_branch") or repo.get("branch")
        if not branch:
            resolved = fetch_default_branch(r, fullname, gh_token)
            branch = resolved or "main"
            if resolved:
                repo["default_branch"] = resolved
        # Ensure branch is set for job instruction
        repo["branch"] = branch
        # Ensure a language is set to aid language-specific workers
        if not repo.get("language"):
            top = fetch_top_language(r, fullname, gh_token)
            if top:
                repo["language"] = top
        # Best-effort fill stars and size if missing (helps UI summaries)
        if repo.get("stargazers_count") in (None, "") or repo.get("size") in (None, ""):
            meta = fetch_repo_meta(r, fullname, gh_token)
            if isinstance(meta, dict):
                if repo.get("stargazers_count") in (None, "") and "stargazers_count" in meta:
                    repo["stargazers_count"] = meta.get("stargazers_count")
                # GitHub "size" is in KB
                if repo.get("size") in (None, "") and "size" in meta:
                    repo["size"] = meta.get("size")

        pipe.lset(repos_key, idx, json.dumps(repo))

        for worker in workers:
            job_id = str(uuid.uuid4())
            instr_obj = create_job_instruction(repo, worker, job_id)
            pipe.rpush(f"jobs:{worker}", instr_obj.to_json())
            pipe.rpush(f"bench:{bench_id}:jobs", job_id)
            pipe.hset(
                f"bench:{bench_id}:job_index",
                mapping={pair_key(fullname, worker): job_id},
            )
            pipe.hset(
                f"bench:{bench_id}:job:{job_id}",
                mapping={
                    "worker": worker,
                    "repo_full_name": fullname,
                    "status": "fired",
                    "sent_at": now_iso(),
                },
            )
            issued += 1
    pipe.hset(
        f"bench:{bench_id}",
        mapping={
            "status": "running",
            "started_at": now_iso(),
            "issued_jobs": str(issued),
        },
    )
    pipe.execute()
    return issued


def collect_results_once(r: redis.Redis, bench_id: str) -> tuple[int, int]:
    """Ingest available worker results once; return (done_count, total_jobs)."""
    jobs = r.lrange(f"bench:{bench_id}:jobs", 0, -1) or []
    workers = get_bench_workers(r, bench_id)

    # Build set of pending job_ids
    pending = set()
    for job_id in jobs:
        stt = r.hget(f"bench:{bench_id}:job:{job_id}", "status") or ""
        if stt not in ("completed", "failed", "cancelled"):
            pending.add(job_id)

    ingested = 0
    for worker in workers:
        raw_list = r.lrange(f"results:{worker}", 0, -1) or []
        for raw in raw_list:
            try:
                job_dict = json.loads(raw)
            except Exception:
                continue
            job_id = job_dict.get("job_id")
            if not job_id or job_id not in pending:
                continue
            # Remove from worker results and store under benchmark
            r.lrem(f"results:{worker}", 1, raw)
            status = job_dict.get("status", "error")
            final = "completed" if status == "ok" else "failed"
            r.hset(
                f"bench:{bench_id}:job:{job_id}",
                mapping={
                    "status": final,
                    "received_at": now_iso(),
                    "result_json": json.dumps(job_dict, ensure_ascii=False),
                },
            )
            ingested += 1

    # Return counts: completed/failed and total
    done = 0
    for job_id in jobs:
        stt = r.hget(f"bench:{bench_id}:job:{job_id}", "status") or ""
        if stt in ("completed", "failed", "cancelled"):
            done += 1
    total = len(jobs)
    return done, total


def cancel_benchmark(r: redis.Redis, bench_id: str) -> int:
    """Cancel a running benchmark by removing queued jobs and marking them cancelled.

    Returns the number of jobs marked as cancelled.
    """
    workers = get_bench_workers(r, bench_id)
    jobs = r.lrange(f"bench:{bench_id}:jobs", 0, -1) or []

    # Determine pending jobs to cancel
    pending = []
    for job_id in jobs:
        stt = r.hget(f"bench:{bench_id}:job:{job_id}", "status") or ""
        if stt not in ("completed", "failed", "cancelled"):
            pending.append(job_id)

    # Remove queued messages for pending jobs from worker queues
    pending_set = set(pending)
    for w in workers:
        queue = f"jobs:{w}"
        raw_list = r.lrange(queue, 0, -1) or []
        for raw in raw_list:
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            jid = obj.get("job_id")
            if jid and jid in pending_set:
                r.lrem(queue, 1, raw)

    # Mark pending jobs as cancelled
    pipe = r.pipeline()
    for job_id in pending:
        pipe.hset(
            f"bench:{bench_id}:job:{job_id}",
            mapping={"status": "cancelled", "cancelled_at": now_iso()},
        )
    pipe.hset(f"bench:{bench_id}", mapping={"status": "cancelled", "cancelled_at": now_iso()})
    pipe.execute()
    return len(pending)


def reset_benchmark_jobs(r: redis.Redis, bench_id: str) -> int:
    """Remove prior job records for a benchmark so it can be re-executed cleanly.

    Returns the number of removed job records.
    """
    jobs = r.lrange(f"bench:{bench_id}:jobs", 0, -1) or []
    # Delete per-job hashes
    pipe = r.pipeline()
    for job_id in jobs:
        pipe.delete(f"bench:{bench_id}:job:{job_id}")
    # Clear job list and index
    pipe.delete(f"bench:{bench_id}:jobs")
    pipe.delete(f"bench:{bench_id}:job_index")
    # Reset meta status back to created (keep name/repos/workers)
    pipe.hset(f"bench:{bench_id}", mapping={"status": "created", "reset_at": now_iso()})
    pipe.execute()
    return len(jobs)


def reexecute_benchmark(r: redis.Redis, bench_id: str) -> int:
    """Reset previous jobs and start the benchmark again; return issued count."""
    reset_benchmark_jobs(r, bench_id)
    return start_benchmark(r, bench_id)


def retry_non_completed_benchmark(r: redis.Redis, bench_id: str) -> int:
    """Queue jobs only for repo/worker pairs that did not complete.

    Leaves completed job records intact and updates the job index to point to
    the newly issued jobs. Returns the number of jobs issued.
    """
    repos = get_bench_repos(r, bench_id)
    workers = get_bench_workers(r, bench_id)
    repos_key = f"bench:{bench_id}:repos"
    gh_token = GITHUB_TOKEN

    issued = 0
    pipe = r.pipeline()
    job_index_key = f"bench:{bench_id}:job_index"
    for idx, repo in enumerate(repos):
        repo = dict(repo)
        fullname = repo.get("full_name")
        branch = repo.get("default_branch") or repo.get("branch")
        if not branch:
            resolved = fetch_default_branch(r, fullname, gh_token)
            branch = resolved or "main"
            if resolved:
                repo["default_branch"] = resolved
        repo["branch"] = branch
        if not repo.get("language"):
            top = fetch_top_language(r, fullname, gh_token)
            if top:
                repo["language"] = top
        if repo.get("stargazers_count") in (None, "") or repo.get("size") in (None, ""):
            meta = fetch_repo_meta(r, fullname, gh_token)
            if isinstance(meta, dict):
                if repo.get("stargazers_count") in (None, "") and "stargazers_count" in meta:
                    repo["stargazers_count"] = meta.get("stargazers_count")
                if repo.get("size") in (None, "") and "size" in meta:
                    repo["size"] = meta.get("size")

        pipe.lset(repos_key, idx, json.dumps(repo))

        for worker in workers:
            pk = pair_key(fullname, worker)
            prior_id = r.hget(job_index_key, pk)
            completed = False
            if prior_id:
                stt = r.hget(f"bench:{bench_id}:job:{prior_id}", "status") or ""
                completed = stt == "completed"
            if completed:
                continue

            job_id = str(uuid.uuid4())
            instr_obj = create_job_instruction(repo, worker, job_id)
            # Queue the job
            pipe.rpush(f"jobs:{worker}", instr_obj.to_json())
            # Append new job id and update index mapping
            pipe.rpush(f"bench:{bench_id}:jobs", job_id)
            pipe.hset(job_index_key, mapping={pk: job_id})
            # Create job hash
            pipe.hset(
                f"bench:{bench_id}:job:{job_id}",
                mapping={
                    "worker": worker,
                    "repo_full_name": fullname,
                    "status": "fired",
                    "sent_at": now_iso(),
                },
            )
            issued += 1

    if issued:
        pipe.hset(
            f"bench:{bench_id}",
            mapping={
                "status": "running",
                "started_at": now_iso(),
                "issued_jobs": str(issued),
            },
        )
    pipe.execute()
    return issued


def delete_benchmark(r: redis.Redis, bench_id: str) -> int:
    """Delete a benchmark and all associated Redis keys.

    Removes:
    - `bench:{id}` meta hash
    - `bench:{id}:repos` list
    - `bench:{id}:jobs` list
    - `bench:{id}:job_index` hash
    - `bench:{id}:job:{job_id}` hashes for all jobs
    - membership in `benches` set

    Returns count of deleted per-job hashes (for reference).
    """
    # Gather job ids
    jobs = r.lrange(f"bench:{bench_id}:jobs", 0, -1) or []
    deleted_jobs = 0
    pipe = r.pipeline()
    # Delete per-job hashes
    for job_id in jobs:
        pipe.delete(f"bench:{bench_id}:job:{job_id}")
        deleted_jobs += 1
    # Delete containers
    pipe.delete(f"bench:{bench_id}:jobs")
    pipe.delete(f"bench:{bench_id}:job_index")
    pipe.delete(f"bench:{bench_id}:repos")
    pipe.delete(f"bench:{bench_id}")
    pipe.srem("benches", bench_id)
    pipe.execute()
    return deleted_jobs
