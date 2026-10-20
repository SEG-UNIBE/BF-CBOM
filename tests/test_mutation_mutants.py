"""Mutants as branches (mutation/mutants.py) on a temporary bare mirror: deterministic ids and SHAs, one file changed
per mutant, rebuilt from mutants.json when the branch is missing, the library rule, and the spec's `mutation:` block."""

import subprocess
from pathlib import Path

import pytest

from common.experiment import (
    ExperimentError,
    MutationSpec,
    RepoSpec,
    ensure_mirror,
    mirror_path,
    publish_fixture,
    spec_from_dict,
    spec_to_dict,
)
from mutation.mutants import ensure_mutants

MICRO = Path(__file__).resolve().parents[1] / "corpus" / "micro"


def _publish(src: Path, full_name: str, language: str, mirror: Path) -> tuple[RepoSpec, str]:
    repo = RepoSpec(full_name=full_name, path=str(src), language=language)
    commit = publish_fixture(repo, mirror_dir=mirror)
    ensure_mirror(repo, commit, mirror_dir=mirror)
    return repo, commit


def _git(bare: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(bare), *args], capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize("language", ["java", "python", "go"])
def test_mutants_are_deterministic_single_file_commits(tmp_path, language):
    params = MutationSpec(per_repo=4)
    made = []
    for k in (1, 2):  # two fresh mirrors: the same ids and SHAs
        mirror = tmp_path / f"mirror{k}"
        repo, commit = _publish(MICRO / language, f"bfcbom/micro-{language}", language.title(), mirror)
        manifest: dict = {}
        ids = ensure_mutants(repo, commit, params, manifest, mirror_dir=mirror, log=lambda m: None)
        entry = manifest["repos"][repo.full_name]
        made.append((ids, [m["commit"] for m in entry["mutants"]]))
    assert made[0] == made[1] and len(made[0][0]) == 4
    styles = [m["style"] for m in entry["mutants"]]
    assert styles == ["direct", "wrapper", "indirect", "direct"]  # round-robin over the styles
    bare = mirror_path(repo.full_name, mirror)
    for m in entry["mutants"]:
        assert m["id"].startswith(f"ins-{m['snippet']}-{m['style']}-")
        assert _git(bare, "rev-parse", f"refs/heads/bfcbom/{m['id']}").strip() == m["commit"]
        assert _git(bare, "diff", "--name-only", commit, m["commit"]).split() == [m["file"]]
        assert _git(bare, "rev-parse", f"{m['commit']}^").strip() == commit
        diff = _git(bare, "diff", "--numstat", commit, m["commit"]).split()
        assert diff[1] == "0"  # insertions only
        line = _git(bare, "show", f"{m['commit']}:{m['file']}").split("\n")[m["call_line"] - 1]
        assert line.strip() and m["patch"].startswith("diff --git")


def test_missing_branches_are_rebuilt_from_mutants_json_with_the_same_sha(tmp_path):
    mirror = tmp_path / "mirror"
    repo, commit = _publish(MICRO / "java", "bfcbom/micro-java", "Java", mirror)
    manifest: dict = {}
    ids = ensure_mutants(repo, commit, MutationSpec(per_repo=3), manifest, mirror_dir=mirror, log=lambda m: None)
    bare = mirror_path(repo.full_name, mirror)
    recorded = {m["id"]: m["commit"] for m in manifest["repos"][repo.full_name]["mutants"]}
    for i in ids:
        _git(bare, "update-ref", "-d", f"refs/heads/bfcbom/{i}")
    logged: list[str] = []
    again = ensure_mutants(repo, commit, MutationSpec(per_repo=5, seed=9), manifest, mirror_dir=mirror,
                           log=logged.append)  # fmt: skip
    assert again == ids  # mutants.json wins over a changed spec, as lock.json does
    assert any("keeping the mutants in mutants.json" in m for m in logged)
    for i in ids:
        assert _git(bare, "rev-parse", f"refs/heads/bfcbom/{i}").strip() == recorded[i]
    manifest["repos"][repo.full_name]["mutants"][0]["inserts"][0]["lines"].append("// tampered")
    _git(bare, "update-ref", "-d", f"refs/heads/bfcbom/{ids[0]}")
    with pytest.raises(ExperimentError, match="rebuilt as"):
        ensure_mutants(repo, commit, MutationSpec(per_repo=3), manifest, mirror_dir=mirror, log=lambda m: None)


