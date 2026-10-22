"""Scoring a tool against the micro projects' ground truth (corpus/micro/ground_truth.yaml, generated from the
snippet catalogs by mutation/micro.py; its header states the rules).

This implements the entry bar (`ENTRY_BAR`: a tool finds at least half of a project's calls, lenient, in at least
one language; below it in every language it was scored on, it is disqualified, `entry_verdict`) and gives recall,
precision and F1 per tool on the micro projects, at two levels (CLAUDE.md, refinement 1):
- call sites: strict (a record of the item's group on its lines; for a keygen item also a key record) and lenient
  (also a record of unknown family there); the entry bar uses lenient;
- repository: the item's group reported anywhere in the project, location ignored, so tools without locations get a
  number too. Items sharing a group (two RSA, three AES) are found together at this level.
Precision is over the places the tool reports in the project's source file (correct: on a call's lines with its
group or an unknown family, or on a line the ground truth accepts), so every report on a decoy (code that names or
resembles cryptography without using it) or elsewhere lowers it; F1 combines it with the lenient recall.

`score_records` scores the normalised records (analysis/normalize/) and is what `analysis micro` uses.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_TRUTH = Path(
    os.getenv("GROUND_TRUTH_PATH") or Path(__file__).resolve().parents[1] / "corpus" / "micro" / "ground_truth.yaml"
)
ENTRY_BAR = 0.5  # a tool must find at least half of a project's calls (lenient) in at least one language
PASSED, DISQUALIFIED, NOT_JUDGED = "passed", "disqualified", "not judged"  # entry_verdict


@dataclass(frozen=True)
class TruthAsset:
    id: str
    group: str  # the item's own group (algorithms.yaml `groups`)
    families: frozenset[str]  # the groups a record at its lines may have to count (default: the group alone)
    lines: tuple[int, ...]
    expected: dict  # the canonical record: unit (L4 identity), family, parameters
    insertion_style: str = "direct"  # `indirect`: the algorithm passes through a variable (the catalog's style)
    keygen: bool = False  # a key generation: a key record (related-crypto-material) of its group counts too

    @property
    def family(self) -> frozenset[str]:
        """The asset's own group for the repository level."""
        return frozenset({self.group})


@dataclass(frozen=True)
class TruthProject:
    name: str
    language: str
    file: str
    assets: tuple[TruthAsset, ...]
    related: dict[int, str]
    accepted: dict[int, frozenset[str]]
    decoys: dict[str, tuple[int, ...]] = field(default_factory=dict)  # decoy id -> its lines

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
    n_decoys: int = 0
    decoys_hit: list[str] = field(default_factory=list)  # decoys with a crypto record on their lines

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
    def precision(self) -> float | None:
        """Correct places out of the places the tool reported in the project's source file; None without any."""
        return self.sites_correct / self.sites_in_file if self.sites_in_file else None

    @property
    def f1(self) -> float:
        """Harmonic mean of the lenient recall (over the expected calls) and the precision (over the reported
        places); 0 when the tool found no call."""
        r, p = self.recall_lenient, self.precision
        return 2 * p * r / (p + r) if r and p else 0.0

    @property
    def bar_items(self) -> int:
        """The entry bar in calls: half of the project's, rounded up (7 of 14)."""
        return math.ceil(ENTRY_BAR * self.n_assets)

    @property
    def passes_bar(self) -> bool:
        return len(self.hits_lenient) >= self.bar_items

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
            "n_items": self.n_assets,
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
            "precision": None if self.precision is None else round(self.precision, 3),
            "f1": round(self.f1, 3),
            "bar_items": self.bar_items,
            "passes_bar": self.passes_bar,
            "false_sites": list(self.false_sites),
            "decoys": self.n_decoys,
            "decoys_hit": list(self.decoys_hit),
        }


def entry_verdict(scores) -> str:
    """A tool's verdict over its micro projects (one score per project, None where it has no scored run): passed when
    it clears the entry bar in at least one language, disqualified when it was scored and clears it nowhere, not
    judged without any scored run (it failed, timed out or does not support the languages)."""
    scored = [s for s in scores if s is not None]
    if not scored:
        return NOT_JUDGED
    return PASSED if any(s.passes_bar for s in scored) else DISQUALIFIED


