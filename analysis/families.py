"""Algorithm families: the coarse normalisation used for call-site matching and the micro ground truth.

A name maps to the *set* of families whose regex matches it (case-insensitive), so composite names
keep every part: "HmacSHA512" -> {HMAC, SHA-2}, "SHA256withECDSA" -> {EC, SHA-2}. An empty set means
"unknown"; by design unknown names never match anything in the matcher (the pilot's wildcard rule
made weak tools look confirmed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_TABLE = Path(__file__).resolve().parent / "data" / "families.yaml"


@dataclass(frozen=True)
class FamilyTable:
    version: str
    rules: tuple[tuple[str, re.Pattern[str]], ...]

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.rules]

    def classify(self, *texts: str | None) -> frozenset[str]:
        """Families of the first text that yields any; later texts are fallbacks (e.g. a rule id)."""
        for text in texts:
            if not text:
                continue
            upper = str(text).upper()
            found = frozenset(name for name, rx in self.rules if rx.search(upper))
            if found:
                return found
        return frozenset()


@lru_cache(maxsize=4)
def load_families(path: str | Path | None = None) -> FamilyTable:
    data = yaml.safe_load(Path(path or DEFAULT_TABLE).read_text(encoding="utf-8")) or {}
    rules = []
    for entry in data.get("families") or []:
        rules.append((str(entry["name"]), re.compile(str(entry["pattern"]))))
    if not rules:
        raise ValueError(f"no families in {path or DEFAULT_TABLE}")
    return FamilyTable(version=str(data.get("version") or "?"), rules=tuple(rules))


def rule_id_to_text(rule_id: str | None) -> str | None:
    """Make a scanner rule id classifiable: drop `cbom-<lang>-` / `xbom-<lang>-` prefixes and module paths."""
    if not rule_id:
        return None
    text = str(rule_id).rsplit(".", 1)[-1]  # semgrep ids carry the rule file's module path
    text = re.sub(r"^(cbom|xbom|cradar)-[a-z]+-", "", text)
    return text.replace("-", " ").replace("_", " ")
