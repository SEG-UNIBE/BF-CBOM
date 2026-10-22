"""Matcher (c): RaQuN, the n-way model matching of Rubin and Chechik in the PyQuN implementation, over the normalised
records with a structured similarity (CLAUDE.md Phase 3, matcher (c)).

Models are tools; an element is one distinct asset a tool reports in the repository: the set of its canonical
dimension values as attributes (`algorithm=<identity at the ladder level>`, `family=`, `primitive=`, `mode=`,
`padding=`, `curve=`, `key_size=`, `digest=`; the standard's dimensions, not our coarse `group`), so RaQuN's Jaccard
similarity of attribute sets is the per-field agreement instead of the UI's bag of BERT-embedded strings. Its input
is the exact matcher's: algorithm records that are mapped and have an L4 identity (`levels.level_matches`), so
`undefined`, `ambiguous` and API names are never elements. Candidates come from a one-hot vector per
element and RaQuN's nearest-neighbour search; the greedy matching merges candidates by similarity under RaQuN's rule
that a match holds at most one element per model. Location-free, like RaQuN; the groups are repo scope. The BERT
variant stays the UI's "Find Similar Components" (misc/pyqun) as a comparison, not an analysis matcher.

Configured as in the RaQuN paper (Schultheiß et al. 2023): k = n neighbours per element (n = the tools in the
repository) and the Jaccard index with threshold 0.75, its best F-measure.

PyQuN comes with the `raqun` extra (the user's fork, pinned by its tag; the runner image and CI install it);
`available()` is false only in a host venv without it, and the CLI then skips this matcher. The groups are
deterministic, whatever `PYTHONHASHSEED` is: ids are ints in a fixed order, the one-hot dimensions are sorted, and
PyQuN >= 1.0.0 walks the elements by id and sorts the candidates by similarity with ties broken by id
(tests/test_raqun.py).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from common.tools_registry import votes

from ..levels import UNIT_LEVEL, identity, parameter_at
from ..normalize.record import NormRecord
from ..normalize.vocab import Vocabulary, load_vocabulary
from .base import MatchGroup

STRUCTURED_DIMS = ("family", "primitive", "mode", "padding", "curve", "key_size", "digest")
THRESHOLD = 0.75  # the Jaccard index a match must keep


def available() -> bool:
    try:
        import RaQuN_Lab.strategies.RaQuN.RaQuN  # noqa: F401
    except ImportError:
        return False
    return True


def attributes(rec: NormRecord, level: str, vocab: Vocabulary | None = None) -> frozenset[str]:
    """The structured attribute set of a record at a ladder level: `algorithm=<identity at the level>` plus
    `<dimension>=<value>` for every value admitted at that level (`levels.parameter_at`: nothing below L2, standard
    fields at L2, parsed names from L3, any source at L4)."""
    attrs = set()
    key = identity(rec, level, vocab)
    if key:
        attrs.add(f"algorithm={key}")
    for dim in STRUCTURED_DIMS:
        v = parameter_at(rec, level, dim)
        if v is not None:
            attrs.add(f"{dim}={v}")
    return frozenset(attrs)


class RaqunMatcher:
    name = "raqun"

    def __init__(
        self,
        *,
        level: str = "L3",
        voter_of: dict[str, str] | None = None,
        vocab: Vocabulary | None = None,
    ):
        self.level = level
        self.voter_of = voter_of if voter_of is not None else {}
        self.vocab = vocab or load_vocabulary()

    def match(self, records: list[NormRecord]) -> list[MatchGroup]:
        import numpy as np
        from RaQuN_Lab.datamodel.matching.similarities.match.JaccardIndex import JaccardIndex
        from RaQuN_Lab.datamodel.matching.similarities.match.shouldmatch.ThresholdSMStrategy import (
            ThresholdSMStrategy,
        )
        from RaQuN_Lab.datamodel.modelset.attribute.DefaultAttribute import DefaultAttribute
        from RaQuN_Lab.datamodel.modelset.Element import Element
        from RaQuN_Lab.datamodel.modelset.Model import Model
        from RaQuN_Lab.datamodel.modelset.ModelSet import ModelSet
        from RaQuN_Lab.strategies.RaQuN.candidatesearch.NNCandidateSearch.NNCandidateSearch import NNCandidateSearch
        from RaQuN_Lab.strategies.RaQuN.candidatesearch.NNCandidateSearch.vectorization.Vectorizer import Vectorizer
        from RaQuN_Lab.strategies.RaQuN.matching.GreedyMatching import GreedyMatching
        from RaQuN_Lab.strategies.RaQuN.RaQuN import VanillaRaQuN

        algorithms = [
            r
            for r in records
            if r.values.get("asset_type") == "algorithm"
            and r.resolution in (None, "mapped")
            and identity(r, UNIT_LEVEL, self.vocab)
        ]
        tools = sorted({r.tool for r in algorithms})
        behind: dict[int, list[NormRecord]] = {}
        models = []
        next_id = len(tools)  # RaQuN identifies models and elements by id alone: keep them disjoint
        for mi, tool in enumerate(tools):
            by_attrs: dict[frozenset[str], list[NormRecord]] = defaultdict(list)
            for r in algorithms:
                if r.tool == tool:
                    attrs = attributes(r, self.level, self.vocab)
                    if attrs:
                        by_attrs[attrs].append(r)
            elements = set()
            for attrs, recs in sorted(by_attrs.items(), key=lambda kv: sorted(kv[0])):
                element = Element(next_id, {DefaultAttribute(a) for a in attrs}, name=recs[0].name)
                element.set_model_id(mi)
                element.set_custom_id(next_id)
                behind[next_id] = recs
                elements.add(element)
                next_id += 1
            models.append(Model(mi, elements))
        if next_id == len(tools):
            return []
        model_set = ModelSet(set(models))

        class OneHot(Vectorizer):
            """One binary dimension per distinct attribute value of the model set."""

            def __init__(self):
                self.index: dict[str, int] = {}

            def innit(self, m_s) -> None:
                values = sorted({str(a.value) for e in m_s.get_elements() for a in e.attributes})
                self.index = {v: i for i, v in enumerate(values)}

            def vectorize(self, element):
                vec = np.zeros(max(len(self.index), 1))
                for a in element.attributes:
                    vec[self.index[str(a.value)]] = 1.0
                return vec

            def dim(self) -> int:
                return max(len(self.index), 1)

        search = NNCandidateSearch(vectorizer=OneHot(), neighbourhood_size=len(tools))
        matching = GreedyMatching(JaccardIndex(ThresholdSMStrategy(THRESHOLD)))
        strategy = VanillaRaQuN("bf-cbom-structured", candidate_search=search, matching_strategy=matching)
        matching, _ = strategy.match(model_set)
        matches = sorted(matching.get_matches(), key=lambda m: sorted(e.get_id() for e in m))
        groups = []
        for n, m in enumerate(matches, start=1):
            elements = sorted(m.get_elements(), key=lambda e: e.get_id())
            members = [r for e in elements for r in behind[e.get_id()]]
            keys = Counter(
                str(a.value).split("=", 1)[1]
                for e in elements
                for a in e.attributes
                if str(a.value).startswith("algorithm=")
            )
            key = sorted(keys, key=lambda k: (-keys[k], k))[0] if keys else "?"
            sets = [{str(a.value) for a in e.attributes} for e in elements]
            similarity = len(set.intersection(*sets)) / len(set.union(*sets)) if sets else 0.0
            # the items it holds, as the exact matcher counts them (matchlevels: items are L4 identities at every level)
            voting = [r for r in members if votes(self.voter_of, r.tool)] or members
            units = sorted({u for r in voting if (u := identity(r, UNIT_LEVEL, self.vocab))})
            extra = {
                "level": self.level,
                "n_records": len(members),
                "n_elements": len(elements),
                "similarity": round(similarity, 3),
                "units": units,
                "n_units": len(units),
            }
            groups.append(MatchGroup(f"R{n:05d}", "repo", key, members, extra))
        return groups
