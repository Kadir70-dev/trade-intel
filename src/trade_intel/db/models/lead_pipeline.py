"""`LeadPipeline` — mutable state machine row per `Match`.

Stages: NEW → CONTACTED → RESPONDED → NEGOTIATING → READY → CLOSED | LOST.
Transitions are advisory (no DB-level enforcement) — service layer is the gate.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trade_intel.db.base import Base
from trade_intel.db.models._sa import str_enum_type
from trade_intel.db.models.enums import PipelineStage, Priority
from trade_intel.db.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from trade_intel.db.models.match import Match


class LeadPipeline(TimestampMixin, Base):
    __tablename__ = "lead_pipeline"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id"), default=None)
    stage: Mapped[PipelineStage] = mapped_column(
        str_enum_type(PipelineStage, length=16),
        nullable=False,
    )
    priority: Mapped[Priority | None] = mapped_column(
        str_enum_type(Priority, length=4),
        default=None,
    )
    close_probability: Mapped[float | None] = mapped_column(default=None)
    last_action: Mapped[str | None] = mapped_column(Text, default=None)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    match: Mapped["Match | None"] = relationship(back_populates="pipeline_entries")

    __table_args__ = (
        Index("idx_pipeline_stage", "stage"),
        Index("idx_pipeline_priority", "priority"),
        Index("idx_pipeline_match", "match_id"),
    )
