"""One snippet into one host: the mutated text and where everything landed.

Styles (refinement 5): `direct` and `indirect` put the snippet before the host's first statement (Java inside its
try/catch, Go inside its own block); `wrapper` puts it into a new helper next to the host (Java: a private static
method after it, Go: a function after it, Python: a module-level function before the host's top-level statement) and
one call of the helper before the host's first statement. Imports go where the language keeps them. Every edit is an
insertion of whole lines, recorded as `inserts` against the original line numbers, so the mutant can be rebuilt from
the base commit alone (`apply`), the original's findings can be mapped onto the mutant's lines (`shift`), and the
inserted lines are known (`spans`, in mutant line numbers).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import Catalog, Form, Rendered, Snippet, placeholders, render, wrap_lines
from .lang import Host, ImportPlan, Insert, SkipHost, SourceFile, _fresh

SPECIAL = {"body", "helper"}


class InsertError(ValueError):
    pass


@dataclass
class Mutation:
    path: str
    snippet: str
    style: str
    form: int
    host: Host
    text: str
    inserts: list[Insert]
    call_line: int  # mutant line naming the algorithm
    related_lines: list[int]  # mutant lines holding it in a variable, or calling the helper
    spans: list[dict] = field(default_factory=list)  # {kind, start, end} in mutant lines
    shifts: list[list[int]] = field(default_factory=list)  # [after original line, lines inserted]
    imports_added: list[str] = field(default_factory=list)
    qualified: list[str] = field(default_factory=list)


def apply(text: str, inserts: list[Insert]) -> tuple[str, list[tuple[Insert, int]]]:
    """The text with `inserts` applied, and each insert's first line in the result. A CRLF file gets CRLF lines."""
    lines = text.split("\n")
    cr = "\r" if lines and lines[0].endswith("\r") else ""
    out: list[str] = []
    placed: list[tuple[Insert, int]] = []
    pos = 0
    real = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
    for ins in sorted(inserts, key=lambda i: (i.after, i.order)):
        if not 0 <= ins.after <= real:
            raise InsertError(f"insert after line {ins.after} outside the file ({real} lines)")
        while pos < ins.after:
            out.append(lines[pos])
            pos += 1
        placed.append((ins, len(out) + 1))
        out.extend(line + cr for line in ins.lines)
    out.extend(lines[pos:])
    return "\n".join(out), placed


def shift(line: int, shifts: list[list[int]]) -> int:
    """An original line's number in the mutant."""
    return line + sum(count for after, count in shifts if line > after)


def _names_for(source: SourceFile, templates: list[str], plan: ImportPlan, taken: set[str]) -> dict[str, str]:
    names = dict(plan.names)
    used = set(names.values()) | set(taken)
    for key in sorted(set().union(*(placeholders(t) for t in templates)) - SPECIAL - set(names)):
        names[key] = _fresh(key, used)
        used.add(names[key])
    return names


def _helper_lines(language: str, name: str, body: Rendered, unit: str, member: str) -> tuple[list[str], int]:
    """The helper's lines (at the host's member indentation) and the offset of the body's first line in them."""
    inner = [member + unit + line if line else "" for line in body.lines]
    if language == "java":
        return ["", f"{member}private static void {name}() {{", *inner, f"{member}}}"], 2
    if language == "go":
        return ["", f"{member}func {name}() {{", *inner, f"{member}}}"], 2
    return [f"def {name}():", *[unit + line if line else "" for line in body.lines], "", ""], 1


def mutate(
    source: SourceFile, host: Host, catalog: Catalog, snippet: Snippet, form_index: int, style: str, *, context: dict
) -> Mutation:
    """Render `snippet` (its form `form_index`) in `style` into `host`; raises InsertError or SkipHost when it does
    not fit (the caller tries another host)."""
    form: Form = snippet.forms[form_index]
    template = form.templates.get("direct" if style == "wrapper" else style)
    if not template:
        raise InsertError(f"{snippet.id} has no {style} form")
    if style == "wrapper" and not host.wrapper_ok:
        raise SkipHost("no room for a helper next to the host")
    plan = source.resolve_imports((*form.imports, *catalog.wrap_imports), host, context=context)
    outer = [t for t in (catalog.wrap, catalog.scope) if t]
    names = _names_for(source, [template, *outer], plan, source.local_taken(host, plan))
    unit = host.unit
    body = render(template, names, unit=unit)
    if catalog.wrap:
        body = wrap_lines(catalog.wrap, body, names, unit=unit)
    inserts = list(plan.inserts)
    related_offsets: list[tuple[str, int]] = []
    if style == "wrapper":
        base = snippet.helper if source.language != "python" else "_" + snippet.helper
        helper = _fresh(base, source.helper_taken(plan, context=context) | set(names.values()))
        lines, offset = _helper_lines(source.language, helper, body, unit, host.member_indent)
        after = host.end_line if source.language != "python" else host.top_line - 1
        inserts.append(Insert(after, tuple(lines), "helper", 1))
        call = f"{helper}();" if source.language == "java" else f"{helper}()"
        inserts.append(Insert(host.first_line - 1, (host.indent + call,), "call", 2))
        call_ref = ("helper", offset + (body.call or 0))
        related_offsets.append(("call", 0))
    else:
        if catalog.scope:
            body = wrap_lines(catalog.scope, body, names, unit=unit)
        lines = [host.indent + line if line else "" for line in body.lines]
        inserts.append(Insert(host.first_line - 1, tuple(lines), "snippet", 2))
        call_ref = ("snippet", body.call or 0)
        related_offsets += [("snippet", r) for r in body.related]
    if style == "wrapper":
        related_offsets += [("helper", offset + r) for r in body.related]
    text, placed = apply(source.text, inserts)
    if not source.parses(text):
        raise InsertError(f"{snippet.id}/{style} does not parse in {host.path}:{host.name}")
    first = {ins.kind: start for ins, start in placed}
    spans = []
    for ins, start in placed:
        content = [i for i, line in enumerate(ins.lines) if line.strip()]
        if content:
            spans.append({"kind": ins.kind, "start": start + content[0], "end": start + content[-1]})
    shifts = sorted([ins.after, len(ins.lines)] for ins in inserts)
    return Mutation(
        path=host.path,
        snippet=snippet.id,
        style=style,
        form=form_index,
        host=host,
        text=text,
        inserts=inserts,
        call_line=first[call_ref[0]] + call_ref[1],
        related_lines=sorted(first[kind] + off for kind, off in related_offsets),
        spans=spans,
        shifts=shifts,
        imports_added=plan.added,
        qualified=plan.qualified,
    )
