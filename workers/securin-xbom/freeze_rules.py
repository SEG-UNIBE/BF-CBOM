"""Build-time helper: freeze the semgrep registry rule sets that xbom would fetch online.

Online, xbom adds the semgrep.dev rule sets `r/java.lang.security` and `r/python.cryptography` (its third,
`r/python.lang.security.audit.insecure-hash-algorithms`, has no rules in semgrep-rules). Those rule sets are
directories of the semgrep-rules repository. This script takes them whole from a pinned checkout, as xbom
would get them (`java.lang.security` also holds XSS/SQL/cookie rules, which xbom then reports as crypto
assets: that is the tool's behaviour, kept on purpose), and gives every rule its registry id
(`<dir.dotted>.<file>.<id>`).

Usage: python freeze_rules.py <semgrep-rules checkout> <out dir>
Writes one file per rule set; JSON content in .yaml files (JSON is YAML, so semgrep reads it).
"""

import json
import sys
from pathlib import Path

from ruamel.yaml import YAML

SETS = {
    "r.java.lang.security.yaml": "java/lang/security",
    "r.python.cryptography.yaml": "python/cryptography",
}


def main(src: Path, out: Path) -> None:
    yaml = YAML(typ="safe", pure=True)
    out.mkdir(parents=True, exist_ok=True)
    for name, sub in SETS.items():
        rules = []
        for f in sorted((src / sub).rglob("*.yaml")):
            if ".test." in f.name:
                continue
            doc = yaml.load(f.read_text(encoding="utf-8")) or {}
            prefix = ".".join(f.relative_to(src).with_suffix("").parts)
            for rule in doc.get("rules", []):
                rule["id"] = f"{prefix}.{rule['id']}"
                rules.append(rule)
        if not rules:
            sys.exit(f"no rules under {sub}")
        (out / name).write_text(json.dumps({"rules": rules}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{name}: {len(rules)} rules")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
