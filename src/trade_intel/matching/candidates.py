"""Candidate generation for matching (Phase 4).

Turns DB rows into vectors, stuffs them into a :class:`VectorIndex`,
and queries by demand → inventory (or inventory → demand) to surface
top-K semantic neighbors. The persistence layer then scores each pair
and writes Match rows.

Embedding text shape — kept consistent across both sides so the
embedder sees comparable inputs::

    "<canonical_product> | storage=<s>"

Product + storage together define the effective SKU. Condition and
location are negotiation / logistics dimensions handled separately by
:func:`compute_spec_fit` so they're intentionally **not** in the
embedding text — keeping them out stabilizes the semantic signal under
both HashEmbedder (deterministic noise) and MiniLM (cleaner gradient).
The canonical product is always the leading token because it dominates
the embedding signal and matching is product-centric.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_intel.db.models import Demand, Inventory
from trade_intel.ml.faiss_index import NumpyIndex, VectorIndex

if TYPE_CHECKING:
    from trade_intel.ml.embeddings import Embedder


# ---------------------------------------------------------------- text shape


def embedding_text_for_inventory(inv: Inventory) -> str:
    """Build the canonical text string for an Inventory row's embedding.

    Product-centric: includes only ``normalized_product`` + ``storage``
    (the two SKU-defining fields). Condition / location are
    intentionally omitted — :func:`compute_spec_fit` handles those.
    """
    parts: list[str] = [inv.normalized_product]
    if inv.storage:
        parts.append(f"storage={inv.storage}")
    return " | ".join(parts)


def embedding_text_for_demand(dem: Demand) -> str:
    """Mirror of :func:`embedding_text_for_inventory` — product-centric."""
    parts: list[str] = [dem.normalized_product]
    if dem.storage:
        parts.append(f"storage={dem.storage}")
    return " | ".join(parts)


# ---------------------------------------------------------------- index build


def build_inventory_index(
    session: Session,
    *,
    embedder: Embedder,
    index_factory: Callable[[int], VectorIndex] = NumpyIndex,
    only_active: bool = True,
) -> tuple[VectorIndex, dict[int, int]]:
    """Embed every (active) Inventory row and load into a fresh index.

    Returns ``(index, id_map)`` where ``id_map`` maps the integer ID
    stored in the index back to ``inventory.id``. For NumpyIndex /
    FaissFlatIndex the map is currently identity — but exposing it lets
    future indexes use a different ID space without changing callers.
    """
    stmt = select(Inventory)
    if only_active:
        stmt = stmt.where(Inventory.is_active.is_(True))
    rows = list(session.scalars(stmt).all())
    return _index_rows(
        rows,
        embedder=embedder,
        index_factory=index_factory,
        text_for=embedding_text_for_inventory,
    )


def build_demand_index(
    session: Session,
    *,
    embedder: Embedder,
    index_factory: Callable[[int], VectorIndex] = NumpyIndex,
    only_active: bool = True,
) -> tuple[VectorIndex, dict[int, int]]:
    """Embed every (active) Demand row and load into a fresh index."""
    stmt = select(Demand)
    if only_active:
        stmt = stmt.where(Demand.is_active.is_(True))
    rows = list(session.scalars(stmt).all())
    return _index_rows(
        rows,
        embedder=embedder,
        index_factory=index_factory,
        text_for=embedding_text_for_demand,
    )


def _index_rows(
    rows: list[Any],
    *,
    embedder: Embedder,
    index_factory: Callable[[int], VectorIndex],
    text_for: Callable[[Any], str],
) -> tuple[VectorIndex, dict[int, int]]:
    """Shared embed-then-add for both inventory and demand sides."""
    index = index_factory(embedder.dim)
    if not rows:
        return index, {}
    texts = [text_for(r) for r in rows]
    vecs = embedder.encode(texts)
    ids = [int(r.id) for r in rows]
    index.add(vecs, ids)
    id_map: dict[int, int] = {i: i for i in ids}
    return index, id_map


# ---------------------------------------------------------------- query


def find_inventory_candidates(
    demand: Demand,
    *,
    embedder: Embedder,
    index: VectorIndex,
    k: int = 10,
) -> list[tuple[int, float]]:
    """Top-K inventory IDs semantically nearest to ``demand``."""
    if index.size == 0:
        return []
    query = embedder.encode([embedding_text_for_demand(demand)])
    return index.search(query[0], k=k)


def find_demand_candidates(
    inventory: Inventory,
    *,
    embedder: Embedder,
    index: VectorIndex,
    k: int = 10,
) -> list[tuple[int, float]]:
    """Top-K demand IDs semantically nearest to ``inventory``."""
    if index.size == 0:
        return []
    query = embedder.encode([embedding_text_for_inventory(inventory)])
    return index.search(query[0], k=k)


__all__ = [
    "build_demand_index",
    "build_inventory_index",
    "embedding_text_for_demand",
    "embedding_text_for_inventory",
    "find_demand_candidates",
    "find_inventory_candidates",
]
