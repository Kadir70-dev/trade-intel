"""Domain enums.

All enums are `StrEnum` (3.12 native) and stored as TEXT in the database via
SQLAlchemy's `Enum(native_enum=False)` — gives a CHECK constraint that works
identically on SQLite and PostgreSQL without binding to PG-native enums.
"""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """Provenance of a `Group` of messages."""

    WHATSAPP = "whatsapp"
    CSV = "csv"
    EXCEL = "excel"
    MANUAL = "manual"


class IntentType(StrEnum):
    """Classified intent of a single message."""

    BUYER = "buyer"
    SELLER = "seller"
    BROKER = "broker"
    UNKNOWN = "unknown"


class Condition(StrEnum):
    """Stock condition for inventory / demand."""

    NEW = "new"
    USED = "used"
    REFURB = "refurb"
    SEALED = "sealed"
    UNKNOWN = "unknown"


class Urgency(StrEnum):
    """Buyer-side urgency signal."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MatchType(StrEnum):
    """Coarse match category."""

    EXACT = "exact"
    SIMILAR = "similar"
    PROBABLE = "probable"


class Grade(StrEnum):
    """Final match grade. See ARCHITECTURE.md → Semantic Matching Logic."""

    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"


class VectorEntityType(StrEnum):
    """Discriminator for the polymorphic `embeddings` table."""

    MESSAGE = "message"
    INVENTORY = "inventory"
    DEMAND = "demand"
    PRODUCT = "product"


class PipelineStage(StrEnum):
    """Lead pipeline state. Transitions logged via last_action / last_action_at."""

    NEW = "NEW"
    CONTACTED = "CONTACTED"
    RESPONDED = "RESPONDED"
    NEGOTIATING = "NEGOTIATING"
    READY = "READY"
    CLOSED = "CLOSED"
    LOST = "LOST"


class Priority(StrEnum):
    """Lead priority tier."""

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
