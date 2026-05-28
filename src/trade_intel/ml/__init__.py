"""ML primitives — embedding service + vector index.

Phase 4 ships:
- :mod:`embeddings` — `Embedder` protocol with `MiniLMEmbedder` (real,
  lazy-imports `sentence-transformers` from the `[ml]` extra) and
  `HashEmbedder` (deterministic, dependency-free, for tests).
- :mod:`faiss_index` — `VectorIndex` protocol with `FaissFlatIndex`
  (real, lazy-imports `faiss-cpu`) and `NumpyIndex` (pure-numpy
  fallback for tests).
"""

from trade_intel.ml.embeddings import (
    DEFAULT_DIM,
    DEFAULT_MODEL_NAME,
    Embedder,
    HashEmbedder,
    MiniLMEmbedder,
)
from trade_intel.ml.faiss_index import (
    FaissFlatIndex,
    NumpyIndex,
    VectorIndex,
)

__all__ = [
    "DEFAULT_DIM",
    "DEFAULT_MODEL_NAME",
    "Embedder",
    "FaissFlatIndex",
    "HashEmbedder",
    "MiniLMEmbedder",
    "NumpyIndex",
    "VectorIndex",
]
