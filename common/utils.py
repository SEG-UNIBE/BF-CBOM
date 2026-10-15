import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from .config import AVAILABLE_WORKERS
from .models import RepoInfo

logger = logging.getLogger(__name__)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return default


def repo_dict_to_info(repo: dict) -> RepoInfo:
    full = (repo.get("full_name") or "").strip()
    git_url = repo.get("clone_url") or repo.get("git_url") or (f"https://github.com/{full}.git" if full else "")
    # An explicit branch (CLI config, experiment variant) wins over the GitHub default branch.
    branch = repo.get("branch") or repo.get("default_branch") or "main"
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
        commit=(repo.get("commit") or None),
    )


def get_project_version_from_toml(toml_path="pyproject.toml") -> str:
    try:
        import toml

        data = toml.load(toml_path)
        return data["project"]["version"]
    except Exception:
        return "?"


def _date_days_ago(days):
    dt = datetime.now(UTC) - timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def github_search(language, stars_min, stars_max, days_since_commit, sample_size=10, github_token=None):
    # Build query
    query = f"language:{language} stars:{stars_min}..{stars_max} pushed:>={_date_days_ago(days_since_commit)}"
    url = f"https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page={sample_size}"
    headers = {}
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code == 200:
        items = resp.json().get("items", [])
        # Ensure uniqueness by full_name
        unique = {}
        for repo in items:
            unique[repo["full_name"]] = {
                "full_name": repo["full_name"],
                "git_url": repo["clone_url"],
                "branch": repo.get("default_branch", "main"),
                "stars": repo["stargazers_count"],
                "last_commit": repo["pushed_at"],
                "language": repo["language"],
                "size_kb": repo.get("size", 0),
            }
        return list(unique.values())[:sample_size]
    else:
        logger.error("GitHub API error: %s", resp.text)
        return []


def check_git_installed():
    """
    Checks if Git is installed and accessible in the system's PATH.

    Tries PATH first; if not found, attempts common absolute locations and
    amends PATH accordingly to make subsequent subprocess calls work.

    Returns:
        bool: True if Git is found, False otherwise.
    """
    if shutil.which("git") is not None:
        return True

    # Fallback: look for common absolute paths and patch PATH if present
    try:
        candidates = [
            "/usr/bin/git",
            "/usr/local/bin/git",
            "/bin/git",
        ]
        for abs_git in candidates:
            if os.path.exists(abs_git):
                # Prepend directory to PATH so `git` resolves normally
                git_dir = os.path.dirname(abs_git)
                cur = os.environ.get("PATH", "")
                if git_dir not in cur.split(os.pathsep):
                    os.environ["PATH"] = f"{git_dir}{os.pathsep}{cur}" if cur else git_dir
                if shutil.which("git") is not None:
                    return True
    except Exception:
        pass

    logger.error("'git' is not installed or not found in PATH")
    return False


_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _redact_url(url: str) -> str:
    """Strip user:token@ credentials from an HTTPS URL before logging it."""
    return re.sub(r"https://[^@/]+@", "https://", str(url))


def _git_env() -> dict:
    env = os.environ.copy()
    # Never prompt for credentials: public repos only, fail fast otherwise.
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GCM_INTERACTIVE", "Never")
    env.setdefault("GIT_ASKPASS", "echo")
    return env


def _run_git(args: list[str], *, timeout: float | None = None) -> tuple[int, str]:
    """Run a git command; return (returncode, tail of combined output)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=_git_env(),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"git {' '.join(args[:2])} timed out after {timeout}s"
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return proc.returncode, "\n".join(lines[-15:])


def resolve_remote_commit(git_url: str, ref: str, timeout: float = 60.0) -> str | None:
    """Resolve a branch or tag to its commit SHA with `git ls-remote` (any host, no token needed).

    A full SHA passed as `ref` is returned unchanged. Returns None when the ref is unknown or
    the remote is unreachable.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    if len(ref) == 40 and _SHA_RE.match(ref):
        return ref
    rc, out = _run_git(["ls-remote", git_url, f"refs/heads/{ref}", f"refs/tags/{ref}"], timeout=timeout)
    if rc != 0:
        logger.warning("ls-remote failed for %s %s: %s", _redact_url(git_url), ref, out)
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and _SHA_RE.match(parts[0]):
            return parts[0]
    return None


def _clone_failure_reason(tail: str, branch: str) -> str:
    low = (tail or "").lower()
    if "remote branch" in low and "not found" in low:
        return f"branch '{branch}' not found"
    if "repository not found" in low:
        return "private or not found"
    if "authentication failed" in low or "could not read username" in low:
        return "private repo (auth required)"
    if "ssl certificate" in low or "certificate verify failed" in low:
        return "ssl certificate error"
    if "could not resolve host" in low or "timed out" in low:
        return "network error"
    if "not our ref" in low or "couldn't find remote ref" in low or "expected " in low:
        return "commit not found on remote"
    return "unknown"


