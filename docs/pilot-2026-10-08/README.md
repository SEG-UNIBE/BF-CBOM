# Pilot experiment of 2026-10-08

This directory preserves a colleague's one-day pilot for the journal extension: a comparison of CBOM generators
on pinned repositories and micro projects. These are historical results; current methodology and project status
are documented in [CLAUDE.md](../../CLAUDE.md) and the [analysis guide](../../analysis/README.md).

The micro projects, ground truth and sample verdicts were produced with AI assistance and have not been
human-reviewed. The pilot's grading rules were provided by A. Boll. Review these artifacts before paper use.

Retained reports and survey:

- [Java/Python analysis](javapy_analysis_report.txt)
- [Go, C and JavaScript report](native_REPORT.md)
- [Tool landscape survey](landscape-README.md) and [survey data](cbom_producers.csv)

Related artifacts in BF-CBOM:

- [Full pinned pilot corpus](../../experiments/corpus/pilot-2026-10-08-all.yaml) and the [paper-language subset](../../experiments/corpus/pilot-2026-10-08.yaml).
- [Selected CBOM fixtures](../../tests/fixtures/pilot/) and the [original ground truth](../../tests/fixtures/pilot/ground_truth-2026-10-08.yaml).
- [Tool registry](../../common/tools.yaml), which retains the dated pilot verdicts.

The original micro projects have since been replaced; see the [corpus guide](../../experiments/corpus/README.md).
Apart from the selected fixtures, raw tool outputs, logs and private session transcripts were not carried over.
