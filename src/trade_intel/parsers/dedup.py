"""Hash-based deduplication of parsed messages (P3.4).

The parser already emits a deterministic D-007 SHA-256 hash for every
message. This layer turns that hash into a streaming dedup filter:
first occurrence wins, subsequent duplicates are dropped, counts and
duplicate rate are exposed via :class:`DedupStats`.

In-batch dedup is the default. For cross-batch / cross-file / cross-run
dedup, share a :class:`DedupSet` between calls — or, eventually
(P3.7 persistence), preload one from the ``messages.hash`` column via
:meth:`DedupSet.from_hashes`.

The module is intentionally DB-free: the only requirement on input is a
``message_hash: str`` attribute. Composes directly with the WhatsApp
parser::

    from trade_intel.parsers import parse_file, dedupe, DedupSet

    seen = DedupSet()
    for msg in dedupe(parse_file("a.txt"), seen=seen):
        ...
    for msg in dedupe(parse_file("b.txt"), seen=seen):
        ...  # cross-file dedup; seen.stats accumulates
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from trade_intel.parsers.whatsapp import ParsedMessage


@dataclass(slots=True)
class DedupStats:
    """Counter for a dedup pass."""

    seen: int = 0
    unique: int = 0
    duplicates: int = 0

    @property
    def duplicate_rate(self) -> float:
        """Fraction of ``seen`` records that were duplicates ([0.0, 1.0])."""
        return self.duplicates / self.seen if self.seen else 0.0

    def as_dict(self) -> dict[str, float | int]:
        """Log-friendly snapshot. ``duplicate_rate`` rounded to 4dp."""
        return {
            "seen": self.seen,
            "unique": self.unique,
            "duplicates": self.duplicates,
            "duplicate_rate": round(self.duplicate_rate, 4),
        }


@dataclass(slots=True)
class DedupSet:
    """Reusable set of seen hashes plus an attached :class:`DedupStats`.

    Pass the same instance to multiple :func:`dedupe` calls to dedup
    across files / batches / runs. Stats accumulate across calls; to
    measure a single batch in isolation, snapshot ``stats`` before the
    call and diff, or use a fresh :class:`DedupSet` per batch.

    Preload from existing storage via :meth:`from_hashes`.
    """

    _hashes: set[str] = field(default_factory=set)
    stats: DedupStats = field(default_factory=DedupStats)

    @classmethod
    def from_hashes(cls, hashes: Iterable[str]) -> DedupSet:
        """Build a :class:`DedupSet` preloaded with ``hashes``.

        Stats start at zero — preloaded hashes are not counted as
        ``seen``; only records that flow through :func:`dedupe` are.
        """
        return cls(_hashes=set(hashes))

    def __contains__(self, h: str) -> bool:
        return h in self._hashes

    def __len__(self) -> int:
        return len(self._hashes)

    def add(self, h: str) -> bool:
        """Add ``h``. Returns ``True`` if newly added, ``False`` if dup."""
        if h in self._hashes:
            return False
        self._hashes.add(h)
        return True


def dedupe(
    messages: Iterable[ParsedMessage],
    *,
    seen: DedupSet | None = None,
) -> Iterator[ParsedMessage]:
    """Yield only the first occurrence of each unique ``message_hash``.

    Streaming-safe: holds only the hash set in memory (~64 chars per
    record), not the messages themselves. Order-preserving: the first
    record carrying a given hash wins; subsequent ones are silently
    dropped and counted in ``state.stats.duplicates``.

    Args:
        messages: any iterable of records exposing ``message_hash: str``.
            The parser's :class:`ParsedMessage` satisfies this.
        seen: optional pre-existing :class:`DedupSet` for cross-batch
            dedup. If omitted, a fresh one is used (per-call stats).
    """
    state = seen if seen is not None else DedupSet()
    for msg in messages:
        state.stats.seen += 1
        if state.add(msg.message_hash):
            state.stats.unique += 1
            yield msg
        else:
            state.stats.duplicates += 1


__all__ = ["DedupSet", "DedupStats", "dedupe"]
