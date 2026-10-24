"""The research questions, defined once (CLAUDE.md "Research questions", decided 2026-10-22): their ids, titles,
wording and the tables that answer each. The summary's headings and its "Research questions" section come from here;
tests/test_rqs.py keeps CLAUDE.md's section equal to it. The bar exam and the stability run are method sections next
to the questions, named here too so every document calls them the same."""

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
    "Conformance",
    "Which CycloneDX version and CBOM extension do the tools declare, and do their documents conform to it?",
    ("schema-run{i}.csv", "conformance-run{i}.csv", "meaningful-run{i}.csv"),
)
RQ2 = RQ(
    "RQ2",
    "Volume",
    "How much do the tools report, and in what detail?",
    ("runs.csv", "records-run{i}.csv", "units-run{i}.csv", "units-repo-run{i}.csv", "coverage-run{i}.csv",
     "traceability-run{i}.csv", "runtime.csv"),
)  # fmt: skip
RQ3 = RQ(
    "RQ3",
    "Agreement",
    "How far do the tools agree on what is in a repository, per kind of information, and how much of their "
    "disagreement is representational rather than substantive?",
    ("levels-run{i}.csv", "matchlevels-run{i}.csv", "vocab.csv", "items-run{i}.csv", "groups-exact-run{i}.csv",
     "members-exact-run{i}.csv", "agreement-exact-run{i}.csv", "groups-raqun-run{i}.csv", "members-raqun-run{i}.csv",
     "agreement-raqun-run{i}.csv", "raqun-L0-run{i}.csv", "raqun-L1-run{i}.csv", "raqun-L2-run{i}.csv",
     "raqun-L3-run{i}.csv", "raqun-L4-run{i}.csv", "matchers-run{i}.csv", "score-run{i}.csv", "loo-run{i}.csv",
     "grouping-run{i}.csv"),
)  # fmt: skip
RQ4 = RQ(
    "RQ4",
    "Effectiveness",
    "Given calls and decoys planted into real code, how well does each tool find the calls and ignore the decoys, "
    "and does the agreement of RQ3 predict that?",
    ("mutants.csv", "mutation.csv", "mutation-rates.csv", "mutation-scores.csv", "bridge-run{i}.csv"),
)
RQS = (RQ1, RQ2, RQ3, RQ4)

BAR_EXAM = "Bar exam: the micro projects"
BAR_EXAM_TABLES = ("micro-run{i}.csv", "micro-items-run{i}.csv")
BAR_EXAM_NOTE = (
    "Before the questions comes the bar exam: every tool on the generated micro projects (14 planted calls and 4 "
    "decoys per language, so with ground truth) gives recall, precision and F1, the verdict (`passed` at 7 of 14 "
    "calls, leniently, in one language, else `disqualified`) and the languages a tool is eligible in, which the user "
    "writes into the later specs by hand; the one filter on the tools."
)
STABILITY = "Run-to-run stability"
STABILITY_NOTE = (
    "Run-to-run stability (an experiment with repeated runs) is a method check behind every agreement claim, not a "
    "question of its own."
)
