"""`Group` — provenance container for a batch of messages."""

from typing import TYPE_CHECKING

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trade_intel.db.base import Base
from trade_intel.db.models._sa import str_enum_type
from trade_intel.db.models.enums import SourceType
from trade_intel.db.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from trade_intel.db.models.message import Message


class Group(CreatedAtMixin, Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        str_enum_type(SourceType, length=16),
        nullable=False,
    )
    source_path: Mapped[str | None] = mapped_column(String, default=None)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="group",
        passive_deletes=True,
    )

    __table_args__ = (Index("idx_groups_source_type", "source_type"),)
