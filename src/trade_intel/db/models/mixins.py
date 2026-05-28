"""Cross-cutting column mixins for ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func


class CreatedAtMixin:
    """Adds `created_at` with a DB-side default. Server-generated so wall-clock
    consistency holds across processes / time zones."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class TimestampMixin(CreatedAtMixin):
    """Adds `updated_at` on top of `created_at`. Use for rows that mutate
    after insert (e.g. `lead_pipeline`)."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )
