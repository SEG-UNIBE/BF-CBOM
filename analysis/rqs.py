"""The research questions, defined once (four since 2026-11-18, the paper's order
and wording): their titles, wording and the tables that answer each. A question's id is its place in `RQS`, so no
other module spells a number: the summary's headings and its "Research questions" section, the names of the paper's
tables (`slug`) and every cross-reference come from here.
The bar exam (subject selection, a preparatory step before the questions since 2026-10-30), the stability run and
RaQuN's cross-check are method sections next to the questions, named here too so every document calls them the
same."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RQ:
    title: str
    question: str
    tables: tuple[str, ...]  # the CSV tables of _ANALYSIS/ that answer it (`{i}`: the repetition)

    @property
    def id(self) -> str:
        return f"RQ{RQS.index(self) + 1}"

    @property
    def slug(self) -> str:
        """The title in lower case, the part of a LaTeX table's file name (`tab_<slug>_<what>.tex`)."""
        return self.title.lower()

    def part(self, letter: str) -> str:
        """A lettered part of the question, as the paper cites it: `RQ3a`."""
        return f"{self.id}{letter}"

    def heading(self, level: int = 2) -> str:
        return f"{'#' * level} {self.id} {self.title}"


CONTENT = RQ(
    "Content",
    "What do the generators report, and how far are their findings (a) traceable to the source code and (b) "
    "conformant to the standard they declare?",
    ("runs.csv", "records-run{i}.csv", "units-run{i}.csv", "units-repo-run{i}.csv", "coverage-run{i}.csv",
     "runtime.csv", "traceability-run{i}.csv", "schema-run{i}.csv", "conformance-run{i}.csv",
     "meaningful-run{i}.csv", "sources-run{i}.csv"),
)  # fmt: skip
NORMALIZATION = RQ(
    "Normalization",
    "How far can heterogeneous CBOMs be normalized onto a common vocabulary?",
    ("vocab.csv", "registry-run{i}.csv"),
)
AGREEMENT = RQ(
    "Agreement",
    "How much do the generators agree on their cryptographic findings, (a) as normalization proceeds and (b) once "
    "it is complete?",
    ("levels-run{i}.csv", "matchlevels-run{i}.csv", "items-run{i}.csv", "groups-exact-run{i}.csv",
     "members-exact-run{i}.csv", "agreement-exact-run{i}.csv", "score-run{i}.csv", "loo-run{i}.csv",
     "grouping-run{i}.csv"),
)  # fmt: skip
DETECTION = RQ(
    "Detection",
    "How accurately do the generators detect cryptographic calls injected into real projects and reject injected "
    "decoys?",
    ("mutants.csv", "mutation.csv", "mutation-rates.csv", "mutation-scores.csv", "bridge-run{i}.csv"),
)
RQS = (CONTENT, NORMALIZATION, AGREEMENT, DETECTION)

BAR_EXAM = "Bar exam"
BAR_EXAM_TABLES = ("micro-run{i}.csv", "micro-items-run{i}.csv")
# the notes are sentences, one per line in the summary (analysis/summary_blocks.py `_text`)
BAR_EXAM_NOTE = (
    "Before the questions, a preparatory step selects the subject tools.",
    "It has two parts: the tool search (where and when the candidates were looked for, and which requirements each "
    "had to meet, in `docs/tool-selection.md`) and the bar exam.",
    "The bar exam runs every tool on the generated micro projects (14 known calls and 8 decoys per language, so "
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
RAQUN = "RaQuN cross-check"
RAQUN_TABLES = (
    "matchlevels-raqun-run{i}.csv", "matchers-raqun-run{i}.csv", "groups-raqun-run{i}.csv",
    "members-raqun-run{i}.csv", "agreement-raqun-run{i}.csv", "raqun-L0-run{i}.csv", "raqun-L1-run{i}.csv",
    "raqun-L2-run{i}.csv", "raqun-L3-run{i}.csv", "raqun-L4-run{i}.csv",
)  # fmt: skip
RAQUN_NOTE = (
    "RaQuN, a published n-way matcher, is an optional cross-check of the exact matcher (`analysis report --raqun`), "
    "in a section and in files of its own.",
    "It answers none of the questions, and no table of theirs holds its numbers.",
)
