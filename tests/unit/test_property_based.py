"""Property-based tests (Hypothesis) — P8.3.

Covers the load-bearing pure-function invariants of the pipeline:

* ``normalize_text`` — NFKC normalization is idempotent + emoji-safe.
* ``extract_identity`` — never raises; phone / aliases are well-formed.
* ``extract_products`` — never raises; matches are non-overlapping + ordered.
* ``classify_intent`` — never raises; confidence + scores are bounded.
* ``dedupe`` + ``DedupSet`` — idempotent, order-preserving, hash-unique.
* ``hash_for`` — deterministic + sensitive to every input field.
* Scoring (``compute_*``, ``grade_for``) — bounded + monotonic where docs claim.
* Parser robustness (``parse_lines``, ``detect_date_order``) — total functions.

Each example is bounded (``max_size=200``) and example counts are kept
modest so the full suite stays fast.
"""

from __future__ import annotations

import string
from datetime import datetime, timedelta

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from trade_intel.core.intent import IntentResult, classify_intent
from trade_intel.db.models import Condition, Demand, Grade, Inventory
from trade_intel.matching.scoring import (
    A_GRADE_THRESHOLD,
    A_PLUS_GRADE_THRESHOLD,
    B_GRADE_THRESHOLD,
    compute_evidence_weight,
    compute_match_type,
    compute_recency_multiplier,
    compute_spec_fit,
    grade_for,
    score_match,
)
from trade_intel.normalization import (
    ProductMatch,
    SenderIdentity,
    casefold_for_match,
    extract_identity,
    extract_products,
    normalize_text,
)
from trade_intel.parsers import (
    DedupSet,
    ParsedMessage,
    ParseStatus,
    dedupe,
    detect_date_order,
    hash_for,
    parse_lines,
)

# Hypothesis: keep runs bounded so the suite stays fast.
_DEFAULT_SETTINGS = settings(
    max_examples=50,
    deadline=None,  # NFKC + regex sweeps occasionally trip the 200ms ceiling.
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

# Reusable strategies.
_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Co")),
    max_size=200,
)
_NONEMPTY_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Co")),
    min_size=1,
    max_size=200,
)
_ASCII_TEXT = st.text(alphabet=string.printable, max_size=200)
_LINE_ITER = st.lists(_TEXT, max_size=30)
_PRODUCT_KEYWORDS = st.sampled_from(
    [
        "12pm 256gb",
        "iphone 15 pro max",
        "ipad mini 6",
        "ipad air 5",
        "iphone 14 pro 1tb",
        "ipad pro m2",
        "iphone 13 mini",
        "tabs wifi",
        "iphone 16 plus",
        "mini 5 64gb",
    ]
)


# ============================================================
# Group 1 — normalize_text properties
# ============================================================


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_normalize_text_default_is_idempotent(s: str) -> None:
    once = normalize_text(s)
    twice = normalize_text(once)
    assert once == twice


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_normalize_text_casefold_flatten_is_idempotent(s: str) -> None:
    once = normalize_text(s, casefold=True, flatten=True)
    twice = normalize_text(once, casefold=True, flatten=True)
    assert once == twice


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_normalize_text_strips_bom_and_bidi_marks(s: str) -> None:
    """Output must never contain WhatsApp's BOM / LRM / RLM noise."""
    noisy = "﻿" + "‎" + s + "‏" + "‪"
    out = normalize_text(noisy)
    for bad in ("﻿", "‎", "‏", "‪", "‬"):
        assert bad not in out


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_normalize_text_flatten_collapses_newlines(s: str) -> None:
    """With flatten=True every newline collapses to a single space."""
    out = normalize_text(s, flatten=True)
    assert "\n" not in out
    assert "\r" not in out


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_casefold_for_match_is_idempotent(s: str) -> None:
    once = casefold_for_match(s)
    twice = casefold_for_match(once)
    assert once == twice


@given(st.sampled_from(["", "   ", "\t\n", "‎‏﻿", "     "]))
def test_normalize_text_whitespace_only_yields_empty(s: str) -> None:
    assert normalize_text(s, flatten=True).strip() == ""


# ============================================================
# Group 2 — extract_identity properties
# ============================================================


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_extract_identity_never_raises(s: str) -> None:
    identity = extract_identity(s)
    assert isinstance(identity, SenderIdentity)