def _checkout_commit(git_url: str, branch: str, commit: str, target_dir: str) -> tuple[int, str]:
    """Materialise exactly `commit` in `target_dir`.

    Fast path: fetch that single commit (GitHub and the local git server allow fetching
    reachable SHAs). Fallback: fetch the branch history and check the commit out from it.
    """
    for args in (["init", "-q", target_dir], ["-C", target_dir, "remote", "add", "origin", git_url]):
        rc, tail = _run_git(args)
        if rc != 0:
            return rc, tail
    rc, tail = _run_git(["-C", target_dir, "fetch", "-q", "--depth", "1", "origin", commit])
    if rc != 0:
        logger.info("Direct fetch of %s failed (%s); fetching branch %s instead", commit[:12], tail[-120:], branch)
        rc, tail = _run_git(["-C", target_dir, "fetch", "-q", "origin", branch])
        if rc != 0:
            return rc, tail
    rc, tail = _run_git(["-C", target_dir, "checkout", "-q", "--detach", commit])
    if rc != 0:
        return rc, tail
    rc, head = _run_git(["-C", target_dir, "rev-parse", "HEAD"])
    if rc != 0 or not head.strip().startswith(commit):
        return 1, f"checked out {head.strip()[:12]} but expected {commit[:12]}"
    return 0, ""


def clone_repo(github_url, branch="main", target_dir="repo", commit: str | None = None):
    """Check out a repository into `target_dir` and return its absolute path (None on failure).

    Without `commit`: shallow clone of exactly `branch`; no silent fallback to the default
    branch, because a scan attributed to the wrong branch is worse than a failed job.
    With `commit`: the working tree is that exact SHA (see `_checkout_commit`), so repeated
    runs and mutant variants scan identical bytes. Any existing `target_dir` is replaced.
    """
    if not check_git_installed():
        logger.error("Git is required to clone repositories but was not found")
        return None

    shown = _redact_url(github_url)
    logger.info("Cloning %s (branch %s%s)", shown, branch, f", commit {commit[:12]}" if commit else "")
    try:
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
        if commit:
            rc, tail = _checkout_commit(github_url, branch, commit, target_dir)
        else:
            rc, tail = _run_git(["clone", "-q", "--depth", "1", "-b", branch, github_url, target_dir])
        if rc != 0:
            logger.error("Git clone failed (rc=%s): %s", rc, _clone_failure_reason(tail, branch))
            if tail:
                logger.error("%s", _redact_url(tail))
            return None
        logger.info("Clone completed: %s", target_dir)
        return os.path.abspath(target_dir)
    except Exception as e:
        logger.error("Unexpected error during clone of %s: %s", shown, e)
        return None


