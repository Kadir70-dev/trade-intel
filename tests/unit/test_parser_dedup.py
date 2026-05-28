"""Unit tests for hash-based message dedup (P3.4)."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest

from trade_intel.parsers import (
    DedupSet,
    DedupStats,
    ParsedMessage,
    ParseStatus,
    dedupe,
    parse_file,
    parse_lines,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "whatsapp"


# ---------- helpers ----------


def _msg(h: str, *, status: ParseStatus = ParseStatus.OK) -> ParsedMessage:
    """Build a minimal :class:`ParsedMessage` with a chosen hash."""
    return ParsedMessage(
        timestamp=None,
        sender_name=None,
        raw_message="",
        normalized_message="",
        group_name=None,
        message_hash=h,
        source_file="t.txt",
        is_media=False,
        parse_status=status,
    )


# ---------- DedupStats ----------


def test_stats_defaults_are_zero() -> None:
    s = DedupStats()
    assert s.seen == 0
    assert s.unique == 0
    assert s.duplicates == 0
    assert s.duplicate_rate == 0.0


def test_stats_duplicate_rate_zero_on_empty() -> None:
    assert DedupStats().duplicate_rate == 0.0


def test_stats_duplicate_rate_math() -> None:
    s = DedupStats(seen=10, unique=7, duplicates=3)
    assert s.duplicate_rate == 0.3


def test_stats_as_dict_schema() -> None:
    s = DedupStats(seen=4, unique=3, duplicates=1)
    d = s.as_dict()
    assert d == {
        "seen": 4,
        "unique": 3,
        "duplicates": 1,
        "duplicate_rate": 0.25,
    }


def test_stats_as_dict_rounds_rate() -> None:
    # 1/3 → 0.333333… → rounded to 4dp
    s = DedupStats(seen=3, unique=2, duplicates=1)
    assert s.as_dict()["duplicate_rate"] == 0.3333


# ---------- DedupSet ----------


def test_dedupset_starts_empty() -> None:
    d = DedupSet()
    assert len(d) == 0
    assert "anything" not in d


def test_dedupset_add_returns_true_then_false() -> None:
    d = DedupSet()
    assert d.add("abc") is True
    assert d.add("abc") is False
    assert d.add("xyz") is True
    assert len(d) == 2


def test_dedupset_contains_after_add() -> None:
    d = DedupSet()
    d.add("hash-1")
    assert "hash-1" in d
    assert "hash-2" not in d


def test_dedupset_from_hashes_preloads() -> None:
    d = DedupSet.from_hashes(["a", "b", "c"])
    assert len(d) == 3
    assert "a" in d
    # Preloaded hashes do not count as "seen"
    assert d.stats.seen == 0
    # Re-adding a preloaded one returns False
    assert d.add("a") is False


def test_dedupset_from_hashes_accepts_iterator() -> None:
    def gen() -> Iterator[str]:
        yield "a"
        yield "b"

    d = DedupSet.from_hashes(gen())
    assert len(d) == 2


# ---------- dedupe(): basic semantics ----------


def test_dedupe_empty_input_yields_nothing() -> None:
    out = list(dedupe([]))
    assert out == []


def test_dedupe_all_unique_passes_through() -> None:
    msgs = [_msg("a"), _msg("b"), _msg("c")]
    seen = DedupSet()
    out = list(dedupe(msgs, seen=seen))
    assert [m.message_hash for m in out] == ["a", "b", "c"]
    assert seen.stats.seen == 3
    assert seen.stats.unique == 3
    assert seen.stats.duplicates == 0


def test_dedupe_all_duplicates_yields_one() -> None:
    msgs = [_msg("a"), _msg("a"), _msg("a")]
    seen = DedupSet()
    out = list(dedupe(msgs, seen=seen))
    assert [m.message_hash for m in out] == ["a"]
    assert seen.stats.seen == 3
    assert seen.stats.unique == 1
    assert seen.stats.duplicates == 2
    assert seen.stats.duplicate_rate == pytest.approx(2 / 3)


def test_dedupe_mixed_unique_and_duplicates() -> None:
    msgs = [_msg("a"), _msg("b"), _msg("a"), _msg("c"), _msg("b"), _msg("a")]
    seen = DedupSet()
    out = list(dedupe(msgs, seen=seen))
    assert [m.message_hash for m in out] == ["a", "b", "c"]
    assert seen.stats.seen == 6
    assert seen.stats.unique == 3
    assert seen.stats.duplicates == 3


def test_dedupe_preserves_first_occurrence_order() -> None:
    msgs = [_msg("c"), _msg("a"), _msg("c"), _msg("b"), _msg("a")]
    out = list(dedupe(msgs))
    assert [m.message_hash for m in out] == ["c", "a", "b"]


def test_dedupe_first_occurrence_object_is_kept() -> None:
    """The first record carrying a given hash should be the one yielded
    — not a later duplicate that happens to share the hash."""
    first = _msg("dup")
    second = _msg("dup")  # same hash, different object
    out = list(dedupe([first, second]))
    assert len(out) == 1
    assert out[0] is first


def test_dedupe_works_on_generator_input() -> None:
    def gen() -> Iterator[ParsedMessage]:
        yield _msg("a")
        yield _msg("b")
        yield _msg("a")

    out = list(dedupe(gen()))
    assert [m.message_hash for m in out] == ["a", "b"]


def test_dedupe_returns_iterator_not_list() -> None:
    """Streaming-safe: the call itself shouldn't materialize all inputs."""
    it = dedupe([_msg("a"), _msg("b")])
    # Must be an iterator we can step manually.
    assert iter(it) is it
    assert next(it).message_hash == "a"
    assert next(it).message_hash == "b"
    with pytest.raises(StopIteration):
        next(it)