def test_pyca_snippets_only_where_cryptography_is_declared(tmp_path):
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "requirements.txt").write_text("requests\n")
    (src / "pkg" / "core.py").write_text(
        "import json\n\n\ndef load(path):\n    return json.loads(path)\n\n\ndef main():\n    return load('{}')\n"
    )
    mirror = tmp_path / "mirror"
    repo, commit = _publish(src, "acme/plain", "Python", mirror)
    manifest: dict = {}
    ensure_mutants(repo, commit, MutationSpec(per_repo=25), manifest, mirror_dir=mirror, log=lambda m: None)
    entry = manifest["repos"]["acme/plain"]
    stdlib = {"sha-1", "sha-256", "md5", "sha3-256", "hmac-sha256", "pbkdf2-hmac-sha256", "tls13-context"}
    assert {m["snippet"] for m in entry["mutants"]} == stdlib  # no RSA/EC/AES: `cryptography` is not declared
    assert len(entry["mutants"]) == 21  # 7 snippets x 3 styles, every (snippet, style) once
    assert entry["notes"] == ["21 of 25 mutants: no further pair fits a host"]
    assert all(m["file"] == "pkg/core.py" for m in entry["mutants"])  # dependency files are never touched


def test_repos_without_a_catalog_or_a_host_get_no_mutants(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.c").write_text("int main(void) { return 0; }\n")
    mirror = tmp_path / "mirror"
    repo, commit = _publish(src, "acme/c", "C", mirror)
    manifest: dict = {}
    assert ensure_mutants(repo, commit, MutationSpec(), manifest, mirror_dir=mirror, log=lambda m: None) == []
    assert "no snippet catalog" in manifest["repos"]["acme/c"]["notes"][0]
    (src / "lib.py").write_text("X = 1\n")
    repo, commit = _publish(src, "acme/py", "Python", mirror)
    assert ensure_mutants(repo, commit, MutationSpec(), manifest, mirror_dir=mirror, log=lambda m: None) == []
    assert "no host" in manifest["repos"]["acme/py"]["notes"][0]


def test_spec_mutation_block_parses_round_trips_and_excludes_listed_variants():
    base = {"name": "e4", "tools": ["t1"], "repos": [{"full_name": "a/b", "language": "Java"}]}
    spec = spec_from_dict({**base, "mutation": {"per_repo": 3, "styles": ["direct"], "snippets": ["sha-1"]}})
    assert spec.mutation == MutationSpec(per_repo=3, styles=["direct"], snippets=["sha-1"], seed=1)
    assert spec.expected_jobs == 4  # original + 3 mutants, one tool, one repetition
    assert spec_from_dict(spec_to_dict(spec)).mutation == spec.mutation
    assert spec_from_dict({**base, "mutation": {"snippets": "all"}}).mutation == MutationSpec()
    with pytest.raises(ExperimentError, match="cannot be combined"):
        spec_from_dict({**base, "repos": [{"full_name": "a/b", "variants": ["original", "m1"]}], "mutation": {}})
    with pytest.raises(ExperimentError, match="styles"):
        spec_from_dict({**base, "mutation": {"styles": ["sideways"]}})
    with pytest.raises(ExperimentError, match="unknown"):
        spec_from_dict({**base, "mutation": {"per_rep": 3}})
    with pytest.raises(ExperimentError, match="per_repo"):
        spec_from_dict({**base, "mutation": {"per_repo": 0}})
    assert spec_from_dict({**base, "mutation": {"seed": 0}}).mutation.seed == 0
