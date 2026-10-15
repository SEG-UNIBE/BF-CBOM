import datetime as dt
import json
import os
import uuid

import redis
import requests
import streamlit as st

from common.cbom_analysis import (
    create_component_match_instruction,
)
from common.config import GITHUB_CACHE_TTL_SEC, GITHUB_TOKEN
from common.models import ComponentMatchJobInstruction, Inspection
from common.utils import repo_dict_to_info, resolve_remote_commit
from coordinator.logger_config import logger


# ----- Job instruction helper -----
def create_job_instruction(repo: dict, worker: str, job_id: str):
    """Construct a JobInstruction object."""

    from common.models import JobInstruction

    repo_info = repo_dict_to_info(repo)
    job_instr = JobInstruction(job_id=job_id, tool=worker, repo_info=repo_info)
    logger.info(
        "New JobInstruction created: %r \t (%r on %r)",
        job_instr.job_id,
        job_instr.tool,
        job_instr.repo_info.full_name,
    )
    return job_instr


def collect_repo_cboms(
    r: redis.Redis,
    insp_id: str,
    repo_full_name: str,
    workers: list[str],
) -> dict[str, str]:
    job_idx = r.hgetall(f"insp:{insp_id}:job_index") or {}
    cboms: dict[str, str] = {}

    for worker in workers:
        job_id = job_idx.get(pair_key(repo_full_name, worker))
        if not job_id:
            continue
        job_meta = r.hgetall(f"insp:{insp_id}:job:{job_id}") or {}
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
    insp_id: str,
    repo: dict,
    exclude_types: bool = True,
) -> ComponentMatchJobInstruction | None:
    workers = get_insp_workers(r, insp_id)
    full_name = (repo.get("full_name") or "").strip()
    if not full_name:
        return None
    cbom_map = collect_repo_cboms(r, insp_id, full_name, workers)
    return create_component_match_instruction(
        repo,
        insp_id,
        cbom_map,
        exclude_types=exclude_types,
    )


def enqueue_component_match_instruction(
    r: redis.Redis,
    instruction: ComponentMatchJobInstruction,
    queue_name: str,
) -> None:
    """Push a component match instruction onto the worker queue."""
    logger.info(f"enqueue, job id: {instruction.job_id} to {queue_name}")
    r.rpush(queue_name, instruction.to_json())


def build_component_match_jobs(
    r: redis.Redis,
    insp_id: str,
    exclude_types: bool = True,
) -> tuple[list[ComponentMatchJobInstruction], dict[str, str]]:
    """Return component match instructions + repo mapping for an inspection."""

    repos = get_insp_repos(r, insp_id)

    instructions: list[ComponentMatchJobInstruction] = []
    repo_map: dict[str, str] = {}

    for repo in repos:
        instruction = prepare_component_match_instruction(
            r,
            insp_id,
            repo,
            exclude_types=exclude_types,
        )
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
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


# ----- Worker liveness -----


def get_worker_liveness(r: redis.Redis, workers: list[str]) -> dict[str, dict | None]:
    """Return worker -> heartbeat payload ({state, job_id, ts}) or None when the worker is down.

    Workers refresh `worker:<name>:alive` (see common.worker.publish_heartbeat); the key
    expires on its own, so a missing key means no live process is consuming that queue.
    """
    names = list(workers or [])
    if not names:
        return {}
    pipe = r.pipeline()
    for name in names:
        pipe.get(f"worker:{name}:alive")
    out: dict[str, dict | None] = {}
    for name, raw in zip(names, pipe.execute(), strict=True):
        payload = None
        if raw:
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"state": "unknown"}
        out[name] = payload
    return out


def dead_workers(r: redis.Redis, workers: list[str]) -> list[str]:
    """Names from `workers` that currently have no live process."""
    return [name for name, payload in get_worker_liveness(r, workers).items() if payload is None]


# ----- Inspection schema helpers -----


