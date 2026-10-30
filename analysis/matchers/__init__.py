"""n-way matchers over the normalised records (CLAUDE.md Phase 3, "match"): the same records in, match groups with
per-dimension agreement out, so matchers can be compared by joining their tables (RQ4).

- `base`:  the group type, the matcher interface and the per-dimension agreement of a group
- `exact`: matcher (b), content-based on the canonical algorithm identity of a ladder level, at repository level
           (every tool takes part) with call-site subgroups (same file, lines within a window) for located records
- `raqun`: matcher (c), RaQuN (PyQuN) over the records with a structured per-field similarity; optional package
- `compare`: how far two matchers agree on which records belong together (RQ4)
"""

from .base import PARAMETER_DIMS, MatchGroup, agreement
from .exact import ExactMatcher

__all__ = ["PARAMETER_DIMS", "ExactMatcher", "MatchGroup", "agreement"]
