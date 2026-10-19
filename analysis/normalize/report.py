"""Coverage per tool and the per-field mapping table over many normalised CBOMs.

`normalize.json`: per tool what was read (formats declared, records, asset types), how complete the algorithm
records are per dimension (reported by the tool vs derived by us), where the values came from (standard field,
tool property, occurrence, name, API context, ...), spec issues by kind, conflicts and the top unmapped values.

`vocab.csv`: one row per (tool, source, raw value) with what it mapped to. This is the reviewable mapping table of
the paper (RQ3): rows with status `unmapped` or `partial` are curation gaps of analysis/data/algorithms.yaml or of the
tool's spec (analysis/adapters/<tool>.yaml); `ambiguous` rows name several algorithms at once.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from .record import ASSET_DIMS, NormalizedCbom, unmapped_text

VOCAB_COLUMNS = ["tool", "source", "raw", "status", "mapped", "unmapped_tokens", "via", "interpretive", "kind", "count"]
COVERAGE_COLUMNS = [
    "tool", "cboms", "algorithm_records", "dimension", "reported", "derived", "reported_share", "derived_share",
]  # fmt: skip
CONFORMANCE_COLUMNS = ["tool", "cboms", "scope", "kind", "count"]
FILL_DIMS = ("family", "algorithm", "primitive", "functions", "mode", "padding", "curve", "key_size", "digest")
COVERAGE_DIMS = FILL_DIMS + ("protocol", "location")


def source_kind(src: str | None) -> str:
    """Coarse kind of a source path: field | property | occurrence | name | ref | dependency | derived."""
    if not src:
        return "none"
    if src.startswith("derived"):
        return "derived"
    if src == "name":
        return "name"
    if src.startswith("properties["):
        return "property"
    if src.startswith("evidence.occurrences"):
        return "occurrence"
    if src.endswith("->ref"):
        return "ref"
    if src == "dependencies":
        return "dependency"
    return "field"


class Report:
    def __init__(self) -> None:
        self.tools: dict[str, dict] = defaultdict(self._empty)
        self.vocab: dict[tuple[str, str, str], dict] = {}

    @staticmethod
    def _empty() -> dict:
        return {
            "cboms": 0,
            "declared": Counter(),
            "document_issues": Counter(),
            "adapters": Counter(),
            "counts": Counter(),
            "skipped": Counter(),
            "asset_types": Counter(),
            "fill": Counter(),
            "fill_derived": Counter(),
            "sources": defaultdict(Counter),
            "spec_issue_kinds": Counter(),
            "spec_issues": Counter(),
            "conflicts": Counter(),
            "unmapped": Counter(),
            "resolution": Counter(),
        }

    def add(self, n: NormalizedCbom) -> None:
        t = self.tools[n.tool]
        t["cboms"] += 1
        t["declared"][f"{n.declared.get('bomFormat')} {n.declared.get('specVersion')}"] += 1
        t["adapters"][n.adapter.get("spec")] += 1
        for issue in n.document_issues:
            t["document_issues"][issue] += 1
        for k, v in n.stats.items():
            if isinstance(v, int):
                t["counts"][k] += v
        for k, v in (n.stats.get("skipped") or {}).items():
            t["skipped"][k] += v
        for r in n.records:
            at = r.values.get("asset_type") or "unknown"
            t["asset_types"][at] += 1
            for issue in r.spec_issues:
                t["spec_issue_kinds"][issue.split(":", 1)[0]] += 1
                t["spec_issues"][issue] += 1
            for d in r.conflicts:
                t["conflicts"][d] += 1
            for u in r.unmapped:
                t["unmapped"][unmapped_text(u)] += 1
            if at != "algorithm":
                continue
            t["resolution"][r.resolution or "undefined"] += 1
            t["counts"]["algorithm_records"] += 1
            for d in FILL_DIMS + ("protocol",):
                if r.values.get(d) is None:
                    continue
                kind = source_kind(r.src.get(d))
                if r.via.get(d) == "api":
                    kind = "context"
                t["sources"][d][kind] += 1
                if kind == "derived":
                    t["fill_derived"][d] += 1
                else:
                    t["fill"][d] += 1
            if r.file and r.line:
                t["fill"]["location"] += 1
        for (src, raw), entry in n.mappings.items():
            key = (n.tool, src, raw)
            row = self.vocab.setdefault(key, {"mapped": {}, "via": set(), "tokens": (), "count": 0, "kind": None})
            for d, v in entry["mapped"].items():
                row["mapped"].setdefault(d, v)
            row["via"] |= entry["via"]
            row["tokens"] = row["tokens"] or entry["tokens"]
            row["kind"] = row["kind"] or entry.get("kind")
            row["count"] += entry["count"]

    def to_dict(self) -> dict:
        out = {}
        for tool, t in sorted(self.tools.items()):
            algos = t["counts"]["algorithm_records"]
            out[tool] = {
                "cboms": t["cboms"],
                "adapter": dict(t["adapters"]),
                "declared": dict(t["declared"]),
                "document_issues": dict(t["document_issues"]),
                "counts": dict(t["counts"]),
                "skipped": dict(t["skipped"]),
                "asset_types": dict(t["asset_types"]),
                "algorithm_fill": {
                    d: {
                        "reported": round(t["fill"][d] / algos, 3) if algos else 0.0,
                        "derived": round(t["fill_derived"][d] / algos, 3) if algos else 0.0,
                    }
                    for d in COVERAGE_DIMS
                },
                "sources": {d: dict(c) for d, c in t["sources"].items()},
                "spec_issues": dict(t["spec_issue_kinds"]),
                "spec_issues_top": dict(t["spec_issues"].most_common(15)),
                "conflicts": dict(t["conflicts"]),
                "unmapped_top": dict(t["unmapped"].most_common(25)),
                "resolution": dict(
                    t["resolution"]
                ),  # algorithm records: mapped | ambiguous | api | non-crypto | undefined
            }
        return out

    def coverage_rows(self) -> list[dict]:
        """Fill rates of the algorithm records, long format: one row per (tool, dimension). `reported` counts values
        the tool gave (any source but derivation), `derived` values we derived (key size from the curve, ...)."""
        rows = []
        for tool, t in sorted(self.tools.items()):
            algos = t["counts"]["algorithm_records"]
            for d in COVERAGE_DIMS:
                reported, derived = t["fill"][d], t["fill_derived"][d]
                rows.append(
                    {
                        "tool": tool,
                        "cboms": t["cboms"],
                        "algorithm_records": algos,
                        "dimension": d,
                        "reported": reported,
                        "derived": derived,
                        "reported_share": round(reported / algos, 3) if algos else 0.0,
                        "derived_share": round(derived / algos, 3) if algos else 0.0,
                    }
                )
        return rows

    def conformance_rows(self) -> list[dict]:
        """Spec issues per tool and kind: `record` scope counts values (field, enum, type, deprecated), `document`
        scope counts CBOMs (dialect, version). Tools without issues have no row."""
        rows = []
        for tool, t in sorted(self.tools.items()):
            base = {"tool": tool, "cboms": t["cboms"]}
            for kind, n in sorted(t["spec_issue_kinds"].items()):
                rows.append({**base, "scope": "record", "kind": kind, "count": n})
            docs = Counter(i.split(":", 1)[0] for i in t["document_issues"].elements())
            for kind, n in sorted(docs.items()):
                rows.append({**base, "scope": "document", "kind": kind, "count": n})
        return rows

    def vocab_rows(self) -> list[dict]:
        rows = []
        for (tool, src, raw), e in sorted(self.vocab.items()):
            mapped = e["mapped"]
            if e["tokens"] == ("*",):
                status = "unmapped"
            elif e["tokens"]:
                status = "partial"
            elif "ambiguous" in mapped:
                status = "ambiguous"
            elif mapped:
                status = "mapped"
            else:
                status = e.get("kind") or "known"
            rows.append(
                {
                    "tool": tool,
                    "source": src,
                    "raw": raw,
                    "status": status,
                    "mapped": ";".join(f"{d}={'|'.join(v) if isinstance(v, tuple) else v}" for d, v in mapped.items()),
                    "unmapped_tokens": " ".join(t for t in e["tokens"] if t != "*"),
                    "via": ";".join(sorted(e["via"])),
                    "interpretive": "interpretive" in e["via"],
                    "kind": e.get("kind") or "",
                    "count": e["count"],
                }
            )
        return rows

    def summary_lines(self) -> list[str]:
        head = f"{'tool':<15}{'cboms':>6}{'records':>9}{'algos':>8}  " + "  ".join(
            f"{d[:6]:>6}" for d in ("family", "key_size", "mode", "padding", "curve", "digest", "location")
        )
        lines = [head + "   spec issues"]
        for tool, t in sorted(self.tools.items()):
            algos = t["counts"]["algorithm_records"]

            def pct(d: str, t=t, algos=algos) -> str:
                return f"{t['fill'][d] / algos:>6.0%}" if algos else f"{'-':>6}"

            kinds = sorted(t["spec_issue_kinds"].items())
            docs = Counter(i.split(":", 1)[0] for i in t["document_issues"].elements())
            issues = (
                ",".join([f"{k}:{v}" for k, v in kinds] + [f"{k}:{v} CBOMs" for k, v in sorted(docs.items())]) or "-"
            )
            dims = ("family", "key_size", "mode", "padding", "curve", "digest", "location")
            lines.append(
                f"{tool:<15}{t['cboms']:>6}{t['counts']['records']:>9}{algos:>8}  "
                + "  ".join(pct(d) for d in dims)
                + f"   {issues}"
            )
        return lines


__all__ = ["ASSET_DIMS", "CONFORMANCE_COLUMNS", "COVERAGE_COLUMNS", "Report", "VOCAB_COLUMNS", "source_kind"]
