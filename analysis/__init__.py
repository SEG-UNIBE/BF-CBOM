"""Offline analysis of stored CBOMs (BF-CBOM 2.0, Phase 3): normalise -> match -> score.

Pure Python, no Redis, no UI. Reads the result tree written by the batch runner
(`common/experiment.py`) or any CycloneDX CBOM dict. Current contents:

- `normalize`:   CBOM -> normalised records (CBOM^norm), one adapter spec per tool (analysis/adapters/),
                 shared vocabulary (analysis/data/algorithms.yaml + the CycloneDX 1.7 registry), conformance
                 of every value read against the CycloneDX version the CBOM declares
- `levels`:      the L0-L4 normalisation ladder (how much agreement normalisation creates) and which algorithms
                 each tool reports per repository
- `schema`:      RQ1: whole-document CycloneDX validation, meaningful shares, traceability (`snapshots` reads the
                 files of a pinned commit from the git mirror or a fetched cache)
- `matchers`:    n-way matchers over the records: `exact` (matcher (b), canonical identity, call-site subgroups)
- `groundtruth`: scoring against the micro projects' ground truth (corpus/micro/ground_truth.yaml)
- `tables`:      flat CSV tables (runs, normalised records, matcher groups/members/agreement, micro scores)
- `summary`:     `_SUMMARY.md` rendered from the CSV tables; `freeze`: the frozen vocabulary and specs
"""
