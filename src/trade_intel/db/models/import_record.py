"""`ImportRecord` — audit row for one uploaded file (who, what, how many)."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trade_intel.db.base import Base
from trade_intel.db.models._sa import str_enum_type
from trade_intel.db.models.enums import SourceType
from trade_intel.db.models.mixins import CreatedAtMixin


class ImportRecord(CreatedAtMixin, Base):
    __tablename__ = "imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    uploader: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_format: Mapped[SourceType] = mapped_column(
        str_enum_type(SourceType, length=16), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    file_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    bytes_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_skipped_dup: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_skipped_other: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inventory_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    demand_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matches_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_detail: Mapped[str | None] = mapped_column(Text, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (Index("idx_imports_created", "created_at"),)
