"""Shared CBOM adapters (common/cbom_adapters.py) and the cbomkit-jar worker that uses them."""

import importlib.util
from pathlib import Path

from common.cbom_adapters import repo_root_prefixes, strip_location_prefixes

_spec = importlib.util.spec_from_file_location(
    "cbomkit_jar_worker", Path(__file__).resolve().parents[1] / "workers" / "cbomkit-jar" / "main.py"
)
cbomkit_jar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cbomkit_jar)


def _cbom(*locations) -> dict:
    return {"components": [{"evidence": {"occurrences": [{"location": loc, "line": 1} for loc in locations]}}]}


def test_first_matching_prefix_wins_and_others_stay():
    cbom = _cbom("/a/repo/x.py", "repo/y.py", "z.py", None)
    assert strip_location_prefixes(cbom, ["/a/repo/", "repo/", ""]) == 2
    assert [o["location"] for o in cbom["components"][0]["evidence"]["occurrences"]] == ["x.py", "y.py", "z.py", None]


def test_tolerates_odd_shapes():
    cbom = {"components": [None, "x", {"evidence": None}, {"evidence": {"occurrences": None}}, {}]}
    assert strip_location_prefixes(cbom, ["/r/"]) == 0
    assert strip_location_prefixes({}, ["/r/"]) == 0


def test_repo_root_prefixes_include_resolved_path(tmp_path):
    link = tmp_path / "link"
    real = tmp_path / "real"
    real.mkdir()
    link.symlink_to(real)
    assert repo_root_prefixes(link) == [f"{link}/", f"{real.resolve()}/"]
    assert repo_root_prefixes(real.resolve()) == [f"{real.resolve()}/"]


def test_cbomkit_jar_relativizes_cwd_relative_and_absolute_paths(tmp_path):
    repo = tmp_path / "repo"
    cbom = _cbom("repo/src/A.java", f"{repo}/src/B.java", "src/C.java")
    assert cbomkit_jar.relativize_locations(cbom, repo) == 2
    assert [o["location"] for o in cbom["components"][0]["evidence"]["occurrences"]] == [
        "src/A.java",
        "src/B.java",
        "src/C.java",
    ]
