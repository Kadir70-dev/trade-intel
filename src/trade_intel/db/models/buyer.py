"""`Buyer` — canonicalized buyer identity with repeat-buyer signals."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trade_intel.db.base import Base
from trade_intel.db.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from trade_intel.db.models.demand import Demand
    from trade_intel.db.models.message import Message


class Buyer(CreatedAtMixin, Base):
    __tablename__ = "buyers"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str | None] = mapped_column(String, default=None)
    aliases: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    location: Mapped[str | None] = mapped_column(String, default=None)
    tags: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    messages: Mapped[list["Message"]] = relationship(back_populates="buyer", passive_deletes=True)
    demands: Mapped[list["Demand"]] = relationship(back_populates="buyer", passive_deletes=True)

    __table_args__ = (
        Index(
            "uq_buyers_phone",
            "phone",
            unique=True,
            sqlite_where=text("phone IS NOT NULL"),
            postgresql_where=text("phone IS NOT NULL"),
        ),
        Index("idx_buyers_canonical_name", "canonical_name"),
    )
