"""Read-only access to the files of a pinned snapshot, for `file_exists` and the traceability check (RQ1).

Two places can hold a snapshot's objects, both bare git repositories:
- the git server's mirror on the VM (`GIT_MIRROR_DIR`, `common/experiment.py:mirror_path`), present wherever the
  experiment ran;
- a local **snapshot cache** (`SNAPSHOT_CACHE_DIR`, default `~/.cache/bf-cbom/snapshots`) for a copied export on the
  laptop: `fetch` pulls one commit from the repo's `source_url` (`git fetch --depth 1`), opt-in (`--fetch`), once.

Without either, `files` and `read` return None and the analysis reports the columns as unavailable.

Files are read through one long-lived `git cat-file --batch` process per bare repository (one request per file on
its stdin, no fork): a `git show` per file costs ~16 ms of process start against ~0.4 ms this way, and a report reads
every located record's file twice (the traceability check and the records' places). A file is read as a checkout
shows it, which is what the tools scanned: a symlink inside the repository as its target (`--follow-symlinks`; read
as the link's one-line text, Aegis's linked test vectors had their tools' lines past the end, 2026-10-31), and its
lines numbered at `\\n` only, as git and the tools number them (`split_lines`).
"""

from __future__ import annotations

import os
import subprocess
from collections import OrderedDict
from pathlib import Path

from common.experiment import GIT_MIRROR_DIR, RepoSpec, mirror_path, publish_fixture

SNAPSHOT_CACHE_DIR = Path(os.getenv("SNAPSHOT_CACHE_DIR", str(Path.home() / ".cache" / "bf-cbom" / "snapshots")))
_CACHE_BYTES = 64 * 2**20  # split lines kept in memory, summed over files: a bound by count let 512 bundled
# megabyte files of one repository (MaxKey's front-end) exceed the runner's 2 GB on 2026-10-23


_LINK_REPLIES = (b"symlink", b"dangling", b"loop", b"notdir")  # --follow-symlinks: a link it cannot resolve in the tree


def split_lines(text: str) -> list[str]:
    """A file's lines as git and the tools number them: split at `\\n` only, a trailing `\\r` dropped. `str.splitlines`
    also splits at a form feed, a lone `\\r` or `\\u2028`, which shifts every later line against the tools' numbers."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


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
    def __init__(
        self,
        mirror_dir: Path = GIT_MIRROR_DIR,
        cache_dir: Path = SNAPSHOT_CACHE_DIR,
        *,
        cache_bytes: int = _CACHE_BYTES,
    ):
        self.mirror_dir, self.cache_dir = Path(mirror_dir), Path(cache_dir)
        self._repos: dict[tuple[str, str], tuple[Path, str] | None] = {}
        self._files: dict[tuple[str, str], frozenset[str] | None] = {}
        self._cache_bytes, self._cached_bytes = cache_bytes, 0
        self._lines: OrderedDict[tuple[str, str, str], tuple[list[str] | None, int]] = OrderedDict()  # (lines, size)
        self._batches: dict[Path, subprocess.Popen] = {}  # one `git cat-file --batch` per bare repo

    def close(self) -> None:
        """End the cat-file processes (also on garbage collection)."""
        for proc in self._batches.values():
            if proc.poll() is None:
                proc.stdin.close()
                proc.wait(timeout=10)
        self._batches.clear()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001  interpreter shutdown: nothing to report
            pass

    def _cat(self, bare: Path, spec: str) -> bytes | None:
        """One blob (`<commit>:<path>`) through the bare repo's `git cat-file --batch --follow-symlinks` process,
        started on first use: a symlink inside the tree reads as its target; None when the object is missing, no blob
        (a directory) or a link that leaves the tree or resolves to nothing. A broken process is dropped and
        restarted."""
        proc = self._batches.get(bare)
        if proc is None or proc.poll() is not None:
            proc = subprocess.Popen(
                ["git", "-C", str(bare), "cat-file", "--batch", "--follow-symlinks"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._batches[bare] = proc
        try:
            proc.stdin.write(spec.encode("utf-8", errors="surrogateescape") + b"\n")
            proc.stdin.flush()
            line = proc.stdout.readline().rstrip(b"\n")
            if line.endswith((b" missing", b" ambiguous")):  # `<spec> missing`; the path may hold spaces
                return None
            kind, _, size = line.partition(b" ")
            if kind in _LINK_REPLIES:  # `<kind> <size>`, then the link's text and a newline
                proc.stdout.read(int(size) + 1)
                return None
            header = line.split()
            if len(header) != 3:
                return None
            data = proc.stdout.read(int(header[2]))
            proc.stdout.read(1)  # the newline after the content
            return data if header[1] == b"blob" else None
        except (OSError, ValueError):
            proc.kill()
            self._batches.pop(bare, None)
            return None

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

    def publish_fixture(self, full_name: str, directory: Path, commit: str, *, log=None) -> bool:
        """Make the snapshot cache hold a fixture (`path:` in the spec) from its local directory, committed exactly as
        the runner published it (common/experiment.py:publish_fixture is deterministic); True when that gives
        `commit`, else the directory changed since the run and the fixture stays unavailable."""
        sha = publish_fixture(RepoSpec(full_name=full_name, path=str(directory)), bare=self.cache_path(full_name))
        self._repos.pop((full_name, commit), None)
        if sha != commit and log:
            log(f"  {full_name}: {directory} gives commit {sha[:12]}, the experiment ran {commit[:12]} (changed since)")
        return sha == commit

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

    def _text(self, full_name: str, commit: str | None, path: str) -> str | None:
        """The file's text at the snapshot, uncached (undecodable bytes replaced), or None when absent."""
        found = self.repo(full_name, commit)
        if found is None or "\n" in path:
            return None
        data = self._cat(found[0], f"{commit}:{path}")
        return data.decode("utf-8", errors="replace") if data is not None else None

    def lines(self, full_name: str, commit: str | None, path: str) -> list[str] | None:
        """The file's lines at the snapshot, or None when absent; kept least-recently-used within `cache_bytes` (a
        file above the whole budget is read and not kept)."""
        key = (full_name, commit or "", path)
        if key in self._lines:
            self._lines.move_to_end(key)
            return self._lines[key][0]
        text = self._text(full_name, commit, path)
        lines, size = (split_lines(text), len(text)) if text is not None else (None, 0)
        if size <= self._cache_bytes:
            self._lines[key] = (lines, size)
            self._cached_bytes += size
            while self._cached_bytes > self._cache_bytes:
                _, (_, dropped) = self._lines.popitem(last=False)
                self._cached_bytes -= dropped
        return lines

    def read(self, full_name: str, commit: str | None, path: str) -> str | None:
        """The file's text at the snapshot (its `lines` joined), or None when absent."""
        lines = self.lines(full_name, commit, path)
        return None if lines is None else "\n".join(lines)
