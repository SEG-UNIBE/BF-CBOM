"""Memory and CPU measurement for workers and the runner (cgroup v2 and /proc/meminfo; best effort, None when
unavailable).

- A worker's container is its own cgroup, so `/sys/fs/cgroup` inside it describes that container: the working set
  (`memory.current` minus `inactive_file`, what `docker stats` shows), the limit (`memory.max`, the compose
  `deploy.resources.limits.memory`), how often usage reached the limit and the OOM kills (both `memory.events`).
  `JobSampler` samples them while a job runs, so every JobResult carries the peak of the whole container (the tool, its
  servers such as CBOMkit's Postgres, and our worker), sampled once per second: spikes shorter than that can be
  missed. A job with no limit hits ran without the limit ever constraining it; at the limit the kernel reclaims (file
  cache first), and on a host without swap memory it cannot reclaim ends in an OOM kill. The container's CPU time
  (`cpu.stat` `usage_usec`) during the job, divided by the job's wall time, is the mean number of cores it kept busy;
  no CPU limits are set, so a tool takes what the host gives it.
- `/proc/meminfo` is not namespaced, so inside any container it describes the Docker VM (or Linux host): total RAM
  and what is in use. The runner samples it for the peak of the whole experiment.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

CGROUP_DIR = Path(os.getenv("BF_CBOM_CGROUP_DIR", "/sys/fs/cgroup"))
MEMINFO = Path(os.getenv("BF_CBOM_MEMINFO", "/proc/meminfo"))
_MIB = 1024 * 1024


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None


def _keyed(text: str | None) -> dict[str, int]:
    out = {}
    for line in (text or "").splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def working_set_bytes(cgroup: Path = CGROUP_DIR) -> int | None:
    current = (_read(cgroup / "memory.current") or "").strip()
    if not current.isdigit():
        return None
    inactive = _keyed(_read(cgroup / "memory.stat")).get("inactive_file", 0)
    return max(int(current) - inactive, 0)


def limit_bytes(cgroup: Path = CGROUP_DIR) -> int | None:
    raw = (_read(cgroup / "memory.max") or "").strip()
    return int(raw) if raw.isdigit() else None


def oom_kills(cgroup: Path = CGROUP_DIR) -> int | None:
    events = _keyed(_read(cgroup / "memory.events"))
    return events.get("oom_kill")


def limit_hits(cgroup: Path = CGROUP_DIR) -> int | None:
    """How often the container's usage reached its limit (`memory.events` `max`)."""
    return _keyed(_read(cgroup / "memory.events")).get("max")


def cpu_usec(cgroup: Path = CGROUP_DIR) -> int | None:
    """CPU time the container has used since it started, in microseconds (`cpu.stat` `usage_usec`)."""
    return _keyed(_read(cgroup / "cpu.stat")).get("usage_usec")


def mib(n: int | None) -> float | None:
    return None if n is None else round(n / _MIB, 1)


class JobSampler:
    """Peak working set, limit hits, OOM kills and CPU time of this container between `__init__` and `result()`."""

    def __init__(self, cgroup: Path | None = None):
        self.cgroup = cgroup = cgroup or CGROUP_DIR
        self.peak: int | None = None
        self.oom_start = oom_kills(cgroup)
        self.hits_start = limit_hits(cgroup)
        self.cpu_start, self.wall_start = cpu_usec(cgroup), time.monotonic()
        self.sample()

    def sample(self) -> None:
        ws = working_set_bytes(self.cgroup)
        if ws is not None and (self.peak is None or ws > self.peak):
            self.peak = ws

    def result(self) -> dict:
        self.sample()
        oom_end, hits_end = oom_kills(self.cgroup), limit_hits(self.cgroup)
        oom = None if self.oom_start is None or oom_end is None else oom_end - self.oom_start
        hits = None if self.hits_start is None or hits_end is None else hits_end - self.hits_start
        cpu_end, wall = cpu_usec(self.cgroup), time.monotonic() - self.wall_start
        cpu = None if self.cpu_start is None or cpu_end is None else (cpu_end - self.cpu_start) / 1e6
        return {
            "peak_memory_mb": mib(self.peak),
            "memory_limit_mb": mib(limit_bytes(self.cgroup)),
            "limit_hits": hits,
            "oom_kills": oom,
            "cpu_seconds": None if cpu is None else round(cpu, 1),
            "cpu_cores": None if cpu is None or wall <= 0 else round(cpu / wall, 2),
        }


def host_memory(meminfo: Path = MEMINFO) -> dict:
    """{'total_mb', 'used_mb'} of the Docker VM / host (used = MemTotal - MemAvailable), {} when unreadable."""
    info = _keyed(_read(meminfo))  # values in kB
    total, available = info.get("MemTotal"), info.get("MemAvailable")
    if total is None or available is None:
        return {}
    return {"total_mb": round(total / 1024, 1), "used_mb": round((total - available) / 1024, 1)}


class HostSampler:
    """Peak RAM in use on the Docker VM / host while an experiment session runs (the runner calls `sample`)."""

    def __init__(self, meminfo: Path | None = None):
        self.meminfo = meminfo = meminfo or MEMINFO
        first = host_memory(meminfo)
        self.total_mb = first.get("total_mb")
        self.start_used_mb = first.get("used_mb")
        self.peak_used_mb = first.get("used_mb")

    def sample(self) -> None:
        used = host_memory(self.meminfo).get("used_mb")
        if used is not None and (self.peak_used_mb is None or used > self.peak_used_mb):
            self.peak_used_mb = used