def list_inspections(r: redis.Redis) -> list[tuple[str, dict[str, str]]]:
    """Return list of (insp_id, meta) sorted newest-first by created/started timestamp.

    Falls back to a minimal timestamp when parsing fails so newer items float to top.
    """
    ids = list(r.smembers("inspects"))
    items: list[tuple[str, dict[str, str], str]] = []
    for bid in ids:
        meta = r.hgetall(f"insp:{bid}") or {}
        created = meta.get("created_at") or meta.get("started_at") or ""
        items.append((bid, meta, created))

    def _parse_iso(s: str) -> dt.datetime:
        """Parse ISO timestamp, always returning offset-aware datetime for consistent comparison."""
        if not s:
            return dt.datetime.min.replace(tzinfo=dt.UTC)
        try:
            parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            # Ensure offset-aware for consistent comparison
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.UTC)
            return parsed
        except Exception:
            return dt.datetime.min.replace(tzinfo=dt.UTC)

    items.sort(key=lambda t: _parse_iso(t[2]), reverse=True)
    return [(bid, meta) for (bid, meta, _) in items]


def get_insp_meta(r: redis.Redis, insp_id: str) -> dict[str, str]:
    """Return inspection metadata hash as a plain dict (may be empty)."""
    return r.hgetall(f"insp:{insp_id}") or {}


def get_insp_repos(r: redis.Redis, insp_id: str) -> list[dict]:
    """Return the list of repository dicts snapshot for an inspection."""
    items = r.lrange(f"insp:{insp_id}:repos", 0, -1) or []
    return [json.loads(x) for x in items]


def get_insp_workers(r: redis.Redis, insp_id: str) -> list[str]:
    """Return the list of worker names associated with an inspection."""
    meta = get_insp_meta(r, insp_id)
    try:
        return json.loads(meta.get("workers_json", "[]"))
    except Exception:
        return []


def pair_key(repo_full_name: str, worker: str) -> str:
    """Return a stable join key for repo/worker pairs used in Redis indices."""
    return f"{repo_full_name}|{worker}"


# Global hash job_id -> insp_id, so any poller can route any worker result.
JOB_ROUTING_KEY = "jobs:routing"
# Routing targets with this prefix are batch experiments (misc/cli experiment), not inspections:
# their results are forwarded untouched to `results:exp:<name>` for the runner to persist.
EXPERIMENT_ROUTE_PREFIX = "exp:"
# Statuses that count a job as done for progress purposes.
TERMINAL_JOB_STATUSES = ("completed", "failed", "cancelled")
# Flat job-hash fields that are enough to render status/duration/size without the CBOM body.
JOB_SUMMARY_FIELDS = ("status", "worker_status", "duration_sec", "size_bytes")


def job_fields_from_result(job_dict: dict) -> dict[str, str]:
    """Flatten the cheap-to-read parts of a worker JobResult into hash fields.

    Readers use these instead of re-parsing `result_json` (which embeds the whole CBOM).
    """
    worker_status = str(job_dict.get("status") or "error")
    fields = {
        "worker_status": worker_status,  # ok | timeout | error
        "received_at": now_iso(),
        "result_json": json.dumps(job_dict, ensure_ascii=False),
    }
    dur = job_dict.get("duration_sec")
    if isinstance(dur, int | float):
        fields["duration_sec"] = f"{float(dur):.3f}"
    size = job_dict.get("size_bytes")
    if size is None:
        size = len((job_dict.get("json") or "").encode("utf-8"))
    fields["size_bytes"] = str(int(size))
    return fields


def job_status_key(meta: dict) -> str:
    """Map a job hash to a canonical UI status: completed | timeout | failed | cancelled | pending."""
    stt = (meta.get("status") or "").lower()
    if stt == "completed":
        return "completed"
    if stt == "cancelled":
        return "cancelled"
    if stt == "failed":
        ws = (meta.get("worker_status") or "").lower()
        if not ws and meta.get("result_json"):
            # Legacy hashes written before worker_status existed
            try:
                ws = str(json.loads(meta["result_json"]).get("status") or "").lower()
            except Exception:
                ws = ""
        return "timeout" if ws == "timeout" else "failed"
    return "pending"


