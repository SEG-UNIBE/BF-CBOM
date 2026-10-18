"""Scoring a tool against the micro projects' ground truth (corpus/micro/ground_truth.yaml).

This implements the pilot's inclusion bar ("finds >= 50 % of the 11 assets with a usable location")
and gives recall/precision per tool on the hand-written micro projects, at two levels (CLAUDE.md, refinement 1):
- call sites: strict (right line, family compatible with the asset) and lenient (right line, family unknown or
  compatible); the inclusion bar uses lenient;
- repository: the asset's own family (`expected.family`) reported anywhere in the project, location ignored, so
  tools without locations get a number too. Assets sharing a family (two RSA, two EC, two AES) are found together
  at this level.

`score_records` scores the normalised records (analysis/normalize/) and is what `analysis micro` uses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_TRUTH = Path(
    os.getenv("GROUND_TRUTH_PATH") or Path(__file__).resolve().parents[1] / "corpus" / "micro" / "ground_truth.yaml"
)
INCLUSION_BAR = 0.5


@dataclass(frozen=True)
class TruthAsset:
    id: str
    name: str
    families: frozenset[str]
    lines: tuple[int, ...]
    expected: dict
    insertion_style: str = "direct"  # `constant`: the algorithm name is passed through a constant (Phase 4 style)

    @property
    def family(self) -> frozenset[str]:
        """The asset's own family for the repository level: `expected.family` (HMAC for HMAC-SHA512), not the
        call-site set `families`, which also holds the families a composite name mentions (HMAC, SHA-2)."""
        own = self.expected.get("family")
        return frozenset({str(own)}) if own else self.families


@dataclass(frozen=True)
class TruthProject:
    name: str
    language: str
    file: str
    assets: tuple[TruthAsset, ...]
    related: dict[int, str]
    accepted: dict[int, frozenset[str]]

    def families_at(self, line: int) -> frozenset[str]:
        fams: set[str] = set()
        for a in self.assets:
            if line in a.lines:
                fams |= a.families
        fams |= self.accepted.get(line, frozenset())
        return frozenset(fams)

    @property
    def known_families(self) -> frozenset[str]:
        """Every family the project contains: the assets' and those accepted on extra lines."""
        fams: set[str] = set()
        for a in self.assets:
            fams |= a.families
        for f in self.accepted.values():
            fams |= f
        return frozenset(fams)

    def correct_at(self, line: int, families: frozenset[str]) -> bool:
        """A report of `families` (empty = unknown) at `line` is correct: an asset or accepted line with a compatible
        or unknown family, or a line the ground truth marks as related (e.g. the constant holding a hash name)."""
        expected = self.families_at(line)
        return bool(expected and (not families or families & expected)) or line in self.related


@dataclass
class TruthScore:
    tool: str
    project: str
    n_assets: int
    hits_strict: list[str] = field(default_factory=list)
    hits_lenient: list[str] = field(default_factory=list)
    sites: int = 0
    sites_in_file: int = 0
    sites_correct: int = 0  # on an asset line with a compatible/unknown family, on a related or accepted line
    false_sites: list[dict] = field(default_factory=list)
    hits_repo: list[str] | None = None  # repository level (location ignored); None: not computed
    repo_extra: list[str] = field(default_factory=list)  # families reported anywhere that the project does not have

    @property
    def recall_strict(self) -> float:
        return len(self.hits_strict) / self.n_assets if self.n_assets else 0.0

    @property
    def recall_lenient(self) -> float:
        return len(self.hits_lenient) / self.n_assets if self.n_assets else 0.0

    @property
    def recall_repo(self) -> float | None:
        if self.hits_repo is None:
            return None
        return len(self.hits_repo) / self.n_assets if self.n_assets else 0.0

    @property
    def precision(self) -> float:
        return self.sites_correct / self.sites_in_file if self.sites_in_file else 0.0

    @property
    def passes_bar(self) -> bool:
        return self.recall_lenient >= INCLUSION_BAR

    @property
    def missed(self) -> list[str]:
        return [a for a in self._asset_ids if a not in self.hits_lenient]

    @property
    def repo_missed(self) -> list[str] | None:
        return None if self.hits_repo is None else [a for a in self._asset_ids if a not in self.hits_repo]

    _asset_ids: list[str] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        recall_repo = self.recall_repo
        return {
            "tool": self.tool,
            "project": self.project,
            "n_assets": self.n_assets,
            "recall_strict": round(self.recall_strict, 3),
            "recall_lenient": round(self.recall_lenient, 3),
            "recall_repo": None if recall_repo is None else round(recall_repo, 3),
            "hits_strict": list(self.hits_strict),
            "hits_lenient": list(self.hits_lenient),
            "hits_repo": self.hits_repo,
            "missed": self.missed,
            "repo_missed": self.repo_missed,
            "repo_extra": list(self.repo_extra),
            "sites": self.sites,
            "sites_in_file": self.sites_in_file,
            "sites_correct": self.sites_correct,
            "precision": round(self.precision, 3),
            "passes_bar": self.passes_bar,
            "false_sites": list(self.false_sites),
        }


