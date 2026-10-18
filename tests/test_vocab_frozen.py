"""The vocabulary and the adapter specs are frozen (refinement 14): a change needs a version bump and a changelog
entry with the new fingerprint (`python -m analysis.freeze` prints it)."""

import yaml

from analysis import freeze
from analysis.normalize.vocab import VOCAB_FILE


def test_vocabulary_matches_the_latest_changelog_entry():
    entries = freeze.changelog_entries()
    assert entries, "analysis/data/CHANGELOG.md has no fingerprinted entry"
    latest = entries[0]
    version = str(yaml.safe_load(VOCAB_FILE.read_text(encoding="utf-8")).get("version"))
    assert latest["version"] == version, (
        f"algorithms.yaml is version {version} but the newest changelog entry is {latest['version']}: "
        "bump the version and add an entry"
    )
    assert latest["fingerprint"] == freeze.fingerprint(), (
        "the vocabulary or an adapter spec changed without a changelog entry: bump `version` in "
        "analysis/data/algorithms.yaml, run `python -m analysis.freeze` and add the new fingerprint with the reason"
    )


def test_fingerprint_covers_every_spec(tmp_path):
    vocab = tmp_path / "algorithms.yaml"
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    vocab.write_text("version: x\n", encoding="utf-8")
    (adapters / "a.yaml").write_text("spec: a\n", encoding="utf-8")
    before = freeze.fingerprint(vocab, adapters)
    (adapters / "a.yaml").write_text("spec: a\ndictionary: {name: {x: {family: AES}}}\n", encoding="utf-8")
    assert freeze.fingerprint(vocab, adapters) != before
