"""Coverage per tool and the per-field mapping table over many normalised CBOMs.

`normalize.json`: per tool what was read (formats declared, records, asset types), how complete the algorithm
assets are per dimension (reported by the tool vs derived by us), what their names resolve to, where the values came
from (standard field, tool property, occurrence, name, API context, ...), spec issues by kind, conflicts and the top
unmapped values.

Fill rates, resolutions, sources and spec issues count **assets** (CBOM components), the unit of the RQ1 and RQ2
tables: a record is one occurrence of an asset, and counted per record a tool's few assets with hundreds of
occurrences would dominate its shares (CryptoScan: 199 algorithm assets, 13996 records on the pilot corpus).

`vocab.csv`: one row per (tool, source, raw value) with what it mapped to. This is the reviewable mapping table of
the paper (RQ3): rows with status `unmapped` or `partial` are curation gaps of analysis/data/algorithms.yaml or of the
tool's spec (analysis/adapters/<tool>.yaml); `ambiguous` rows name several algorithms at once.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from .engine import RESOLUTIONS
from .record import ASSET_DIMS, NormalizedCbom, NormRecord, unmapped_text

VOCAB_COLUMNS = ["tool", "source", "raw", "status", "mapped", "unmapped_tokens", "via", "interpretive", "kind", "count"]
COVERAGE_COLUMNS = [
    "tool", "cboms", "assets", "records", *RESOLUTIONS, "dimension", "reported", "derived", "reported_share",
    "derived_share",
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
        assets: dict[int, list[NormRecord]] = defaultdict(list)
        for r in n.records:
            assets[r.component_index].append(r)
        for records in assets.values():
            self._add_asset(t, records)
        self._add_mappings(n)

    @staticmethod
    def _add_asset(t: dict, records: list[NormRecord]) -> None:
        """One asset (a CBOM component) with its records, one per occurrence. A value, an issue or an unmapped name
        counts once per asset; a dimension is `reported` when any record has a value the tool gave, `derived` when
        only derived ones; the resolution is the best of the records' (they differ only where occurrences carry
        their own values)."""
        at = records[0].values.get("asset_type") or "unknown"
        t["asset_types"][at] += 1
        issues = {i for r in records for i in r.spec_issues}
        for issue in issues:
            t["spec_issue_kinds"][issue.split(":", 1)[0]] += 1
            t["spec_issues"][issue] += 1
        for d in {d for r in records for d in r.conflicts}:
            t["conflicts"][d] += 1
        for u in {unmapped_text(u) for r in records for u in r.unmapped}:
            t["unmapped"][u] += 1
        if at != "algorithm":
            return
        t["counts"]["algorithm_assets"] += 1
        t["counts"]["algorithm_records"] += len(records)
        resolutions = {r.resolution or "undefined" for r in records}
        t["resolution"][next(x for x in RESOLUTIONS if x in resolutions)] += 1
        sources: set[tuple[str, str]] = set()
        for r in records:
            for d in FILL_DIMS + ("protocol",):
                if r.values.get(d) is None:
                    continue
                kind = source_kind(r.src.get(d))
                sources.add((d, "context" if r.via.get(d) == "api" else kind))
            if r.file and r.line:
                sources.add(("location", "occurrence"))
        for d, kind in sources:
            if d != "location":
                t["sources"][d][kind] += 1
        reported = {d for d, kind in sources if kind != "derived"}
        for d in reported:
            t["fill"][d] += 1
        for d in {d for d, kind in sources if kind == "derived"} - reported:
            t["fill_derived"][d] += 1

    def _add_mappings(self, n: NormalizedCbom) -> None:
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
            assets = t["counts"]["algorithm_assets"]
            out[tool] = {
                "cboms": t["cboms"],
                "adapter": dict(t["adapters"]),
                "declared": dict(t["declared"]),
                "document_issues": dict(t["document_issues"]),
                "counts": dict(t["counts"]),
                "skipped": dict(t["skipped"]),
                "asset_types": dict(t["asset_types"]),  # assets per normalised asset type
                "algorithm_fill": {  # shares of the algorithm assets
                    d: {
                        "reported": round(t["fill"][d] / assets, 3) if assets else 0.0,
                        "derived": round(t["fill_derived"][d] / assets, 3) if assets else 0.0,
                    }
                    for d in COVERAGE_DIMS
                },
                "sources": {d: dict(c) for d, c in t["sources"].items()},
                "spec_issues": dict(t["spec_issue_kinds"]),
                "spec_issues_top": dict(t["spec_issues"].most_common(15)),
                "conflicts": dict(t["conflicts"]),
                "unmapped_top": dict(t["unmapped"].most_common(25)),
                "resolution": dict(t["resolution"]),  # algorithm assets: mapped | ambiguous | api | ...
            }
        return out

    def coverage_rows(self) -> list[dict]:
        """Fill rates of the algorithm assets, long format: one row per (tool, dimension). `reported` counts assets
        with a value the tool gave (any source but derivation), `derived` assets with only a value we derived (key
        size from the curve, ...). `assets` and `records` (their occurrences) and the assets per resolution
        (RESOLUTIONS) repeat on every row of a tool."""
        rows = []
        for tool, t in sorted(self.tools.items()):
            assets = t["counts"]["algorithm_assets"]
            base = {
                "tool": tool,
                "cboms": t["cboms"],
                "assets": assets,
                "records": t["counts"]["algorithm_records"],
                **{k: t["resolution"][k] for k in RESOLUTIONS},
            }
            for d in COVERAGE_DIMS:
                reported, derived = t["fill"][d], t["fill_derived"][d]
                rows.append(
                    {
                        **base,
                        "dimension": d,
                        "reported": reported,
                        "derived": derived,
                        "reported_share": round(reported / assets, 3) if assets else 0.0,
                        "derived_share": round(derived / assets, 3) if assets else 0.0,
                    }
                )
        return rows

    def conformance_rows(self) -> list[dict]:
        """Spec issues per tool and kind: `asset` scope counts assets with a value of that kind of issue (field, enum,
        type, deprecated), `document` scope counts CBOMs (dialect, version). Tools without issues have no row."""
        rows = []
        for tool, t in sorted(self.tools.items()):
            base = {"tool": tool, "cboms": t["cboms"]}
            for kind, n in sorted(t["spec_issue_kinds"].items()):
                rows.append({**base, "scope": "asset", "kind": kind, "count": n})
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
        dims = ("key_size", "mode", "padding", "curve", "digest")
        head = f"{'tool':<15}{'cboms':>6}{'assets':>8}{'records':>9}{'mapped':>8}  " + "  ".join(
            f"{d[:6]:>6}" for d in dims
        )
        lines = [head + "   spec issues"]
        for tool, t in sorted(self.tools.items()):
            assets = t["counts"]["algorithm_assets"]

            def pct(n: int, assets=assets) -> str:
                return f"{n / assets:>6.0%}" if assets else f"{'-':>6}"

            kinds = sorted(t["spec_issue_kinds"].items())
            docs = Counter(i.split(":", 1)[0] for i in t["document_issues"].elements())
            issues = (
                ",".join([f"{k}:{v}" for k, v in kinds] + [f"{k}:{v} CBOMs" for k, v in sorted(docs.items())]) or "-"
            )
            lines.append(
                f"{tool:<15}{t['cboms']:>6}{assets:>8}{t['counts']['algorithm_records']:>9}  "
                + pct(t["resolution"]["mapped"])
                + "  "
                + "  ".join(pct(t["fill"][d]) for d in dims)
                + f"   {issues}"
            )
        return lines


__all__ = ["ASSET_DIMS", "CONFORMANCE_COLUMNS", "COVERAGE_COLUMNS", "Report", "VOCAB_COLUMNS", "source_kind"]
