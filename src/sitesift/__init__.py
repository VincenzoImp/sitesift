"""sitesift — structured, validated, reproducible metadata for URLs at scale.

Public version constant. Everything else is imported from submodules on demand
to keep ``import sitesift`` cheap (the CLI is the primary entry point).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.2"
