"""`MessageItem` — one structured line item (requirement or offer) from a message."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_intel.db.base import Base
from trade_intel.db.models.mixins import CreatedAtMixin


class MessageItem(CreatedAtMixin, Base):
    __tablename__ = "message_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str | None] = mapped_column(String(8), default=None)  # buy | sell
    side_source: Mapped[str | None] = mapped_column(String(12), default=None)
    sender_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sender_name: Mapped[str | None] = mapped_column(String(120), default=None)
    sender_phone: Mapped[str | None] = mapped_column(String(32), default=None)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(60), default=None)
    model: Mapped[str | None] = mapped_column(String(80), default=None)
    capacity_gb: Mapped[int | None] = mapped_column(Integer, default=None)
    generation: Mapped[str | None] = mapped_column(String(8), default=None)
    speed_mhz: Mapped[int | None] = mapped_column(Integer, default=None)
    form: Mapped[str | None] = mapped_column(String(32), default=None)
    interface: Mapped[str | None] = mapped_column(String(16), default=None)
    health_min: Mapped[int | None] = mapped_column(Integer, default=None)
    health_max: Mapped[int | None] = mapped_column(Integer, default=None)
    condition: Mapped[str | None] = mapped_column(String(24), default=None)
    cpu: Mapped[str | None] = mapped_column(String(24), default=None)
    ram_gb: Mapped[int | None] = mapped_column(Integer, default=None)
    ssd_gb: Mapped[int | None] = mapped_column(Integer, default=None)
    qty: Mapped[int | None] = mapped_column(Integer, default=None)
    price_aed: Mapped[float | None] = mapped_column(Float, default=None)
    flags: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    raw_line: Mapped[str] = mapped_column(Text, nullable=False)
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (
        Index("idx_items_message", "message_id"),
        Index("idx_items_side_cat", "side", "category"),
        Index("idx_items_sender", "sender_key"),
        Index("idx_items_posted", "posted_at"),
    )
