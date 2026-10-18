"""Batch experiments: spec parsing, snapshot pinning, git mirroring and on-disk result layout.

Pure logic shared by the `bf-cbom experiment` CLI (misc/cli) and the analysis code. Nothing in
here talks to Redis; the CLI does the queueing. All paths default to the Docker volumes mounted
into the `runner` container (`RESULTS_DIR`, `GIT_MIRROR_DIR`) so that nothing lands on the host.

Layout of one experiment (under RESULTS_DIR):

    <exp>/
      _experiment.yaml         copy of the spec as it was run (leading underscore: listed first)
      _run-log.json            one entry per `experiment run` session: times, host RAM/CPUs, budget, waves, peak RAM
      _ANALYSIS/               derived tables and _SUMMARY.md (`analysis report`; regenerated, never input)
      lock.json                resolved snapshot per repo and variant (commit SHAs, git URLs)
      <owner>__<repo>@<sha12>/<variant>/<tool>/run<k>.json        the CBOM
      <owner>__<repo>@<sha12>/<variant>/<tool>/run<k>.meta.json   JobResult without the CBOM body

On the git server (GIT_MIRROR_DIR): `<owner>/<repo>.git` is the base mirror, `<owner>/<repo>--<sha12>.git`
the per-snapshot repo workers actually scan (HEAD = snapshot branch, objects shared via alternates).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from .jobs import tool_supports
from .models import JobInstruction, JobResult, RepoInfo
from .tools_registry import load_tools, plan_waves  # noqa: F401  plan_waves re-exported for the runner and tests
from .utils import resolve_remote_commit

RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/data/results"))
GIT_MIRROR_DIR = Path(os.getenv("GIT_MIRROR_DIR", "/srv/git"))
# How workers reach the local git server (compose network); the runner writes the mirrors directly.
GITSERVER_URL = os.getenv("GITSERVER_URL", "http://gitserver").rstrip("/")

ORIGINAL_VARIANT = "original"
BRANCH_PREFIX = "bfcbom/"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ExperimentError(ValueError):
    pass


# ----- spec -----


@dataclass
class RepoSpec:
    full_name: str
    git_url: str | None = None
    branch: str | None = None
    commit: str | None = None
    language: str | None = None
    stars: int | None = None
    size_kb: int | None = None
    variants: list[str] = field(default_factory=lambda: [ORIGINAL_VARIANT])
    # A plain directory (e.g. corpus/micro/java) published to the git server by the runner instead of
    # a remote repo; resolved to an absolute path at spec load time.
    path: str | None = None

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]

    @property
    def is_fixture(self) -> bool:
        return bool(self.path)

    @property
    def source_url(self) -> str:
        if self.git_url:
            return self.git_url
        if self.path:
            return f"fixture:{self.path}"
        return f"https://github.com/{self.full_name}.git"


@dataclass
class ExperimentSpec:
    name: str
    tools: list[str]
    repos: list[RepoSpec]
    repetitions: int = 1
    description: str = ""

    @property
    def expected_jobs(self) -> int:
        return sum(len(r.variants) for r in self.repos) * len(self.tools) * self.repetitions


def _opt_str(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _as_repo_spec(item: dict, *, base_dir: Path | None = None) -> RepoSpec:
    full_name = str(item.get("full_name") or "").strip()
    if full_name.count("/") != 1:
        raise ExperimentError(f"repo full_name must be owner/name, got {full_name!r}")
    variants = item.get("variants") or [ORIGINAL_VARIANT]
    for v in variants:
        if not _NAME_RE.match(str(v)):
            raise ExperimentError(f"invalid variant name {v!r} for {full_name}")
    commit = item.get("commit")
    if commit is not None and not _SHA_RE.match(str(commit)):
        raise ExperimentError(f"commit for {full_name} must be a full 40-hex SHA")
    path = item.get("path")
    if path:
        if item.get("git_url") or commit:
            raise ExperimentError(f"{full_name}: 'path' cannot be combined with git_url or commit")
        p = Path(str(path))
        if not p.is_absolute() and base_dir is not None:
            p = base_dir / p
        path = str(p.resolve()) if p.exists() else str(p)
    return RepoSpec(
        full_name=full_name,
        git_url=item.get("git_url") or None,
        branch=_opt_str(item.get("branch")),  # YAML reads `1.8` as a float: branches are strings
        commit=commit,
        language=item.get("language") or None,
        stars=item.get("stars"),
        size_kb=item.get("size_kb") or item.get("size"),
        variants=[str(v) for v in variants],
        path=path or None,
    )


def load_spec(path: str | Path, *, base_dir: Path | None = None) -> ExperimentSpec:
    """Parse an experiment YAML. `corpus: <file>` pulls the repo list from a corpus.yaml."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return spec_from_dict(data, base_dir=base_dir or path.parent)


