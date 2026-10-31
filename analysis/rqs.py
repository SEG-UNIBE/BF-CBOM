"""The research questions, defined once (CLAUDE.md "Research questions"; five since 2026-10-30, the paper's order):
their ids, titles, wording and the tables that answer each. The summary's headings and its "Research questions"
section come from here; tests/test_rqs.py keeps CLAUDE.md's section equal to it. The bar exam (subject selection,
a preparatory step before the questions since 2026-10-30) and the stability run are method sections next to the
questions, named here too so every document calls them the same."""

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
    "Volume",
    "What do the generators' CBOMs contain: how many assets, records and items of which kinds, in what detail, and "
    "with what traceability to source code?",
    ("runs.csv", "records-run{i}.csv", "units-run{i}.csv", "units-repo-run{i}.csv", "coverage-run{i}.csv",
     "traceability-run{i}.csv", "runtime.csv"),
)  # fmt: skip
RQ2 = RQ(
    "RQ2",
    "Conformity",
    "Which CBOM standard and version do the generators follow, and how far do their documents comply with it in "
    "letter and in substance: do they validate against its schema, and do they carry their information in the "
    "standard's own fields?",
    ("schema-run{i}.csv", "conformance-run{i}.csv", "meaningful-run{i}.csv", "sources-run{i}.csv"),
)
RQ3 = RQ(
    "RQ3",
    "Normalisation",
    "How far does CycloneDX's own registry carry the normalisation of real CBOMs, what must be added beyond it, and "
    "how much of the generators' disagreement is representational rather than substantive?",
    ("levels-run{i}.csv", "matchlevels-run{i}.csv", "vocab.csv", "registry-run{i}.csv"),
)
RQ4 = RQ(
    "RQ4",
    "Agreement",
    "To what extent do generators agree on cryptographic findings?",
    ("items-run{i}.csv", "groups-exact-run{i}.csv", "members-exact-run{i}.csv", "agreement-exact-run{i}.csv",
     "groups-raqun-run{i}.csv", "members-raqun-run{i}.csv", "agreement-raqun-run{i}.csv", "raqun-L0-run{i}.csv",
     "raqun-L1-run{i}.csv", "raqun-L2-run{i}.csv", "raqun-L3-run{i}.csv", "raqun-L4-run{i}.csv",
     "matchers-run{i}.csv", "score-run{i}.csv", "loo-run{i}.csv", "grouping-run{i}.csv"),
)  # fmt: skip
RQ5 = RQ(
    "RQ5",
    "Detection",
    "Given cryptographic calls and decoys planted into real projects, how well does each generator detect the calls "
    "and ignore the decoys?",
    ("mutants.csv", "mutation.csv", "mutation-rates.csv", "mutation-scores.csv", "bridge-run{i}.csv"),
)
RQS = (RQ1, RQ2, RQ3, RQ4, RQ5)

BAR_EXAM = "Bar exam"
BAR_EXAM_TABLES = ("micro-run{i}.csv", "micro-items-run{i}.csv")
# the notes are sentences, one per line in the summary (analysis/summary.py `_text`)
BAR_EXAM_NOTE = (
    "Before the questions, a preparatory step selects the subject tools.",
    "It has two parts: the tool search (where and when the candidates were looked for, and which requirements each "
    "had to meet, in `docs/tool-selection.md`) and the bar exam.",
    "The bar exam runs every tool on the generated micro projects (14 planted calls and 8 decoys per language, so "
    "with ground truth), in a section of its own when the experiment includes them.",
    "It gives recall, precision and F1, the verdict (`bar passed` at 7 of 14 calls, leniently, in one language, else "
    "`bar failed`) and the languages a tool is eligible in, which the later experiments name by hand.",
    "It is the one filter on the tools.",
)
STABILITY = "Run-to-run stability"
STABILITY_NOTE = (
    "Run-to-run stability (an experiment with repeated runs) is a method check behind every agreement claim, not a "
    "question of its own.",
)
