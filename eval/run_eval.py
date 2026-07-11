#!/usr/bin/env python
"""Run the offline rules eval. Thin wrapper over sitesift.evalharness.

Usage: ``python eval/run_eval.py`` (from the repo root), or ``sitesift eval``.
"""

from __future__ import annotations

from sitesift.evalharness import main

if __name__ == "__main__":
    main()
