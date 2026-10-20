"""A pinned commit's files, read from the bare mirror without a checkout, and what a project already declares.

`Tree` lists the commit's blobs (`git ls-tree -r -l`) and reads them in one `git cat-file --batch`. `declared` answers
whether a snippet's `requires` holds for the project a file belongs to: Python's `cryptography` from the nearest
dependency file above it (pyproject.toml, requirements*.txt, setup.py, setup.cfg, Pipfile), Go's version and
`golang.org/x/crypto` from the nearest go.mod. Dependency files are only read, never edited (decided 2026-10-20).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REGULAR_MODES = ("100644", "100755")
PY_DEPENDENCY_FILES = ("pyproject.toml", "setup.cfg", "setup.py", "Pipfile")
_VERSION = re.compile(r"(\d+(?:\.\d+)*)")


class SourceError(RuntimeError):
    pass


def _git(bare: Path, *args: str, data: bytes | None = None) -> bytes:
    proc = subprocess.run(["git", "-C", str(bare), *args], input=data, capture_output=True)
    if proc.returncode != 0:
        raise SourceError(f"git {' '.join(args[:2])} failed: {proc.stderr.decode(errors='replace').strip()[-300:]}")
    return proc.stdout


@dataclass
class Blob:
    path: str
    mode: str
    sha: str
    size: int


@dataclass
class Tree:
    """The regular files of `commit` in the bare repo `bare`; `read` caches what it fetched."""

    bare: Path
    commit: str
    blobs: dict[str, Blob] = field(default_factory=dict)
    _cache: dict[str, bytes] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, bare: Path, commit: str) -> Tree:
        tree = cls(bare=bare, commit=commit)
        for entry in _git(bare, "ls-tree", "-r", "-l", "-z", "--full-tree", commit).split(b"\0"):
            if not entry:
                continue
            meta, _, path = entry.partition(b"\t")
            mode, kind, sha, size = meta.decode().split()
            if kind == "blob" and mode in REGULAR_MODES:
                p = path.decode("utf-8", errors="surrogateescape")
                tree.blobs[p] = Blob(p, mode, sha, int(size))
        return tree

    def read(self, paths: list[str]) -> dict[str, bytes]:
        """{path: content} for the existing `paths`, in one `git cat-file --batch` for those not cached yet."""
        missing = [p for p in paths if p in self.blobs and p not in self._cache]
        if missing:
            out = _git(
                self.bare, "cat-file", "--batch", data="".join(f"{self.blobs[p].sha}\n" for p in missing).encode()
            )
            pos = 0
            for p in missing:
                header_end = out.index(b"\n", pos)
                _, kind, size = out[pos:header_end].decode().split()
                start = header_end + 1
                self._cache[p] = out[start : start + int(size)]
                pos = start + int(size) + 1
        return {p: self._cache[p] for p in paths if p in self._cache}

    def text(self, path: str) -> str | None:
        """UTF-8 text of `path` (None when missing or not UTF-8)."""
        data = self.read([path]).get(path)
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def dir_files(self, directory: str) -> list[str]:
        """Paths directly in `directory` ("" = the root)."""
        return sorted(p for p in self.blobs if str(PurePosixPath(p).parent) == (directory or "."))


def _parents(path: str) -> list[str]:
    """`a/b/c.py` -> ["a/b", "a", ""]: the directories from the file's own up to the root."""
    parts = PurePosixPath(path).parent.parts
    return ["/".join(parts[:i]) for i in range(len(parts), -1, -1)]


def _join(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def _vtuple(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split("."))


# ----- Python: is `cryptography` declared? -----

_PY_REQ = re.compile(
    r"""(?mi)(?:["']|^\s*)cryptography(?![\w.-])\s*(?:\[[^\]]*\])?\s*(?P<spec>(?:[<>=!~^]=?\s*[\d.*]+\s*,?\s*)*)"""
)
_PY_TOML_KEY = re.compile(r"""(?mi)^\s*cryptography\s*=\s*(?P<spec>"[^"]*"|'[^']*'|\{[^}]*\})""")  # Poetry, Pipfile


def python_project_files(tree: Tree, path: str) -> list[str]:
    """The dependency files of the project `path` belongs to: those in the nearest directory above it that has any
    (requirements/*.txt next to them count too)."""
    for d in _parents(path):
        names = [_join(d, n) for n in PY_DEPENDENCY_FILES]
        names += [p for p in tree.dir_files(d) if PurePosixPath(p).name.startswith("requirements")]
        names += [p for p in tree.dir_files(_join(d, "requirements")) if p.endswith(".txt")]
        found = [p for p in names if p in tree.blobs]
        if found:
            return sorted(set(found))
    return []


def _spec_allows(spec: str, minimum: str) -> bool:
    """False when a version specifier pins or caps below `minimum` (`==2.9`, `<3.0`, `^2.0`); True otherwise."""
    if not minimum:
        return True
    low = _vtuple(minimum)
    for op, ver in re.findall(r"(==|<=|<|\^|~=|~)\s*(\d+(?:\.\d+)*)", spec):
        v = _vtuple(ver)
        if op == "==" and v < low:
            return False
        if op in ("<", "<=") and v <= low:
            return False
        if op in ("^", "~", "~=") and v[0] < low[0]:
            return False
    return True


def python_declares(tree: Tree, path: str, package: str, minimum: str = "") -> bool:
    """Whether the project of `path` declares `package` (only `cryptography` is needed) in a version that allows
    `minimum`. Optional extras count: the project's own code may use what it declares there."""
    if package != "cryptography":
        raise SourceError(f"unknown Python requirement {package!r}")
    for f in python_project_files(tree, path):
        text = tree.text(f) or ""
        matches = list(_PY_TOML_KEY.finditer(text)) + list(_PY_REQ.finditer(text))
        if matches:
            return all(_spec_allows(m.group("spec") or "", minimum) for m in matches)
    return False


# ----- Go: go.mod -----


@dataclass(frozen=True)
class GoModule:
    path: str  # go.mod's path in the repo
    go: tuple[int, ...] | None
    requires: frozenset[str]


def go_module(tree: Tree, path: str) -> GoModule | None:
    """The nearest go.mod above `path`: its `go` version and required module paths."""
    for d in _parents(path):
        mod = _join(d, "go.mod")
        if mod in tree.blobs:
            text = tree.text(mod) or ""
            m = re.search(r"(?m)^go\s+(\d+(?:\.\d+)*)", text)
            requires: set[str] = set()
            for block in re.findall(r"(?ms)^require\s*\((.*?)^\)", text):
                requires |= {
                    ln.split()[0] for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("//")
                }
            requires |= set(re.findall(r"(?m)^require\s+([^\s(]+)\s", text))
            return GoModule(mod, _vtuple(m.group(1)) if m else None, frozenset(requires))
    return None


def go_satisfies(module: GoModule | None, requires: dict) -> bool:
    if module is None:
        return not requires
    for key, value in requires.items():
        if key == "go":
            if module.go is None or module.go < _vtuple(value):
                return False
        elif key not in module.requires:
            return False
    return True


def satisfied(tree: Tree, language: str, path: str, requires: dict) -> bool:
    """Whether the project of `path` already has everything a snippet form `requires`."""
    if not requires:
        return True
    if language == "python":
        return all(python_declares(tree, path, pkg, minimum) for pkg, minimum in requires.items())
    if language == "go":
        return go_satisfies(go_module(tree, path), requires)
    return False  # Java snippets are JCA only and require nothing
