"""FastAPI HTTP API (Phase 6).

Entry point: :func:`create_app` returns a configured :class:`FastAPI`
instance. The default factory pattern keeps Settings injection clean —
each call reads current env-derived settings, so test fixtures that
swap `SQLITE_URL` / `API_KEY` between cases get a fresh app.
"""

from trade_intel.api.main import create_app

__all__ = ["create_app"]
