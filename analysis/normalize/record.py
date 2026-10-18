"""The normalised form (CBOM^norm): one record per (component, occurrence), the same shape for every tool.

The JSON Schema of a normalised file is analysis/data/cbom-norm.schema.json; keep both in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCHEMA_ID = "bf-cbom-norm/1"

# Dimensions with a canonical value, in output order. Every one keeps its raw value and its source.
VALUE_DIMS = (
    "asset_type", "material", "family", "algorithm", "primitive", "functions", "mode", "padding", "curve",
    "key_size", "digest", "param_set", "iterations", "oid", "protocol", "protocol_version", "purl", "confidence",
)  # fmt: skip
# Dimensions that describe the crypto asset itself (fill rates, agreement); the rest is kind, evidence or packaging.
ASSET_DIMS = (
    "family", "algorithm", "primitive", "functions", "mode", "padding", "curve", "key_size", "digest",
    "param_set", "iterations", "protocol", "protocol_version", "material",
)  # fmt: skip
LIST_SEP = ";"


@dataclass
class NormRecord:
    record_id: str
    tool: str
    component_index: int
    occurrence_index: int
    component: str | None = None
    name: str | None = None
    group: str | None = None
    values: dict = field(default_factory=dict)  # dim -> canonical value (VALUE_DIMS)
    raw: dict = field(default_factory=dict)  # dim -> value as found
    src: dict = field(default_factory=dict)  # dim -> JSON path / source it was read from ("derived:<dim>")
    via: dict = field(
        default_factory=dict
    )  # dim -> field | vocabulary | dictionary | interpretive | parse | api | dependency | ref | derived
    file: str | None = None
    line: int | None = None
    line_end: int | None = None
    location: str | None = None  # as reported, before relativizing
    in_scope: bool | None = None
    file_exists: bool | None = None
    context: str | None = None
    ambiguous: str | None = None  # a name listing several algorithms that no other source resolved
    unmapped: list = field(default_factory=list)
    conflicts: dict = field(default_factory=dict)  # dim -> {"value", "src"} of a lower-precedence source
    spec_issues: list = field(default_factory=list)

    @property
    def site_id(self) -> str | None:
        return f"{self.tool}:{self.file}:{self.line}" if self.file and self.line else None

    def get(self, dim: str):
        return self.group if dim == "group" else self.values.get(dim)

    def to_dict(self) -> dict:
        out: dict = {
            "record_id": self.record_id,
            "site_id": self.site_id,
            "component": self.component,
            "component_index": self.component_index,
            "occurrence_index": self.occurrence_index,
            "name": self.name,
            "group": self.group,
        }
        for dim in VALUE_DIMS:
            v = self.values.get(dim)
            out[dim] = list(v) if isinstance(v, tuple) else v
        out.update(
            {
                "file": self.file,
                "line": self.line,
                "line_end": self.line_end,
                "location": self.location,
                "in_scope": self.in_scope,
                "file_exists": self.file_exists,
                "context": self.context,
                "ambiguous": self.ambiguous,
                "raw": self.raw,
                "src": self.src,
                "via": self.via,
                "unmapped": self.unmapped,
                "conflicts": self.conflicts,
                "spec_issues": self.spec_issues,
            }
        )
        return {k: v for k, v in out.items() if v not in (None, [], {}, "")}

    def csv_row(self) -> dict:
        row = {
            "record_id": self.record_id,
            "site_id": self.site_id,
            "component": self.component,
            "name": self.name,
            "group": self.group,
            "file": self.file,
            "line": self.line,
            "line_end": self.line_end,
            "in_scope": self.in_scope,
            "file_exists": self.file_exists,
            "context": self.context,
            "ambiguous": self.ambiguous,
            "unmapped": LIST_SEP.join(unmapped_text(u) for u in self.unmapped),
            "conflicts": LIST_SEP.join(f"{d}={c['value']}@{c['src']}" for d, c in self.conflicts.items()),
            "spec_issues": LIST_SEP.join(self.spec_issues),
        }
        for dim in VALUE_DIMS:
            v = self.values.get(dim)
            row[dim] = LIST_SEP.join(v) if isinstance(v, tuple) else v
            raw = self.raw.get(dim)
            row[f"raw_{dim}"] = LIST_SEP.join(map(str, raw)) if isinstance(raw, list | tuple) else raw
            row[f"src_{dim}"] = self.src.get(dim)
        return row


def unmapped_text(u: dict) -> str:
    """`src: raw` for an unknown value, `src: raw ~ tok tok` for leftover tokens of a parsed text."""
    tokens = u.get("tokens")
    return f"{u['src']}: {u['raw']}" + (f" ~ {' '.join(tokens)}" if tokens else "")


RECORD_COLUMNS = (
    ["record_id", "site_id", "component", "name", "group"]
    + list(VALUE_DIMS)
    + ["file", "line", "line_end", "in_scope", "file_exists", "context", "ambiguous"]
    + [f"raw_{d}" for d in VALUE_DIMS]
    + [f"src_{d}" for d in VALUE_DIMS]
    + ["unmapped", "conflicts", "spec_issues"]
)


@dataclass
class NormalizedCbom:
    tool: str
    adapter: dict
    vocabulary: dict
    source: dict
    declared: dict
    reader: str
    checked_against: str | None
    document_issues: list
    stats: dict
    records: list[NormRecord] = field(default_factory=list)
    mappings: dict = field(default_factory=dict)  # (src, raw) -> what it mapped to; for the vocabulary report

    def spec_issue_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.document_issues:
            kind = issue.split(":", 1)[0]
            counts[kind] = counts.get(kind, 0) + 1
        for r in self.records:
            for issue in r.spec_issues:
                kind = issue.split(":", 1)[0]
                counts[kind] = counts.get(kind, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_ID,
            "tool": self.tool,
            "adapter": self.adapter,
            "vocabulary": self.vocabulary,
            "source": self.source,
            "declared": self.declared,
            "reader": self.reader,
            "checked_against": self.checked_against,
            "document_issues": self.document_issues,
            "spec_issues": self.spec_issue_counts(),
            "stats": self.stats,
            "records": [r.to_dict() for r in self.records],
        }