# ---------- dedupe(): status-blind ----------


@pytest.mark.parametrize(
    "status",
    [
        ParseStatus.OK,
        ParseStatus.SYSTEM,
        ParseStatus.MEDIA,
        ParseStatus.MALFORMED,
        ParseStatus.ORPHAN_CONTINUATION,
    ],
)
def test_dedupe_treats_all_statuses_identically(status: ParseStatus) -> None:
    """Dedup is on hash alone; ParseStatus must not influence the filter."""
    msgs = [_msg("x", status=status), _msg("x", status=status)]
    out = list(dedupe(msgs))
    assert len(out) == 1
    assert out[0].parse_status is status


# ---------- dedupe(): cross-batch via shared DedupSet ----------


def test_dedupe_cross_batch_shared_set() -> None:
    seen = DedupSet()
    batch_a = [_msg("a"), _msg("b")]
    batch_b = [_msg("b"), _msg("c")]  # 'b' is a cross-batch dup

    out_a = list(dedupe(batch_a, seen=seen))
    out_b = list(dedupe(batch_b, seen=seen))

    assert [m.message_hash for m in out_a] == ["a", "b"]
    assert [m.message_hash for m in out_b] == ["c"]
    # Stats accumulate across both batches.
    assert seen.stats.seen == 4
    assert seen.stats.unique == 3
    assert seen.stats.duplicates == 1


def test_dedupe_separate_sets_keep_stats_isolated() -> None:
    a_set, b_set = DedupSet(), DedupSet()
    msgs = [_msg("x"), _msg("x")]
    list(dedupe(msgs, seen=a_set))
    assert a_set.stats.seen == 2
    assert b_set.stats.seen == 0


def test_dedupe_default_seen_does_not_leak_between_calls() -> None:
    """Two dedupe() calls without a shared `seen=` should not share state."""
    first = list(dedupe([_msg("x")]))
    second = list(dedupe([_msg("x")]))
    assert len(first) == 1
    assert len(second) == 1


def test_dedupe_with_preloaded_dedupset_skips_known_hashes() -> None:
    """Models the P3.7 hand-off: DB-known hashes preloaded; new records flow."""
    seen = DedupSet.from_hashes(["already-in-db"])
    msgs = [_msg("already-in-db"), _msg("new-hash"), _msg("new-hash")]
    out = list(dedupe(msgs, seen=seen))
    assert [m.message_hash for m in out] == ["new-hash"]
    assert seen.stats.seen == 3
    assert seen.stats.unique == 1
    assert seen.stats.duplicates == 2


