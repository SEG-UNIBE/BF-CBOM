"""Rule-set overlap of the pattern-rule tools: the evidence behind their voter groups (analysis/README.md "Voter
groups").

securin-xbom, CipherRadar and SCANOSS all match code with rules on semgrep or opengrep (a semgrep fork). What makes
two tools' findings dependent is shared detection logic, so the question for their voter groups is whether the rule
sets share rules. Per language both tools of a pair scan, this compares the rules at the commits the workers pin
(docker/Dockerfile.worker-*): shared rule ids, identical patterns (metavariables and whitespace normalised), rules
whose patterns are near-identical (token Jaccard >= 0.8), identical messages. securin-xbom's rules are the registry
rule sets it freezes (workers/securin-xbom/freeze_rules.py), CipherRadar's the opengrep rules embedded in its binary
(its tree-sitter and YARA-X passes have no rule files), SCANOSS's the open rule set.

Usage (needs git and network once; the checkouts go into <dir>):
    uv run python misc/rule_overlap.py <dir>
"""

from __future__ import annotations

import itertools
import re
import subprocess
import sys
from pathlib import Path

import yaml

# name -> (repository, commit), as pinned in docker/Dockerfile.worker-{securin-xbom,cipherradar,scanoss}
SOURCES = {
    "semgrep-rules": ("https://github.com/semgrep/semgrep-rules", "16b9e6d45f10649f6a531ec4838ed9924372c3bc"),
    "cipherradar": ("https://github.com/nk-sentinel/cipherradar", "efa5d9de322c9120b15acc2e958dc6c1c4e609e1"),
    "open-crypto-rules": ("https://github.com/scanoss/open-crypto-rules", "55d46c2365c1fea84aa41baab70af62e9538a81d"),
}
SECURIN_SETS = {"java": "java/lang/security", "python": "python/cryptography"}  # freeze_rules.py SETS
LANGUAGES = ("java", "python", "go", "javascript")
PATTERN_KEYS = {
    "pattern", "pattern-inside", "pattern-not", "pattern-not-inside", "pattern-regex", "pattern-not-regex", "regex",
}  # fmt: skip


def fetch(root: Path) -> None:
    for name, (url, commit) in SOURCES.items():
        repo = root / name
        if (repo / ".git").exists():
            continue
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "fetch", "-q", "--depth", "1", url, commit], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "FETCH_HEAD"], check=True)


def _norm(pattern: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\$[A-Z_][A-Z0-9_]*", "$V", pattern)).strip()


def _patterns(node, out: set[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in PATTERN_KEYS and isinstance(v, str):
                out.add(_norm(v))
            else:
                _patterns(v, out)
    elif isinstance(node, list):
        for x in node:
            _patterns(x, out)


def _rules(files: list[Path], language: str) -> list[dict]:
    rules = []
    for f in files:
        for r in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("rules") or []:
            if language not in [str(x).lower() for x in r.get("languages") or []]:
                continue
            pats: set[str] = set()
            _patterns(r, pats)
            rules.append(
                {"id": str(r.get("id")), "message": " ".join(str(r.get("message") or "").split()), "pats": pats}
            )
    return rules


def rule_sets(root: Path, language: str) -> dict[str, list[dict]]:
    out = {}
    if language in SECURIN_SETS:
        d = root / "semgrep-rules" / SECURIN_SETS[language]
        out["securin-xbom"] = _rules([f for f in sorted(d.rglob("*.yaml")) if ".test." not in f.name], language)
    cradar = root / "cipherradar" / "cli" / "internal" / "rules" / "data" / f"{language}.yml"
    if cradar.exists():
        out["cipherradar"] = _rules([cradar], language)
    scanoss = root / "open-crypto-rules" / "semgrep-rules" / language
    if scanoss.exists():
        out["scanoss"] = _rules(sorted(scanoss.rglob("*.yaml")), language)
    return out


def _tokens(pats: set[str]) -> set[str]:
    return set(re.findall(r'[A-Za-z_][A-Za-z0-9_.]*|"[^"]*"', " ".join(pats)))


def compare(a: list[dict], b: list[dict]) -> dict:
    pa = set().union(*(r["pats"] for r in a))
    pb = set().union(*(r["pats"] for r in b))
    tb = [t for r in b if (t := _tokens(r["pats"]))]
    near = sum(1 for r in a if (ta := _tokens(r["pats"])) and any(len(ta & t) / len(ta | t) >= 0.8 for t in tb))
    short = lambda rules: {r["id"].rsplit(".", 1)[-1] for r in rules}  # noqa: E731
    return {
        "rules": (len(a), len(b)),
        "patterns": (len(pa), len(pb)),
        "identical_patterns": sorted(pa & pb),
        "rules_with_a_shared_pattern": (sum(bool(r["pats"] & pb) for r in a), sum(bool(r["pats"] & pa) for r in b)),
        "near_identical_rules": near,
        "shared_ids": len(short(a) & short(b)),
        "shared_messages": len({r["message"] for r in a if r["message"]} & {r["message"] for r in b if r["message"]}),
    }


def main(root: Path) -> None:
    fetch(root)
    for language in LANGUAGES:
        sets = rule_sets(root, language)
        print(f"{language}: " + ", ".join(f"{tool} {len(rules)} rules" for tool, rules in sets.items()))
        for x, y in itertools.combinations(sorted(sets), 2):
            print(f"  {x} vs {y}: {compare(sets[x], sets[y])}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
