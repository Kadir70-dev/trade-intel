"""`Demand` — buyer-side request extracted from a message."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trade_intel.db.base import Base
from trade_intel.db.models._sa import str_enum_type
from trade_intel.db.models.enums import Condition, Urgency

if TYPE_CHECKING:
    from trade_intel.db.models.buyer import Buyer
    from trade_intel.db.models.match import Match
    from trade_intel.db.models.message import Message


class Demand(Base):
    __tablename__ = "demand"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), default=None)
    buyer_id: Mapped[int | None] = mapped_column(ForeignKey("buyers.id"), default=None)
    brand: Mapped[str | None] = mapped_column(String, default=None)
    product_raw: Mapped[str | None] = mapped_column(String, default=None)
    normalized_product: Mapped[str] = mapped_column(String, nullable=False)
    variant: Mapped[str | None] = mapped_column(String, default=None)
    storage: Mapped[str | None] = mapped_column(String, default=None)
    condition: Mapped[Condition | None] = mapped_column(
        str_enum_type(Condition, length=16),
        default=None,
    )
    quantity: Mapped[int | None] = mapped_column(Integer, default=None)
    quantity_raw: Mapped[str | None] = mapped_column(String, default=None)
    budget_aed: Mapped[float | None] = mapped_column(default=None)
    location: Mapped[str | None] = mapped_column(String, default=None)
    urgency: Mapped[Urgency | None] = mapped_column(
        str_enum_type(Urgency, length=8),
        default=None,
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    message: Mapped["Message | None"] = relationship(back_populates="demands")
    buyer: Mapped["Buyer | None"] = relationship(back_populates="demands")
    matches: Mapped[list["Match"]] = relationship(back_populates="demand", passive_deletes=True)

    __table_args__ = (
        Index("idx_demand_product", "normalized_product"),
        Index("idx_demand_buyer", "buyer_id"),
        Index("idx_demand_active", "is_active"),
        Index("idx_demand_posted_at", "posted_at"),
    )
