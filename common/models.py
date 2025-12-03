from dataclasses import dataclass

from dataclasses_json import DataClassJsonMixin


class Trace:
    """
    Lightweight trace aggregator for workers.

    Usage:
      t = Trace(); t.add("step started"); t.add_exc("cdxgen failed", e)
      err_text = t.text()
    """

    def __init__(self) -> None:
        self._items: list[str] = []

    def add(self, msg: str) -> None:
        if not msg:
            return
        self._items.append(str(msg).strip())

    def add_exc(
        self,
        prefix: str,
        e: Exception,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        parts = [f"{prefix}: {e}"]
        if stdout:
            parts.append(f"stdout: {stdout.strip()}")
        if stderr:
            parts.append(f"stderr: {stderr.strip()}")
        self.add("\n".join(parts))

    def text(self) -> str | None:
        return "\n".join(self._items) if self._items else None


@dataclass
class RepoInfo(DataClassJsonMixin):
    full_name: str
    git_url: str
    branch: str
    size_kb: int
    main_language: str | None = None
    stars: int | None = None


@dataclass
class JobResult(DataClassJsonMixin):
    job_id: str
    status: str
    repo_info: RepoInfo
    json: str
    duration_sec: float | None = None
    size_bytes: int | None = None
    worker: str | None = None
    error: str | None = None


@dataclass
class JobInstruction(DataClassJsonMixin):
    job_id: str
    tool: str
    repo_info: RepoInfo


@dataclass
class Inspection(DataClassJsonMixin):
    insp_id: str
    name: str
    status: str = "created"
    params: dict | None = None
    workers: list[str] | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    repo_count: int | None = None
    worker_count: int | None = None
    expected_jobs: int | None = None
    issued_jobs: int | None = None


@dataclass
class CbomJson(DataClassJsonMixin):
    tool: str
    components_as_json: list[str]
    entire_json_raw: str


@dataclass
class ComponentMatchJobInstruction(DataClassJsonMixin):
    job_id: str
    inspection_id: str
    repo_info: RepoInfo
    CbomJsons: list[CbomJson]


@dataclass
class ComponentMatchJobResult(DataClassJsonMixin):
    job_id: str
    inspection_id: str
    repo_full_name: str
    tools: list[str]
    match_count: int
    matches: list[dict]
    duration_sec: float | None = None
    status: str = "ok"
    error: str | None = None


# ----- Minimal CLI config schema -----


@dataclass
class RepoRef(DataClassJsonMixin):
    """Minimal repository reference used by the CLI config."""

    full_name: str
    git_url: str
    branch: str | None = None


@dataclass
class InspectionConfig(DataClassJsonMixin):
    """Minimal, reproducible inspection configuration for CLI/CI.

    Fields are intentionally small to keep configs portable and tool-agnostic.
    """

    schema_version: str = "1"
    name: str = ""
    workers: list[str] | None = None
    repos: list[RepoRef] | None = None
