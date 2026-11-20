"""Snippet catalogs (mutation/catalog/<language>.yaml): known crypto calls with their expected canonical record.

A snippet has one or more forms (Go's PBKDF2: `crypto/pbkdf2` or `golang.org/x/crypto/pbkdf2`); a form carries the
imports, what the project must already declare (`requires`) and the `direct` and `indirect` templates. The `wrapper`
style is derived from `direct` (mutation/insert.py). Templates are rendered by `render`: `$name` placeholders are
resolved through a mapping, the trailing markers `@call` / `@related` are stripped and their line indices returned.
A catalog's `decoys:` (code that names or resembles cryptography without using it) load as snippets with `decoy`
set, no expected record and one `direct` form; they are kept apart in `Catalog.decoys` and share `by_id`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CATALOG_DIR = Path(__file__).resolve().parent / "catalog"
LANGUAGES = ("java", "python", "go")
STYLES = ("direct", "wrapper", "indirect")
TEMPLATE_STYLES = ("direct", "indirect")  # written in the catalog; `wrapper` is derived from `direct`
INDENT = "    "  # one indentation level in a template

PLACEHOLDER = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
MARKER = re.compile(r"\s+@(call|related)\s*$")


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Form:
    imports: tuple[str, ...]
    requires: dict  # {"cryptography": "3.1"}, {"go": "1.24"}, {"golang.org/x/crypto": ""}
    templates: dict  # style -> template text (None: the style cannot be expressed)


@dataclass(frozen=True)
class Snippet:
    id: str
    language: str
    expected: dict  # canonical record: unit (L4 identity, None for a protocol), family, parameters
    helper: str
    forms: tuple[Form, ...]
    also: tuple[str, ...] = ()  # identities the snippet necessarily contains besides `expected`
    decoy: bool = False  # a known negative: no expected record, a tool that reports it has a false positive
    what: str = ""  # a decoy's description

    @property
    def unit(self) -> str | None:
        return self.expected.get("unit")

    def supports(self, style: str) -> bool:
        source = "direct" if style == "wrapper" else style
        return any(f.templates.get(source) for f in self.forms)


@dataclass(frozen=True)
class Catalog:
    language: str
    version: str
    snippets: tuple[Snippet, ...]
    wrap: str | None = None  # Java: every body goes into this (try/catch), in the host and in a wrapper helper
    wrap_imports: tuple[str, ...] = ()
    scope: str | None = None  # Go: a body inserted into a host goes into this block (its locals stay its own)
    decoys: tuple[Snippet, ...] = ()
    path: str = ""
    by_id: dict = field(default_factory=dict, compare=False, repr=False)

    def get(self, snippet_id: str) -> Snippet:
        return self.by_id[snippet_id]


def _form(item: dict, where: str) -> Form:
    templates = {}
    for style in TEMPLATE_STYLES:
        if style not in item:
            raise CatalogError(f"{where}: missing `{style}` (null with a reason if the style cannot be expressed)")
        templates[style] = item[style] or None
    return Form(
        imports=tuple(str(i) for i in item.get("imports") or ()),
        requires={str(k): str(v or "") for k, v in (item.get("requires") or {}).items()},
        templates=templates,
    )


@lru_cache(maxsize=8)
def load_catalog(language: str, directory: Path = CATALOG_DIR) -> Catalog:
    language = language.lower()
    if language not in LANGUAGES:
        raise CatalogError(f"no snippet catalog for {language!r} (languages: {', '.join(LANGUAGES)})")
    path = directory / f"{language}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("language") != language:
        raise CatalogError(f"{path}: language must be {language!r}")
    snippets = []
    for item in data.get("snippets") or []:
        sid = str(item["id"])
        where = f"{path.name}:{sid}"
        forms = [_form(f, where) for f in item["forms"]] if item.get("forms") else [_form(item, where)]
        snippets.append(
            Snippet(
                id=sid,
                language=language,
                expected=dict(item.get("expected") or {}),
                helper=str(item.get("helper") or "cryptoHelper"),
                forms=tuple(forms),
                also=tuple(str(a) for a in item.get("also") or ()),
            )
        )
    decoys = []
    for item in data.get("decoys") or []:
        did = str(item["id"])
        if not item.get("direct"):
            raise CatalogError(f"{path.name}:{did}: a decoy needs a `direct` body")
        form = Form(
            imports=tuple(str(i) for i in item.get("imports") or ()),
            requires={},
            templates={"direct": item["direct"], "indirect": None},
        )
        decoys.append(
            Snippet(
                id=did,
                language=language,
                expected={},
                helper="",
                forms=(form,),
                decoy=True,
                what=str(item.get("what") or ""),
            )  # fmt: skip
        )
    ids = [s.id for s in (*snippets, *decoys)]
    if len(ids) != len(set(ids)):
        raise CatalogError(f"{path}: duplicate snippet ids")
    return Catalog(
        language=language,
        version=str(data.get("version") or ""),
        snippets=tuple(snippets),
        wrap=data.get("wrap"),
        wrap_imports=tuple(str(i) for i in data.get("wrap_imports") or ()),
        scope=data.get("scope"),
        decoys=tuple(decoys),
        path=str(path),
        by_id={s.id: s for s in (*snippets, *decoys)},
    )


def placeholders(template: str) -> set[str]:
    return set(PLACEHOLDER.findall(template))


@dataclass
class Rendered:
    lines: list[str]  # without line endings, indented with `indent` levels below the base indentation
    call: int | None = None  # index of the line naming the algorithm
    related: list[int] = field(default_factory=list)


def render(template: str, names: dict[str, str], *, unit: str = INDENT, base: str = "") -> Rendered:
    """Template -> lines: `$name` replaced through `names` (every placeholder must be in it), markers stripped and
    recorded, every leading four-space level replaced by `unit`, `base` prefixed to every non-empty line."""
    out = Rendered(lines=[])
    for i, raw in enumerate(template.rstrip("\n").split("\n")):
        line = raw.rstrip()
        m = MARKER.search(line)
        if m:
            line = line[: m.start()]
            if m.group(1) == "call":
                out.call = i
            else:
                out.related.append(i)
        stripped = line.lstrip(" ")
        levels, rest = divmod(len(line) - len(stripped), len(INDENT))
        line = unit * levels + " " * rest + stripped

        def sub(match: re.Match) -> str:
            key = match.group(1)
            if key not in names:
                raise CatalogError(f"unresolved placeholder ${key}")
            return names[key]

        line = PLACEHOLDER.sub(sub, line)
        out.lines.append(base + line if line else "")
    return out


def wrap_lines(outer: str, inner: Rendered, names: dict[str, str], *, unit: str = INDENT) -> Rendered:
    """Render `outer` (a template with one `$body` line) around the already rendered `inner` lines; the markers'
    indices are shifted to the combined lines."""
    out = Rendered(lines=[])
    for raw in outer.rstrip("\n").split("\n"):
        if raw.strip() == "$body":
            indent = raw[: len(raw) - len(raw.lstrip(" "))]
            levels, rest = divmod(len(indent), len(INDENT))
            prefix = unit * levels + " " * rest
            offset = len(out.lines)
            out.lines += [prefix + line if line else "" for line in inner.lines]
            out.call = None if inner.call is None else inner.call + offset
            out.related += [r + offset for r in inner.related]
        else:
            out.lines += render(raw, names, unit=unit).lines
    return out