def spec_from_dict(data: dict, *, base_dir: Path | None = None) -> ExperimentSpec:
    name = str(data.get("name") or "").strip()
    if not _NAME_RE.match(name):
        raise ExperimentError("experiment name must match [A-Za-z0-9][A-Za-z0-9._-]* (max 64 chars)")
    tools = [str(t) for t in (data.get("tools") or [])]
    if not tools:
        raise ExperimentError("experiment needs a non-empty 'tools' list")
    repetitions = int(data.get("repetitions") or 1)
    if repetitions < 1:
        raise ExperimentError("repetitions must be >= 1")
    repos_raw = list(data.get("repos") or [])
    corpus_ref = data.get("corpus")
    if corpus_ref:
        corpus_path = Path(corpus_ref)
        if not corpus_path.is_absolute() and base_dir is not None:
            corpus_path = base_dir / corpus_path
        corpus = yaml.safe_load(corpus_path.read_text(encoding="utf-8")) or {}
        repos_raw = list(corpus.get("repos") or []) + repos_raw
    if not repos_raw:
        raise ExperimentError("experiment needs 'repos' or a 'corpus' file with repos")
    repos = [_as_repo_spec(r, base_dir=base_dir) for r in repos_raw]
    seen: set[str] = set()
    for r in repos:
        if r.full_name in seen:
            raise ExperimentError(f"duplicate repo {r.full_name}")
        seen.add(r.full_name)
    return ExperimentSpec(
        name=name, tools=tools, repos=repos, repetitions=repetitions, description=str(data.get("description") or "")
    )


# ----- paths -----


SPEC_FILE = "_experiment.yaml"
RUN_LOG_FILE = "_run-log.json"
ANALYSIS_DIR = "_ANALYSIS"


def spec_path(exp_dir: Path) -> Path:
    """The stored spec copy of an experiment."""
    return exp_dir / SPEC_FILE


def analysis_dir(exp_dir: Path) -> Path:
    return exp_dir / ANALYSIS_DIR


def write_spec_copy(exp_dir: Path, spec: ExperimentSpec) -> Path:
    path = spec_path(exp_dir)
    path.write_text(yaml.safe_dump(spec_to_dict(spec), sort_keys=False), encoding="utf-8")
    return path


def is_experiment(exp_dir: Path) -> bool:
    return spec_path(exp_dir).exists()