@given(st.sampled_from([None, "", "   ", "\t", "\n\n"]))
def test_extract_identity_empty_returns_empty_identity(s: str | None) -> None:
    identity = extract_identity(s)
    assert identity.phone is None
    assert identity.aliases == ()
    assert not identity.is_business


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_extract_identity_phone_if_present_is_well_formed(s: str) -> None:
    """If identity.phone is not None it always starts with '+' OR is all digits,
    and is 7-16 characters total."""
    phone = extract_identity(s).phone
    if phone is None:
        return
    assert phone.startswith("+") or phone.isdigit()
    digits = phone.lstrip("+")
    assert digits.isdigit()
    assert 7 <= len(digits) <= 16


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_extract_identity_aliases_are_unique_when_present(s: str) -> None:
    aliases = extract_identity(s).aliases
    assert isinstance(aliases, tuple)
    # Set-equal — no duplicate alias entries.
    assert len(aliases) == len(set(aliases))


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_extract_identity_phone_only_with_phone_normalizes_to_phone(
    s: str,
) -> None:
    """When phone canonicalization succeeded AND ``is_phone_only`` fired,
    ``normalized_name`` is the canonical phone — so cross-handle dedup
    can rendezvous on the phone string.

    Short numeric runs (e.g. ``'0'``, ``'12'``) trigger ``is_phone_only``
    but fail phone canonicalization (< 7 digits); those are not asserted
    here because the system tracks them by alias instead.
    """
    identity = extract_identity(s)
    if identity.is_phone_only and identity.phone:
        assert identity.normalized_name == identity.phone


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_extract_identity_is_deterministic(s: str) -> None:
    a = extract_identity(s)
    b = extract_identity(s)
    assert a == b


# ============================================================
# Group 3 — extract_products properties
# ============================================================


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_extract_products_never_raises(s: str) -> None:
    matches = extract_products(s)
    assert isinstance(matches, list)


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_extract_products_offsets_are_valid(s: str) -> None:
    """For every match: 0 <= start < end, and the slice text matches surface."""
    flat = normalize_text(s, casefold=True, flatten=True)
    for m in extract_products(s):
        assert isinstance(m, ProductMatch)
        assert 0 <= m.start < m.end <= len(flat)
        # surface is the literal slice in the flattened text.
        assert flat[m.start : m.end] == m.surface


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_extract_products_non_overlapping_and_ordered(s: str) -> None:
    matches = extract_products(s)
    for i in range(1, len(matches)):
        # Source-order + non-overlap: previous end ≤ next start.
        assert matches[i - 1].end <= matches[i].start


@given(st.sampled_from(["", "   ", "\n\n", "﻿"]))
def test_extract_products_empty_returns_empty_list(s: str) -> None:
    assert extract_products(s) == []


@_DEFAULT_SETTINGS
@given(_PRODUCT_KEYWORDS, _TEXT, _TEXT)
def test_extract_products_finds_seeded_keyword_in_context(
    seed: str, prefix: str, suffix: str
) -> None:
    """Embedding a known product phrase inside arbitrary surrounding text
    must still surface at least one ProductMatch for it."""
    sentence = f"{prefix} {seed} {suffix}"
    matches = extract_products(sentence)
    assert matches, f"failed to extract seeded {seed!r} from {sentence!r}"


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_extract_products_confidence_in_unit_interval(s: str) -> None:
    for m in extract_products(s):
        assert 0.0 <= m.confidence <= 1.0


# ============================================================
# Group 4 — classify_intent properties
# ============================================================


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_classify_intent_never_raises(s: str) -> None:
    result = classify_intent(s)
    assert isinstance(result, IntentResult)


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_classify_intent_confidence_in_unit_interval(s: str) -> None:
    result = classify_intent(s)
    assert 0.0 <= result.confidence <= 1.0


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_classify_intent_scores_are_non_negative(s: str) -> None:
    result = classify_intent(s)
    assert result.buyer_score >= 0
    assert result.seller_score >= 0
    assert result.broker_score >= 0


@_DEFAULT_SETTINGS
@given(_TEXT)
def test_classify_intent_signal_offsets_are_valid(s: str) -> None:
    """Every signal span lies within the flattened input."""
    flat = normalize_text(s, casefold=True, flatten=True)
    for sig in classify_intent(s).signals:
        assert 0 <= sig.start <= sig.end <= len(flat)
        assert sig.weight > 0


