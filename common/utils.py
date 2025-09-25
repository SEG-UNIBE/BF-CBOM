import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from tqdm import tqdm

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


def get_project_version_from_toml(toml_path="pyproject.toml") -> str:
    try:
        import toml

        data = toml.load(toml_path)
        return data["project"]["version"]
    except Exception:
        return "?"


def _date_days_ago(days):
    dt = datetime.utcnow() - timedelta(days=days)
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

    print(
        "Error: 'git' is not installed or not found in your system's PATH.",
        file=sys.stderr,
    )
    return False


def clone_repo(github_url, branch="main", target_dir="repo"):
    """
    Clones a GitHub repository with detailed progress bars for different clone stages.

    This function uses `subprocess` to run the 'git clone' command and captures
    its stderr stream to parse progress information, which is then displayed
    using `tqdm` progress bars.

    Args:
        github_url (str): The HTTPS or SSH URL of the GitHub repository.
        branch (str): The name of the branch to clone. Defaults to "main".
        target_dir (str): The local directory to clone the repository into.
                          This directory will be deleted if it already exists.

    Returns:
        Optional[str]: The absolute path to the cloned repository on success,
                       or None if an error occurs.
    """
    if not check_git_installed():
        print(
            "Git not installed! Git is required to clone repositories.",
            file=sys.stderr,
        )
        return None

    # Avoid leaking tokens if present in URL.
    display_url = re.sub(r"https://[^@]+@github.com/", "https://github.com/", str(github_url))
    print(f"Starting clone: {display_url} (branch: {branch})")
    try:
        # Clean up the target directory before cloning
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)

        # Prepare env to avoid credential prompts hanging the process
        env = os.environ.copy()
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GCM_INTERACTIVE", "Never")
        env.setdefault("GIT_ASKPASS", "echo")
        # Public repos only: do NOT inject tokens. If auth is required, we will fail cleanly.
        auth_url = github_url

        def _run_clone(cmd: list[str]) -> tuple[int, str]:
            # Start the git process, redirecting stderr to stdout to capture all output
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Capture progress info from stderr
                text=True,
                bufsize=1,  # Line-buffered
                env=env,
            )

            # Regex patterns to parse progress percentages from git's output
            patterns = {
                "Counting": re.compile(r"Counting objects:\s+(\d+)%"),
                "Compressing": re.compile(r"Compressing objects:\s+(\d+)%"),
                "Receiving": re.compile(r"Receiving objects:\s+(\d+)%"),
                "Resolving": re.compile(r"Resolving deltas:\s+(\d+)%"),
                "Updating files": re.compile(r"Updating files:\s+(\d+)%"),
            }
            progress_bars = {key: None for key in patterns}
            last_lines: list[str] = []

            # Read git's output line by line
            for line in process.stdout:
                line = (line or "").rstrip()
                if line:
                    # Keep tail for debugging
                    last_lines = (last_lines + [line])[-15:]
                matched = False
                for key, pattern in patterns.items():
                    match = pattern.search(line)
                    if match:
                        percent = int(match.group(1))
                        bar = progress_bars.get(key)
                        if bar is None:
                            bar = tqdm(total=100, desc=key.ljust(12), leave=False)
                            progress_bars[key] = bar
                        bar.update(percent - bar.n)
                        matched = True
                        break
                if not matched and line:
                    print(line)

            process.wait()

            # Ensure all progress bars are closed and show 100%
            for bar in progress_bars.values():
                if bar:
                    bar.update(100 - bar.n)
                    bar.close()
            return process.returncode, "\n".join(last_lines)

        # Try with requested branch (shallow)
        attempts = []
        cmd_with_branch = [
            "git",
            "clone",
            "--progress",
            "--depth",
            "1",
            "-b",
            branch,
            auth_url,
            target_dir,
        ]
        rc, tail = _run_clone(cmd_with_branch)
        attempts.append((rc, tail))

        # Fallback: clone default branch (shallow) when branch is missing or other errors occur
        if rc != 0:
            # Clean up target dir before retry
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)
            cmd_default = [
                "git",
                "clone",
                "--progress",
                "--depth",
                "1",
                auth_url,
                target_dir,
            ]
            rc, tail = _run_clone(cmd_default)
            attempts.append((rc, tail))

            # If default succeeded and a non-default branch was requested, try checking it out
            if rc == 0 and branch:
                try:
                    subprocess.run(
                        ["git", "-C", target_dir, "checkout", "-q", branch],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                    )
                except Exception:
                    pass

        if rc != 0:
            # Derive friendlier reason; still print tail for diagnostics
            reason = "unknown"
            low = (tail or "").lower()
            if "repository not found" in low:
                reason = "private or not found"
            elif "authentication failed" in low or "could not read username" in low:
                reason = "private repo (auth required)"
            elif "ssl certificate" in low or "certificate verify failed" in low:
                reason = "ssl certificate error"
            elif "could not resolve host" in low or "timed out" in low:
                reason = "network error"
            print(f"Error: Git clone failed (rc=128): {reason}", file=sys.stderr)
            if tail:
                safe_tail = re.sub(r"https://[^@]+@github.com/", "https://github.com/", tail)
                print(safe_tail, file=sys.stderr)
            return None

        print("\nClone completed successfully!")
        return os.path.abspath(target_dir)

    except Exception as e:
        print(f"An unexpected error occurred during clone: {e}", file=sys.stderr)
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
            print(f"File deleted: {file_path}")
            return True
        else:
            # It's not an error if the file is already gone
            return True
    except OSError as e:
        print(f"Error deleting file {file_path}: {e}", file=sys.stderr)
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
            print(f"Directory deleted: {path}")
            return True
        else:
            # It's not an error if the directory is already gone
            return True
    except OSError as e:
        print(f"Error deleting directory {path}: {e}", file=sys.stderr)
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
    Example: '💬 Python · ⭐1,234 · 💾20,000 KB'
    """
    parts: list[str] = []
    try:
        lang = repo.get("language")
        if lang:
            parts.append(f"💬 {str(lang)}")
    except Exception:
        pass
    try:
        stars = repo.get("stargazers_count")
        if isinstance(stars, int | float):
            parts.append(f"⭐{int(stars):,}")
    except Exception:
        pass
    try:
        size_kb = repo.get("size") or repo.get("size_kb")
        if isinstance(size_kb, int | float):
            parts.append(f"💾{int(size_kb):,} KB")
    except Exception:
        pass
    return " · ".join(parts)


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
