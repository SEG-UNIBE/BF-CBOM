"""Offline analysis of stored CBOMs (BF-CBOM 2.0, Phase 3): normalise -> match -> score.

Pure Python, no Redis, no UI. Reads the result tree written by the batch runner
(`common/experiment.py`) or any CycloneDX CBOM dict. Current contents (seeded from the pilot
experiment of 2026-10-08):

- `families`:    coarse algorithm-family table (analysis/data/families.yaml)
- `sites`:       CBOM -> call sites (tool, file, line, families)
- `match`:       location-aware n-way matching of call sites (same file, line +-k, compatible family)
- `groundtruth`: scoring against the micro projects' ground truth (corpus/micro/ground_truth.yaml)
"""