@given(st.sampled_from([None, "", "   ", "\t\n"]))
def test_classify_intent_empty_is_unknown_medium(s: str | None) -> None:
    result = classify_intent(s)
    assert result.intent.value == "unknown"
    assert result.urgency.value == "medium"
    assert result.confidence == 0.0


# ============================================================
# Group 5 — dedupe / DedupSet properties
# ============================================================


def _msg(hash_: str, line_no: int = 0) -> ParsedMessage:
    """Cheap ParsedMessage stand-in for dedup-property tests."""
    return ParsedMessage(
        timestamp=None,
        sender_name=None,
        raw_message="",
        normalized_message="",
        group_name=None,
        message_hash=hash_,
        source_file="prop.txt",
        is_media=False,
        parse_status=ParseStatus.OK,
        line_no=line_no,
    )


_HASH_STRATEGY = st.text(alphabet=string.hexdigits, min_size=8, max_size=64)
_MSG_LIST = st.lists(_HASH_STRATEGY, max_size=40).map(
    lambda hashes: [_msg(h, i) for i, h in enumerate(hashes)]
)


@_DEFAULT_SETTINGS
@given(_MSG_LIST)
def test_dedupe_output_bounded_by_input(msgs: list[ParsedMessage]) -> None:
    out = list(dedupe(msgs))
    assert len(out) <= len(msgs)


@_DEFAULT_SETTINGS
@given(_MSG_LIST)
def test_dedupe_output_hashes_are_unique(msgs: list[ParsedMessage]) -> None:
    out = list(dedupe(msgs))
    hashes = [m.message_hash for m in out]
    assert len(hashes) == len(set(hashes))


@_DEFAULT_SETTINGS
@given(_MSG_LIST)
def test_dedupe_is_idempotent(msgs: list[ParsedMessage]) -> None:
    """dedupe(dedupe(x)) emits the same hash sequence as dedupe(x)."""
    once = list(dedupe(msgs))
    twice = list(dedupe(once))
    assert [m.message_hash for m in once] == [m.message_hash for m in twice]


@_DEFAULT_SETTINGS
@given(_MSG_LIST)
def test_dedupe_preserves_source_order(msgs: list[ParsedMessage]) -> None:
    """For two surviving hashes a, b: if a appears before b in input, it
    must also appear before b in output (first-occurrence wins)."""
    out = list(dedupe(msgs))
    first_seen: dict[str, int] = {}
    for i, m in enumerate(msgs):
        first_seen.setdefault(m.message_hash, i)
    out_order = [first_seen[m.message_hash] for m in out]
    assert out_order == sorted(out_order)


@_DEFAULT_SETTINGS
@given(_MSG_LIST)
def test_dedupset_stats_arithmetic(msgs: list[ParsedMessage]) -> None:
    """``seen == unique + duplicates`` for any input."""
    seen = DedupSet()
    list(dedupe(msgs, seen=seen))
    assert seen.stats.seen == len(msgs)
    assert seen.stats.seen == seen.stats.unique + seen.stats.duplicates


@_DEFAULT_SETTINGS
@given(_MSG_LIST, _MSG_LIST)
def test_dedupset_cross_batch_is_global(
    batch1: list[ParsedMessage], batch2: list[ParsedMessage]
) -> None:
    """When the same shared DedupSet runs across two batches, the union
    of output hashes equals the set of distinct hashes across the union
    of inputs."""
    shared = DedupSet()
    out1 = list(dedupe(batch1, seen=shared))
    out2 = list(dedupe(batch2, seen=shared))
    combined_hashes = {m.message_hash for m in (*out1, *out2)}
    input_hashes = {m.message_hash for m in (*batch1, *batch2)}
    assert combined_hashes == input_hashes


# ============================================================
# Group 6 — hash_for properties
# ============================================================


def _msg_full(
    *,
    sender: str | None,
    phone: str | None,
    raw: str,
    ts: datetime | None,
    line_no: int = 0,
) -> ParsedMessage:
    return ParsedMessage(
        timestamp=ts,
        sender_name=sender,
        raw_message=raw,
        normalized_message=raw,
        group_name=None,
        message_hash="",
        source_file="prop.txt",
        is_media=False,
        parse_status=ParseStatus.OK,
        line_no=line_no,
        sender_phone=phone,
    )


_DT = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
).map(lambda d: d.replace(microsecond=0))


