"""Read-only access to the files of a pinned snapshot, for `file_exists` and the traceability check (RQ1).

Two places can hold a snapshot's objects, both bare git repositories:
- the git server's mirror on the VM (`GIT_MIRROR_DIR`, `common/experiment.py:mirror_path`), present wherever the
  experiment ran;
- a local **snapshot cache** (`SNAPSHOT_CACHE_DIR`, default `~/.cache/bf-cbom/snapshots`) for a copied export on the
  laptop: `fetch` pulls one commit from the repo's `source_url` (`git fetch --depth 1`), opt-in (`--fetch`), once.

Without either, `files` and `read` return None and the analysis reports the columns as unavailable.
"""

from __future__ import annotations

import os
import subprocess
from collections import OrderedDict
from pathlib import Path

from common.experiment import GIT_MIRROR_DIR, mirror_path

SNAPSHOT_CACHE_DIR = Path(os.getenv("SNAPSHOT_CACHE_DIR", str(Path.home() / ".cache" / "bf-cbom" / "snapshots")))
_READ_CACHE = 512  # files kept in memory


def _git(args: list[str], *, timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _has_commit(bare: Path, commit: str) -> bool:
    if not (bare / "HEAD").exists():
        return False
    return _git(["-C", str(bare), "cat-file", "-e", f"{commit}^{{commit}}"]).returncode == 0


class SnapshotStore:
    def __init__(self, mirror_dir: Path = GIT_MIRROR_DIR, cache_dir: Path = SNAPSHOT_CACHE_DIR):
        self.mirror_dir, self.cache_dir = Path(mirror_dir), Path(cache_dir)
        self._repos: dict[tuple[str, str], tuple[Path, str] | None] = {}
        self._files: dict[tuple[str, str], frozenset[str] | None] = {}
        self._texts: OrderedDict[tuple[str, str, str], str | None] = OrderedDict()

    def cache_path(self, full_name: str) -> Path:
        return self.cache_dir / (full_name.replace("/", "__") + ".git")

    def repo(self, full_name: str, commit: str | None) -> tuple[Path, str] | None:
        """(bare repo, 'mirror' | 'cache') holding `commit`, or None."""
        if not commit:
            return None
        key = (full_name, commit)
        if key not in self._repos:
            self._repos[key] = None
            candidates = ((mirror_path(full_name, self.mirror_dir), "mirror"), (self.cache_path(full_name), "cache"))
            for bare, source in candidates:
                if _has_commit(bare, commit):
                    self._repos[key] = (bare, source)
                    break
        return self._repos[key]

    def fetch(self, full_name: str, source_url: str, commit: str, *, log=None) -> bool:
        """Make the snapshot cache hold `commit` of `source_url` (one shallow fetch); True when it does afterwards."""
        if self.repo(full_name, commit):
            return True
        bare = self.cache_path(full_name)
        if not (bare / "HEAD").exists():
            bare.mkdir(parents=True, exist_ok=True)
            if _git(["init", "--bare", "--quiet", str(bare)]).returncode != 0:
                return False
        if log:
            log(f"  fetching {full_name}@{commit[:12]} from {source_url}")
        proc = _git(["-C", str(bare), "fetch", "--quiet", "--depth", "1", source_url, commit], timeout=1800)
        if proc.returncode != 0 and log:
            log(f"  {full_name}: fetch failed: {proc.stderr.strip()[-200:]}")
        self._repos.pop((full_name, commit), None)
        return self.repo(full_name, commit) is not None

    def source(self, full_name: str, commit: str | None) -> str | None:
        found = self.repo(full_name, commit)
        return found[1] if found else None

    def files(self, full_name: str, commit: str | None) -> frozenset[str] | None:
        """Paths of all files of the snapshot, or None when no store holds it."""
        key = (full_name, commit or "")
        if key not in self._files:
            found = self.repo(full_name, commit)
            if found is None:
                self._files[key] = None
            else:
                proc = _git(["-C", str(found[0]), "ls-tree", "-r", "--name-only", "-z", commit or ""], timeout=120)
                self._files[key] = frozenset(p for p in proc.stdout.split("\0") if p) if proc.returncode == 0 else None
        return self._files[key]

    def read(self, full_name: str, commit: str | None, path: str) -> str | None:
        """The file's text at the snapshot (undecodable bytes replaced), or None when absent."""
        key = (full_name, commit or "", path)
        if key in self._texts:
            self._texts.move_to_end(key)
            return self._texts[key]
        text = None
        found = self.repo(full_name, commit)
        if found is not None:
            proc = subprocess.run(
                ["git", "-C", str(found[0]), "show", f"{commit}:{path}"],
                capture_output=True,
                timeout=120,
                check=False,
            )
            if proc.returncode == 0:
                text = proc.stdout.decode("utf-8", errors="replace")
        self._texts[key] = text
        if len(self._texts) > _READ_CACHE:
            self._texts.popitem(last=False)
        return text
