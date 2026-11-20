"""Offline launcher for securin-xbom: identical to the `xbom` entry point, but the semgrep.dev registry
rule sets are replaced by a frozen local copy.

Online, xbom pulls extra rules from the semgrep registry, which changes results between runs (and needs
network); its own offline fallback only triggers after a long network timeout, and then drops those rules
(micro Java 9/11 -> 6/11, Python 8/11 -> 7/11). The image freezes the registry rule sets from a pinned
semgrep-rules commit into XBOM_RULES_DIR (see freeze_rules.py). XBOM_RULES_DIR="" gives the bundled rules
only, as in the pilot of 2026-10-08.
The directory must be named `rules`: semgrep prefixes rule ids with the file path, and normalisers strip
everything up to `.rules.`.
"""

import glob
import os
import sys

import xbom.modules.cbom.semgrep as semgrep_module

RULES_DIR = os.getenv("XBOM_RULES_DIR", "/opt/xbom-registry/rules")
semgrep_module.REGISTRY_RULESETS = sorted(glob.glob(os.path.join(RULES_DIR, "*.yaml"))) if RULES_DIR else []

from xbom.cli import app  # noqa: E402  (must come after the patch)

sys.argv[0] = "xbom"
sys.exit(app())