@_DEFAULT_SETTINGS
@given(_NONEMPTY_TEXT, _NONEMPTY_TEXT, _DT)
def test_hash_for_is_deterministic(
    sender: str, raw: str, ts: datetime
) -> None:
    msg_a = _msg_full(sender=sender, phone=None, raw=raw, ts=ts)
    msg_b = _msg_full(sender=sender, phone=None, raw=raw, ts=ts)
    assert hash_for(msg_a) == hash_for(msg_b)


@_DEFAULT_SETTINGS
@given(_NONEMPTY_TEXT, _NONEMPTY_TEXT, _NONEMPTY_TEXT, _DT)
def test_hash_for_changes_when_sender_changes(
    sender_a: str, sender_b: str, raw: str, ts: datetime
) -> None:
    assume(sender_a != sender_b)
    a = _msg_full(sender=sender_a, phone=None, raw=raw, ts=ts)
    b = _msg_full(sender=sender_b, phone=None, raw=raw, ts=ts)
    assert hash_for(a) != hash_for(b)


@_DEFAULT_SETTINGS
@given(_NONEMPTY_TEXT, _NONEMPTY_TEXT, _NONEMPTY_TEXT, _DT)
def test_hash_for_changes_when_body_changes(
    sender: str, raw_a: str, raw_b: str, ts: datetime
) -> None:
    assume(raw_a != raw_b)
    a = _msg_full(sender=sender, phone=None, raw=raw_a, ts=ts)
    b = _msg_full(sender=sender, phone=None, raw=raw_b, ts=ts)
    assert hash_for(a) != hash_for(b)


@_DEFAULT_SETTINGS
@given(_NONEMPTY_TEXT, _NONEMPTY_TEXT, _DT, st.integers(min_value=1, max_value=86_400))
def test_hash_for_changes_when_timestamp_changes(
    sender: str, raw: str, ts: datetime, delta_seconds: int
) -> None:
    a = _msg_full(sender=sender, phone=None, raw=raw, ts=ts)
    b = _msg_full(
        sender=sender, phone=None, raw=raw, ts=ts + timedelta(seconds=delta_seconds)
    )
    assert hash_for(a) != hash_for(b)


@_DEFAULT_SETTINGS
@given(_NONEMPTY_TEXT, _NONEMPTY_TEXT, _DT)
def test_hash_for_returns_sha256_hex(sender: str, raw: str, ts: datetime) -> None:
    h = hash_for(_msg_full(sender=sender, phone=None, raw=raw, ts=ts))
    assert isinstance(h, str)
    assert len(h) == 64
    assert all(c in string.hexdigits for c in h)


@_DEFAULT_SETTINGS
@given(_NONEMPTY_TEXT, _NONEMPTY_TEXT, st.integers(min_value=1, max_value=10_000))
def test_hash_for_orphan_folds_in_line_no(
    sender: str, raw: str, line_no: int
) -> None:
    """For ts-less records, the hash must fold in source_file + line_no so
    same-body orphans on different lines stay distinct."""
    a = _msg_full(sender=sender, phone=None, raw=raw, ts=None, line_no=line_no)
    b = _msg_full(sender=sender, phone=None, raw=raw, ts=None, line_no=line_no + 1)
    assert hash_for(a) != hash_for(b)


# ============================================================
# Group 7 — scoring properties
# ============================================================


_NOW = datetime(2026, 6, 1, 12, 0)


@_DEFAULT_SETTINGS
@given(st.floats(min_value=-1000.0, max_value=10_000.0, allow_nan=False))
def test_recency_multiplier_in_documented_range(hours_back: float) -> None:
    """Output is always one of the 6 documented tier values, capped to
    [0.05, 1.0]."""
    posted = _NOW - timedelta(hours=hours_back)
    mult = compute_recency_multiplier(posted, now=_NOW)
    assert mult in {0.05, 0.15, 0.40, 0.70, 0.90, 1.00}


@_DEFAULT_SETTINGS
@given(
    st.floats(min_value=0.0, max_value=24 * 30, allow_nan=False),
    st.floats(min_value=0.0, max_value=24 * 30, allow_nan=False),
)
def test_recency_monotonically_non_increasing_with_age(
    age_a: float, age_b: float
) -> None:
    """Older inventory never scores higher than fresher inventory."""
    assume(age_a <= age_b)
    posted_a = _NOW - timedelta(hours=age_a)
    posted_b = _NOW - timedelta(hours=age_b)
    a = compute_recency_multiplier(posted_a, now=_NOW)
    b = compute_recency_multiplier(posted_b, now=_NOW)
    assert a >= b


