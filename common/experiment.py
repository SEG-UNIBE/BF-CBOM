"""Batch experiments: spec parsing, snapshot pinning, git mirroring and on-disk result layout.

Pure logic shared by the `bf-cbom experiment` CLI (misc/cli) and the analysis code. Nothing in
here talks to Redis; the CLI does the queueing. All paths default to the Docker volumes mounted
into the `runner` container (`RESULTS_DIR`, `GIT_MIRROR_DIR`) so that nothing lands on the host.

Layout of one experiment (under RESULTS_DIR):

    <exp>/
      _experiment.yaml         the record: the spec as run, every repo with its resolved branch and commit (leading
                               underscore: listed first); the next run keeps these commits
      _run-log.json            one entry per `experiment run` session: times, host RAM/CPUs, budget, waves, peak RAM
      _ANALYSIS/               derived tables and _SUMMARY.md (`analysis report`; regenerated, never input)
      mutants.json             with a `mutation:` block: every mutant (snippet, style, host, lines, inserts, patch)
      <owner>__<repo>@<sha12>/<variant>/<tool>/run<i>.json        the CBOM
      <owner>__<repo>@<sha12>/<variant>/<tool>/run<i>.meta.json   JobResult without the CBOM body

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
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import NamedTuple

import yaml

from .corpus import SIZE_CAP_RATIO, repo_size_cap_mb
from .jobs import tool_supports
from .languages import source_files
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
    # A plain directory (e.g. experiments/corpus/micro/java) published to the git server by the runner instead of
    # a remote repo; resolved to an absolute path at spec load time.
    path: str | None = None
    # Corpus metadata for the analysis, ignored by the runner: `low` marks a negative case (little crypto expected)
    expect_crypto: str | None = None
    # Counted by the runner at the pinned commit and written into the record, never spec input (`snapshot_size`): the
    # snapshot's files and the source files of its language (the size the run-time table scales by) and the bytes of
    # the checkout and of those source files (the blobs at that commit, no history); an export carries them.
    files: int | None = None
    source_files: int | None = None
    checkout_bytes: int | None = None
    source_bytes: int | None = None

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


MUTATION_STYLES = ("direct", "wrapper", "indirect")  # mutation/catalog.py:STYLES (refinement 5)


@dataclass
class MutationSpec:
    """A spec's `mutation:` block: every repo gets `per_repo` planted calls and `decoys` planted decoys as variants
    (mutation/mutants.py; `equal`: as many decoys as calls)."""

    per_repo: int = 10
    styles: list[str] = field(default_factory=lambda: list(MUTATION_STYLES))
    snippets: list[str] | None = None  # None: the whole catalog of the repo's language
    seed: int = 1
    decoys: int | str = "equal"

    @property
    def n_decoys(self) -> int:
        return self.per_repo if self.decoys == "equal" else int(self.decoys)

    def to_dict(self) -> dict:
        return {"per_repo": self.per_repo, "styles": list(self.styles), "snippets": self.snippets, "seed": self.seed,
                "decoys": self.decoys}  # fmt: skip


def _as_mutation_spec(item) -> MutationSpec:
    if not isinstance(item, dict):
        raise ExperimentError("'mutation' must be a mapping (per_repo, styles, snippets, seed, decoys)")
    unknown = set(item) - {"per_repo", "styles", "snippets", "seed", "decoys"}
    if unknown:
        raise ExperimentError(f"unknown 'mutation' keys: {sorted(unknown)}")
    styles = [str(s) for s in item.get("styles") or MUTATION_STYLES]
    if not set(styles) <= set(MUTATION_STYLES):
        raise ExperimentError(f"mutation styles must be among {list(MUTATION_STYLES)}, got {styles}")
    snippets = item.get("snippets")
    if snippets in (None, "all"):
        snippets = None
    elif isinstance(snippets, list):
        snippets = [str(s) for s in snippets]
    else:
        raise ExperimentError("mutation 'snippets' must be 'all' or a list of snippet ids")
    per_repo = int(item.get("per_repo", 10))
    if per_repo < 1:
        raise ExperimentError("mutation 'per_repo' must be >= 1")
    decoys = item.get("decoys", "equal")
    if decoys != "equal":
        if isinstance(decoys, bool) or not isinstance(decoys, int) or decoys < 0:
            raise ExperimentError("mutation 'decoys' must be 'equal' or an integer >= 0")
    return MutationSpec(
        per_repo=per_repo, styles=styles, snippets=snippets, seed=int(item.get("seed", 1)), decoys=decoys
    )


@dataclass
class ExperimentSpec:
    name: str
    tools: list[str]
    repos: list[RepoSpec]
    repetitions: int = 1
    description: str = ""
    mutation: MutationSpec | None = None
    tool_languages: dict[str, list[str]] = field(default_factory=dict)  # tool -> the languages it runs in here

    @property
    def expected_jobs(self) -> int:
        """Planned runs; with a `mutation:` block an upper bound (a repo with few hosts gets fewer mutants, the exact
        count is in mutants.json: `expected_jobs`)."""
        if self.mutation:
            per_repo = lambda r: 1 + self.mutation.per_repo + self.mutation.n_decoys  # noqa: E731
        else:
            per_repo = lambda r: len(r.variants)  # noqa: E731
        return sum(per_repo(r) for r in self.repos) * len(self.tools) * self.repetitions


def _opt_str(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _opt_int(value) -> int | None:
    return int(value) if value not in (None, "") else None


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
    if path:  # a `commit` next to it pins the fixture as published (written so into _experiment.yaml)
        if item.get("git_url"):
            raise ExperimentError(f"{full_name}: 'path' cannot be combined with git_url")
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
        expect_crypto=_opt_str(item.get("expect_crypto")),
        files=_opt_int(item.get("files")),
        source_files=_opt_int(item.get("source_files")),
        checkout_bytes=_opt_int(item.get("checkout_bytes")),
        source_bytes=_opt_int(item.get("source_bytes")),
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
    mutation = _as_mutation_spec(data["mutation"]) if data.get("mutation") is not None else None
    if mutation is not None:
        listed = [r.full_name for r in repos if r.variants != [ORIGINAL_VARIANT]]
        if listed:
            raise ExperimentError(
                f"'variants' cannot be combined with 'mutation' (the mutants are generated): {listed}"
            )
    tool_languages = _as_tool_languages(data.get("tool_languages"), tools)
    return ExperimentSpec(
        name=name,
        tools=tools,
        repos=repos,
        repetitions=repetitions,
        description=str(data.get("description") or ""),
        mutation=mutation,
        tool_languages=tool_languages,
    )


def _as_tool_languages(item, tools: list[str]) -> dict[str, list[str]]:
    """A spec's optional `tool_languages:` {tool: [languages]}: the languages a listed tool runs in within this
    experiment, a hand edit (after reading the bar-micro report, say); tools not named run wherever
    common/tools.yaml declares support. No code derives it from anything."""
    if item is None:
        return {}
    if not isinstance(item, dict):
        raise ExperimentError("'tool_languages' must be a mapping of tool -> list of languages")
    out: dict[str, list[str]] = {}
    for tool, languages in item.items():
        if str(tool) not in tools:
            raise ExperimentError(f"'tool_languages' names {tool!r}, which is not in 'tools'")
        if not isinstance(languages, list) or not languages or not all(isinstance(x, str) for x in languages):
            raise ExperimentError(f"'tool_languages' for {tool!r} must be a non-empty list of languages")
        out[str(tool)] = [str(x) for x in languages]
    return out


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
    exp_dir.mkdir(parents=True, exist_ok=True)
    path = spec_path(exp_dir)
    path.write_text(yaml.safe_dump(spec_to_dict(spec), sort_keys=False), encoding="utf-8")
    return path


def pinned_repos(exp_dir: Path) -> dict[str, RepoSpec]:
    """The repos of the experiment's record (`_experiment.yaml`) by full name, each with the branch and commit it was
    resolved to; empty before the first run."""
    if not spec_path(exp_dir).exists():
        return {}
    return {r.full_name: r for r in load_spec(spec_path(exp_dir)).repos}


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


_SNAPSHOT_DIR = re.compile(r".+__.+@[0-9a-f]+")  # repo_dir_name's shape


def remove_experiment(exp_dir: Path) -> int:
    """Remove an experiment folder with everything in it (`experiment delete`, `experiment run --overwrite-old`): the
    stored runs, the record `_experiment.yaml`, the run log, `mutants.json`, `_ANALYSIS/` and anything else, so a
    rerun starts as if the experiment had never run. Results are regenerated, never kept: the spec pins the commits
    and the mutants are deterministic (mutation/mutants.py), so a rerun scans the same snapshots. Returns the number
    of stored runs removed."""
    if not exp_dir.is_dir():
        return 0
    removed = sum(1 for _ in exp_dir.glob("*/*/*/run*.meta.json"))
    shutil.rmtree(exp_dir)
    return removed


def result_paths(
    exp_dir: Path, full_name: str, commit: str, variant: str, tool: str, run_index: int
) -> tuple[Path, Path]:
    """(CBOM path, meta path) for one run."""
    d = exp_dir / repo_dir_name(full_name, commit) / variant / tool
    return d / f"run{run_index}.json", d / f"run{run_index}.meta.json"


def variant_branch(variant: str, commit: str) -> str:
    """Branch name on the local git server: original snapshots are `bfcbom/<sha>`, mutants `bfcbom/<id>`."""
    return f"{BRANCH_PREFIX}{commit}" if variant == ORIGINAL_VARIANT else f"{BRANCH_PREFIX}{variant}"


_REPO_ROOT = Path(__file__).resolve().parents[1]


def fixture_dir(recorded: str | None) -> Path | None:
    """The directory of a fixture on this machine: the recorded path (`/app/experiments/corpus/micro/java` in the
    runner), else the same path under this checkout (`experiments/...`), so a laptop reads the micro projects behind a
    copied export; None when neither exists."""
    if not recorded:
        return None
    p = Path(recorded)
    if p.is_dir():
        return p
    parts = p.parts
    if "experiments" in parts:
        local = _REPO_ROOT.joinpath(*parts[parts.index("experiments") :])
        if local.is_dir():
            return local
    return None


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
    """Run git; a call that gives no answer within `timeout` seconds (a stalled network, a huge repository) is an
    `ExperimentError` naming the command, so the step fails with that reason instead of a traceback."""
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, env=env, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        shown = " ".join(a for a in args if not a.startswith("--"))
        raise ExperimentError(
            f"git {shown} gave no answer within {timeout} s (a stalled network or a very large repository)"
        ) from None


def ensure_mirror(spec: RepoSpec, commit: str, *, mirror_dir: Path = GIT_MIRROR_DIR, log=lambda m: None) -> Path:
    """Make sure the bare mirror holds `commit` and has the branch for the original variant.

    Creates the bare repo on first use (`git clone --bare`), fetches when the commit is missing,
    and allows fetching arbitrary reachable SHAs so workers can check out exactly that commit. Both go over the
    network and can take minutes, so each is announced through `log`; a mirror that has the commit costs nothing.
    """
    bare = mirror_path(spec.full_name, mirror_dir)
    if not (bare / "HEAD").exists():
        bare.parent.mkdir(parents=True, exist_ok=True)
        log(f"  cloning {spec.full_name} into the mirror (first use)")
        proc = _git(["clone", "--bare", "--quiet", spec.source_url, str(bare)])
        if proc.returncode != 0:
            raise ExperimentError(f"mirror clone failed for {spec.full_name}: {proc.stderr.strip()[-300:]}")
    if _git(["-C", str(bare), "cat-file", "-e", f"{commit}^{{commit}}"]).returncode != 0:
        log(f"  fetching {spec.full_name}: commit {commit[:12]} is not in the mirror yet")
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


def publish_fixture(spec: RepoSpec, *, mirror_dir: Path = GIT_MIRROR_DIR, bare: Path | None = None) -> str:
    """Commit a plain directory (`RepoSpec.path`, e.g. experiments/corpus/micro/java) into the base mirror (or into
    `bare`: the analysis's snapshot cache on a laptop, analysis/snapshots.py).

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
        bare = bare or mirror_path(spec.full_name, mirror_dir)
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


def mirror_size_mb(bare: Path) -> int:
    """Size of a bare mirror's object store in MB (what a full clone through the git server has to pack)."""
    objects = bare / "objects"
    return sum(p.stat().st_size for p in objects.rglob("*") if p.is_file()) // 1024**2 if objects.is_dir() else 0


def snapshot_files(full_name: str, commit: str | None, *, mirror_dir: Path = GIT_MIRROR_DIR) -> frozenset[str] | None:
    """Paths of all files of a snapshot (from the base mirror), or None when the mirror is not mounted
    (e.g. an exported copy on the host). Used by the normaliser for `file_exists` and path resolution."""
    bare = mirror_path(full_name, mirror_dir)
    if not commit or not (bare / "HEAD").exists():
        return None
    try:
        proc = _git(["-C", str(bare), "ls-tree", "-r", "--name-only", "-z", commit], timeout=120)
    except ExperimentError:
        return None
    if proc.returncode != 0:
        return None
    return frozenset(p for p in proc.stdout.split("\0") if p)


class SnapshotSize(NamedTuple):
    """A snapshot's size at its commit, for the record (the field names of `RepoSpec`)."""

    files: int
    source_files: int  # of the repository's language (common/languages.py)
    checkout_bytes: int  # the blobs at that commit, no history
    source_bytes: int


def snapshot_size(
    full_name: str, commit: str | None, language: str | None, *, mirror_dir: Path = GIT_MIRROR_DIR
) -> SnapshotSize | None:
    """The files at `commit` and their bytes, all and those of `language`, counted from the base mirror
    (`git ls-tree -r -l`: blobs only, so a submodule is no file) for the record, so an exported experiment carries
    the size the run-time table scales by; None without the mirror."""
    bare = mirror_path(full_name, mirror_dir)
    if not commit or not (bare / "HEAD").exists():
        return None
    try:
        proc = _git(["-C", str(bare), "ls-tree", "-r", "-l", "-z", commit], timeout=120)
    except ExperimentError:
        return None
    if proc.returncode != 0:
        return None
    blobs: dict[str, int] = {}
    for entry in proc.stdout.split("\0"):
        head, _, path = entry.partition("\t")
        parts = head.split()
        if len(parts) == 4 and parts[1] == "blob" and path:
            blobs[path] = int(parts[3])
    code = [p for p in blobs if source_files((p,), language)]
    return SnapshotSize(len(blobs), len(code), sum(blobs.values()), sum(blobs[p] for p in code))


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


# ----- resolved snapshots -----


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


MUTANTS_FILE = "mutants.json"


def mutants_path(exp_dir: Path) -> Path:
    return exp_dir / MUTANTS_FILE


def load_mutants(exp_dir: Path) -> dict:
    """mutants.json: {"repos": {full_name: {commit, language, mutation, catalog_version, hosts, mutants, notes}}}."""
    p = mutants_path(exp_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"repos": {}}


def save_mutants(exp_dir: Path, manifest: dict) -> None:
    exp_dir.mkdir(parents=True, exist_ok=True)
    manifest["written_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mutants_path(exp_dir).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def expected_jobs(spec: ExperimentSpec, exp_dir: Path) -> int:
    """Planned runs of the experiment: with mutants made already, their exact number (mutants.json)."""
    if spec.mutation is None or not mutants_path(exp_dir).exists():
        return spec.expected_jobs
    repos = load_mutants(exp_dir).get("repos", {})
    variants = sum(1 + len((repos.get(r.full_name) or {}).get("mutants", [])) for r in spec.repos)
    return variants * len(spec.tools) * spec.repetitions


def resolve_snapshots(
    spec: ExperimentSpec,
    exp_dir: Path,
    *,
    mirror_dir: Path = GIT_MIRROR_DIR,
    server_url: str = GITSERVER_URL,
    log=print,
    each=lambda repos: repos,
) -> list[Snapshot]:
    """Pin every repo to a commit, mirror it, and return one Snapshot per (repo, variant). `each` wraps the loop over
    the spec's repos (the CLI counts them on its progress line).

    Then the spec is written as the experiment's record, `_experiment.yaml`: the spec as run, every repo with the
    branch and commit it was resolved to and its size at that commit (`files`, `source_files`, `checkout_bytes`,
    `source_bytes`, counted from the mirror by `snapshot_size`; an export carries them). Commits already in the
    record win over the spec, so a resumed experiment
    never silently moves to a newer upstream commit (`--overwrite-old` removes the record first, so the spec
    decides); a repo the spec no longer names leaves the record. Nothing is
    written when a repo fails to resolve. With a `mutation:` block the variants are the original plus the repo's
    mutants, which mutants.json pins the same way (mutation/mutants.py), for exactly the record's repos.
    """
    prior = pinned_repos(exp_dir)
    resolved: list[RepoSpec] = []
    manifest = load_mutants(exp_dir) if spec.mutation is not None else None
    snapshots: list[Snapshot] = []
    cap_mb = repo_size_cap_mb()
    for repo in each(spec.repos):
        before = prior.get(repo.full_name)
        branch = repo.branch or (before and before.branch) or "main"
        commit = (before and before.commit) or repo.commit
        if repo.is_fixture:
            published = publish_fixture(repo, mirror_dir=mirror_dir)
            if commit and commit != published:
                log(f"  {repo.full_name}: fixture changed since pinned ({published[:12]}); keeping {commit[:12]}")
            commit = commit or published
        if not commit:
            commit = resolve_remote_commit(repo.source_url, branch)
            if not commit:
                raise ExperimentError(f"cannot resolve {repo.full_name}@{branch} (unknown branch or unreachable)")
        log(f"  {repo.full_name}@{branch} -> {commit[:12]}")
        bare = ensure_mirror(repo, commit, mirror_dir=mirror_dir, log=log)
        if cap_mb and (size := mirror_size_mb(bare)) > cap_mb:
            log(
                f"  warning: mirror of {repo.full_name} is {size} MB, above the repo size cap {cap_mb} MB "
                f"(GITSERVER_MEMORY / {SIZE_CAP_RATIO}); a full clone through the git server may be killed"
            )
        counted = snapshot_size(repo.full_name, commit, repo.language, mirror_dir=mirror_dir)
        size = counted._asdict() if counted else {f: getattr(before, f) for f in SnapshotSize._fields if before}
        resolved.append(replace(repo, branch=branch, commit=commit, **size))
        variants = repo.variants
        if spec.mutation is not None:
            from mutation.mutants import ensure_mutants  # the runner only: workers bake common/ without mutation/

            mutants = ensure_mutants(repo, commit, spec.mutation, manifest, mirror_dir=mirror_dir, log=log)
            variants = [ORIGINAL_VARIANT, *mutants]
        for variant in variants:
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
    write_spec_copy(exp_dir, replace(spec, repos=resolved))
    if manifest is not None:
        names = {r.full_name for r in resolved}
        manifest["repos"] = {n: e for n, e in (manifest.get("repos") or {}).items() if n in names}
        save_mutants(exp_dir, manifest)
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


OOM = "oom"  # a stored `ok` run whose container was OOM-killed meanwhile: not a clean result (refinement 15)


def stored_status(meta: dict) -> str | None:
    """A stored run's status as the runner and the analysis read it: `ok` is demoted to `oom` when the cgroup recorded
    an OOM kill during the run (the kernel killed a child such as Gradle and the tool wrote what it had: our cap, not
    the tool's result). `--retry-failed` redoes such runs; the analysis reads no CBOM from them and lists them."""
    status = meta.get("status")
    if status == "ok" and (meta.get("oom_kills") or 0) > 0:
        return OOM
    return status


def _stored_status(meta_path: Path) -> str | None:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return stored_status(meta)


def plan_jobs(
    spec: ExperimentSpec,
    snapshots: list[Snapshot],
    exp_dir: Path,
    *,
    new_id,
    retry_failed: bool = False,
    rerun: set[str] | frozenset[str] = frozenset(),
) -> tuple[list[PlannedJob], int]:
    """Jobs still to run. Resumable: a run whose meta file exists is skipped, unless `retry_failed` and its stored
    status is not "ok" (an `ok` run with OOM kills counts as not ok), or its tool is in `rerun` (every stored run of
    that tool is redone, unsupported pairs excepted). Redone runs have their stored files removed first.
    Returns (jobs, skipped)."""
    jobs: list[PlannedJob] = []
    skipped = 0
    for snap in snapshots:
        for tool in spec.tools:
            for i in range(1, spec.repetitions + 1):
                cbom, meta = result_paths(exp_dir, snap.full_name, snap.commit, snap.variant, tool, i)
                if meta.exists():
                    status = _stored_status(meta)
                    redo = (retry_failed and status not in ("ok", UNSUPPORTED)) or (
                        tool in rerun and status != UNSUPPORTED
                    )
                    if not redo:
                        skipped += 1
                        continue
                    meta.unlink()
                    cbom.unlink(missing_ok=True)
                    cbom.with_name(f"run{i}.norm.json").unlink(missing_ok=True)  # `analysis normalize`'s output
                jobs.append(PlannedJob(job_id=new_id(), snapshot=snap, tool=tool, run_index=i))
    return jobs, skipped


UNSUPPORTED = "unsupported"


def mark_unsupported(
    spec: ExperimentSpec, snapshots: list[Snapshot], exp_dir: Path, *, registry: dict[str, dict] | None = None
) -> list[tuple[str, str]]:
    """Record (tool, repo) pairs outside the tool's language scope (common/tools.yaml `languages`, narrowed further
    by the spec's `tool_languages` for that tool, if given).

    Writes a `run<i>.meta.json` with status `unsupported` and no CBOM for every repetition that has no stored
    run yet, so `plan_jobs` skips them (also with --retry-failed) and `summarize` counts them. Returns the
    (tool, full_name) pairs recorded this time.
    """
    registry = load_tools() if registry is None else registry
    recorded: list[tuple[str, str]] = []
    for snap in snapshots:
        for tool in spec.tools:
            if tool_supports(tool, snap.language, registry, only=spec.tool_languages.get(tool)):
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
    """Persist one JobResult: the CBOM body as run<i>.json (pretty, sorted) and everything else as meta."""
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


def iter_results(exp_dir: Path, spec: ExperimentSpec | None = None):
    """Yield (meta dict, cbom path) for every stored run of the repos, tools and repetitions the experiment's spec
    copy names. `_experiment.yaml` is the one record of what an experiment covers: a run stored under an earlier
    version of the spec (a repo or tool dropped since, fewer repetitions) is never read; `--overwrite-old` removes
    them with the whole folder (remove_experiment). `spec`: the spec copy, when the caller has it loaded already."""
    spec = spec or load_spec(spec_path(exp_dir))
    repos, tools = {r.full_name for r in spec.repos}, set(spec.tools)
    for meta_path in sorted(exp_dir.glob("*/*/*/run*.meta.json")):
        snapshot, tool = meta_path.parts[-4], meta_path.parts[-2]
        k = meta_path.name[len("run") : -len(".meta.json")]
        if repo_full_name(snapshot) not in repos or tool not in tools or not k.isdigit() or int(k) > spec.repetitions:
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        yield meta, meta_path.with_name(meta_path.name.replace(".meta.json", ".json"))


def spec_to_dict(spec: ExperimentSpec) -> dict:
    return {
        "name": spec.name,
        "description": spec.description,
        "tools": list(spec.tools),
        "repetitions": spec.repetitions,
        **({"tool_languages": {t: list(ls) for t, ls in spec.tool_languages.items()}} if spec.tool_languages else {}),
        "repos": [{k: v for k, v in asdict(r).items() if v not in (None, [], "")} for r in spec.repos],
        **({"mutation": spec.mutation.to_dict()} if spec.mutation is not None else {}),
    }
