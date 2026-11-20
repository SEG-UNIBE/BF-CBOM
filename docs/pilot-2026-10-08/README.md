# Tool search of 2026-10-06 and pilot experiment of 2026-10-08

This directory preserves two things from the start of the journal extension: the record of the tool search and a
one-day pilot.

## The tool search

[`cbom_producers.csv`](cbom_producers.csv) is the record of the search for CBOM producers, as of 2026-10-06. It
holds 176 candidates, one per row, each with its tier (`tier`), whether it is in scope and why not (`scope`,
`in_scope`), its first publication and what that date rests on (`first_published`, `first_published_basis`), its
popularity (`popularity`, `gh_stars`) and the pages its entries were read from (`evidence_urls`, given for all 79
in-scope candidates and for 111 rows in all; nearly all other rows carry the candidate's own `url`).
The [survey](landscape-README.md) condenses the record into tables, explains the tiers in its legend and describes
in its section "Method" where and how the candidates were looked for.

The search was carried out with AI assistance. The evidence pages are named so that an entry can be checked
against its source.

Counts taken from the record are read like this:

- **176 candidates:** the rows of the file.
- **79 in scope:** the rows of tiers A to D with `scope` = `yes`, the producers of a CBOM by static analysis. Tier E,
  the commercial products, is a reference list.
- **62 of them first published in 2026:** `first_published` starts with `2026`.
- **Their GitHub stars:** `gh_stars` holds a count for 42 of the 62, and 40 of those have at most five.

The survey's section "Selection criteria and first selection" holds the criteria as decided on 2026-10-06 and the
tools that met them then. More tools were selected afterwards under relaxed criteria, so that list is not the final
one: the tools the study runs are listed in the [main README](../../README.md#cbom-generators-under-scrutiny).

## The pilot

A comparison of CBOM generators on pinned repositories and micro projects. Its measurements are historical: the
current methodology is documented in the [experiment guide](../../experiments/README.md) and the
[analysis guide](../../analysis/README.md), and the paper's numbers come from its own runs.

The pilot's micro projects, ground truth and sample verdicts were written with AI assistance.

Retained reports:

- [Java/Python analysis](javapy_analysis_report.txt)
- [Go, C and JavaScript report](native_REPORT.md)

Related artifacts in BF-CBOM:

- [Full pinned pilot corpus](../../experiments/corpus/pilot-2026-10-08-all.yaml) and the [paper-language subset](../../experiments/corpus/pilot-2026-10-08.yaml).
- [Selected CBOM fixtures](../../tests/fixtures/pilot/) and the [original ground truth](../../tests/fixtures/pilot/ground_truth-2026-10-08.yaml).

The original micro projects have since been replaced; see the [corpus guide](../../experiments/corpus/README.md).
Apart from the selected fixtures, the pilot's raw tool outputs and logs were not carried over.
