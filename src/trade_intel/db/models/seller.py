"""`Seller` — canonicalized seller identity with alias + tag handling."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trade_intel.db.base import Base
from trade_intel.db.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from trade_intel.db.models.inventory import Inventory
    from trade_intel.db.models.message import Message


class Seller(CreatedAtMixin, Base):
    __tablename__ = "sellers"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str | None] = mapped_column(String, default=None)
    aliases: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    location: Mapped[str | None] = mapped_column(String, default=None)
    tags: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    post_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    trust_score: Mapped[float | None] = mapped_column(default=None)

    messages: Mapped[list["Message"]] = relationship(back_populates="seller", passive_deletes=True)
    inventory_items: Mapped[list["Inventory"]] = relationship(
        back_populates="seller", passive_deletes=True
    )

    __table_args__ = (
        Index(
            "uq_sellers_phone",
            "phone",
            unique=True,
            sqlite_where=text("phone IS NOT NULL"),
            postgresql_where=text("phone IS NOT NULL"),
        ),
        Index("idx_sellers_canonical_name", "canonical_name"),
    )
