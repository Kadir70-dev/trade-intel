"""`Match` — scored demand↔inventory pairing.

Score formula (see ARCHITECTURE.md → Semantic Matching Logic):
    final_score = semantic_score * spec_fit_score * recency_multiplier * evidence_weight
Grades: A+ ≥ 0.85, A ≥ 0.70, B ≥ 0.50, C < 0.50.
"""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trade_intel.db.base import Base
from trade_intel.db.models._sa import str_enum_type
from trade_intel.db.models.enums import Grade, MatchType
from trade_intel.db.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from trade_intel.db.models.demand import Demand
    from trade_intel.db.models.inventory import Inventory
    from trade_intel.db.models.lead_pipeline import LeadPipeline


class Match(CreatedAtMixin, Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    demand_id: Mapped[int | None] = mapped_column(ForeignKey("demand.id"), default=None)
    inventory_id: Mapped[int | None] = mapped_column(ForeignKey("inventory.id"), default=None)
    match_type: Mapped[MatchType | None] = mapped_column(
        str_enum_type(MatchType, length=16),
        default=None,
    )
    semantic_score: Mapped[float | None] = mapped_column(default=None)
    spec_fit_score: Mapped[float | None] = mapped_column(default=None)
    recency_multiplier: Mapped[float | None] = mapped_column(default=None)
    evidence_weight: Mapped[float | None] = mapped_column(default=None)
    final_score: Mapped[float | None] = mapped_column(default=None)
    grade: Mapped[Grade | None] = mapped_column(
        str_enum_type(Grade, length=4),
        default=None,
    )
    reasoning: Mapped[str | None] = mapped_column(Text, default=None)

    demand: Mapped["Demand | None"] = relationship(back_populates="matches")
    inventory: Mapped["Inventory | None"] = relationship(back_populates="matches")
    pipeline_entries: Mapped[list["LeadPipeline"]] = relationship(
        back_populates="match", passive_deletes=True
    )

    __table_args__ = (
        Index("idx_matches_grade", "grade"),
        Index("idx_matches_score", "final_score"),
        Index("idx_matches_demand", "demand_id"),
        Index("idx_matches_inventory", "inventory_id"),
    )
