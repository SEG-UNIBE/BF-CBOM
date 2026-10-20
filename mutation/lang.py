"""Java, Python and Go source files for the inserter: which files and methods may host a snippet, who calls what, and
how a snippet's imports fit into the file.

Java and Go are parsed with tree-sitter, Python with its own `ast` (exact positions, docstrings and names, and the
strictest validity check there is for Python). A host is a method or function with a caller elsewhere in the
repository's first-party code (by name) or an entry point named `main`, never in test, vendor, example or generated
code (refinement 6). Every language answers the same questions (`SourceFile`): its hosts, the names it calls, how
the snippet's imports resolve (simple name, alias or fully qualified, plus the lines to add), which names a snippet
local or a helper must not take, and whether a mutated text still parses.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath

from common.corpus import NON_FIRST_PARTY

MAX_FILE_BYTES = 200_000
EXTENSIONS = {"java": ".java", "python": ".py", "go": ".go"}
_EXCLUDED_DIRS = {"java": {"target", "generated", "generated-sources"}, "python": set(), "go": {"testdata"}}
_PY_SKIP_DECORATORS = {
    "property", "cached_property", "abstractmethod", "abstractproperty", "overload", "setter", "getter", "deleter",
}  # fmt: skip
_GO_GENERATED = re.compile(r"(?m)^// Code generated .* DO NOT EDIT\.\r?$")
_GO_IGNORED = re.compile(r"(?m)^//(go:build|\s*\+build)\s+ignore\b")


class SkipHost(Exception):
    """This host cannot take the snippet (e.g. a blank import of a package the snippet needs)."""


@dataclass(frozen=True)
class Insert:
    after: int  # original line after which the lines go (0 = the top of the file)
    lines: tuple[str, ...]
    kind: str  # import | snippet | helper | call
    order: int = 0  # among inserts after the same line


@dataclass
class ImportPlan:
    names: dict[str, str]  # placeholder (the import's default binding) -> the name to write
    inserts: list[Insert] = field(default_factory=list)
    added: list[str] = field(default_factory=list)  # imports added, as written
    qualified: list[str] = field(default_factory=list)  # Java: types written fully qualified because of a clash


@dataclass
class Host:
    path: str
    name: str
    line: int  # first line of the declaration (annotations and decorators included)
    first_line: int  # line of the first body statement: the snippet goes right before it
    indent: str  # that statement's indentation
    unit: str  # one indentation level in this file
    end_line: int  # last line of the method or function
    top_line: int  # Python: first line of the module-level statement holding the host; otherwise `line`
    member_indent: str  # indentation of the declaration itself
    scope_names: frozenset[str]  # names visible at the insertion point (parameters, receiver, results; Python: all
    #                               names used in the function)
    wrapper_ok: bool  # a helper can go next to it (Java, Go: nothing else on the closing line)
    callers: int = 0
    entry: bool = False


def candidate_path(language: str, path: str) -> bool:
    """A first-party source file of `language` that may hold a host (refinement 6: no tests, vendor, examples...)."""
    p = PurePosixPath(path)
    if p.suffix != EXTENSIONS[language]:
        return False
    dirs = {d.lower() for d in p.parts[:-1]}
    if dirs & set(NON_FIRST_PARTY) or dirs & _EXCLUDED_DIRS[language] or any(d.startswith(".") for d in dirs):
        return False
    name = p.name
    if language == "python":
        return not (name.startswith("test_") or name.endswith("_test.py") or name in ("conftest.py", "setup.py"))
    if language == "go":
        return not name.endswith("_test.go")
    return name not in ("package-info.java", "module-info.java")


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _unit(outer: str, inner: str, default: str) -> str:
    return inner[len(outer) :] if inner.startswith(outer) and len(inner) > len(outer) else default


def _fresh(base: str, taken: set[str] | frozenset[str]) -> str:
    name, n = base, 2
    while name in taken:
        name, n = f"{base}{n}", n + 1
    return name


class SourceFile:
    language = ""
    default_unit = "    "

    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text
        self.lines = text.split("\n")
        self.ok = False

    def line(self, n: int) -> str:
        """1-based line `n` without its line ending."""
        return self.lines[n - 1].rstrip("\r") if 0 < n <= len(self.lines) else ""

    def called(self) -> Counter:
        raise NotImplementedError

    def hosts(self, callers: Counter) -> list[Host]:
        raise NotImplementedError

    def resolve_imports(self, imports: tuple[str, ...], host: Host, *, context: dict) -> ImportPlan:
        raise NotImplementedError

    def local_taken(self, host: Host, plan: ImportPlan) -> set[str]:
        raise NotImplementedError

    def helper_taken(self, plan: ImportPlan, *, context: dict) -> set[str]:
        raise NotImplementedError

    def parses(self, text: str) -> bool:
        raise NotImplementedError


# ----- tree-sitter (Java, Go) -----


@lru_cache(maxsize=2)
def _parser(language: str):
    import tree_sitter as ts

    if language == "java":
        import tree_sitter_java as grammar
    else:
        import tree_sitter_go as grammar
    return ts.Parser(ts.Language(grammar.language()))


def _walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


class _TreeSitterFile(SourceFile):
    comment_types: tuple[str, ...] = ()

    def __init__(self, path: str, text: str):
        super().__init__(path, text)
        self.tree = _parser(self.language).parse(text.encode("utf-8"))
        self.root = self.tree.root_node
        self.ok = not self.root.has_error

    def parses(self, text: str) -> bool:
        return not _parser(self.language).parse(text.encode("utf-8")).root_node.has_error

    def identifiers(self, types: tuple[str, ...] = ("identifier",)) -> set[str]:
        return {_text(n) for n in _walk(self.root) if n.type in types}

    def _own_line(self, node) -> bool:
        """`node` starts its line (only indentation before it)."""
        row, col = node.start_point
        return self.lines[row].encode("utf-8")[:col].strip() == b""

    def _ends_line(self, node) -> bool:
        """Nothing but a comment follows `node` on its last line."""
        row, col = node.end_point
        rest = self.lines[row].encode("utf-8")[col:].decode("utf-8", errors="replace").strip()
        return not rest or rest.startswith("//")

    def _host(self, decl, body_stmts: list, name: str, scope: set[str], callers: Counter, entry: bool) -> Host | None:
        stmts = [s for s in body_stmts if s.type not in self.comment_types]
        if not stmts or not self._own_line(stmts[0]):
            return None
        body = decl.child_by_field_name("body")
        first = stmts[0]
        if first.start_point[0] <= body.start_point[0]:
            return None  # the first statement shares the line of the opening brace
        if not callers.get(name) and not entry:
            return None
        member = _indent(self.line(decl.start_point[0] + 1))
        indent = _indent(self.line(first.start_point[0] + 1))
        return Host(
            path=self.path,
            name=name,
            line=decl.start_point[0] + 1,
            first_line=first.start_point[0] + 1,
            indent=indent,
            unit=_unit(member, indent, self.default_unit),
            end_line=decl.end_point[0] + 1,
            top_line=decl.start_point[0] + 1,
            member_indent=member,
            scope_names=frozenset(scope),
            wrapper_ok=self._own_line(decl) and self._ends_line(decl),
            callers=int(callers.get(name, 0)),
            entry=entry,
        )


# ----- Java -----


class JavaFile(_TreeSitterFile):
    language = "java"
    comment_types = ("line_comment", "block_comment")
    _DECLS = (
        "class_declaration", "interface_declaration", "enum_declaration", "record_declaration",
        "annotation_type_declaration",
    )  # fmt: skip

    def called(self) -> Counter:
        out: Counter = Counter()
        for n in _walk(self.root):
            if n.type == "method_invocation":
                out[_text(n.child_by_field_name("name"))] += 1
            elif n.type == "method_reference":
                ids = [c for c in n.named_children if c.type == "identifier"]
                if ids:
                    out[_text(ids[-1])] += 1
        return out

    def hosts(self, callers: Counter) -> list[Host]:
        out = []
        for cls in self.root.named_children:
            if cls.type != "class_declaration":
                continue
            for member in cls.child_by_field_name("body").named_children:
                if member.type != "method_declaration" or member.child_by_field_name("body") is None:
                    continue
                mods = next((c for c in member.children if c.type == "modifiers"), None)
                keywords = {c.type for c in mods.children} if mods is not None else set()
                if keywords & {"abstract", "native"}:
                    continue
                name = _text(member.child_by_field_name("name"))
                params = member.child_by_field_name("parameters")
                scope = {_text(n) for n in _walk(params) if n.type == "identifier"}
                host = self._host(
                    member,
                    member.child_by_field_name("body").named_children,
                    name,
                    scope,
                    callers,
                    entry=name == "main" and "static" in keywords,
                )
                if host:
                    out.append(host)
        return out

    def _imports(self):
        single: dict[str, str] = {}
        on_demand: set[str] = set()
        rows: list[tuple[str, int]] = []  # (imported name, row) of the non-static imports, for sorted insertion
        package_row = last_row = None
        for n in self.root.named_children:
            if n.type == "package_declaration":
                package_row = n.end_point[0]
            elif n.type == "import_declaration":
                last_row = n.end_point[0]
                if any(c.type == "static" for c in n.children):
                    continue
                target = next((c for c in n.named_children if c.type in ("scoped_identifier", "identifier")), None)
                name = re.sub(r"\s+", "", _text(target))
                rows.append((name, n.start_point[0]))
                if any(c.type == "asterisk" for c in n.children):
                    on_demand.add(name)
                elif name:
                    single[name.rsplit(".", 1)[-1]] = name
        return single, on_demand, package_row, last_row, rows

    def resolve_imports(self, imports: tuple[str, ...], host: Host, *, context: dict) -> ImportPlan:
        """A type is written by its simple name when it is imported (singly or on demand) or can be imported without
        a clash; fully qualified when the simple name is taken: imported from elsewhere, declared in the file, a type
        of the same package (a sibling `<Name>.java`) or used as a type in the file without an import of ours."""
        single, on_demand, package_row, last_row, rows = self._imports()
        declared = {_text(n.child_by_field_name("name")) for n in _walk(self.root) if n.type in self._DECLS}
        used_types = self.identifiers(("type_identifier",))
        siblings = set(context.get("siblings") or ())
        plan = ImportPlan(names={})
        new: list[str] = []
        for fqn in imports:
            package, simple = fqn.rsplit(".", 1)
            if single.get(simple) == fqn or package in on_demand:
                plan.names[simple] = simple
            elif simple in single or simple in declared or simple in siblings or simple in used_types:
                plan.names[simple] = fqn
                plan.qualified.append(fqn)
            else:
                plan.names[simple] = simple
                new.append(f"import {fqn};")
        new = sorted(set(new))
        plan.added = new
        if new:
            if last_row is not None:
                # each before the first import that sorts after it (so into its group), else after the last one
                for order, line in enumerate(new):
                    later = [row for name, row in rows if name > line[len("import ") : -1]]
                    after = min(later) if later else last_row + 1
                    plan.inserts.append(Insert(after, (line,), "import", order))
            elif package_row is not None:
                plan.inserts.append(Insert(package_row + 1, ("", *new), "import"))
            else:
                plan.inserts.append(Insert(0, (*new, ""), "import"))
        return plan

    def local_taken(self, host: Host, plan: ImportPlan) -> set[str]:
        # the snippet's locals live in its try block: only the method's parameters are in scope there
        return set(host.scope_names)

    def helper_taken(self, plan: ImportPlan, *, context: dict) -> set[str]:
        return self.identifiers()


# ----- Go -----


class GoFile(_TreeSitterFile):
    language = "go"
    comment_types = ("comment",)
    default_unit = "\t"

    def __init__(self, path: str, text: str):
        super().__init__(path, text)
        if _GO_GENERATED.search(text) or _GO_IGNORED.search(text):
            self.ok = False
        clause = next((n for n in self.root.named_children if n.type == "package_clause"), None)
        self.package = _text(clause.named_children[0]) if clause is not None and clause.named_children else ""
        self.package_row = clause.end_point[0] if clause is not None else None

    def called(self) -> Counter:
        out: Counter = Counter()
        for n in _walk(self.root):
            if n.type == "call_expression":
                fn = n.child_by_field_name("function")
                if fn is not None and fn.type == "identifier":
                    out[_text(fn)] += 1
                elif fn is not None and fn.type == "selector_expression":
                    out[_text(fn.child_by_field_name("field"))] += 1
        return out

    def top_level_names(self) -> set[str]:
        """Package-level names this file declares (functions, types, variables, constants; not methods)."""
        names: set[str] = set()
        for n in self.root.named_children:
            if n.type == "function_declaration":
                names.add(_text(n.child_by_field_name("name")))
            elif n.type in ("type_declaration", "var_declaration", "const_declaration"):
                for spec in _walk(n):
                    if spec.type in ("type_spec", "type_alias", "var_spec", "const_spec"):
                        names |= {_text(c) for c in spec.children_by_field_name("name")}
        return names

    def hosts(self, callers: Counter) -> list[Host]:
        out = []
        for decl in self.root.named_children:
            if decl.type not in ("function_declaration", "method_declaration"):
                continue
            body = decl.child_by_field_name("body")
            name = _text(decl.child_by_field_name("name"))
            if body is None or name == "init":
                continue
            stmts = next((c for c in body.named_children if c.type == "statement_list"), None)
            scope: set[str] = set()
            for field_name in ("receiver", "parameters", "result"):
                part = decl.child_by_field_name(field_name)
                if part is not None:
                    scope |= {_text(n) for n in _walk(part) if n.type == "identifier"}
            entry = name == "main" and self.package == "main" and decl.type == "function_declaration"
            host = self._host(decl, stmts.named_children if stmts else [], name, scope, callers, entry)
            if host:
                out.append(host)
        return out

    def _imports(self):
        specs = []  # (path, alias or None, row)
        groups = []
        last_row = None
        for n in self.root.named_children:
            if n.type != "import_declaration":
                continue
            last_row = n.end_point[0]
            for spec_list in (c for c in n.named_children if c.type == "import_spec_list"):
                groups.append(spec_list)
            for spec in (c for c in _walk(n) if c.type == "import_spec"):
                path = _text(spec.child_by_field_name("path")).strip('"`')
                alias = spec.child_by_field_name("name")
                specs.append((path, _text(alias) if alias is not None else None, spec.start_point[0]))
        return specs, groups, last_row

    def resolve_imports(self, imports: tuple[str, ...], host: Host, *, context: dict) -> ImportPlan:
        """A path already imported is used under its name (a blank or dot import makes the host unusable); a new one
        gets its default name unless a package-level name, another import or a name in the host's scope has it, then
        an alias (`crand` for crypto/rand). New specs go into the first import group, sorted into the run of the
        standard library or the other modules; without a group into new import lines."""
        specs, groups, last_row = self._imports()
        existing = {p: (a or p.rsplit("/", 1)[-1]) for p, a, _ in specs}
        for p, a, _ in specs:
            if p in imports and a in ("_", "."):
                raise SkipHost(f"{p} is imported as {a!r}")
        taken = set(context.get("package_names") or ()) | set(existing.values()) | set(host.scope_names)
        plan = ImportPlan(names={})
        new: list[tuple[str, str | None]] = []
        for path in imports:
            default = path.rsplit("/", 1)[-1]
            if path in existing:
                plan.names[default] = existing[path]
                continue
            name = default
            if name in taken:
                name = _fresh("c" + default if path.startswith("crypto/") else default + "pkg", taken)
            taken.add(name)
            plan.names[default] = name
            new.append((path, None if name == default else name))
        if not new:
            return plan

        def spec_text(path: str, alias: str | None) -> str:
            return f'{alias} "{path}"' if alias else f'"{path}"'

        plan.added = [spec_text(p, a) for p, a in sorted(new)]
        if groups:
            group_specs = sorted(
                (c.start_point[0], _text(c.child_by_field_name("path")).strip('"`'))
                for c in groups[0].named_children
                if c.type == "import_spec"
            )
            runs: list[list[tuple[int, str]]] = []
            for row, path in group_specs:
                between = self.lines[runs[-1][-1][0] + 1 : row] if runs else []
                if runs and not any(not ln.strip() for ln in between):
                    runs[-1].append((row, path))
                else:
                    runs.append([(row, path)])
            for order, (path, alias) in enumerate(sorted(new)):
                std = "." not in path.split("/", 1)[0]
                fits = [r for r in runs if any(("." not in p.split("/", 1)[0]) == std for _, p in r)]
                run = (fits[0] if std else fits[-1]) if fits else (runs[0] if std else runs[-1])
                indent = _indent(self.line(run[0][0] + 1)) or "\t"
                before = [row for row, p in run if p < path]
                after = (max(before) + 1) if before else run[0][0]
                plan.inserts.append(Insert(after, (indent + spec_text(path, alias),), "import", order))
        elif last_row is not None:
            lines = tuple(f"import {spec_text(p, a)}" for p, a in sorted(new))
            plan.inserts.append(Insert(last_row + 1, lines, "import"))
        else:
            lines = ("", "import (", *(f"\t{spec_text(p, a)}" for p, a in sorted(new)), ")")
            plan.inserts.append(Insert((self.package_row or 0) + 1, lines, "import"))
        return plan

    def local_taken(self, host: Host, plan: ImportPlan) -> set[str]:
        # inside its own block a local only must not shadow the packages the snippet uses
        return set(plan.names.values())

    def helper_taken(self, plan: ImportPlan, *, context: dict) -> set[str]:
        specs, _, _ = self._imports()
        imported = {(a or p.rsplit("/", 1)[-1]) for p, a, _ in specs}
        return set(context.get("package_names") or ()) | imported | set(plan.names.values())


# ----- Python -----


def _names(node) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            out.add(n.name)
        elif isinstance(n, ast.Global | ast.Nonlocal):
            out |= set(n.names)
        elif isinstance(n, ast.alias):
            out.add(n.asname or n.name.split(".", 1)[0])
    return out


def _start(node) -> int:
    return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])


def _decorator_name(d) -> str:
    d = d.func if isinstance(d, ast.Call) else d
    return d.attr if isinstance(d, ast.Attribute) else d.id if isinstance(d, ast.Name) else ""


def _type_checking(test) -> bool:
    """`if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:`."""
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _is_docstring(stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


class PythonFile(SourceFile):
    language = "python"

    def __init__(self, path: str, text: str):
        super().__init__(path, text)
        try:
            self.module = ast.parse(text)
            self.ok = True
        except (SyntaxError, ValueError):
            self.module = None

    def parses(self, text: str) -> bool:
        try:
            ast.parse(text)
        except (SyntaxError, ValueError):
            return False
        return True

    def called(self) -> Counter:
        out: Counter = Counter()
        for n in ast.walk(self.module):
            if isinstance(n, ast.Call):
                if isinstance(n.func, ast.Name):
                    out[n.func.id] += 1
                elif isinstance(n.func, ast.Attribute):
                    out[n.func.attr] += 1
        return out

    def hosts(self, callers: Counter) -> list[Host]:
        out = []
        for top in self.module.body:
            if isinstance(top, ast.FunctionDef | ast.AsyncFunctionDef):
                candidates = [top]
            elif isinstance(top, ast.ClassDef):
                candidates = [m for m in top.body if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)]
            else:
                continue
            for fn in candidates:
                host = self._host(fn, top, callers)
                if host:
                    out.append(host)
        return out

    def _host(self, fn, top, callers: Counter) -> Host | None:
        if fn.name.startswith("__") and fn.name.endswith("__"):
            return None
        if {_decorator_name(d) for d in fn.decorator_list} & _PY_SKIP_DECORATORS:
            return None
        body = fn.body[1:] if fn.body and _is_docstring(fn.body[0]) else fn.body
        if not body:
            return None
        first = body[0]
        line = self.line(first.lineno)
        if line[: first.col_offset].strip() or first.col_offset <= fn.col_offset:
            return None  # the statement does not start its own line (`def f(): return 1`, `"""doc"""; x = 1`)
        entry = fn.name == "main"
        if not callers.get(fn.name) and not entry:
            return None
        member = _indent(self.line(fn.lineno))
        indent = _indent(line)
        return Host(
            path=self.path,
            name=fn.name,
            line=_start(fn),
            first_line=first.lineno,
            indent=indent,
            unit=_unit(member, indent, self.default_unit),
            end_line=fn.end_lineno,
            top_line=_start(top),
            member_indent=member,
            scope_names=frozenset(_names(fn)),
            wrapper_ok=True,
            callers=int(callers.get(fn.name, 0)),
            entry=entry,
        )

    def _bindings(self) -> dict[str, tuple]:
        """Module-level import bindings: name -> ("import", module) or ("from", module, name)."""
        out: dict[str, tuple] = {}
        stmts = list(self.module.body)
        for s in list(stmts):
            if isinstance(s, ast.If) and _type_checking(s.test):
                continue  # bound for the type checker only: reusing it would be a NameError at run time
            if isinstance(s, ast.Try | ast.If):
                stmts += [x for x in s.body if isinstance(x, ast.Import | ast.ImportFrom)]
        for s in stmts:
            if isinstance(s, ast.Import):
                for a in s.names:
                    if a.asname:
                        out[a.asname] = ("import", a.name)
                    else:
                        out.setdefault(a.name.split(".", 1)[0], ("import", a.name.split(".", 1)[0]))
            elif isinstance(s, ast.ImportFrom) and s.module and not s.level:
                for a in s.names:
                    out[a.asname or a.name] = ("from", s.module, a.name)
        return out

    def _anchor(self) -> tuple[int, bool]:
        """(line after which new imports go, whether imports are there already)."""
        body = self.module.body
        i, anchor = 0, 0
        if body and _is_docstring(body[0]):
            anchor, i = body[0].end_lineno, 1
        last_import = last_absolute = None
        while i < len(body) and isinstance(body[i], ast.Import | ast.ImportFrom):
            last_import, i = body[i], i + 1
            if not (isinstance(last_import, ast.ImportFrom) and last_import.level):
                last_absolute = last_import
        if last_import is not None:  # after the absolute imports, before the package's relative ones (isort's order)
            return (last_absolute or last_import).end_lineno, True
        if anchor:
            return anchor, False
        return (_start(body[0]) - 1 if body else 0), False

    def resolve_imports(self, imports: tuple[str, ...], host: Host, *, context: dict) -> ImportPlan:
        """An import already there (under any alias) is reused; a new one binds its default name unless the file
        uses that name otherwise, then an alias (`asymmetric_padding`, `os_module`). New imports go after the leading
        import block (after the docstring and `__future__`)."""
        bindings = self._bindings()
        by_target = {v: k for k, v in sorted(bindings.items())}
        taken = _names(self.module) | set(host.scope_names)
        plan = ImportPlan(names={})
        entries: list[tuple] = []
        for statement in imports:
            node = ast.parse(statement).body[0]
            if isinstance(node, ast.Import):
                wanted = [(("import", a.name), a.name) for a in node.names]
            else:
                wanted = [(("from", node.module, a.name), a.name) for a in node.names]
            for target, default in wanted:
                if target in by_target:
                    plan.names[default] = by_target[target]
                    continue
                name = default
                if name in taken:
                    base = f"{target[1].rsplit('.', 1)[-1]}_{default}" if target[0] == "from" else f"{default}_module"
                    name = _fresh(base, taken)
                taken.add(name)
                plan.names[default] = name
                entries.append((target, None if name == default else name))
        if not entries:
            return plan
        lines: list[str] = []
        froms: dict[str, list[str]] = {}
        for target, alias in entries:
            if target[0] == "import":
                lines.append(f"import {target[1]}" + (f" as {alias}" if alias else ""))
            else:
                froms.setdefault(target[1], []).append(target[2] + (f" as {alias}" if alias else ""))
        lines += [f"from {module} import {', '.join(names)}" for module, names in froms.items()]
        plan.added = list(lines)
        anchor, had_imports = self._anchor()
        if not had_imports:
            before = [""] if anchor else []
            after = [""] if self.line(anchor + 1).strip() else []
            lines = before + lines + after
        plan.inserts.append(Insert(anchor, tuple(lines), "import"))
        return plan

    def local_taken(self, host: Host, plan: ImportPlan) -> set[str]:
        # no block scope in Python: a local must not take any name the function uses, nor a module the snippet uses
        return set(host.scope_names) | set(plan.names.values())

    def helper_taken(self, plan: ImportPlan, *, context: dict) -> set[str]:
        return _names(self.module) | set(plan.names.values())


FILE_TYPES = {"java": JavaFile, "python": PythonFile, "go": GoFile}


def source_file(language: str, path: str, text: str) -> SourceFile:
    return FILE_TYPES[language](path, text)
