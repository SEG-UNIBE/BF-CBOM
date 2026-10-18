"""Where a reported location points: repo-relative paths and the source-code extensions per language."""

from __future__ import annotations

# Primary-source extensions per language; a record in another file is "out of scope" (configs, PEM, docs).
CODE_EXT: dict[str, tuple[str, ...]] = {
    "java": (".java", ".kt", ".scala", ".groovy"),
    "python": (".py",),
    "go": (".go",),
    "c": (".c", ".h", ".cc", ".cpp", ".hpp"),
    "javascript": (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"),
}
CODE_EXT["typescript"] = CODE_EXT["javascript"]


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