def enrich_repo(r: redis.Redis, repo: dict, gh_token: str | None) -> dict:
    """Return a copy of a repo snapshot with branch, commit, language, stars and size filled in.

    The branch is the explicit one if given, else the GitHub default branch. The commit is
    resolved once (git ls-remote) and then kept, so re-runs and retries scan the same snapshot.
    GitHub metadata is best-effort and cached; it is never required for a job to run.
    """
    repo = dict(repo)
    fullname = repo.get("full_name")
    branch = repo.get("branch") or repo.get("default_branch")
    if not branch:
        resolved = fetch_default_branch(r, fullname, gh_token)
        branch = resolved or "main"
        if resolved:
            repo["default_branch"] = resolved
    repo["branch"] = branch
    git_url = repo.get("clone_url") or repo.get("git_url") or (f"https://github.com/{fullname}.git" if fullname else "")
    if not repo.get("commit") and git_url:
        sha = resolve_remote_commit(git_url, branch)
        if sha:
            repo["commit"] = sha
        else:
            logger.warning("Could not resolve commit for %s@%s; jobs will scan the branch head", fullname, branch)
    if not repo.get("language"):
        top = fetch_top_language(r, fullname, gh_token)
        if top:
            repo["language"] = top
    if repo.get("stargazers_count") in (None, "") or repo.get("size") in (None, ""):
        meta = fetch_repo_meta(r, fullname, gh_token)
        if isinstance(meta, dict):
            if repo.get("stargazers_count") in (None, "") and "stargazers_count" in meta:
                repo["stargazers_count"] = meta.get("stargazers_count")
            if repo.get("size") in (None, "") and "size" in meta:  # GitHub "size" is in KB
                repo["size"] = meta.get("size")
    return repo


