"""`Message` — raw text + parsed metadata. Dedup via SHA-256 `hash`."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from trade_intel.db.base import Base
from trade_intel.db.models._sa import str_enum_type
from trade_intel.db.models.enums import IntentType

if TYPE_CHECKING:
    from trade_intel.db.models.buyer import Buyer
    from trade_intel.db.models.demand import Demand
    from trade_intel.db.models.group import Group
    from trade_intel.db.models.inventory import Inventory
    from trade_intel.db.models.seller import Seller


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), default=None)
    sender_name: Mapped[str | None] = mapped_column(String, default=None)
    sender_phone: Mapped[str | None] = mapped_column(String, default=None)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_ts: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    intent: Mapped[IntentType | None] = mapped_column(
        str_enum_type(IntentType, length=16),
        default=None,
    )
    intent_confidence: Mapped[float | None] = mapped_column(default=None)
    language: Mapped[str | None] = mapped_column(String(8), default=None)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    buyer_id: Mapped[int | None] = mapped_column(ForeignKey("buyers.id"), default=None)
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("sellers.id"), default=None)

    group: Mapped["Group | None"] = relationship(back_populates="messages")
    buyer: Mapped["Buyer | None"] = relationship(back_populates="messages")
    seller: Mapped["Seller | None"] = relationship(back_populates="messages")
    inventory_items: Mapped[list["Inventory"]] = relationship(
        back_populates="message", passive_deletes=True
    )
    demands: Mapped[list["Demand"]] = relationship(
        back_populates="message", passive_deletes=True
    )

    __table_args__ = (
        Index("uq_messages_hash", "hash", unique=True),
        Index("idx_messages_ts", "message_ts"),
        Index("idx_messages_intent", "intent"),
        Index("idx_messages_group", "group_id"),
    )