@lru_cache(maxsize=4)
def load_ground_truth(path: str | Path | None = None) -> dict[str, TruthProject]:
    data = yaml.safe_load(Path(path or DEFAULT_TRUTH).read_text(encoding="utf-8")) or {}
    assets = {
        str(a["id"]): (
            str(a["id"]),
            str(a.get("name") or a["id"]),
            frozenset(a.get("families") or []),
            a.get("expected") or {},
            str(a.get("insertion_style") or "direct"),
        )
        for a in data.get("assets") or []
    }
    projects: dict[str, TruthProject] = {}
    for name, p in (data.get("projects") or {}).items():
        lines = p.get("lines") or {}
        unknown = set(lines) - set(assets)
        if unknown:
            raise ValueError(f"ground truth project {name} references unknown assets {sorted(unknown)}")
        projects[str(name)] = TruthProject(
            name=str(name),
            language=str(p.get("language") or ""),
            file=str(p["file"]),
            assets=tuple(
                TruthAsset(
                    id=aid,
                    name=assets[aid][1],
                    families=assets[aid][2],
                    lines=tuple(int(x) for x in ls),
                    expected=assets[aid][3],
                    insertion_style=assets[aid][4],
                )
                for aid, ls in lines.items()
            ),
            related={int(k): str(v) for k, v in (p.get("related") or {}).items()},
            accepted={int(k): frozenset(v) for k, v in (p.get("accepted") or {}).items()},
        )
    return projects


def project_for_repo(full_name: str, truth: dict[str, TruthProject] | None = None) -> TruthProject | None:
    """`bfcbom/micro-java` -> the `micro-java` ground truth (by repo name)."""
    truth = truth or load_ground_truth()
    return truth.get(full_name.split("/", 1)[-1])


# ----- normalised records: call sites and repository level -----

SCORED_ASSET_TYPES = ("algorithm", None)  # as the call-site extraction: algorithms and assets without a type


@dataclass(frozen=True)
class Spot:
    """One location a tool reports, as the call-site scoring sees it: the union of the groups of all its records
    at (file, line .. line_end). A window (CryptobomForge's IBM-draft `detectionContext`) spans several lines."""

    file: str
    line: int
    line_end: int
    families: frozenset[str]
    raw: tuple[str, ...]

    @property
    def unknown(self) -> bool:
        return not self.families

    def covers(self, lines) -> bool:
        return any(self.line <= n <= self.line_end for n in lines)


def record_spots(records) -> list[Spot]:
    """Located records -> one Spot per (file, line, line_end); records without a file or line are left out."""
    acc: dict[tuple[str, int, int], tuple[set[str], list[str]]] = {}
    for r in records:
        if not r.file or not r.line:
            continue
        end = r.line_end if r.line_end and r.line_end > r.line else r.line
        fams, raw = acc.setdefault((r.file, r.line, end), (set(), []))
        if r.group:
            fams.add(r.group)
        if r.name and r.name not in raw:
            raw.append(r.name)
    return [Spot(f, ln, end, frozenset(fams), tuple(raw)) for (f, ln, end), (fams, raw) in sorted(acc.items())]