def delete_file(file_path):
    """
    Deletes a single file from the filesystem.

    Args:
        file_path (str): The path to the file to be deleted.

    Returns:
        bool: True if the file was deleted successfully, False otherwise.
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.debug("File deleted: %s", file_path)
            return True
        else:
            # It's not an error if the file is already gone
            return True
    except OSError as e:
        logger.error("Error deleting file %s: %s", file_path, e)
        return False


def delete_directory(path):
    """
    Deletes a directory and all its contents recursively.

    Args:
        path (str): The path to the directory to be deleted.

    Returns:
        bool: True if the directory was deleted successfully, False otherwise.
    """
    try:
        if os.path.exists(path):
            shutil.rmtree(path)
            logger.debug("Directory deleted: %s", path)
            return True
        else:
            # It's not an error if the directory is already gone
            return True
    except OSError as e:
        logger.error("Error deleting directory %s: %s", path, e)
        return False


def spinner_animation(stop_event):
    """
    Displays a command-line spinner animation in a separate thread.

    The animation continues until the `stop_event` is set from another thread.

    Args:
        stop_event (threading.Event): An event object that signals the
                                      spinner to stop when set.
    """
    ANIMATION_INTERVAL = 0.2
    ANIMATION_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    i = 0
    # The loop continues as long as the event is not set
    while not stop_event.is_set():
        # Cycle through the animation frames
        frame = ANIMATION_FRAMES[i % len(ANIMATION_FRAMES)]
        # Print the spinner frame, using carriage return to overwrite the line
        print(f"\rScan in progress... {frame}", end="", flush=True)
        i += 1
        time.sleep(ANIMATION_INTERVAL)

    # Clean up the line after the spinner stops
    print("\rScan completed!        ", flush=True)


def discover_workers(workers_root=None, require_main_py: bool = True) -> list[str]:
    """
    Discover available worker names by scanning subfolders in a `./workers/` directory.

    A folder is considered a worker when it is a non-hidden directory whose name
    does not start with '_' and, by default, contains a `main.py` file.

    Args:
        workers_root: Optional path to a specific workers directory to scan.
        require_main_py: If True, only include folders that contain `main.py`.

    Returns:
        Sorted list of worker names. If no workers are found, returns a empty list.
    """

    def is_worker_dir(p: Path) -> bool:
        if not p.is_dir():
            return False
        name = p.name
        if name.startswith(".") or name.startswith("_"):
            return False
        return (p / "main.py").exists() if require_main_py else True

    names: list[str] = []
    # Resolve candidate roots: explicit, repo-root/workers, CWD/workers
    if workers_root:
        candidates = [Path(workers_root)]
    else:
        here = Path(__file__).resolve()
        repo_root = here.parents[1]  # common/ -> repo root
        candidates = [
            repo_root / "workers",
            Path.cwd() / "workers",
        ]

    for root in candidates:
        if root.exists() and root.is_dir():
            items = [p.name for p in root.iterdir() if is_worker_dir(p)]
            if items:
                names = sorted(items)
                break

    return names


def get_available_workers() -> list[str]:
    """
    Return the list of workers to expose in the UI.

    If the env var `AVAILABLE_WORKERS` is set (via common.config), use that list.
    Otherwise, fall back to auto-discovery under ./workers/.
    """
    if AVAILABLE_WORKERS:
        return AVAILABLE_WORKERS
    return discover_workers()


def normalize_json(payload: str) -> tuple[str, str | None]:
    """
    Normalize a JSON string for storage/size accounting.

    - Empty/whitespace -> returns "{}" and error "empty_json"
    - Invalid JSON -> returns "{}" and error "invalid_json"
    - Valid JSON -> returns compact string with UTF-8 preserved

    Returns: (normalized_json, error_or_none)
    """
    try:
        if payload is None or not str(payload).strip():
            return "{}", "empty_json"
        parsed = json.loads(payload)
        return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False), None
    except json.JSONDecodeError:
        return "{}", "invalid_json"
    except Exception:
        return "{}", "exception"


# ----- Status labels (centralized) -----

# Structured status metadata
_STATUS_META = {
    "completed": {"emoji": "✅", "label": "Completed", "color": "#2ca02c"},  # green
    "failed": {"emoji": "💥", "label": "Failed", "color": "#d62728"},  # red
    "timeout": {"emoji": "⏰", "label": "Timeout", "color": "#9467bd"},  # purple
    "cancelled": {"emoji": "🚫", "label": "Cancelled", "color": "#7f7f7f"},  # gray
    "pending": {"emoji": "💤", "label": "Pending", "color": "#1f77b4"},  # blue
}


def get_status_meta() -> dict[str, dict]:
    """Return a copy of status -> {emoji, label}."""
    return {k: v.copy() for k, v in _STATUS_META.items()}


def get_status_keys_order() -> list[str]:
    """Preferred key order for statuses."""
    return ["completed", "failed", "timeout", "cancelled", "pending"]


def status_text(key: str, mode: str = "both") -> str:
    """
    Render a status as text.
    - mode='emoji' -> just the emoji
    - mode='label' -> just the label
    - mode='both'  -> "<emoji> <label>"
    Unknown key yields ''.
    """
    meta = _STATUS_META.get(key)
    if not meta:
        return ""
    if mode == "emoji":
        return meta["emoji"]
    if mode == "label":
        return meta["label"]
    return f"{meta['emoji']} {meta['label']}"


# Backwards-compat helpers used by existing pages
def get_status_labels() -> dict[str, str]:
    """Return mapping of key -> "<emoji> <label>" (compat)."""
    return {k: status_text(k, "both") for k in _STATUS_META}


def get_status_order() -> list[str]:
    """Return list of "<emoji> <label>" in preferred order (compat)."""
    return [status_text(k, "both") for k in get_status_keys_order()]


def get_status_emoji(key: str) -> str:
    """Return emoji for status key."""
    return status_text(key, "emoji")


# ----- Repo formatting helpers -----


def format_repo_identifier(repo: dict) -> str:
    """Return the canonical repo identifier, e.g., 'owner/name'."""
    try:
        return (repo.get("full_name") or "").strip()
    except Exception:
        return ""


def format_repo_info(repo: dict) -> str:
    """
    Compose a short info string with language, stars, and size (KB).
    Example: '💬 Python · ⭐1,234 · 💾20,000 KB · 🔒 2dbd821'
    """
    parts: list[str] = []

    # Language
    try:
        lang = repo.get("language") or repo.get("main_language")
        if lang:
            parts.append(f"💬 {str(lang)}")
    except Exception:
        pass

    # Stars - check multiple field names and coerce to int
    try:
        stars = repo.get("stargazers_count") or repo.get("stars")
        if stars is not None:
            stars_int = _coerce_int(stars, default=0)
            if stars_int > 0:
                parts.append(f"⭐{stars_int:,}")
    except Exception:
        pass

    # Size - check multiple field names and coerce to int
    try:
        size_kb = repo.get("size") or repo.get("size_kb")
        if size_kb is not None:
            size_int = _coerce_int(size_kb, default=0)
            if size_int > 0:
                parts.append(f"💾{size_int:,} KB")
    except Exception:
        pass

    # Pinned commit (resolved at inspection start)
    commit = repo.get("commit")
    if isinstance(commit, str) and len(commit) >= 7:
        parts.append(f"🔒 {commit[:7]}")

    return " · ".join(parts) if parts else "—"


def repo_html_url(repo: dict) -> str:
    """Return the https://github.com/<full_name> URL for a repo dict."""
    try:
        url = repo.get("html_url")
        if url:
            return url
        full = (repo.get("full_name") or "").strip()
        return f"https://github.com/{full}" if full else ""
    except Exception:
        return ""
