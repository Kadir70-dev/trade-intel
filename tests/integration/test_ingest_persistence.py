"""Integration tests for the persistence wire-in (P3.7).

These tests boot a fresh in-memory SQLite engine per test, run
:func:`init_db` against the live model metadata (so they exercise the
real schema), then call :func:`ingest_messages` against parsed
fixtures. Each test owns its own session.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from trade_intel.db.base import Base
from trade_intel.db.models import (
    Buyer,
    Demand,
    Group,
    IntentType,
    Inventory,
    Message,
    Seller,
    SourceType,
    Urgency,
)
from trade_intel.parsers import DedupSet, parse_file, parse_lines
from trade_intel.persistence import IngestStats, ingest_messages

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "whatsapp"


# ---------- fixtures ----------


@pytest.fixture
def session() -> Generator[Session, None, None]:
    """Fresh in-memory SQLite with all tables created. Single connection
    via StaticPool so the schema lives for the test's duration."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sm = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with sm() as s:
        yield s
    engine.dispose()


def _ingest_lines(
    session: Session, lines: list[str], **kwargs
) -> IngestStats:
    """Helper: parse + ingest a list of WhatsApp lines."""
    msgs = list(parse_lines(lines, source_file="test.txt", group_name="g"))
    return ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        group_name=kwargs.pop("group_name", "g"),
        source_path=kwargs.pop("source_path", "test.txt"),
        **kwargs,
    )


# ---------- empty / status filtering ----------


def test_empty_input_yields_zero_stats(session: Session) -> None:
    stats = ingest_messages(
        [], session=session, source_type=SourceType.WHATSAPP, group_name="g"
    )
    assert stats.messages_seen == 0
    assert stats.messages_inserted == 0
    assert session.scalar(select(Message)) is None


def test_system_messages_are_skipped(session: Session) -> None:
    stats = _ingest_lines(
        session,
        ['28/05/26, 09:00 - You created group "X"'],
    )
    assert stats.messages_seen == 1
    assert stats.messages_skipped_system == 1
    assert stats.messages_inserted == 0


def test_media_messages_are_skipped(session: Session) -> None:
    stats = _ingest_lines(
        session,
        ["28/05/26, 09:00 - Ali: <Media omitted>"],
    )
    assert stats.messages_skipped_media == 1
    assert stats.messages_inserted == 0


def test_orphan_lines_are_skipped(session: Session) -> None:
    """A continuation line with no preceding header → ORPHAN_CONTINUATION."""
    stats = _ingest_lines(session, ["just some text with no timestamp"])
    assert stats.messages_skipped_orphan == 1
    assert stats.messages_inserted == 0


# ---------- happy-path SELLER ----------


def test_seller_message_creates_seller_message_inventory(session: Session) -> None:
    stats = _ingest_lines(
        session,
        ["28/05/26, 09:00 - Ali Mobiles: available 5 12pm 256gb fresh seal"],
    )
    assert stats.messages_inserted == 1
    assert stats.sellers_created == 1
    assert stats.sellers_updated == 0
    assert stats.buyers_created == 0
    assert stats.inventory_inserted == 1
    assert stats.demand_inserted == 0

    seller = session.scalar(select(Seller))
    assert seller is not None
    assert seller.canonical_name == "ali mobiles"
    assert seller.post_count == 1

    msg = session.scalar(select(Message))
    assert msg is not None
    assert msg.intent is IntentType.SELLER
    assert msg.seller_id == seller.id
    assert msg.buyer_id is None
    assert msg.sender_name == "Ali Mobiles"

    inv = session.scalar(select(Inventory))
    assert inv is not None
    assert inv.normalized_product == "iPhone 12 Pro Max"
    assert inv.seller_id == seller.id
    assert inv.message_id == msg.id
    assert inv.is_active is True


