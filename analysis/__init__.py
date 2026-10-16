"""Offline analysis of stored CBOMs (BF-CBOM 2.0, Phase 3): normalise -> match -> score.

Pure Python, no Redis, no UI. Reads the result tree written by the batch runner
(`common/experiment.py`) or any CycloneDX CBOM dict. Current contents:

- `normalize`:   CBOM -> normalised records (CBOM^norm), one adapter spec per tool (analysis/adapters/),
                 shared vocabulary (analysis/data/algorithms.yaml + the CycloneDX 1.7 registry), conformance
                 of every value read against the CycloneDX version the CBOM declares
- `families`:    coarse algorithm-family table (analysis/data/families.yaml), the `group` of a record
- `sites`:       CBOM -> call sites (tool, file, line, families), the input of the call-site matcher
- `match`:       location-aware n-way matching of call sites (same file, line +-k, compatible family)
- `groundtruth`: scoring against the micro projects' ground truth (corpus/micro/ground_truth.yaml)
- `tables`:      flat CSV tables (runs, call sites, normalised records, matcher groups, micro scores)
"""