def create_inspection(r: redis.Redis, name: str, params: dict, repos: list[dict], workers: list[str]) -> str:
    """Create an inspection record with a snapshot of repos and selected workers."""
    insp_id = str(uuid.uuid4())

    insp = Inspection(
        insp_id=insp_id,
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
    pipe.sadd("inspects", insp_id)
    # Store legacy fields for existing readers and a full JSON for type-safe consumers
    pipe.hset(
        f"insp:{insp_id}",
        mapping={
            "name": insp.name,
            "status": insp.status,
            "params_json": json.dumps(insp.params or {}),
            "workers_json": json.dumps(insp.workers or []),
            "created_at": insp.created_at or "",
            "repo_count": str(insp.repo_count or 0),
            "worker_count": str(insp.worker_count or 0),
            "expected_jobs": str(insp.expected_jobs or 0),
            "meta_json": insp.to_json(),
        },
    )
    for repo in repos:
        pipe.rpush(f"insp:{insp_id}:repos", json.dumps(repo))
    pipe.execute()
    return insp_id


def start_inspection(r: redis.Redis, insp_id: str) -> int:
    """Queue jobs to workers for the inspection; return number of issued jobs."""
    meta = get_insp_meta(r, insp_id)
    status = meta.get("status", "created")
    if status == "running":
        return 0

    repos = get_insp_repos(r, insp_id)
    workers = get_insp_workers(r, insp_id)
    repos_key = f"insp:{insp_id}:repos"

    issued = 0
    pipe = r.pipeline()
    # Optional token for higher rate limits when resolving default branches
    gh_token = GITHUB_TOKEN

    for idx, repo in enumerate(repos):
        repo = enrich_repo(r, repo, gh_token)
        fullname = repo.get("full_name")
        pipe.lset(repos_key, idx, json.dumps(repo))

        for worker in workers:
            job_id = str(uuid.uuid4())
            instr_obj = create_job_instruction(repo, worker, job_id)
            pipe.rpush(f"jobs:{worker}", instr_obj.to_json())
            pipe.rpush(f"insp:{insp_id}:jobs", job_id)
            pipe.hset(
                f"insp:{insp_id}:job_index",
                mapping={pair_key(fullname, worker): job_id},
            )
            pipe.hset(JOB_ROUTING_KEY, job_id, insp_id)
            pipe.hset(
                f"insp:{insp_id}:job:{job_id}",
                mapping={
                    "worker": worker,
                    "repo_full_name": fullname,
                    "status": "fired",
                    "sent_at": now_iso(),
                },
            )
            issued += 1
    pipe.hset(
        f"insp:{insp_id}",
        mapping={
            "status": "running",
            "started_at": now_iso(),
            "issued_jobs": str(issued),
        },
    )
    pipe.execute()
    return issued


def _route_result(r: redis.Redis, polling_insp_id: str, job_id: str) -> str | None:
    """Return the inspection a job belongs to, or None if unknown."""
    insp = r.hget(JOB_ROUTING_KEY, job_id)
    if insp:
        return insp
    # Jobs issued before the routing index existed: accept if the poller owns it.
    if r.exists(f"insp:{polling_insp_id}:job:{job_id}"):
        return polling_insp_id
    return None


def ingest_worker_results(r: redis.Redis, polling_insp_id: str, workers: list[str]) -> int:
    """Drain `results:<worker>` lists and file each result under its inspection.

    Every popped entry is consumed exactly once: routed to its inspection, stored as a
    late result if that job was already cancelled, or dropped (and logged) if it belongs
    to no known job. Nothing is left behind to be rescanned on the next poll.
    """
    ingested = 0
    for worker in workers:
        results_key = f"results:{worker}"
        while True:
            raw = r.lpop(results_key)
            if raw is None:
                break
            try:
                job_dict = json.loads(raw)
            except Exception:
                logger.warning("Dropping unparsable result from %s: %.200s", results_key, raw)
                continue
            job_id = job_dict.get("job_id")
            insp_id = _route_result(r, polling_insp_id, job_id) if job_id else None
            if not insp_id:
                logger.warning("Dropping result for unknown job %s from %s", job_id, results_key)
                continue
            if insp_id.startswith(EXPERIMENT_ROUTE_PREFIX):
                pipe = r.pipeline()
                pipe.rpush(f"results:{insp_id}", raw)
                pipe.hdel(JOB_ROUTING_KEY, job_id)
                pipe.execute()
                ingested += 1
                continue
            job_key = f"insp:{insp_id}:job:{job_id}"
            current = r.hget(job_key, "status") or ""
            fields = job_fields_from_result(job_dict)
            if current == "cancelled":
                # Worker finished after the user cancelled: keep the data, keep the status.
                fields["late_received_at"] = fields.pop("received_at")
            elif current in ("completed", "failed"):
                logger.warning("Duplicate result for job %s (status=%s); keeping the first", job_id, current)
                continue
            else:
                fields["status"] = "completed" if fields["worker_status"] == "ok" else "failed"
            pipe = r.pipeline()
            pipe.hset(job_key, mapping=fields)
            pipe.hdel(JOB_ROUTING_KEY, job_id)
            pipe.execute()
            ingested += 1
    return ingested


def count_done_jobs(r: redis.Redis, insp_id: str) -> tuple[int, int]:
    """Return (done, total) for an inspection using one pipelined round-trip."""
    jobs = r.lrange(f"insp:{insp_id}:jobs", 0, -1) or []
    if not jobs:
        return 0, 0
    pipe = r.pipeline()
    for job_id in jobs:
        pipe.hget(f"insp:{insp_id}:job:{job_id}", "status")
    statuses = pipe.execute()
    done = sum(1 for stt in statuses if (stt or "") in TERMINAL_JOB_STATUSES)
    return done, len(jobs)


def collect_results_once(r: redis.Redis, insp_id: str) -> tuple[int, int]:
    """Ingest available worker results once; return (done_count, total_jobs)."""
    workers = get_insp_workers(r, insp_id)
    ingest_worker_results(r, insp_id, workers)
    return count_done_jobs(r, insp_id)


def cancel_inspection(r: redis.Redis, insp_id: str) -> int:
    """Cancel a running inspection by removing queued jobs and marking them cancelled.

    Returns the number of jobs marked as cancelled.
    """
    workers = get_insp_workers(r, insp_id)
    jobs = r.lrange(f"insp:{insp_id}:jobs", 0, -1) or []

    # Determine pending jobs to cancel
    pending = []
    for job_id in jobs:
        stt = r.hget(f"insp:{insp_id}:job:{job_id}", "status") or ""
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
            f"insp:{insp_id}:job:{job_id}",
            mapping={"status": "cancelled", "cancelled_at": now_iso()},
        )
    pipe.hset(f"insp:{insp_id}", mapping={"status": "cancelled", "cancelled_at": now_iso()})
    pipe.execute()
    return len(pending)


def reset_inspection_jobs(r: redis.Redis, insp_id: str) -> int:
    """Remove prior job records for an inspection so it can be re-executed cleanly.

    Returns the number of removed job records.
    """
    jobs = r.lrange(f"insp:{insp_id}:jobs", 0, -1) or []
    # Delete per-job hashes
    pipe = r.pipeline()
    for job_id in jobs:
        pipe.delete(f"insp:{insp_id}:job:{job_id}")
        pipe.hdel(JOB_ROUTING_KEY, job_id)
    # Clear job list and index
    pipe.delete(f"insp:{insp_id}:jobs")
    pipe.delete(f"insp:{insp_id}:job_index")
    # Reset meta status back to created (keep name/repos/workers)
    pipe.hset(f"insp:{insp_id}", mapping={"status": "created", "reset_at": now_iso()})
    pipe.execute()
    return len(jobs)


