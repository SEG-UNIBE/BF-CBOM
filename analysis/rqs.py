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
    tables: tuple[str, ...]  # the CSV tables of _ANALYSIS/ that answer it (`{k}`: the repetition)

    def heading(self, level: int = 2) -> str:
        return f"{'#' * level} {self.id} {self.title}"


RQ1 = RQ(
    "RQ1",
    "Conformance",
    "Which CycloneDX version and CBOM extension do the tools declare, and do their documents conform to it?",
    ("schema-run{k}.csv", "conformance-run{k}.csv", "meaningful-run{k}.csv"),
)
RQ2 = RQ(
    "RQ2",
    "Volume",
    "How much do the tools report, and in what detail?",
    ("runs.csv", "records-run{k}.csv", "units-run{k}.csv", "units-repo-run{k}.csv", "coverage-run{k}.csv",
     "traceability-run{k}.csv"),
)  # fmt: skip
RQ3 = RQ(
    "RQ3",
    "Agreement",
    "How far do the tools agree on what is in a repository, per kind of information, and how much of their "
    "disagreement is representational rather than substantive?",
    ("levels-run{k}.csv", "matchlevels-run{k}.csv", "vocab.csv", "items-run{k}.csv", "groups-exact-run{k}.csv",
     "members-exact-run{k}.csv", "agreement-exact-run{k}.csv", "groups-raqun-run{k}.csv", "members-raqun-run{k}.csv",
     "agreement-raqun-run{k}.csv", "matchers-run{k}.csv", "score-run{k}.csv", "loo-run{k}.csv",
     "grouping-run{k}.csv"),
)  # fmt: skip
RQ4 = RQ(
    "RQ4",
    "Effectiveness",
    "Given calls and decoys planted into real code, how well does each tool find the calls and ignore the decoys, "
    "and does the agreement of RQ3 predict that?",
    ("mutants.csv", "mutation.csv", "mutation-rates.csv", "mutation-scores.csv", "bridge-run{k}.csv"),
)
RQS = (RQ1, RQ2, RQ3, RQ4)

BAR_EXAM = "Bar exam: the micro projects"
BAR_EXAM_TABLES = ("micro-run{k}.csv", "micro-items-run{k}.csv")
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
