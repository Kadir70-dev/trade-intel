"""Trader CLI (Phase 5).

Entry point: ``trade-intel <command> [...]``.  See :mod:`main` for the
Typer app and individual command implementations.
"""

from trade_intel.cli.main import app

__all__ = ["app"]