def score_records(records, project: TruthProject, tool: str, *, asset_types=SCORED_ASSET_TYPES) -> TruthScore:
    """Score one tool's normalised records at both levels (module docstring). Call sites: the asset's own lines,
    strict = a compatible family there, lenient = also an unknown family there; a window counts at every line it
    spans. `asset_types=None` keeps every crypto record except
    library entries (dependency inventory)."""
    if asset_types is None:
        own = [r for r in records if r.tool == tool and r.values.get("asset_type") != "library"]
    else:
        own = [r for r in records if r.tool == tool and r.values.get("asset_type") in asset_types]
    score = TruthScore(tool=tool, project=project.name, n_assets=len(project.assets))
    score._asset_ids = [a.id for a in project.assets]
    spots = record_spots(own)
    score.sites = len(spots)
    in_file = [s for s in spots if s.file == project.file]
    score.sites_in_file = len(in_file)
    for asset in project.assets:
        at = [s for s in in_file if s.covers(asset.lines)]
        if any(s.families & asset.families for s in at):
            score.hits_strict.append(asset.id)
            score.hits_lenient.append(asset.id)
        elif any(s.unknown for s in at):
            score.hits_lenient.append(asset.id)
    for s in in_file:
        if any(project.correct_at(n, s.families) for n in range(s.line, s.line_end + 1)):
            score.sites_correct += 1
        else:
            false_site = {"line": s.line, "families": sorted(s.families), "raw": list(s.raw)}
            if s.line_end != s.line:
                false_site["line_end"] = s.line_end
            score.false_sites.append(false_site)
    reported = {r.group for r in own if r.group}
    score.hits_repo = [a.id for a in project.assets if a.family & reported]
    score.repo_extra = sorted(reported - project.known_families)
    return score


# ----- normalised records vs the expected canonical record (per dimension) -----

# ground-truth key -> record dimension; `family` in the ground truth is the coarse group, `variant` the family/algorithm
_EXPECTED_DIMS = {"family": "group", "keysize": "key_size", "mode": "mode", "padding": "padding", "digest": "digest",
                  "curve": "curve", "iterations": "iterations"}  # fmt: skip


def expected_agreement(records, project: TruthProject, vocab=None) -> dict[str, dict[str, tuple]]:
    """Field by field: per ground-truth asset, (expected, reported) for every dimension `expected` names.

    The reported record is the algorithm record at one of the asset's lines (in the project's file) whose group
    matches the expected family; with several, the one agreeing on most dimensions. `variant` (SHA-256, ECDSA,
    PBKDF2) is compared with the record's algorithm or family. Expected values are canonicalised with the same
    vocabulary as the records (P-256 -> secp256r1). Assets without a record get `reported` None everywhere.
    """
    from .normalize.vocab import load_vocabulary  # local import: keeps groundtruth usable without the normaliser

    vocab = vocab or load_vocabulary()
    out: dict[str, dict[str, tuple]] = {}
    for asset in project.assets:
        expected = {}
        for key, value in asset.expected.items():
            if key in _EXPECTED_DIMS:
                dim = _EXPECTED_DIMS[key]
                expected[dim] = value if dim in ("group", "key_size", "iterations") else vocab.canonical(dim, value)
            elif key == "variant":
                expected["variant"] = vocab.canonical("algorithm", value) or vocab.canonical("family", value) or value
        candidates = [
            r
            for r in records
            if r.file == project.file and r.line in asset.lines and r.values.get("asset_type") in ("algorithm", None)
        ]

        best = max(
            (r for r in candidates if r.group == expected.get("group")),
            key=lambda r, exp=expected: sum(_reported(r, d, exp) == v for d, v in exp.items()),
            default=None,
        )
        out[asset.id] = {d: (v, _reported(best, d, expected)) for d, v in expected.items()}
    return out


def _reported(r, dim: str, expected: dict):
    if r is None:
        return None
    if dim == "variant":  # matches when either the algorithm or the family names the expected variant
        want = expected.get("variant")
        return want if want in (r.get("algorithm"), r.get("family")) else (r.get("algorithm") or r.get("family"))
    return r.get(dim)
