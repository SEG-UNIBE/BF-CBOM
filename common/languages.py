"""Source-code extensions per language key (the spellings of `common/tools.yaml`), shared by the runner and the
analysis: the runner counts a snapshot's source files with them into the record (`files`, `source_files`), the
analysis decides a record's `in_scope` by them and scales run times by the count."""

from __future__ import annotations

from collections.abc import Iterable

# Primary-source extensions per language; a record in another file is "outside code" (configs, PEM, docs).
CODE_EXT: dict[str, tuple[str, ...]] = {
    "java": (".java", ".kt", ".scala", ".groovy"),
    "python": (".py",),
    "go": (".go",),
    "c": (".c", ".h", ".cc", ".cpp", ".hpp"),
    "javascript": (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"),
}
CODE_EXT["typescript"] = CODE_EXT["javascript"]


def source_files(paths: Iterable[str], language: str | None) -> int:
    """How many of `paths` are source files of `language`; every path when the language is unknown."""
    exts = CODE_EXT.get((language or "").lower())
    return sum(1 for p in paths if exts is None or p.lower().endswith(exts))
