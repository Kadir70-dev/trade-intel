"""WhatsApp ready-to-send message templates (P5.4).

Given a :class:`Match` row, produces a short trader-style ping the
user can copy into WhatsApp verbatim. Templates are intentionally
short, friendly, and end with a clear ask ("LP?", "still available?")
so the recipient can respond in one tap.

Two flavors:

- :func:`seller_ping` — the trader holds a buyer-side need and wants
  to ask the seller about their inventory ("Bhai do you still have…").
- :func:`buyer_ping` — the trader holds an inventory row and wants to
  pitch it to a buyer who has posted matching demand.
"""

from __future__ import annotations

from trade_intel.db.models import Demand, Inventory, Match


def _qty_phrase(quantity: int | None, fallback: str = "some") -> str:
    if quantity is None or quantity <= 0:
        return fallback
    return f"{quantity}x"


def _storage_phrase(storage: str | None) -> str:
    return f" {storage}" if storage else ""


def _condition_phrase(condition: object) -> str:
    if condition is None:
        return ""
    return f" {condition.value}"  # type: ignore[attr-defined]


def _location_phrase(location: str | None) -> str:
    return f" in {location.upper()}" if location else ""


def seller_ping(*, inventory: Inventory, demand: Demand) -> str:
    """Compose a message to the seller asking about their inventory."""
    seller_name = (
        inventory.seller.canonical_name
        if inventory.seller is not None
        else "bhai"
    )
    product = inventory.normalized_product
    qty = _qty_phrase(inventory.quantity, fallback="some")
    storage = _storage_phrase(inventory.storage)
    condition = _condition_phrase(inventory.condition)
    location = _location_phrase(inventory.location)
    demand_qty = _qty_phrase(demand.quantity, fallback="qty")
    return (
        f"Salaam {seller_name}, "
        f"do you still have {qty} {product}{storage}{condition}{location}? "
        f"I have a confirmed buyer for {demand_qty}. "
        "Kindly send LP."
    )


def buyer_ping(*, inventory: Inventory, demand: Demand) -> str:
    """Compose a message to a buyer pitching the matched inventory."""
    buyer_name = (
        demand.buyer.canonical_name if demand.buyer is not None else "bhai"
    )
    product = demand.normalized_product
    qty = _qty_phrase(demand.quantity, fallback="qty")
    storage = _storage_phrase(demand.storage)
    inv_qty = _qty_phrase(inventory.quantity, fallback="stock")
    inv_location = _location_phrase(inventory.location)
    price_part = (
        f" at AED {inventory.price_aed:g}"
        if inventory.price_aed is not None
        else ""
    )
    return (
        f"Hi {buyer_name}, "
        f"got fresh {inv_qty} {product}{storage}{inv_location}{price_part} "
        f"matching your {qty} request. Want me to lock it for you?"
    )


def render_match_message(match: Match, *, side: str = "seller") -> str:
    """Build a ready-to-send message for a :class:`Match` row.

    Args:
        match: Match row (must have ``demand`` and ``inventory`` loaded).
        side: ``"seller"`` (ask the seller about their stock) or
            ``"buyer"`` (pitch stock to a buyer with matching demand).

    Raises:
        ValueError: when the Match lacks a linked demand/inventory or
            when ``side`` is not one of the two known values.
    """
    if match.demand is None or match.inventory is None:
        raise ValueError(
            f"match {match.id} is missing demand or inventory linkage"
        )
    if side == "seller":
        return seller_ping(inventory=match.inventory, demand=match.demand)
    if side == "buyer":
        return buyer_ping(inventory=match.inventory, demand=match.demand)
    raise ValueError(f"unknown side {side!r}; expected 'seller' or 'buyer'")


__all__ = ["buyer_ping", "render_match_message", "seller_ping"]
