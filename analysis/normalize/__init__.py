"""CBOM normalisation (Phase 3, "normalise" of normalise -> match -> score).

Three kinds of files, so nothing is confused:
- one adapter spec per tool, `analysis/adapters/<tool>.yaml`: where the tool puts each dimension and how it spells
  things (its mapping and dictionary), inheriting the CycloneDX reader `_cyclonedx.yaml`;
- one shared vocabulary for synonyms that are not tool-specific: the vendored CycloneDX 1.7 registry
  (`analysis/data/cyclonedx/`) plus `analysis/data/algorithms.yaml`;
- one target format for every tool, `analysis/data/cbom-norm.schema.json`: the normalised file (CBOM^norm).

Worker adapters (`common/cbom_adapters.py`, `workers/*`) are something else: they make a tool's raw output usable
CycloneDX at scan time. The adapter specs here read CycloneDX into records, offline.
"""

from .engine import normalize_cbom
from .record import RECORD_COLUMNS, SCHEMA_ID, VALUE_DIMS, NormalizedCbom, NormRecord
from .report import VOCAB_COLUMNS, Report
from .specs import load_adapter
from .vocab import load_vocabulary

__all__ = [
    "RECORD_COLUMNS",
    "SCHEMA_ID",
    "VALUE_DIMS",
    "VOCAB_COLUMNS",
    "NormRecord",
    "NormalizedCbom",
    "Report",
    "load_adapter",
    "load_vocabulary",
    "normalize_cbom",
]