def load_run_log(exp_dir: Path) -> list[dict]:
    path = exp_dir / RUN_LOG_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save_run_log_entry(exp_dir: Path, entry: dict) -> None:
    """Insert or update the session `entry["session"]` in _run-log.json (atomic rewrite)."""
    log = [e for e in load_run_log(exp_dir) if e.get("session") != entry.get("session")]
    log.append(entry)
    path = exp_dir / RUN_LOG_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(log, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def experiment_dir(name: str, results_dir: Path = RESULTS_DIR) -> Path:
    return results_dir / name


def repo_dir_name(full_name: str, commit: str) -> str:
    return f"{full_name.replace('/', '__')}@{commit[:12]}"


def repo_full_name(dir_name: str) -> str:
    """`<owner>__<repo>@<sha12>` -> `<owner>/<repo>` (the inverse of repo_dir_name)."""
    return dir_name.split("@", 1)[0].replace("__", "/", 1)


def result_paths(
    exp_dir: Path, full_name: str, commit: str, variant: str, tool: str, run_index: int
) -> tuple[Path, Path]:
    """(CBOM path, meta path) for one run."""
    d = exp_dir / repo_dir_name(full_name, commit) / variant / tool
    return d / f"run{run_index}.json", d / f"run{run_index}.meta.json"


def variant_branch(variant: str, commit: str) -> str:
    """Branch name on the local git server: original snapshots are `bfcbom/<sha>`, mutants `bfcbom/<id>`."""
    return f"{BRANCH_PREFIX}{commit}" if variant == ORIGINAL_VARIANT else f"{BRANCH_PREFIX}{variant}"


def mirror_path(full_name: str, mirror_dir: Path = GIT_MIRROR_DIR) -> Path:
    """Base mirror of a repo: all upstream refs plus the mutant branches."""
    return mirror_dir / f"{full_name}.git"


def scan_repo_name(full_name: str, variant_commit: str) -> str:
    """Served path of one snapshot: `<owner>/<repo>--<sha12>` (no `@`, which URL parsers read as userinfo)."""
    return f"{full_name}--{variant_commit[:12]}"


def scan_repo_path(full_name: str, variant_commit: str, mirror_dir: Path = GIT_MIRROR_DIR) -> Path:
    return mirror_dir / f"{scan_repo_name(full_name, variant_commit)}.git"


def mirror_url(full_name: str, variant_commit: str, server_url: str = GITSERVER_URL) -> str:
    return f"{server_url}/{scan_repo_name(full_name, variant_commit)}.git"


# ----- git mirroring (runs in the runner, writes the git-data volume) -----


def _git(args: list[str], *, timeout: float | None = 900) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return subprocess.run(["git", *args], capture_output=True, text=True, env=env, timeout=timeout, check=False)


def ensure_mirror(spec: RepoSpec, commit: str, *, mirror_dir: Path = GIT_MIRROR_DIR) -> Path:
    """Make sure the bare mirror holds `commit` and has the branch for the original variant.

    Creates the bare repo on first use (`git clone --bare`), fetches when the commit is missing,
    and allows fetching arbitrary reachable SHAs so workers can check out exactly that commit.
    """
    bare = mirror_path(spec.full_name, mirror_dir)
    if not (bare / "HEAD").exists():
        bare.parent.mkdir(parents=True, exist_ok=True)
        proc = _git(["clone", "--bare", "--quiet", spec.source_url, str(bare)])
        if proc.returncode != 0:
            raise ExperimentError(f"mirror clone failed for {spec.full_name}: {proc.stderr.strip()[-300:]}")
    if _git(["-C", str(bare), "cat-file", "-e", f"{commit}^{{commit}}"]).returncode != 0:
        proc = _git(["-C", str(bare), "fetch", "--quiet", "origin", "+refs/heads/*:refs/heads/*"])
        if proc.returncode != 0 or _git(["-C", str(bare), "cat-file", "-e", f"{commit}^{{commit}}"]).returncode != 0:
            raise ExperimentError(f"commit {commit[:12]} of {spec.full_name} not found upstream")
    for key, value in (("uploadpack.allowAnySHA1InWant", "true"), ("uploadpack.allowReachableSHA1InWant", "true")):
        _git(["-C", str(bare), "config", key, value])
    branch = variant_branch(ORIGINAL_VARIANT, commit)
    _git(["-C", str(bare), "update-ref", f"refs/heads/{branch}", commit])
    return bare


FIXTURE_AUTHOR = ("BF-CBOM fixtures", "fixtures@bf-cbom.invalid")
FIXTURE_DATE = "2026-10-08T00:00:00Z"


def publish_fixture(spec: RepoSpec, *, mirror_dir: Path = GIT_MIRROR_DIR) -> str:
    """Commit a plain directory (`RepoSpec.path`, e.g. corpus/micro/java) into the base mirror.

    The commit is deterministic (fixed author, date and message), so the SHA depends only on the
    files' contents and modes: the same fixture gets the same snapshot id on every machine.
    Returns the commit; `ensure_mirror` then finds it in the mirror like any upstream commit.
    """
    if not spec.path:
        raise ExperimentError(f"{spec.full_name} is not a fixture (no path)")
    src = Path(spec.path)
    if not src.is_dir():
        raise ExperimentError(f"fixture directory for {spec.full_name} not found: {src}")
    env = {
        "GIT_AUTHOR_NAME": FIXTURE_AUTHOR[0],
        "GIT_AUTHOR_EMAIL": FIXTURE_AUTHOR[1],
        "GIT_AUTHOR_DATE": FIXTURE_DATE,
        "GIT_COMMITTER_NAME": FIXTURE_AUTHOR[0],
        "GIT_COMMITTER_EMAIL": FIXTURE_AUTHOR[1],
        "GIT_COMMITTER_DATE": FIXTURE_DATE,
    }
    with tempfile.TemporaryDirectory(prefix="fixture-") as tmp:
        work = Path(tmp) / "work"
        shutil.copytree(src, work, ignore=shutil.ignore_patterns(".git"))
        old_env = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            for args in (
                ["init", "-q", "-b", "main"],
                ["add", "-A", "--", "."],
                ["commit", "-q", "--allow-empty", "-m", f"BF-CBOM fixture {spec.full_name}"],
            ):
                proc = _git(["-C", str(work), *args])
                if proc.returncode != 0:
                    raise ExperimentError(f"fixture commit failed for {spec.full_name}: {proc.stderr.strip()[-300:]}")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        sha = _git(["-C", str(work), "rev-parse", "HEAD"]).stdout.strip()
        if not _SHA_RE.match(sha):
            raise ExperimentError(f"fixture commit failed for {spec.full_name}")
        bare = mirror_path(spec.full_name, mirror_dir)
        if not (bare / "HEAD").exists():
            bare.parent.mkdir(parents=True, exist_ok=True)
            proc = _git(["clone", "--bare", "--quiet", str(work), str(bare)])
        else:
            proc = _git(["-C", str(bare), "fetch", "--quiet", str(work), "+refs/heads/main:refs/heads/main"])
        if proc.returncode != 0:
            raise ExperimentError(f"publishing fixture {spec.full_name} failed: {proc.stderr.strip()[-300:]}")
    return sha


def ensure_scan_repo(
    spec: RepoSpec, variant: str, base_commit: str, commit: str, *, mirror_dir: Path = GIT_MIRROR_DIR
) -> Path:
    """One small bare repo per snapshot whose HEAD is the snapshot branch.

    CBOMkit (JGit) checks out only the clone's default branch, so every (repo, variant) gets its own
    served repository. Objects are shared with the base mirror through `objects/info/alternates`
    (`git clone --bare --shared`), so this costs a few KB per snapshot. Idempotent.
    """
    base = mirror_path(spec.full_name, mirror_dir)
    scan = scan_repo_path(spec.full_name, commit, mirror_dir)
    branch = variant_branch(variant, base_commit)
    if not (scan / "HEAD").exists():
        proc = _git(["clone", "--bare", "--shared", "--quiet", "--no-tags", str(base), str(scan)])
        if proc.returncode != 0:
            raise ExperimentError(
                f"creating scan repo for {spec.full_name}@{variant} failed: {proc.stderr.strip()[-300:]}"
            )
    for key, value in (("uploadpack.allowAnySHA1InWant", "true"), ("uploadpack.allowReachableSHA1InWant", "true")):
        _git(["-C", str(scan), "config", key, value])
    _git(["-C", str(scan), "update-ref", f"refs/heads/{branch}", commit])
    _git(["-C", str(scan), "symbolic-ref", "HEAD", f"refs/heads/{branch}"])
    return scan


def snapshot_files(full_name: str, commit: str | None, *, mirror_dir: Path = GIT_MIRROR_DIR) -> frozenset[str] | None:
    """Paths of all files of a snapshot (from the base mirror), or None when the mirror is not mounted
    (e.g. an exported copy on the host). Used by the normaliser for `file_exists` and path resolution."""
    bare = mirror_path(full_name, mirror_dir)
    if not commit or not (bare / "HEAD").exists():
        return None
    proc = _git(["-C", str(bare), "ls-tree", "-r", "--name-only", "-z", commit], timeout=120)
    if proc.returncode != 0:
        return None
    return frozenset(p for p in proc.stdout.split("\0") if p)


def variant_commit(spec: RepoSpec, variant: str, base_commit: str, *, mirror_dir: Path = GIT_MIRROR_DIR) -> str:
    """Commit of a variant branch in the mirror (mutants are created there by the mutation harness)."""
    if variant == ORIGINAL_VARIANT:
        return base_commit
    bare = mirror_path(spec.full_name, mirror_dir)
    proc = _git(["-C", str(bare), "rev-parse", "--verify", "-q", f"refs/heads/{variant_branch(variant, base_commit)}"])
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not _SHA_RE.match(sha):
        raise ExperimentError(
            f"variant {variant!r} of {spec.full_name} has no branch {variant_branch(variant, base_commit)}"
        )
    return sha


# ----- lock: resolved snapshots -----


@dataclass
class Snapshot:
    full_name: str
    source_url: str
    branch: str
    commit: str
    variant: str
    variant_commit: str
    scan_url: str
    scan_branch: str
    language: str | None = None
    stars: int | None = None
    size_kb: int | None = None

    def repo_info(self) -> RepoInfo:
        return RepoInfo(
            full_name=self.full_name,
            git_url=self.scan_url,
            branch=self.scan_branch,
            size_kb=int(self.size_kb or 0),
            main_language=self.language,
            stars=self.stars,
            commit=self.variant_commit,
        )


def lock_path(exp_dir: Path) -> Path:
    return exp_dir / "lock.json"


def load_lock(exp_dir: Path) -> dict[str, dict]:
    p = lock_path(exp_dir)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("repos", {})


def save_lock(exp_dir: Path, locked: dict[str, dict]) -> None:
    exp_dir.mkdir(parents=True, exist_ok=True)
    payload = {"locked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "repos": locked}
    lock_path(exp_dir).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_snapshots(
    spec: ExperimentSpec,
    exp_dir: Path,
    *,
    mirror_dir: Path = GIT_MIRROR_DIR,
    server_url: str = GITSERVER_URL,
    log=print,
) -> list[Snapshot]:
    """Pin every repo to a commit, mirror it, and return one Snapshot per (repo, variant).

    Commits already in the experiment's lock file win over the spec, so a resumed experiment
    never silently moves to a newer upstream commit.
    """
    locked = load_lock(exp_dir)
    snapshots: list[Snapshot] = []
    for repo in spec.repos:
        prior = locked.get(repo.full_name) or {}
        branch = repo.branch or prior.get("branch") or "main"
        commit = prior.get("commit") or repo.commit
        if repo.is_fixture:
            published = publish_fixture(repo, mirror_dir=mirror_dir)
            if commit and commit != published:
                log(f"  {repo.full_name}: fixture changed since lock.json ({published[:12]}); keeping {commit[:12]}")
            commit = commit or published
        if not commit:
            commit = resolve_remote_commit(repo.source_url, branch)
            if not commit:
                raise ExperimentError(f"cannot resolve {repo.full_name}@{branch} (unknown branch or unreachable)")
        log(f"  {repo.full_name}@{branch} -> {commit[:12]}")
        ensure_mirror(repo, commit, mirror_dir=mirror_dir)
        locked[repo.full_name] = {"branch": branch, "commit": commit, "source_url": repo.source_url}
        for variant in repo.variants:
            vc = variant_commit(repo, variant, commit, mirror_dir=mirror_dir)
            ensure_scan_repo(repo, variant, commit, vc, mirror_dir=mirror_dir)
            snapshots.append(
                Snapshot(
                    full_name=repo.full_name,
                    source_url=repo.source_url,
                    branch=branch,
                    commit=commit,
                    variant=variant,
                    variant_commit=vc,
                    scan_url=mirror_url(repo.full_name, vc, server_url),
                    scan_branch=variant_branch(variant, commit),
                    language=repo.language,
                    stars=repo.stars,
                    size_kb=repo.size_kb,
                )
            )
    save_lock(exp_dir, locked)
    return snapshots


# ----- job planning and result persistence -----


@dataclass
class PlannedJob:
    job_id: str
    snapshot: Snapshot
    tool: str
    run_index: int
    enqueued_at: str | None = None  # set by the runner when the job is pushed to its queue (UTC ISO)

    def instruction(self) -> JobInstruction:
        return JobInstruction(
            job_id=self.job_id, tool=self.tool, repo_info=self.snapshot.repo_info(), run_index=self.run_index
        )

    def paths(self, exp_dir: Path) -> tuple[Path, Path]:
        s = self.snapshot
        return result_paths(exp_dir, s.full_name, s.commit, s.variant, self.tool, self.run_index)


def _stored_status(meta_path: Path) -> str | None:
    try:
        return json.loads(meta_path.read_text(encoding="utf-8")).get("status")
    except (OSError, json.JSONDecodeError):
        return None


def plan_jobs(
    spec: ExperimentSpec, snapshots: list[Snapshot], exp_dir: Path, *, new_id, retry_failed: bool = False
) -> tuple[list[PlannedJob], int]:
    """Jobs still to run. Resumable: a run whose meta file exists is skipped, unless `retry_failed`
    and its stored status is not "ok" (then the stored files are removed and the run is redone).
    Returns (jobs, skipped)."""
    jobs: list[PlannedJob] = []
    skipped = 0
    for snap in snapshots:
        for tool in spec.tools:
            for k in range(1, spec.repetitions + 1):
                cbom, meta = result_paths(exp_dir, snap.full_name, snap.commit, snap.variant, tool, k)
                if meta.exists():
                    if not (retry_failed and _stored_status(meta) not in ("ok", UNSUPPORTED)):
                        skipped += 1
                        continue
                    meta.unlink()
                    cbom.unlink(missing_ok=True)
                jobs.append(PlannedJob(job_id=new_id(), snapshot=snap, tool=tool, run_index=k))
    return jobs, skipped


UNSUPPORTED = "unsupported"


def mark_unsupported(
    spec: ExperimentSpec, snapshots: list[Snapshot], exp_dir: Path, *, registry: dict[str, dict] | None = None
) -> list[tuple[str, str]]:
    """Record (tool, repo) pairs outside the tool's language scope (common/tools.yaml `languages`).

    Writes a `run<k>.meta.json` with status `unsupported` and no CBOM for every repetition that has no stored
    run yet, so `plan_jobs` skips them (also with --retry-failed) and `summarize` counts them. Returns the
    (tool, full_name) pairs recorded this time.
    """
    registry = load_tools() if registry is None else registry
    recorded: list[tuple[str, str]] = []
    for snap in snapshots:
        for tool in spec.tools:
            if tool_supports(tool, snap.language, registry):
                continue
            written = 0
            for k in range(1, spec.repetitions + 1):
                _, meta_path = result_paths(exp_dir, snap.full_name, snap.commit, snap.variant, tool, k)
                if meta_path.exists():
                    continue
                written += 1
                meta_path.parent.mkdir(parents=True, exist_ok=True)
                meta = {
                    "status": UNSUPPORTED,
                    "worker": tool,
                    "run_index": k,
                    "variant": snap.variant,
                    "variant_commit": snap.variant_commit,
                    "base_commit": snap.commit,
                    "source_url": snap.source_url,
                    "language": snap.language,
                    "notes": f"{tool} does not list {snap.language or '?'} in common/tools.yaml; not run",
                    "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            if written:
                recorded.append((tool, snap.full_name))
    return recorded


def write_result(exp_dir: Path, job: PlannedJob, result: JobResult) -> Path:
    """Persist one JobResult: the CBOM body as run<k>.json (pretty, sorted) and everything else as meta."""
    cbom_path, meta_path = job.paths(exp_dir)
    cbom_path.parent.mkdir(parents=True, exist_ok=True)
    body = result.json or "{}"
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict) and isinstance(parsed.get("bom"), dict):
            parsed = parsed["bom"]
        body = json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    except json.JSONDecodeError:
        pass
    meta = result.to_dict()
    meta.pop("json", None)
    meta.update(
        {
            "variant": job.snapshot.variant,
            "variant_commit": job.snapshot.variant_commit,
            "base_commit": job.snapshot.commit,
            "source_url": job.snapshot.source_url,
            "enqueued_at": job.enqueued_at,
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    tmp = cbom_path.with_suffix(".json.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(cbom_path)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return cbom_path


def iter_results(exp_dir: Path):
    """Yield (meta dict, cbom path) for every stored run of an experiment."""
    for meta_path in sorted(exp_dir.glob("*/*/*/run*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        yield meta, meta_path.with_name(meta_path.name.replace(".meta.json", ".json"))


def spec_to_dict(spec: ExperimentSpec) -> dict:
    return {
        "name": spec.name,
        "description": spec.description,
        "tools": list(spec.tools),
        "repetitions": spec.repetitions,
        "repos": [{k: v for k, v in asdict(r).items() if v not in (None, [], "")} for r in spec.repos],
    }
