"""The research questions, defined once (CLAUDE.md "Research questions"; six since 2026-10-29, the paper's order):
their ids, titles, wording and the tables that answer each. The summary's headings and its "Research questions"
section come from here; tests/test_rqs.py keeps CLAUDE.md's section equal to it. The stability run is a method
section next to the questions, named here too so every document calls it the same."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RQ:
    id: str
    title: str
    question: str
    tables: tuple[str, ...]  # the CSV tables of _ANALYSIS/ that answer it (`{i}`: the repetition)

    def heading(self, level: int = 2) -> str:
        return f"{'#' * level} {self.id} {self.title}"


RQ1 = RQ(
    "RQ1",
    "Tool selection",
    "Which CBOM generators for source code exist as of October 2026, and which of them meet the minimum requirements "
    "of this study?",
    ("micro-run{i}.csv", "micro-items-run{i}.csv"),
)
RQ2 = RQ(
    "RQ2",
    "Volume",
    "What do the generators' CBOMs contain: how many assets, records and items of which kinds, in what detail, and "
    "with what traceability to source code?",
    ("runs.csv", "records-run{i}.csv", "units-run{i}.csv", "units-repo-run{i}.csv", "coverage-run{i}.csv",
     "traceability-run{i}.csv", "runtime.csv"),
)  # fmt: skip
RQ3 = RQ(
    "RQ3",
    "Conformity",
    "Which CBOM standard and version do the generators follow, and how far do their documents comply with it in "
    "letter and in substance: do they validate against its schema, and do they carry their information in the "
    "standard's own fields?",
    ("schema-run{i}.csv", "conformance-run{i}.csv", "meaningful-run{i}.csv", "sources-run{i}.csv"),
)
RQ4 = RQ(
    "RQ4",
    "Normalisation",
    "How far does CycloneDX's own registry carry the normalisation of real CBOMs, what must be added beyond it, and "
    "how much of the generators' disagreement is representational rather than substantive?",
    ("levels-run{i}.csv", "matchlevels-run{i}.csv", "vocab.csv", "registry-run{i}.csv"),
)
RQ5 = RQ(
    "RQ5",
    "Agreement",
    "To what extent do generators agree on cryptographic findings?",
    ("items-run{i}.csv", "groups-exact-run{i}.csv", "members-exact-run{i}.csv", "agreement-exact-run{i}.csv",
     "groups-raqun-run{i}.csv", "members-raqun-run{i}.csv", "agreement-raqun-run{i}.csv", "raqun-L0-run{i}.csv",
     "raqun-L1-run{i}.csv", "raqun-L2-run{i}.csv", "raqun-L3-run{i}.csv", "raqun-L4-run{i}.csv",
     "matchers-run{i}.csv", "score-run{i}.csv", "loo-run{i}.csv", "grouping-run{i}.csv"),
)  # fmt: skip
RQ6 = RQ(
    "RQ6",
    "Detection",
    "Given cryptographic calls and decoys planted into real projects, how well does each generator detect the calls "
    "and ignore the decoys?",
    ("mutants.csv", "mutation.csv", "mutation-rates.csv", "mutation-scores.csv", "bridge-run{i}.csv"),
)
RQS = (RQ1, RQ2, RQ3, RQ4, RQ5, RQ6)

RQ1_NOTE = (
    "RQ1 has two parts: the tool search (where and when the candidates were looked for and which requirements each "
    "had to meet; `docs/tool-selection.md`) and the entry exam, which this summary reports: every tool on the "
    "generated micro projects (14 planted calls and 8 decoys per language, so with ground truth) gives recall, "
    "precision and F1, the verdict (`passed` at 7 of 14 calls, leniently, in one language, else `disqualified`) and "
    "the languages a tool is eligible in, which the user writes into the later specs by hand; the one filter on the "
    "tools."
)
STABILITY = "Run-to-run stability"
STABILITY_NOTE = (
    "Run-to-run stability (an experiment with repeated runs) is a method check behind every agreement claim, not a "
    "question of its own."
)
