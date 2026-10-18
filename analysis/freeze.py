"""The frozen vocabulary and adapter specs (CLAUDE.md refinement 14).

The shared vocabulary (`analysis/data/algorithms.yaml`) and the adapter specs (`analysis/adapters/*.yaml`) were
developed on the pilot corpus, which is therefore the development set; the main corpus is held out. Since
2026-10-18 they are frozen: `fingerprint()` hashes all of them, `analysis/data/CHANGELOG.md` records one entry per
version with that fingerprint and the reason for the change, and `tests/test_vocab_frozen.py` fails when the files
change without a new entry. `python -m analysis.freeze` prints the current fingerprint to paste into the changelog.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .normalize.specs import ADAPTER_DIR
from .normalize.vocab import VOCAB_FILE

CHANGELOG = VOCAB_FILE.with_name("CHANGELOG.md")
_ENTRY = re.compile(r"^## (?P<version>\S+) \((?P<date>[0-9-]+)\) fingerprint `(?P<fingerprint>[0-9a-f]{64})`")


def frozen_files(vocab_file: Path = VOCAB_FILE, adapter_dir: Path = ADAPTER_DIR) -> list[Path]:
    return [vocab_file, *sorted(adapter_dir.glob("*.yaml"))]


def fingerprint(vocab_file: Path = VOCAB_FILE, adapter_dir: Path = ADAPTER_DIR) -> str:
    """sha256 over the names and contents of the frozen files."""
    h = hashlib.sha256()
    for path in frozen_files(vocab_file, adapter_dir):
        h.update(path.name.encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def changelog_entries(path: Path = CHANGELOG) -> list[dict]:
    """[{version, date, fingerprint}] in file order (newest first)."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ENTRY.match(line)
        if m:
            out.append(m.groupdict())
    return out


if __name__ == "__main__":  # python -m analysis.freeze
    import yaml

    version = yaml.safe_load(VOCAB_FILE.read_text(encoding="utf-8")).get("version")
    current = fingerprint()
    entries = changelog_entries()
    latest = entries[0] if entries else {}
    state = "matches" if latest.get("fingerprint") == current and latest.get("version") == str(version) else "DIFFERS"
    print(f"vocabulary version {version}, fingerprint {current}: changelog {state}")
    print(f"changelog line: ## {version} (<date>) fingerprint `{current}`")
