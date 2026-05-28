"""Unit tests for the WhatsApp ready-to-send templates (P5.4)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from trade_intel.cli.messages import (
    buyer_ping,
    render_match_message,
    seller_ping,
)
from trade_intel.db.models import Condition


@dataclass
class StubSeller:
    canonical_name: str = "Ali Mobiles"


@dataclass
class StubBuyer:
    canonical_name: str = "Sara"


@dataclass
class StubInventory:
    normalized_product: str = "iPhone 12 Pro Max"
    storage: str | None = "256GB"
    condition: Condition | None = Condition.SEALED
    quantity: int | None = 5
    price_aed: float | None = 3200
    location: str | None = "dxb"
    seller: StubSeller | None = None


@dataclass
class StubDemand:
    normalized_product: str = "iPhone 12 Pro Max"
    storage: str | None = "256GB"
    quantity: int | None = 5
    buyer: StubBuyer | None = None


@dataclass
class StubMatch:
    id: int = 1
    demand: StubDemand | None = None
    inventory: StubInventory | None = None


# ---------- seller_ping ----------


def test_seller_ping_uses_seller_name() -> None:
    inv = StubInventory(seller=StubSeller(canonical_name="Khalid Trading"))
    dem = StubDemand()
    msg = seller_ping(inventory=inv, demand=dem)
    assert "Khalid Trading" in msg


def test_seller_ping_includes_quantity_and_product() -> None:
    inv = StubInventory(seller=StubSeller(), quantity=5)
    dem = StubDemand()
    msg = seller_ping(inventory=inv, demand=dem)
    assert "5x" in msg
    assert "iPhone 12 Pro Max" in msg


def test_seller_ping_includes_storage_when_present() -> None:
    inv = StubInventory(seller=StubSeller(), storage="256GB")
    dem = StubDemand()
    msg = seller_ping(inventory=inv, demand=dem)
    assert "256GB" in msg


def test_seller_ping_omits_storage_when_missing() -> None:
    inv = StubInventory(seller=StubSeller(), storage=None)
    dem = StubDemand()
    msg = seller_ping(inventory=inv, demand=dem)
    # No double-space artifact from a stripped segment.
    assert "  " not in msg


def test_seller_ping_includes_location_uppercased() -> None:
    inv = StubInventory(seller=StubSeller(), location="sharjah")
    dem = StubDemand()
    msg = seller_ping(inventory=inv, demand=dem)
    assert "SHARJAH" in msg


def test_seller_ping_includes_condition_when_present() -> None:
    inv = StubInventory(seller=StubSeller(), condition=Condition.SEALED)
    dem = StubDemand()
    msg = seller_ping(inventory=inv, demand=dem)
    assert "sealed" in msg


def test_seller_ping_ends_with_lp_ask() -> None:
    msg = seller_ping(inventory=StubInventory(seller=StubSeller()), demand=StubDemand())
    assert "LP" in msg


def test_seller_ping_fallback_when_no_seller_relationship() -> None:
    inv = StubInventory(seller=None)
    dem = StubDemand()
    msg = seller_ping(inventory=inv, demand=dem)
    assert "bhai" in msg.lower()


def test_seller_ping_fallback_when_no_quantity() -> None:
    inv = StubInventory(seller=StubSeller(), quantity=None)
    dem = StubDemand()
    msg = seller_ping(inventory=inv, demand=dem)
    assert "some" in msg


# ---------- buyer_ping ----------


def test_buyer_ping_uses_buyer_name() -> None:
    dem = StubDemand(buyer=StubBuyer(canonical_name="Bob"))
    inv = StubInventory(seller=StubSeller())
    msg = buyer_ping(inventory=inv, demand=dem)
    assert "Bob" in msg


def test_buyer_ping_includes_price_when_present() -> None:
    inv = StubInventory(seller=StubSeller(), price_aed=3200)
    dem = StubDemand(buyer=StubBuyer())
    msg = buyer_ping(inventory=inv, demand=dem)
    assert "AED 3200" in msg


def test_buyer_ping_omits_price_when_missing() -> None:
    inv = StubInventory(seller=StubSeller(), price_aed=None)
    dem = StubDemand(buyer=StubBuyer())
    msg = buyer_ping(inventory=inv, demand=dem)
    assert "AED" not in msg


def test_buyer_ping_ends_with_lock_ask() -> None:
    msg = buyer_ping(
        inventory=StubInventory(seller=StubSeller()),
        demand=StubDemand(buyer=StubBuyer()),
    )
    assert "lock" in msg.lower()


def test_buyer_ping_fallback_when_no_buyer() -> None:
    msg = buyer_ping(
        inventory=StubInventory(seller=StubSeller()),
        demand=StubDemand(buyer=None),
    )
    assert "bhai" in msg.lower()


# ---------- render_match_message ----------


def test_render_match_message_seller_side() -> None:
    match = StubMatch(
        demand=StubDemand(buyer=StubBuyer()),
        inventory=StubInventory(seller=StubSeller()),
    )
    msg = render_match_message(match, side="seller")  # type: ignore[arg-type]
    assert "Ali Mobiles" in msg


def test_render_match_message_buyer_side() -> None:
    match = StubMatch(
        demand=StubDemand(buyer=StubBuyer(canonical_name="Bob")),
        inventory=StubInventory(seller=StubSeller()),
    )
    msg = render_match_message(match, side="buyer")  # type: ignore[arg-type]
    assert "Bob" in msg


def test_render_match_message_default_is_seller() -> None:
    match = StubMatch(
        demand=StubDemand(buyer=StubBuyer()),
        inventory=StubInventory(seller=StubSeller(canonical_name="Hub")),
    )
    msg = render_match_message(match)  # type: ignore[arg-type]
    assert "Hub" in msg


def test_render_match_message_rejects_unknown_side() -> None:
    match = StubMatch(
        demand=StubDemand(buyer=StubBuyer()),
        inventory=StubInventory(seller=StubSeller()),
    )
    with pytest.raises(ValueError, match="unknown side"):
        render_match_message(match, side="broker")  # type: ignore[arg-type]


def test_render_match_message_rejects_unlinked_match() -> None:
    match = StubMatch(demand=None, inventory=None)
    with pytest.raises(ValueError, match="missing demand or inventory"):
        render_match_message(match)  # type: ignore[arg-type]
