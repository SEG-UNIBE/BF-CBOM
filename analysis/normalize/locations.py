"""Where a reported location points: repo-relative paths, and the source-code extensions per language
(`common/languages.py`, shared with the runner, which counts a snapshot's source files into the record)."""

from __future__ import annotations

from common.languages import CODE_EXT  # noqa: F401  re-exported for the normaliser and the analysis CLI


def relative_location(location: str, *, repo_markers: tuple[str, ...] = ("/repo/",)) -> str:
    """Repo-relative path: strip `./`, and for absolute paths cut after the first marker (e.g. `/repo/`)."""
    loc = str(location).strip().replace("\\", "/")
    for marker in repo_markers:
        idx = loc.find(marker)
        if idx >= 0:
            return loc[idx + len(marker) :]
    while loc.startswith("./"):
        loc = loc[2:]
    return loc