def test_dedupe_stats_snapshot_pattern() -> None:
    """Caller wanting per-batch stats can snapshot before + diff."""
    seen = DedupSet()
    list(dedupe([_msg("a"), _msg("b")], seen=seen))
    before = dataclasses.replace(seen.stats)
    list(dedupe([_msg("b"), _msg("c"), _msg("c")], seen=seen))
    delta_seen = seen.stats.seen - before.seen
    delta_dups = seen.stats.duplicates - before.duplicates
    assert delta_seen == 3
    assert delta_dups == 2  # one 'b' cross-batch + one 'c' in-batch


# ---------- integration with parse_lines / parse_file ----------


def test_dedupe_real_parse_lines_reimport_is_full_dup() -> None:
    """Idempotent re-parse: feeding the same lines twice should yield each
    message exactly once."""
    lines = [
        "28/05/26, 09:00 - Ali: salaam",
        "28/05/26, 09:01 - Sara: hello",
    ]
    a = list(parse_lines(lines, source_file="t.txt", group_name="g"))
    b = list(parse_lines(lines, source_file="t.txt", group_name="g"))
    seen = DedupSet()
    list(dedupe(a, seen=seen))
    list(dedupe(b, seen=seen))
    assert seen.stats.seen == 4
    assert seen.stats.unique == 2
    assert seen.stats.duplicates == 2


def test_dedupe_real_fixture_reimport_is_full_dup() -> None:
    seen = DedupSet()
    first = list(dedupe(parse_file(FIXTURES / "android_sample.txt"), seen=seen))
    second = list(dedupe(parse_file(FIXTURES / "android_sample.txt"), seen=seen))
    # First pass: all unique. Second pass: every record collides.
    assert len(first) > 0
    assert second == []
    assert seen.stats.duplicates == len(first)


def test_dedupe_distinguishes_records_with_different_timestamps() -> None:
    """Same body, different ts → different hashes → both pass dedup."""
    msgs = list(
        parse_lines(
            [
                "28/05/26, 09:00 - Ali: pm best rate",
                "28/05/26, 09:01 - Ali: pm best rate",
            ],
            source_file="t.txt",
            group_name="g",
        )
    )
    assert len({m.message_hash for m in msgs}) == 2
    out = list(dedupe(msgs))
    assert len(out) == 2


def test_dedupe_collapses_cross_file_identical_messages() -> None:
    """Same sender, same ts, same body, two source files → same hash → one
    record after cross-file dedup (per D-007: source_file is NOT part of the
    hash for ts-bearing records)."""
    line = "28/05/26, 09:00 - Ali: pm best rate"
    a = list(parse_lines([line], source_file="file_a.txt", group_name="g"))
    b = list(parse_lines([line], source_file="file_b.txt", group_name="g"))
    assert a[0].message_hash == b[0].message_hash
    seen = DedupSet()
    out_a = list(dedupe(a, seen=seen))
    out_b = list(dedupe(b, seen=seen))
    assert len(out_a) == 1
    assert out_b == []


def test_dedupe_keeps_orphans_from_different_lines() -> None:
    """Orphan records fold source_file+line_no into their hash, so two
    orphan lines with the same body in the same file are NOT duplicates."""
    msgs = list(
        parse_lines(
            ["random orphan body", "random orphan body"],
            source_file="t.txt",
            group_name="g",
        )
    )
    assert all(m.parse_status is ParseStatus.ORPHAN_CONTINUATION for m in msgs)
    assert msgs[0].message_hash != msgs[1].message_hash
    out = list(dedupe(msgs))
    assert len(out) == 2


# ---------- perf / scale smoke ----------


def test_dedupe_handles_large_unique_stream() -> None:
    """Smoke: 10k unique hashes shouldn't blow up; set is O(N) memory."""
    msgs = [_msg(f"hash-{i:05d}") for i in range(10_000)]
    seen = DedupSet()
    count = sum(1 for _ in dedupe(msgs, seen=seen))
    assert count == 10_000
    assert seen.stats.seen == 10_000
    assert seen.stats.duplicates == 0
