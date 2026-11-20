"""n-way matchers over the normalized records: the same records in, match groups with
per-dimension agreement out, so matchers can be compared by joining their tables (Agreement).

- `base`:  the group type, the matcher interface and the per-dimension agreement of a group
- `exact`: matcher (b), content-based on the canonical algorithm identity of a ladder level, at repository level
           (every tool takes part) with call-site subgroups (same file, lines within a window) for located records
- `raqun`: matcher (c), RaQuN (PyQuN) over the records with a structured per-field similarity; an optional
           cross-check (`analysis report --raqun`, the `raqun` extra), never part of another matcher's tables
- `compare`: how far two matchers agree on which records belong together (RaQuN against `exact`)
"""

from .base import PARAMETER_DIMS, MatchGroup, agreement
from .exact import ExactMatcher

__all__ = ["PARAMETER_DIMS", "ExactMatcher", "MatchGroup", "agreement"]
