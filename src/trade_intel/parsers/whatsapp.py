"""WhatsApp chat export parser.

Handles both Android and iPhone export formats, multi-line messages,
system messages, media placeholders, emojis, and malformed lines.
Streams a file line-by-line so multi-MB exports never have to fit in
memory at once.

Format reference
----------------
- Android (default English locale):
    ``DD/MM/YY, HH:MM - Sender Name: body``
    ``DD/MM/YYYY, HH:MM:SS - Sender Name: body`` (with seconds)
    ``M/D/YY, H:MM AM - Sender: body`` (US locale → MDY)
    ``YYYY/MM/DD, HH:MM - Sender: body`` (some Asian locales → YMD)
- iPhone:
    ``[DD/MM/YY, HH:MM:SS] Sender Name: body``
    (often peppered with U+200E LRM marks, which we strip)

Date order
----------
Three field positions per header (``a/b/c``); meaning depends on locale.
Pass ``date_order="DMY" | "MDY" | "YMD"`` to force an interpretation, or
``"auto"`` (the :func:`parse_file` default) to let
:func:`detect_date_order` infer it from header evidence in the file
(days > 12, 4-digit years, etc.). Ambiguous files (every triplet uses
1..12 for both fields) fall back to ``DMY`` to match UAE locale.

Timestamps default to **naive local time** (UAE has no DST). Pass
``tz=ZoneInfo("Asia/Dubai")`` to attach an explicit zone — useful when
the consumer compares timestamps across regions.

Output
------
Every line eventually becomes a :class:`ParsedMessage`. Continuation
lines collapse into the previous record. System messages, media
placeholders, malformed lines, and orphan continuations are all surfaced
with an appropriate :class:`ParseStatus` so the caller (Phase 3.7)
can decide what to persist.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from enum import StrEnum
from pathlib import Path
from typing import Literal

from trade_intel.core.logging import get_logger
from trade_intel.normalization import SenderIdentity, extract_identity, normalize_text

log = get_logger(__name__, component="parser.whatsapp")

DateOrder = Literal["DMY", "MDY", "YMD"]
DateOrderInput = Literal["DMY", "MDY", "YMD", "auto"]

# Bidi / format characters WhatsApp (esp. iOS) injects around fields.
_BIDI_RE = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_BOM = "\ufeff"

# Header regexes — three positional fields ``a`` / ``b`` / ``c``. The
# first and last fields accept 1-4 digits so a YMD year ("2026") or a
# DMY/MDY two-digit year both match. The middle field is always 1-2
# digits (it's day or month, never a year).
_ANDROID_RE = re.compile(
    r"^(?P<a>\d{1,4})[/.\-](?P<b>\d{1,2})[/.\-](?P<c>\d{1,4}),\s+"
    r"(?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?"
    r"(?:\s*(?P<ampm>[AaPp][Mm]))?"  # \s already covers NBSP under Unicode mode
    r"\s*-\s*(?P<rest>.*)$"
)

_IPHONE_RE = re.compile(
    r"^\[(?P<a>\d{1,4})[/.\-](?P<b>\d{1,2})[/.\-](?P<c>\d{1,4}),\s+"
    r"(?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?"
    r"(?:\s*(?P<ampm>[AaPp][Mm]))?"
    r"\]\s*(?P<rest>.*)$"
)

# Split "Sender Name: body" on the first ": " (a space after the colon
# is required — WhatsApp always emits it, and the constraint keeps us
# from slicing system messages that happen to contain a colon).
_SENDER_BODY_RE = re.compile(r"^(?P<sender>[^:]{1,200}?):\s(?P<body>.*)$")

# Substrings that mark a message body as a media placeholder.
_MEDIA_TOKENS: tuple[str, ...] = (
    "<Media omitted>",
    "image omitted",
    "video omitted",
    "audio omitted",
    "sticker omitted",
    "GIF omitted",
    "document omitted",
    "Contact card omitted",
    "(file attached)",
    "<attached:",
)
_MEDIA_RE = re.compile("|".join(re.escape(t) for t in _MEDIA_TOKENS), re.IGNORECASE)


class ParseStatus(StrEnum):
    """Outcome flag attached to every emitted :class:`ParsedMessage`."""

    OK = "ok"
    SYSTEM = "system"
    MEDIA = "media"
    MALFORMED = "malformed"
    ORPHAN_CONTINUATION = "orphan_continuation"


@dataclass(slots=True)
class ParsedMessage:
    """One coalesced WhatsApp message ready for normalization / persistence.

    Identity fields (P3.3):
    - ``sender_name`` is the raw display string the parser saw (e.g.
      ``"Ali Mobiles"``) — preserved for human display.
    - ``sender_phone`` is the canonical phone (``"+971505551234"``) when
      the sender header parsed as a phone number, else None.
    - ``sender_identity`` is the structured :class:`SenderIdentity` with
      all extracted signals (display, normalized, aliases, business
      heuristic). None for system messages (no sender).
    """

    timestamp: datetime | None
    sender_name: str | None
    raw_message: str
    normalized_message: str
    group_name: str | None
    message_hash: str
    source_file: str
    is_media: bool
    parse_status: ParseStatus
    line_no: int = 0
    sender_phone: str | None = None
    sender_identity: SenderIdentity | None = None


@dataclass(slots=True, frozen=True)
class DateDetection:
    """Result of :func:`detect_date_order`."""

    order: DateOrder
    confidence: float
    samples_seen: int
    evidence: dict[str, int]


@dataclass(slots=True)
class _Buffer:
    """Mutable scratch record we accumulate continuation lines into."""

    timestamp: datetime | None
    sender_name: str | None
    raw_lines: list[str]
    group_name: str | None
    source_file: str
    is_media: bool
    parse_status: ParseStatus
    line_no: int
    _continuation: list[str] = field(default_factory=list)

    def add_continuation(self, line: str) -> None:
        self._continuation.append(line)

    def finalize(self) -> ParsedMessage:
        body_parts: list[str] = [*self.raw_lines, *self._continuation]
        raw = "\n".join(body_parts)
        # `normalized_message` is the matching-friendly form: NFKC + noise
        # stripped + whitespace flattened + case-folded.
        normalized = normalize_text(raw, casefold=True, flatten=True)
        # Continuation may have introduced a media marker; re-check.
        is_media = self.is_media or _is_media_body(raw)
        status = self.parse_status
        if is_media and status is ParseStatus.OK:
            status = ParseStatus.MEDIA
        # System messages have no sender; everything else gets a full
        # SenderIdentity for downstream dedup / merge.
        identity = extract_identity(self.sender_name) if self.sender_name else None
        msg = ParsedMessage(
            timestamp=self.timestamp,
            sender_name=self.sender_name,
            raw_message=raw,
            normalized_message=normalized,
            group_name=self.group_name,
            message_hash="",
            source_file=self.source_file,
            is_media=is_media,
            parse_status=status,
            line_no=self.line_no,
            sender_phone=identity.phone if identity else None,
            sender_identity=identity,
        )
        msg.message_hash = hash_for(msg)
        return msg


# ---------------------------------------------------------------- date order


def detect_date_order(
    lines: Iterable[str], *, max_samples: int = 500
) -> DateDetection:
    """Infer DMY / MDY / YMD from header evidence in an iterable of lines.

    Iterates at most ``max_samples`` header-matching lines, tallies how
    many of them carry an unambiguous signal (day > 12 in one position,
    a 4-digit-or-large year in another), and picks the winner.

    Returns a :class:`DateDetection`. When no conclusive evidence is
    found, ``order="DMY"`` with ``confidence=0.0`` (UAE locale default).
    """
    dmy = mdy = ymd = ambiguous = malformed = 0
    samples = 0

    for line in lines:
        cleaned = _clean(line)
        if not cleaned:
            continue
        match = _ANDROID_RE.match(cleaned) or _IPHONE_RE.match(cleaned)
        if match is None:
            continue
        samples += 1

        a, b, c = match.group("a"), match.group("b"), match.group("c")
        try:
            ai, bi, ci = int(a), int(b), int(c)
        except ValueError:
            malformed += 1
            continue

        a_is_year = len(a) >= 3 or ai > 31
        c_is_year = len(c) >= 3 or ci > 31

        if a_is_year and not c_is_year:
            ymd += 1
        elif c_is_year or (not a_is_year and not c_is_year):
            # Year-last convention (most WhatsApp exports). 2-digit-year
            # ambiguity also lands here.
            if ai > 12 and bi <= 12:
                dmy += 1
            elif bi > 12 and ai <= 12:
                mdy += 1
            elif ai > 12 and bi > 12:
                # Both > 12 → not a real date in either order.
                malformed += 1
            else:
                ambiguous += 1
        else:
            # Both fields look year-shaped (rare); treat as inconclusive.
            ambiguous += 1

        if samples >= max_samples:
            break

    conclusive = dmy + mdy + ymd
    evidence = {
        "dmy": dmy,
        "mdy": mdy,
        "ymd": ymd,
        "ambiguous": ambiguous,
        "malformed": malformed,
    }

    if conclusive == 0:
        return DateDetection(
            order="DMY", confidence=0.0, samples_seen=samples, evidence=evidence
        )

    # Tie-break order: DMY > MDY > YMD (UAE locale bias when evidence ties).
    ordered: list[tuple[DateOrder, int]] = [
        ("DMY", dmy),
        ("MDY", mdy),
        ("YMD", ymd),
    ]
    winner, votes = max(ordered, key=lambda kv: kv[1])
    confidence = votes / conclusive
    return DateDetection(
        order=winner, confidence=confidence, samples_seen=samples, evidence=evidence
    )


# ---------------------------------------------------------------- public API


def parse_file(
    path: str | Path,
    *,
    group_name: str | None = None,
    encoding: str = "utf-8",
    date_order: DateOrderInput = "auto",
    tz: tzinfo | None = None,
) -> Iterator[ParsedMessage]:
    """Stream-parse a WhatsApp ``.txt`` export.

    Args:
        path: file path.
        group_name: chat title; defaults to a value inferred from the file
            name.
        encoding: source encoding (default UTF-8 with ``errors='replace'``
            so mojibaked exports don't crash the parser).
        date_order: ``"DMY"`` / ``"MDY"`` / ``"YMD"`` or ``"auto"``
            (default). When ``"auto"``, the file is scanned once for
            header evidence before the real parse pass; ambiguous files
            fall back to ``DMY``.
        tz: optional :class:`tzinfo` to attach to every parsed
            timestamp. Default is naive (local time).
    """
    p = Path(path)
    chat_name = group_name or _infer_group_name(p)
    log.info(
        "whatsapp_parse_started",
        source_file=p.name,
        group=chat_name,
        date_order=date_order,
    )

    resolved_order: DateOrder
    if date_order == "auto":
        with p.open("r", encoding=encoding, errors="replace", newline="") as fh:
            detection = detect_date_order(fh)
        resolved_order = detection.order
        log.info(
            "whatsapp_date_detected",
            source_file=p.name,
            order=detection.order,
            confidence=round(detection.confidence, 3),
            samples=detection.samples_seen,
            **{f"evidence_{k}": v for k, v in detection.evidence.items()},
        )
    else:
        resolved_order = date_order

    with p.open("r", encoding=encoding, errors="replace", newline="") as fh:
        yield from parse_lines(
            fh,
            source_file=p.name,
            group_name=chat_name,
            date_order=resolved_order,
            tz=tz,
        )


def parse_lines(
    lines: Iterable[str],
    *,
    source_file: str,
    group_name: str | None = None,
    date_order: DateOrder = "DMY",
    tz: tzinfo | None = None,
) -> Iterator[ParsedMessage]:
    """Tokenize an iterable of raw lines into :class:`ParsedMessage` records.

    Continuation lines (lines without a timestamp header) coalesce into
    the previous message. Malformed lines and orphan continuations are
    still emitted, tagged with the appropriate :class:`ParseStatus`.

    ``date_order`` must be a concrete order — for auto-detection use
    :func:`parse_file` (which can two-pass the file efficiently) or call
    :func:`detect_date_order` directly.
    """
    pending: _Buffer | None = None
    emitted = 0
    orphans = 0

    for idx, raw_line in enumerate(lines, start=1):
        cleaned = _clean(raw_line)
        if not cleaned:
            # Blank line: if we have a pending message, it's an
            # intra-message blank — preserve it. Otherwise drop.
            if pending is not None:
                pending.add_continuation("")
            continue

        match = _ANDROID_RE.match(cleaned) or _IPHONE_RE.match(cleaned)
        if match is None:
            if pending is not None:
                pending.add_continuation(cleaned)
            else:
                orphans += 1
                yield _orphan(
                    cleaned,
                    source_file=source_file,
                    group_name=group_name,
                    line_no=idx,
                )
            continue

        # Header line — flush whatever was pending before starting a new one.
        if pending is not None:
            yield pending.finalize()
            pending = None
            emitted += 1

        ts = _parse_timestamp(
            match.group("a"),
            match.group("b"),
            match.group("c"),
            match.group("H"),
            match.group("M"),
            match.group("S"),
            match.group("ampm"),
            date_order=date_order,
            tz=tz,
        )
        rest = match.group("rest")
        sender_match = _SENDER_BODY_RE.match(rest)

        if sender_match is None:
            # No "sender: body" → system message.
            pending = _Buffer(
                timestamp=ts,
                sender_name=None,
                raw_lines=[rest],
                group_name=group_name,
                source_file=source_file,
                is_media=False,
                parse_status=ParseStatus.SYSTEM,
                line_no=idx,
            )
            continue

        sender = sender_match.group("sender").strip()
        body = sender_match.group("body")
        is_media = _is_media_body(body)
        pending = _Buffer(
            timestamp=ts,
            sender_name=sender,
            raw_lines=[body],
            group_name=group_name,
            source_file=source_file,
            is_media=is_media,
            parse_status=ParseStatus.MEDIA if is_media else ParseStatus.OK,
            line_no=idx,
        )

    if pending is not None:
        yield pending.finalize()
        emitted += 1

    log.info(
        "whatsapp_parse_completed",
        source_file=source_file,
        emitted=emitted,
        orphans=orphans,
    )


# ---------------------------------------------------------------- internals


def _orphan(
    raw: str, *, source_file: str, group_name: str | None, line_no: int
) -> ParsedMessage:
    """Wrap a line with no preceding header as an orphan record."""
    log.warning("whatsapp_orphan_line", source_file=source_file, line_no=line_no)
    buf = _Buffer(
        timestamp=None,
        sender_name=None,
        raw_lines=[raw],
        group_name=group_name,
        source_file=source_file,
        is_media=False,
        parse_status=ParseStatus.ORPHAN_CONTINUATION,
        line_no=line_no,
    )
    return buf.finalize()


def _clean(line: str) -> str:
    """Strip BOM, trailing newlines, and bidi marks WhatsApp injects."""
    if line.startswith(_BOM):
        line = line[len(_BOM) :]
    return _BIDI_RE.sub("", line).rstrip("\r\n")


def _is_media_body(body: str) -> bool:
    return bool(_MEDIA_RE.search(body))


def _resolve_year(token: str) -> int:
    """``"26"`` → 2026; ``"2026"`` → 2026."""
    y = int(token)
    return 2000 + y if y < 100 else y


def _parse_timestamp(
    a: str,
    b: str,
    c: str,
    h: str,
    minute: str,
    s: str | None,
    ampm: str | None,
    *,
    date_order: DateOrder,
    tz: tzinfo | None = None,
) -> datetime | None:
    """Return a datetime, or None if the components don't form a real one.

    Naive on purpose — UAE has no DST and we anchor everything to
    Asia/Dubai conceptually. Pass ``tz`` to attach a zone.
    """
    try:
        if date_order == "DMY":
            day, month, year = int(a), int(b), _resolve_year(c)
        elif date_order == "MDY":
            month, day, year = int(a), int(b), _resolve_year(c)
        else:  # YMD
            year, month, day = _resolve_year(a), int(b), int(c)
        hour, mi = int(h), int(minute)
        sec = int(s) if s else 0
        if ampm:
            ampm_u = ampm.upper()
            if ampm_u == "AM" and hour == 12:
                hour = 0
            elif ampm_u == "PM" and hour != 12:
                hour += 12
        dt = datetime(year, month, day, hour, mi, sec)
        if tz is not None:
            dt = dt.replace(tzinfo=tz)
        return dt
    except (ValueError, OverflowError):
        return None


def hash_for(msg: ParsedMessage) -> str:
    """SHA-256 dedup hash per DECISIONS.md D-007. Public — also used by the
    tabular (CSV/Excel) parser so all formats share the same hash formula.

    Canonical formula: ``sender_name || sender_phone || message_ts || raw_text``.
    For records that have no timestamp (orphans, malformed lines) we
    fold in ``source_file`` + ``line_no`` so re-ingesting the same file
    is still idempotent without colliding lines that share text.
    """
    parts: list[str] = [
        msg.sender_name or "",
        msg.sender_phone or "",
        msg.timestamp.isoformat() if msg.timestamp else "",
        msg.raw_message,
    ]
    if msg.timestamp is None:
        parts.extend([msg.source_file, str(msg.line_no)])
    payload = "|".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def _infer_group_name(p: Path) -> str:
    """Pull the chat name out of WhatsApp's default file conventions."""
    stem = p.stem
    lower = stem.lower()
    if lower.startswith("whatsapp chat with "):
        return stem[len("WhatsApp Chat with ") :].strip() or stem
    if lower.startswith("whatsapp chat - "):
        return stem[len("WhatsApp Chat - ") :].strip() or stem
    if stem == "_chat":
        # iOS exports the conversation file as "_chat.txt" inside a folder
        # whose name carries the conversation title.
        return p.parent.name
    return stem


__all__ = [
    "DateDetection",
    "DateOrder",
    "DateOrderInput",
    "ParseStatus",
    "ParsedMessage",
    "detect_date_order",
    "hash_for",
    "parse_file",
    "parse_lines",
]