def reexecute_inspection(r: redis.Redis, insp_id: str) -> int:
    """Reset previous jobs and start the inspection again; return issued count."""
    reset_inspection_jobs(r, insp_id)
    return start_inspection(r, insp_id)


def retry_non_completed_inspection(r: redis.Redis, insp_id: str) -> int:
    """Queue jobs only for repo/worker pairs that did not complete.

    Leaves completed job records intact and updates the job index to point to
    the newly issued jobs. Returns the number of jobs issued.
    """
    repos = get_insp_repos(r, insp_id)
    workers = get_insp_workers(r, insp_id)
    repos_key = f"insp:{insp_id}:repos"
    gh_token = GITHUB_TOKEN

    issued = 0
    pipe = r.pipeline()
    job_index_key = f"insp:{insp_id}:job_index"
    for idx, repo in enumerate(repos):
        repo = enrich_repo(r, repo, gh_token)
        fullname = repo.get("full_name")
        pipe.lset(repos_key, idx, json.dumps(repo))

        for worker in workers:
            pk = pair_key(fullname, worker)
            prior_id = r.hget(job_index_key, pk)
            completed = False
            if prior_id:
                stt = r.hget(f"insp:{insp_id}:job:{prior_id}", "status") or ""
                completed = stt == "completed"
            if completed:
                continue

            job_id = str(uuid.uuid4())
            instr_obj = create_job_instruction(repo, worker, job_id)
            # Queue the job
            pipe.rpush(f"jobs:{worker}", instr_obj.to_json())
            # Append new job id and update index mapping
            pipe.rpush(f"insp:{insp_id}:jobs", job_id)
            pipe.hset(job_index_key, mapping={pk: job_id})
            pipe.hset(JOB_ROUTING_KEY, job_id, insp_id)
            # Create job hash
            pipe.hset(
                f"insp:{insp_id}:job:{job_id}",
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
            f"insp:{insp_id}",
            mapping={
                "status": "running",
                "started_at": now_iso(),
                "issued_jobs": str(issued),
            },
        )
    pipe.execute()
    return issued


def delete_inspection(r: redis.Redis, insp_id: str) -> int:
    """Delete an inspection and all associated Redis keys.

    Removes:
    - `insp:{id}` meta hash
    - `insp:{id}:repos` list
    - `insp:{id}:jobs` list
    - `insp:{id}:job_index` hash
    - `insp:{id}:job:{job_id}` hashes for all jobs
    - membership in `inspects` set

    Returns count of deleted per-job hashes (for reference).
    """
    # Gather job ids
    jobs = r.lrange(f"insp:{insp_id}:jobs", 0, -1) or []
    deleted_jobs = 0
    pipe = r.pipeline()
    # Delete per-job hashes
    for job_id in jobs:
        pipe.delete(f"insp:{insp_id}:job:{job_id}")
        pipe.hdel(JOB_ROUTING_KEY, job_id)
        deleted_jobs += 1
    # Delete containers
    pipe.delete(f"insp:{insp_id}:jobs")
    pipe.delete(f"insp:{insp_id}:job_index")
    pipe.delete(f"insp:{insp_id}:repos")
    pipe.delete(f"insp:{insp_id}:match_results")
    pipe.delete(f"insp:{insp_id}")
    pipe.srem("inspects", insp_id)
    pipe.execute()

    _purge_component_match_data(r, insp_id)
    return deleted_jobs


# Matcher workers that consume ComponentMatchJobInstruction payloads.
COMPONENT_MATCH_WORKERS = ("pyqun", "treesimilartiy")


def _purge_component_match_data(r: redis.Redis, insp_id: str) -> None:
    """Remove queued/finished component-match payloads that belong to an inspection."""

    def _filter_list(key: str):
        items = r.lrange(key, 0, -1) or []
        if not items:
            return
        filtered: list[str] = []
        for item in items:
            try:
                payload = json.loads(item)
            except Exception:
                filtered.append(item)
                continue
            if payload.get("inspection_id") != insp_id:
                filtered.append(item)
        if len(filtered) != len(items):
            pipe = r.pipeline()
            pipe.delete(key)
            if filtered:
                pipe.rpush(key, *filtered)
            pipe.execute()

    for worker in COMPONENT_MATCH_WORKERS:
        _filter_list(f"jobs:{worker}")
        _filter_list(f"results:{worker}")
