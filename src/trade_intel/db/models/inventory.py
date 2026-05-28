"""`Inventory` — seller-side stock extracted from a message.

`posted_at` is the message timestamp; freshness tier is computed at read time
(see DECISIONS.md D-010), never stored. `is_active` flips on explicit signal
(sold-out, removed) — not on age alone.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trade_intel.db.base import Base
from trade_intel.db.models._sa import str_enum_type
from trade_intel.db.models.enums import Condition

if TYPE_CHECKING:
    from trade_intel.db.models.match import Match
    from trade_intel.db.models.message import Message
    from trade_intel.db.models.seller import Seller


class Inventory(Base):
    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), default=None)
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("sellers.id"), default=None)
    brand: Mapped[str | None] = mapped_column(String, default=None)
    product_raw: Mapped[str | None] = mapped_column(String, default=None)
    normalized_product: Mapped[str] = mapped_column(String, nullable=False)
    variant: Mapped[str | None] = mapped_column(String, default=None)
    storage: Mapped[str | None] = mapped_column(String, default=None)
    color: Mapped[str | None] = mapped_column(String, default=None)
    condition: Mapped[Condition | None] = mapped_column(
        str_enum_type(Condition, length=16),
        default=None,
    )
    quantity: Mapped[int | None] = mapped_column(Integer, default=None)
    quantity_raw: Mapped[str | None] = mapped_column(String, default=None)
    price_aed: Mapped[float | None] = mapped_column(default=None)
    price_raw: Mapped[str | None] = mapped_column(String, default=None)
    location: Mapped[str | None] = mapped_column(String, default=None)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    message: Mapped["Message | None"] = relationship(back_populates="inventory_items")
    seller: Mapped["Seller | None"] = relationship(back_populates="inventory_items")
    matches: Mapped[list["Match"]] = relationship(
        back_populates="inventory", passive_deletes=True
    )

    __table_args__ = (
        Index("idx_inventory_product", "normalized_product"),
        Index("idx_inventory_seller", "seller_id"),
        Index("idx_inventory_active", "is_active"),
        Index("idx_inventory_posted_at", "posted_at"),
    )
