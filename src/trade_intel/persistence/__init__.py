"""Persistence writers — turn parser+classifier output into DB rows.

P3.7 ships :mod:`trade_intel.persistence.ingest` for WhatsApp / CSV /
Excel message ingestion. Future phases will add match writers (Phase 4)
and lead-pipeline writers (Phase 5+).
"""

from trade_intel.persistence.ingest import IngestStats, ingest_messages
from trade_intel.persistence.matches import (
    MatchWriteStats,
    match_demand_against_inventory,
    match_inventory_against_demand,
)

__all__ = [
    "IngestStats",
    "MatchWriteStats",
    "ingest_messages",
    "match_demand_against_inventory",
    "match_inventory_against_demand",
]
