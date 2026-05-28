"""Unicode-aware text normalization.

Emoji-safe by design: we preserve U+200D (ZWJ) and the variation
selectors (U+FE0E / U+FE0F) that compose emoji sequences. Stripping
either of those silently rewrites multi-codepoint family emojis into
three separate people and keycap digits into bare numerals. Everything
else invisible (bidi marks, soft hyphens, zero-width spaces, BOMs) is
removed because it pollutes hashes and embeddings without being
human-readable.

NFKC ("Normalization Form Compatibility Composition") folds away the
visually-identical-but-byte-different sequences traders' phones love to
emit: full-width digits (e.g. U+FF12 U+FF15 U+FF16 -> "256"), the FI
ligature U+FB01 -> "fi", non-breaking spaces inside numbers, etc.
"""

from __future__ import annotations

import re
import unicodedata

# Invisible code points we strip outright. Stays in sync with the
# "preserved" comment below.
#   U+00AD  SOFT HYPHEN
#   U+200B  ZERO WIDTH SPACE
#   U+200C  ZERO WIDTH NON-JOINER
#   U+200E  LRM
#   U+200F  RLM
#   U+202A..U+202E   LRE / RLE / PDF / LRO / RLO
#   U+2066..U+2069   LRI / RLI / FSI / PDI
#   U+FEFF  BOM / ZWNBSP
#
# Deliberately PRESERVED:
#   U+200D  ZWJ              (joins emoji sequences)
#   U+FE0E / U+FE0F          (emoji variation selectors)
_NOISE_RE = re.compile(
    "[\u00ad\u200b\u200c\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)

# Horizontal whitespace classes that collapse to a single ASCII space.
# Newline is excluded so multi-line message structure survives.
#   \t                       tab
#   U+00A0                   NBSP
#   U+1680                   OGHAM SPACE MARK
#   U+2000..U+200A           EN QUAD .. HAIR SPACE
#   U+202F                   NARROW NO-BREAK SPACE
#   U+205F                   MEDIUM MATHEMATICAL SPACE
#   U+3000                   IDEOGRAPHIC SPACE
_HSPACE_RE = re.compile(
    "[ \t\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]+"
)

# Two-or-more newlines -> one newline.
_MULTI_NL_RE = re.compile(r"\n{2,}")

# Any whitespace run (used when caller asks to flatten across newlines).
_ANY_WS_RE = re.compile(r"\s+")


def strip_noise(s: str) -> str:
    """Remove invisible formatting characters (BOM, bidi marks, soft hyphen).

    Preserves ZWJ and variation selectors so emoji sequences survive.
    """
    return _NOISE_RE.sub("", s)


def normalize_text(
    s: str,
    *,
    casefold: bool = False,
    flatten: bool = False,
) -> str:
    """Apply NFKC + noise-strip + whitespace collapse to ``s``.

    Args:
        s: any input string (may be empty, may contain emojis or Arabic).
        casefold: when True, return Unicode case-folded text (correct for
            non-Latin scripts; safer than ``.lower()``).
        flatten: when True, collapse newlines + horizontal whitespace into
            single spaces (one-line output). When False, repeated horizontal
            whitespace collapses to one space and ``\\n{2,}`` collapses to
            one ``\\n``; otherwise line structure is preserved.

    Returns:
        Normalized string. Empty / whitespace-only input returns ``""``.
    """
    if not s:
        return ""

    s = unicodedata.normalize("NFKC", s)
    s = _NOISE_RE.sub("", s)

    if flatten:
        s = _ANY_WS_RE.sub(" ", s)
    else:
        s = _HSPACE_RE.sub(" ", s)
        s = _MULTI_NL_RE.sub("\n", s)
        lines = [line.strip() for line in s.split("\n")]
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        s = "\n".join(lines)

    if casefold:
        s = s.casefold()

    return s.strip()


def casefold_for_match(s: str) -> str:
    """Convenience wrapper: ``normalize_text(s, casefold=True, flatten=True)``.

    Use this on text that's going into an embedding, an index lookup, or a
    similarity comparison — anywhere case + whitespace shouldn't matter.
    """
    return normalize_text(s, casefold=True, flatten=True)
