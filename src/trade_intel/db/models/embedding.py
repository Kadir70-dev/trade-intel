"""`Embedding` — pointer row for a vector stored in FAISS.

Polymorphic: `(entity_type, entity_id)` points at messages / inventory / demand
/ product. Intentionally no FK constraint or `relationship()` — the entity-type
discriminator forbids a single FK target.
"""

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from trade_intel.db.base import Base
from trade_intel.db.models._sa import str_enum_type
from trade_intel.db.models.enums import VectorEntityType
from trade_intel.db.models.mixins import CreatedAtMixin


class Embedding(CreatedAtMixin, Base):
    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[VectorEntityType] = mapped_column(
        str_enum_type(VectorEntityType, length=16),
        nullable=False,
    )
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    vector_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    faiss_index_id: Mapped[int | None] = mapped_column(Integer, default=None)

    __table_args__ = (
        Index("idx_embeddings_entity", "entity_type", "entity_id"),
        Index("idx_embeddings_model", "model_name"),
    )
