"""Text + product normalization utilities.

P3.2 ships text-level normalization (NFKC + whitespace + noise strip).
P3.3 ships sender identity extraction. P3.5 ships the product alias
dictionary + extractor.
"""

from trade_intel.normalization.dictionary import (
    ALIASES,
    PRODUCTS,
    CanonicalProduct,
)
from trade_intel.normalization.identity import (
    BUSINESS_KEYWORDS,
    LOCATION_TOKENS,
    SenderIdentity,
    extract_identity,
    normalize_sender_name,
)
from trade_intel.normalization.products import (
    ProductMatch,
    extract_products,
)
from trade_intel.normalization.structured import (
    StructuredFields,
    extract_structured,
)
from trade_intel.normalization.text import (
    casefold_for_match,
    normalize_text,
    strip_noise,
)

__all__ = [
    "ALIASES",
    "BUSINESS_KEYWORDS",
    "LOCATION_TOKENS",
    "PRODUCTS",
    "CanonicalProduct",
    "ProductMatch",
    "SenderIdentity",
    "StructuredFields",
    "casefold_for_match",
    "extract_identity",
    "extract_products",
    "extract_structured",
    "normalize_sender_name",
    "normalize_text",
    "strip_noise",
]
