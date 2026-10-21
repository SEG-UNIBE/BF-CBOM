"""Job bookkeeping shared by workers, the coordinator and the batch runner.

Routing: the coordinator and the runner register every job id in the hash `jobs:routing` (job id ->
inspection id, or `exp:<name>` for batch experiments), in the same transaction that queues it. Workers use it to
mark a job as running the moment they pop it, so the UI can tell queued from running and show how long a job has
been going, and skip a job it no longer lists: cancelled or deleted while it waited, nobody collects its result.

Language scope: `common/tools.yaml` lists the languages each tool supports. `tool_supports` decides
whether a (tool, repository language) pair is worth running; unsupported pairs are recorded with status
`unsupported` instead of producing an empty or failed run.
"""

from __future__ import annotations

import datetime as dt
import logging

import redis

from common.tools_registry import load_tools

JOB_ROUTING_KEY = "jobs:routing"
EXPERIMENT_ROUTE_PREFIX = "exp:"
RUNNING_STATUS = "running"
QUEUED_STATUS = "fired"  # historical value written by the coordinator when a job is pushed to a queue

logger = logging.getLogger(__name__)

# GitHub / user spellings -> the keys used in tools.yaml `languages`
_LANGUAGE_ALIASES = {
    "c++": "cpp",
    "c#": "csharp",
    "objective-c": "objc",
    "js": "javascript",
    "ts": "typescript",
    "golang": "go",
    "py": "python",
}


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_language(language: str | None) -> str:
    """Lower-case language key as used in tools.yaml ('C++' -> 'cpp', 'JavaScript' -> 'javascript')."""
    key = (language or "").strip().lower()
    return _LANGUAGE_ALIASES.get(key, key)


def tool_supports(tool: str, language: str | None, registry: dict[str, dict] | None = None) -> bool:
    """False only when the registry lists languages for `tool` and the repo language is known and not among them."""
    entry = (registry if registry is not None else load_tools()).get(tool) or {}
    languages = entry.get("languages")
    lang = normalize_language(language)
    if not languages or not lang:
        return True
    return lang in {normalize_language(str(x)) for x in languages}


def job_is_routed(r: redis.Redis, job_id: str) -> bool:
    """False only when `jobs:routing` has no entry for `job_id` (cancelled or deleted while queued); True when Redis
    cannot be asked, so a connection blip never drops work."""
    try:
        return bool(r.hexists(JOB_ROUTING_KEY, job_id))
    except redis.exceptions.RedisError as e:
        logger.warning("could not check the routing of job %s: %s", job_id, e)
        return True


def mark_job_running(r: redis.Redis, job_id: str, worker: str) -> bool:
    """Record that `worker` has started `job_id`; returns True when something was updated.

    Inspection jobs: `insp:<id>:job:<job>` goes from `fired` to `running` with `started_at`.
    Experiment jobs: `exp:<name>:running` hash gets job_id -> started_at (the runner removes it on result).
    Never raises: bookkeeping must not break a worker.
    """
    try:
        target = r.hget(JOB_ROUTING_KEY, job_id)
        if not target:
            return False
        if target.startswith(EXPERIMENT_ROUTE_PREFIX):
            r.hset(f"{target}:running", job_id, now_iso())
            return True
        key = f"insp:{target}:job:{job_id}"
        if (r.hget(key, "status") or "") != QUEUED_STATUS:
            return False
        r.hset(key, mapping={"status": RUNNING_STATUS, "started_at": now_iso(), "worker": worker})
        return True
    except redis.exceptions.RedisError as e:
        logger.warning("could not mark job %s running: %s", job_id, e)
        return False