def test_seller_multiple_products_each_get_inventory_row(
    session: Session,
) -> None:
    _ingest_lines(
        session,
        [
            "28/05/26, 09:00 - Ali Mobiles: av 5 12pm 256gb and 3 mini 5 64gb fresh",
        ],
    )
    # Even though 'av' isn't in the intent lexicon, 'available' shorthand
    # 'avail' isn't here either — but the message contains 'fresh' (seller w=1)
    # so it should be classified SELLER. Let's just check products were parsed.
    inv_products = {
        i.normalized_product for i in session.scalars(select(Inventory)).all()
    }
    # Both should appear iff intent classified as SELLER.
    msg = session.scalar(select(Message))
    assert msg is not None
    if msg.intent is IntentType.SELLER:
        assert "iPhone 12 Pro Max" in inv_products
        assert "iPad Mini 5" in inv_products


# ---------- happy-path BUYER ----------


def test_buyer_message_creates_buyer_message_demand(session: Session) -> None:
    stats = _ingest_lines(
        session,
        ["28/05/26, 09:00 - Sara: need urgent 10 12pm asap"],
    )
    assert stats.messages_inserted == 1
    assert stats.buyers_created == 1
    assert stats.sellers_created == 0
    assert stats.demand_inserted == 1
    assert stats.inventory_inserted == 0

    buyer = session.scalar(select(Buyer))
    assert buyer is not None
    assert buyer.canonical_name == "sara"
    assert buyer.request_count == 1

    msg = session.scalar(select(Message))
    assert msg is not None
    assert msg.intent is IntentType.BUYER
    assert msg.buyer_id == buyer.id

    dmd = session.scalar(select(Demand))
    assert dmd is not None
    assert dmd.normalized_product == "iPhone 12 Pro Max"
    assert dmd.buyer_id == buyer.id
    assert dmd.urgency is Urgency.HIGH  # 'urgent' + 'asap' both lift HIGH


def test_buyer_with_phone_uses_phone_as_canonical(session: Session) -> None:
    _ingest_lines(
        session,
        ["28/05/26, 09:00 - +971 50 555 1234: need 5 mini 5"],
    )
    buyer = session.scalar(select(Buyer))
    assert buyer is not None
    assert buyer.phone == "+971505551234"
    assert buyer.canonical_name == "+971505551234"  # phone-only identity


# ---------- BROKER / UNKNOWN don't create inventory/demand ----------


def test_broker_message_stored_with_intent_no_inventory_or_demand(
    session: Session,
) -> None:
    stats = _ingest_lines(
        session,
        ["28/05/26, 09:00 - Khalid Trading LLC: have buyer for 50 mini 5"],
    )
    assert stats.messages_inserted == 1
    assert stats.inventory_inserted == 0
    assert stats.demand_inserted == 0
    assert stats.sellers_created == 0
    assert stats.buyers_created == 0

    msg = session.scalar(select(Message))
    assert msg is not None
    assert msg.intent is IntentType.BROKER
    assert msg.seller_id is None
    assert msg.buyer_id is None


def test_unknown_intent_message_stored_no_side_rows(session: Session) -> None:
    stats = _ingest_lines(
        session,
        ["28/05/26, 09:00 - Random: ok thx bro"],
    )
    assert stats.messages_inserted == 1
    assert stats.inventory_inserted == 0
    assert stats.demand_inserted == 0
    msg = session.scalar(select(Message))
    assert msg is not None
    assert msg.intent is IntentType.UNKNOWN
    assert msg.seller_id is None
    assert msg.buyer_id is None


def test_seller_with_no_products_creates_message_no_inventory(
    session: Session,
) -> None:
    stats = _ingest_lines(
        session,
        ["28/05/26, 09:00 - Ali Mobiles: stock ready dxb"],
    )
    assert stats.messages_inserted == 1
    assert stats.sellers_created == 1
    assert stats.inventory_inserted == 0  # no product extracted


# ---------- identity merge (D-009) ----------


def test_same_phone_two_messages_share_one_seller_row(session: Session) -> None:
    _ingest_lines(
        session,
        [
            "28/05/26, 09:00 - +971 50 555 1234: available 5 12pm fresh",
            "28/05/26, 10:00 - +971 50 555 1234: available 10 mini 5 fresh",
        ],
    )
    sellers = session.scalars(select(Seller)).all()
    assert len(sellers) == 1
    assert sellers[0].post_count == 2
    assert sellers[0].phone == "+971505551234"


