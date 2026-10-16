"""Adapters that workers apply to a tool's native CBOM. Each one is minimal and recorded in the run notes.

Kept here so that workers apply the same fix in the same way (e.g. repo-relative locations for
`workers/cbomkit-jar` and `workers/crypsy-bfcbom`).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def strip_location_prefixes(cbom: dict, prefixes: Iterable[str]) -> int:
    """Make `evidence.occurrences[].location` repo-relative by cutting the first matching prefix.

    Prefixes are tried in the given order; locations that match none stay as they are. Returns the number of
    locations changed.
    """
    prefixes = [p for p in prefixes if p]
    fixed = 0
    for comp in cbom.get("components") or []:
        evidence = comp.get("evidence") if isinstance(comp, dict) else None
        for occ in (evidence.get("occurrences") or []) if isinstance(evidence, dict) else []:
            loc = occ.get("location") if isinstance(occ, dict) else None
            if not isinstance(loc, str):
                continue
            for prefix in prefixes:
                if loc.startswith(prefix):
                    occ["location"] = loc[len(prefix) :]
                    fixed += 1
                    break
    return fixed


def repo_root_prefixes(repo_path: str | Path) -> list[str]:
    """Absolute prefixes of a checked-out repo as tools print them: as given and with symlinks resolved."""
    p = Path(repo_path)
    out: list[str] = []
    for candidate in (str(p), str(p.resolve())):
        prefix = candidate.rstrip("/") + "/"
        if prefix not in out:
            out.append(prefix)
    return out
