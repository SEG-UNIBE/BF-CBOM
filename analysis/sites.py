"""CBOM -> call sites: one record per (tool, file, line) with the union of reported families.

This is the call-site level of analysis (CLAUDE.md, method refinement 1): only occurrences with a
file and a line take part. Locations are made repo-relative; CycloneDX `evidence.occurrences` is the
primary source, with a fallback for tools that put `path:line` into custom properties (Q-DARPAN).
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field

from .families import FamilyTable, load_families, rule_id_to_text

# Primary-source extensions per language; a site in another file is "out of scope" (configs, PEM, docs).
CODE_EXT: dict[str, tuple[str, ...]] = {
    "java": (".java", ".kt", ".scala", ".groovy"),
    "python": (".py",),
    "go": (".go",),
    "c": (".c", ".h", ".cc", ".cpp", ".hpp"),
    "javascript": (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"),
}
CODE_EXT["typescript"] = CODE_EXT["javascript"]
ASSET_COMPONENT_TYPES = ("cryptographic-asset", "crypto-asset")  # the latter: IBM draft 1.4-cbom-1.0
_RULE_PROPS = ("ruleId", "rule_id", "xbom:crypto:rule_id", "qdarpan:family", "matcher_id")
_PATH_LINE_RE = re.compile(r"^(.*?):(\d+)$")


@dataclass
class Site:
    tool: str
    file: str
    line: int
    families: frozenset[str] = frozenset()
    raw: list[str] = field(default_factory=list)
    asset_types: list[str] = field(default_factory=list)
    in_scope: bool = True

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.tool, self.file, self.line)

    @property
    def unknown(self) -> bool:
        return not self.families

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "file": self.file,
            "line": self.line,
            "families": sorted(self.families),
            "raw": list(self.raw),
            "asset_types": list(self.asset_types),
            "in_scope": self.in_scope,
        }


@dataclass
class ExtractionStats:
    components: int = 0
    occurrences: int = 0
    no_location: int = 0
    skipped_asset_type: int = 0
    out_of_scope: int = 0
    unknown_family: int = 0
    sites: int = 0
    unknown_names: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["unknown_names"] = dict(self.unknown_names.most_common(25))
        return d


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


def _props(component: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in component.get("properties") or []:
        if isinstance(p, dict) and p.get("name") is not None:
            out.setdefault(str(p["name"]), str(p.get("value", "")))
    return out


def _occurrences(component: dict, props: dict[str, str]) -> list[tuple[str, int | None]]:
    found: list[tuple[str, int | None]] = []
    for occ in (component.get("evidence") or {}).get("occurrences") or []:
        if not isinstance(occ, dict) or not occ.get("location"):
            continue
        line = occ.get("line")
        found.append((str(occ["location"]), int(line) if isinstance(line, int) or str(line).isdigit() else None))
    if found:
        return found
    for name, value in props.items():  # Q-DARPAN: qdarpan:location.N = "<path>:<line>"
        if "location" in name.lower():
            m = _PATH_LINE_RE.match(value)
            if m:
                found.append((m.group(1), int(m.group(2))))
    return found


def extract_sites(
    cbom: dict,
    tool: str,
    *,
    language: str | None = None,
    asset_types: tuple[str, ...] | None = ("algorithm",),
    table: FamilyTable | None = None,
    repo_markers: tuple[str, ...] = ("/repo/",),
) -> tuple[list[Site], ExtractionStats]:
    """Call sites of one CBOM. `asset_types=None` keeps every cryptographic asset (keys, certificates...)."""
    table = table or load_families()
    exts = CODE_EXT.get(language.lower()) if language else None
    stats = ExtractionStats()
    sites: dict[tuple[str, str, int], Site] = {}
    for comp in cbom.get("components") or []:
        if not isinstance(comp, dict) or comp.get("type") not in ASSET_COMPONENT_TYPES:
            continue
        stats.components += 1
        cp = comp.get("cryptoProperties") or {}
        asset_type = cp.get("assetType")
        if asset_types is not None and asset_type is not None and asset_type not in asset_types:
            stats.skipped_asset_type += 1
            continue
        props = _props(comp)
        name = str(comp.get("name") or "")
        rule_text = next((rule_id_to_text(props[k]) for k in _RULE_PROPS if props.get(k)), None)
        ap = cp.get("algorithmProperties") or {}
        # algorithmFamily: CycloneDX 1.7; tools that name a component after the API call (`GCMParameterSpec`)
        # often state the family only there
        families = table.classify(
            name, rule_text, ap.get("algorithmFamily"), ap.get("curve"), ap.get("parameterSetIdentifier")
        )
        occs = _occurrences(comp, props)
        if not occs:
            stats.no_location += 1
            continue
        for location, line in occs:
            stats.occurrences += 1
            if line is None or line < 1:
                stats.no_location += 1
                continue
            file = relative_location(location, repo_markers=repo_markers)
            in_scope = exts is None or os.path.splitext(file)[1].lower() in exts
            if not in_scope:
                stats.out_of_scope += 1
            site = sites.get((tool, file, line))
            if site is None:
                site = sites[(tool, file, line)] = Site(tool=tool, file=file, line=line, in_scope=in_scope)
            site.families = site.families | families
            if name and name not in site.raw:
                site.raw.append(name)
            if asset_type and asset_type not in site.asset_types:
                site.asset_types.append(str(asset_type))
        if not families:
            stats.unknown_names[name or rule_text or "?"] += 1
    out = sorted(sites.values(), key=lambda s: (s.file, s.line, s.tool))
    stats.sites = len(out)
    stats.unknown_family = sum(1 for s in out if s.unknown)
    return out, stats