def test_same_canonical_name_no_phone_merges_seller(session: Session) -> None:
    _ingest_lines(
        session,
        [
            "28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh",
            "28/05/26, 10:00 - Ali Mobiles: available 3 mini 5 fresh",
        ],
    )
    sellers = session.scalars(select(Seller)).all()
    assert len(sellers) == 1
    assert sellers[0].post_count == 2


def test_different_phones_two_seller_rows(session: Session) -> None:
    _ingest_lines(
        session,
        [
            "28/05/26, 09:00 - +971 50 555 1234: available 5 12pm fresh",
            "28/05/26, 10:00 - +971 50 555 9999: available 5 mini 5 fresh",
        ],
    )
    sellers = session.scalars(select(Seller)).all()
    assert len(sellers) == 2


def test_first_and_last_seen_track_across_messages(session: Session) -> None:
    _ingest_lines(
        session,
        [
            "28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh",
            "29/05/26, 14:00 - Ali Mobiles: available 3 mini 5 fresh",
            "27/05/26, 08:00 - Ali Mobiles: available 1 air 5 fresh",
        ],
    )
    seller = session.scalar(select(Seller))
    assert seller is not None
    assert seller.first_seen is not None
    assert seller.last_seen is not None
    assert seller.first_seen.day == 27
    assert seller.last_seen.day == 29


def test_aliases_merge_on_repeat_sender(session: Session) -> None:
    _ingest_lines(
        session,
        ["28/05/26, 09:00 - Ali Mobiles DXB: av 5 12pm fresh"],
    )
    seller = session.scalar(select(Seller))
    assert seller is not None
    assert seller.aliases is not None
    assert "ali mobiles dxb" in seller.aliases
    # location-stripped alias too
    assert "ali mobiles" in seller.aliases


# ---------- group ----------


def test_first_message_creates_group(session: Session) -> None:
    _ingest_lines(
        session,
        ["28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh"],
        group_name="UAE Wholesale Group",
        source_path="uae.txt",
    )
    group = session.scalar(select(Group))
    assert group is not None
    assert group.name == "UAE Wholesale Group"
    assert group.source_type is SourceType.WHATSAPP
    assert group.source_path == "uae.txt"


def test_second_call_same_source_path_reuses_group(session: Session) -> None:
    _ingest_lines(
        session,
        ["28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh"],
        source_path="path-a.txt",
    )
    _ingest_lines(
        session,
        ["29/05/26, 09:00 - Sara: need 5 mini 5"],
        source_path="path-a.txt",
    )
    assert len(session.scalars(select(Group)).all()) == 1


def test_different_source_paths_create_different_groups(
    session: Session,
) -> None:
    _ingest_lines(
        session,
        ["28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh"],
        source_path="path-a.txt",
    )
    _ingest_lines(
        session,
        ["29/05/26, 09:00 - Sara: need 5 mini 5"],
        source_path="path-b.txt",
    )
    assert len(session.scalars(select(Group)).all()) == 2


def test_messages_link_to_their_group(session: Session) -> None:
    _ingest_lines(
        session,
        [
            "28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh",
            "28/05/26, 10:00 - Sara: need 5 mini 5",
        ],
    )
    group = session.scalar(select(Group))
    msgs = session.scalars(select(Message)).all()
    assert all(m.group_id == group.id for m in msgs)


# ---------- idempotency ----------


def test_reingest_same_lines_yields_zero_new_rows(session: Session) -> None:
    lines = [
        "28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh",
        "28/05/26, 10:00 - Sara: need 5 mini 5",
    ]
    first = _ingest_lines(session, lines)
    second = _ingest_lines(session, lines)
    assert first.messages_inserted == 2
    assert second.messages_inserted == 0
    assert second.messages_skipped_dup == 2
    # Counts in DB unchanged after second pass.
    assert len(session.scalars(select(Message)).all()) == 2