def test_recency_missing_posted_at_returns_neutral() -> None:
    assert compute_recency_multiplier(None, now=_NOW) == 0.5


@_DEFAULT_SETTINGS
@given(st.floats(min_value=-2.0, max_value=2.0, allow_nan=False))
def test_grade_for_buckets_by_threshold(score: float) -> None:
    g = grade_for(score)
    if score >= A_PLUS_GRADE_THRESHOLD:
        assert g is Grade.A_PLUS
    elif score >= A_GRADE_THRESHOLD:
        assert g is Grade.A
    elif score >= B_GRADE_THRESHOLD:
        assert g is Grade.B
    else:
        assert g is Grade.C


@_DEFAULT_SETTINGS
@given(
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_grade_monotonic_under_score(a: float, b: float) -> None:
    """If a >= b then grade_for(a) >= grade_for(b) (by ordinal)."""
    assume(a >= b)
    _ord = {Grade.C: 0, Grade.B: 1, Grade.A: 2, Grade.A_PLUS: 3}
    assert _ord[grade_for(a)] >= _ord[grade_for(b)]


_STORAGE = st.sampled_from([None, "64GB", "128GB", "256GB", "512GB", "1TB"])
_CONDITION = st.sampled_from([None, Condition.SEALED, Condition.NEW, Condition.USED])
_QTY = st.one_of(st.none(), st.integers(min_value=1, max_value=500))


def _make_inv(
    *, product: str, storage, condition, qty
) -> Inventory:
    return Inventory(
        normalized_product=product,
        storage=storage,
        condition=condition,
        quantity=qty,
    )


def _make_dem(
    *, product: str, storage, qty
) -> Demand:
    return Demand(
        normalized_product=product,
        storage=storage,
        quantity=qty,
    )


@_DEFAULT_SETTINGS
@given(_STORAGE, _CONDITION, _QTY, _STORAGE, _QTY)
def test_spec_fit_bounded_unit_interval(
    inv_storage, inv_condition, inv_qty, dem_storage, dem_qty
) -> None:
    inv = _make_inv(
        product="iPhone 12 Pro Max",
        storage=inv_storage,
        condition=inv_condition,
        qty=inv_qty,
    )
    dem = _make_dem(
        product="iPhone 12 Pro Max", storage=dem_storage, qty=dem_qty
    )
    s = compute_spec_fit(inv, dem)
    assert 0.0 <= s <= 1.0


@_DEFAULT_SETTINGS
@given(_STORAGE, _CONDITION, _QTY)
def test_spec_fit_self_match_is_full(
    storage, condition, qty
) -> None:
    """Inventory matched against its own structural shape (storage + qty)
    yields spec_fit == 1.0. (Demand has no `condition` attribute.)"""
    inv = _make_inv(
        product="iPad Air 5", storage=storage, condition=condition, qty=qty
    )
    dem = _make_dem(product="iPad Air 5", storage=storage, qty=qty)
    assert compute_spec_fit(inv, dem) == 1.0


@_DEFAULT_SETTINGS
@given(_STORAGE, _CONDITION, _QTY)
def test_evidence_bounded_unit_interval(storage, condition, qty) -> None:
    inv = _make_inv(
        product="iPhone 15 Pro Max",
        storage=storage,
        condition=condition,
        qty=qty,
    )
    dem = _make_dem(product="iPhone 15 Pro Max", storage=storage, qty=qty)
    e = compute_evidence_weight(inv, dem)
    assert 0.5 <= e <= 1.0


@_DEFAULT_SETTINGS
@given(
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    _STORAGE,
    _CONDITION,
    _QTY,
)
def test_score_match_final_in_unit_interval(
    semantic: float, storage, condition, qty
) -> None:
    inv = _make_inv(
        product="iPhone 12 Pro Max",
        storage=storage,
        condition=condition,
        qty=qty,
    )
    dem = _make_dem(
        product="iPhone 12 Pro Max", storage=storage, qty=qty
    )
    result = score_match(inv, dem, semantic_score=semantic, now=_NOW)
    assert 0.0 <= result.final <= 1.0
    assert 0.0 <= result.semantic <= 1.0
    assert 0.0 <= result.spec_fit <= 1.0
    assert 0.05 <= result.recency <= 1.0 or result.recency == 0.5
    # Documented identity.
    expected = result.semantic * result.spec_fit * result.recency * result.evidence
    assert abs(expected - result.final) < 1e-9


@_DEFAULT_SETTINGS
@given(st.floats(min_value=-5.0, max_value=5.0, allow_nan=False))
def test_score_match_semantic_clamped(semantic: float) -> None:
    """``score_match`` must clamp pathological semantic inputs to [0, 1]."""
    inv = _make_inv(
        product="iPhone 12 Pro Max", storage="256GB", condition=Condition.SEALED, qty=5
    )
    dem = _make_dem(product="iPhone 12 Pro Max", storage="256GB", qty=5)
    r = score_match(inv, dem, semantic_score=semantic, now=_NOW)
    assert 0.0 <= r.semantic <= 1.0


@_DEFAULT_SETTINGS
@given(st.sampled_from(["iPhone 12 Pro Max", "iPad Mini 6"]), _STORAGE, _STORAGE)
def test_match_type_exact_requires_same_product(
    product: str, inv_sto, dem_sto
) -> None:
    """When products differ, match_type is PROBABLE; when they match,
    type is EXACT or SIMILAR."""
    inv = _make_inv(product=product, storage=inv_sto, condition=None, qty=None)
    other = "iPad Air 5" if product != "iPad Air 5" else "iPhone 14 Pro"
    dem_same = _make_dem(product=product, storage=dem_sto, qty=None)
    dem_diff = _make_dem(product=other, storage=dem_sto, qty=None)
    spec_same = compute_spec_fit(inv, dem_same)
    spec_diff = compute_spec_fit(inv, dem_diff)
    assert compute_match_type(inv, dem_same, spec_fit=spec_same).value in {
        "exact",
        "similar",
    }
    assert compute_match_type(inv, dem_diff, spec_fit=spec_diff).value == "probable"


# ============================================================
# Group 8 — Parser robustness (parse_lines + detect_date_order)
# ============================================================


@_DEFAULT_SETTINGS
@given(_LINE_ITER)
def test_parse_lines_never_raises(lines: list[str]) -> None:
    """No combination of arbitrary lines crashes the streaming parser."""
    out = list(parse_lines(lines, source_file="prop.txt", group_name="g"))
    assert isinstance(out, list)


@_DEFAULT_SETTINGS
@given(_LINE_ITER)
def test_parse_lines_status_always_enum(lines: list[str]) -> None:
    for msg in parse_lines(lines, source_file="prop.txt", group_name="g"):
        assert isinstance(msg.parse_status, ParseStatus)
        assert isinstance(msg.message_hash, str)
        assert len(msg.message_hash) == 64  # SHA-256 hex


@_DEFAULT_SETTINGS
@given(_LINE_ITER)
def test_parse_lines_output_hashes_are_unique_per_message(
    lines: list[str],
) -> None:
    """Within a single file, distinct messages must carry distinct
    hashes (D-007 includes source_file + line_no for ts-less records)."""
    out = list(parse_lines(lines, source_file="prop.txt", group_name="g"))
    hashes = [m.message_hash for m in out]
    assert len(hashes) == len(set(hashes))


@_DEFAULT_SETTINGS
@given(_LINE_ITER)
def test_detect_date_order_returns_documented_shape(lines: list[str]) -> None:
    d = detect_date_order(lines)
    assert d.order in {"DMY", "MDY", "YMD"}
    assert 0.0 <= d.confidence <= 1.0
    assert d.samples_seen >= 0
    assert set(d.evidence.keys()) >= {"dmy", "mdy", "ymd", "ambiguous", "malformed"}
    assert all(v >= 0 for v in d.evidence.values())


@_DEFAULT_SETTINGS
@given(_LINE_ITER)
def test_detect_date_order_ambiguous_input_defaults_to_dmy(
    lines: list[str],
) -> None:
    """When no conclusive day-over-12 / 4-digit-year evidence exists,
    detect_date_order falls back to DMY @ confidence=0 (UAE bias)."""
    d = detect_date_order(lines)
    conclusive = d.evidence["dmy"] + d.evidence["mdy"] + d.evidence["ymd"]
    if conclusive == 0:
        assert d.order == "DMY"
        assert d.confidence == 0.0