@lru_cache(maxsize=4)
def load_ground_truth(path: str | Path | None = None) -> dict[str, TruthProject]:
    data = yaml.safe_load(Path(path or DEFAULT_TRUTH).read_text(encoding="utf-8")) or {}
    assets = {str(a["id"]): a for a in data.get("assets") or []}
    projects: dict[str, TruthProject] = {}
    for name, p in (data.get("projects") or {}).items():
        lines = p.get("lines") or {}
        unknown = set(lines) - set(assets)
        if unknown:
            raise ValueError(f"ground truth project {name} references unknown assets {sorted(unknown)}")
        expected = p.get("expected") or {}
        projects[str(name)] = TruthProject(
            name=str(name),
            language=str(p.get("language") or ""),
            file=str(p["file"]),
            assets=tuple(
                TruthAsset(
                    id=aid,
                    group=str(assets[aid]["group"]),
                    families=frozenset(assets[aid].get("families") or [assets[aid]["group"]]),
                    lines=tuple(int(x) for x in ls),
                    expected=dict(expected.get(aid) or {}),
                    insertion_style=str(assets[aid].get("insertion_style") or "direct"),
                    keygen=bool(assets[aid].get("keygen")),
                )
                for aid, ls in lines.items()
            ),
            related={int(k): str(v) for k, v in (p.get("related") or {}).items()},
            accepted={int(k): frozenset(v) for k, v in (p.get("accepted") or {}).items()},
            decoys={str(k): tuple(int(x) for x in d["lines"]) for k, d in (p.get("decoys") or {}).items()},
        )
    return projects


def project_for_repo(full_name: str, truth: dict[str, TruthProject] | None = None) -> TruthProject | None:
    """`bfcbom/micro-java` -> the `micro-java` ground truth (by repo name)."""
    truth = truth or load_ground_truth()
    return truth.get(full_name.split("/", 1)[-1])


# ----- normalised records: call sites and repository level -----

SCORED_ASSET_TYPES = ("algorithm", None)  # as the call-site extraction: algorithms and assets without a type
KEY_TYPE = "related-crypto-material"  # a keygen item's key records count too


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
    strict = a compatible family there (for a keygen asset also a key record of its group), lenient = also an unknown
    family there; a window counts at every line it spans. `asset_types=None` keeps every crypto record except
    library entries (dependency inventory). Decoys: any crypto record on a decoy's lines, whatever its type."""
    crypto = [r for r in records if r.tool == tool and r.values.get("asset_type") != "library"]
    own = crypto if asset_types is None else [r for r in crypto if r.values.get("asset_type") in asset_types]
    keys = [r for r in crypto if r.values.get("asset_type") == KEY_TYPE]
    score = TruthScore(tool=tool, project=project.name, n_assets=len(project.assets), n_decoys=len(project.decoys))
    score._asset_ids = [a.id for a in project.assets]
    spots = record_spots(own)
    score.sites = len(spots)
    in_file = [s for s in spots if s.file == project.file]
    score.sites_in_file = len(in_file)
    key_spots = [s for s in record_spots(keys) if s.file == project.file]
    for asset in project.assets:
        at = [s for s in in_file if s.covers(asset.lines)]
        if asset.keygen:
            at += [s for s in key_spots if s.covers(asset.lines) and s.families & asset.families]
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
    reported_keys = {r.group for r in keys if r.group}
    score.hits_repo = [a.id for a in project.assets if a.family & (reported | (reported_keys if a.keygen else set()))]
    score.repo_extra = sorted(reported - project.known_families)
    crypto_spots = [s for s in record_spots(crypto) if s.file == project.file]
    score.decoys_hit = [d for d, lines in project.decoys.items() if any(s.covers(lines) for s in crypto_spots)]
    return score


# ----- normalised records vs the expected canonical record (per dimension) -----


def expected_agreement(records, project: TruthProject, vocab=None) -> dict[str, dict[str, tuple]]:
    """Field by field: per ground-truth asset, (expected, reported) for every dimension its `expected` record names
    (`unit` against the record's L4 identity, the others against the record's values, both canonical).

    The reported record is the algorithm record at one of the asset's lines (in the project's file) of the asset's
    group; with several, the one agreeing on most dimensions. Assets without a record get `reported` None everywhere.
    """
    from .levels import UNIT_LEVEL, identity  # local import: keeps groundtruth usable without the normaliser
    from .normalize.vocab import load_vocabulary

    vocab = vocab or load_vocabulary()

    def reported(r, dim: str):
        if r is None:
            return None
        return identity(r, UNIT_LEVEL, vocab) if dim == "unit" else r.get(dim)

    out: dict[str, dict[str, tuple]] = {}
    for asset in project.assets:
        expected = {d: v for d, v in asset.expected.items() if v is not None}
        candidates = [
            r
            for r in records
            if r.file == project.file
            and r.line in asset.lines
            and r.values.get("asset_type") in ("algorithm", None)
            and r.group == asset.group
        ]
        best = max(candidates, key=lambda r: sum(reported(r, d) == v for d, v in expected.items()), default=None)
        out[asset.id] = {d: (v, reported(best, d)) for d, v in expected.items()}
    return out
