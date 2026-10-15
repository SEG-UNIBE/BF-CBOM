"""Offline launcher for securin-xbom: identical to the `xbom` entry point, but with the semgrep.dev
registry rule sets emptied, so only xbom's bundled rules are used.

Online, xbom pulls extra rules from the semgrep registry, which changes results between runs (and
needs network); its own offline fallback only triggers after a long network timeout. Taken from the
pilot experiment of 2026-10-08 (work/nano/full/xbom_offline.py).
"""

import sys

import xbom.modules.cbom.semgrep as semgrep_module

semgrep_module.REGISTRY_RULESETS = []

from xbom.cli import app  # noqa: E402  (must come after the patch)

sys.argv[0] = "xbom"
sys.exit(app())