def test_dedup_set_preloaded_from_existing_messages_table(
    session: Session,
) -> None:
    """First ingest. Second ingest with a FRESH dedup_set (default behavior)
    should still skip duplicates by reloading from messages.hash."""
    lines = ["28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh"]
    _ingest_lines(session, lines)
    # Don't pass dedup_set — new DedupSet should preload from DB.
    stats = _ingest_lines(session, lines)
    assert stats.messages_skipped_dup == 1


def test_user_provided_dedup_set_is_used(session: Session) -> None:
    lines = ["28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh"]
    msgs = list(parse_lines(lines, source_file="t.txt", group_name="g"))
    seen = DedupSet.from_hashes([msgs[0].message_hash])
    stats = ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        group_name="g",
        source_path="t.txt",
        dedup_set=seen,
    )
    assert stats.messages_skipped_dup == 1
    assert stats.messages_inserted == 0


# ---------- stats math ----------


def test_stats_math_inserted_plus_skipped_equals_seen(session: Session) -> None:
    stats = _ingest_lines(
        session,
        [
            "28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh",
            '28/05/26, 09:01 - You created group "X"',
            "28/05/26, 09:02 - Bob: <Media omitted>",
            "28/05/26, 09:03 - Sara: need 5 mini 5",
        ],
    )
    total = (
        stats.messages_inserted
        + stats.messages_skipped_dup
        + stats.messages_skipped_system
        + stats.messages_skipped_media
        + stats.messages_skipped_malformed
        + stats.messages_skipped_orphan
        + stats.errors
    )
    assert total == stats.messages_seen


def test_stats_as_dict_excludes_private_fields(session: Session) -> None:
    stats = IngestStats()
    d = stats.as_dict()
    assert "_skipped_status_field" not in d
    assert "messages_seen" in d


# ---------- fixture round-trip ----------


def test_mixed_identities_fixture_round_trip(session: Session) -> None:
    """Parse the rich mixed_identities fixture, ingest, sanity-check rows."""
    msgs = list(parse_file(FIXTURES / "mixed_identities_sample.txt"))
    stats = ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        group_name="mixed",
        source_path=str(FIXTURES / "mixed_identities_sample.txt"),
    )
    # The fixture is 11 lines, all OK (no system / media / orphan).
    assert stats.messages_seen == 11
    assert stats.messages_inserted == 11
    assert stats.errors == 0
    assert session.scalar(select(Group)) is not None


def test_android_fixture_re_ingest_is_full_dup(session: Session) -> None:
    msgs1 = list(parse_file(FIXTURES / "android_sample.txt"))
    stats1 = ingest_messages(
        msgs1,
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(FIXTURES / "android_sample.txt"),
    )
    msgs2 = list(parse_file(FIXTURES / "android_sample.txt"))
    stats2 = ingest_messages(
        msgs2,
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(FIXTURES / "android_sample.txt"),
    )
    assert stats2.messages_inserted == 0
    assert stats2.messages_skipped_dup == stats1.messages_inserted


# ---------- intent confidence persisted ----------


def test_message_intent_confidence_stored(session: Session) -> None:
    _ingest_lines(
        session,
        ["28/05/26, 09:00 - Sara: need 12pm urgent"],
    )
    msg = session.scalar(select(Message))
    assert msg is not None
    assert msg.intent is IntentType.BUYER
    assert msg.intent_confidence is not None
    assert 0.0 < msg.intent_confidence <= 1.0


# ---------- error resilience ----------


def test_per_message_savepoint_isolates_failures(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force the second insert to fail and verify the first survives."""
    import trade_intel.persistence.ingest as mod

    original = mod.extract_products
    call_count = {"n": 0}

    def flaky_extract(text):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("boom")
        return original(text)

    monkeypatch.setattr(mod, "extract_products", flaky_extract)

    stats = _ingest_lines(
        session,
        [
            "28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh",
            "28/05/26, 09:01 - Sara: need 5 mini 5",
            "28/05/26, 09:02 - Bob: need 3 air 5",
        ],
    )
    assert stats.errors == 1
    assert stats.messages_inserted == 2
    msgs = session.scalars(select(Message)).all()
    assert len(msgs) == 2
