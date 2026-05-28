"""Embedding service (P4.1).

Defines the :class:`Embedder` protocol and two concrete implementations:

- :class:`MiniLMEmbedder` — production wrapper around
  ``sentence-transformers/all-MiniLM-L6-v2`` (D-004 baseline). Lazy-loads
  the ``sentence-transformers`` package on first :meth:`encode` so
  importing the module is cheap and CI without the ``[ml]`` extra
  installed still passes.
- :class:`HashEmbedder` — deterministic, dependency-free embedder.
  Same input → same vector across runs. Not semantically meaningful;
  intended for tests of downstream layers (index, scoring, persistence).

Both implementations emit L2-normalized float32 vectors so cosine
similarity collapses to a plain inner product — :class:`FaissFlatIndex`
uses ``IndexFlatIP`` accordingly and :class:`NumpyIndex` uses
``vectors @ query.T``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import numpy as np

#: Default model name (D-004).
DEFAULT_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"

#: Output dimensionality of :data:`DEFAULT_MODEL_NAME`.
DEFAULT_DIM: int = 384


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into L2-normalized float32 vectors."""

    @property
    def dim(self) -> int:
        """Output dimensionality."""
        ...

    @property
    def model_name(self) -> str:
        """Human-readable model identifier (stored on ``embeddings.model_name``)."""
        ...

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Return ``(len(texts), dim)`` float32 array of L2-normalized vectors."""
        ...


class HashEmbedder:
    """Deterministic dependency-free embedder for tests.

    Derives a vector via BLAKE2b on the input text, projects to floats
    in ``[-1, 1]``, then L2-normalizes. Same text → same vector (across
    runs and processes). Different texts → effectively uncorrelated
    vectors. Useful for testing index + scoring + persistence layers
    without paying the ``sentence-transformers`` install cost.
    """

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return f"hash-{self._dim}"

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        out = np.empty((len(texts), self._dim), dtype=np.float32)
        byte_target = self._dim * 4  # 4 bytes per float32
        for i, text in enumerate(texts):
            digest = b""
            counter = 0
            seed = text.encode("utf-8")
            while len(digest) < byte_target:
                digest += hashlib.blake2b(
                    seed + counter.to_bytes(4, "little"),
                    digest_size=64,
                ).digest()
                counter += 1
            raw = np.frombuffer(digest[:byte_target], dtype=np.uint32)
            # Map uint32 → [-1, 1] float32.
            vec = (raw.astype(np.float64) / np.iinfo(np.uint32).max) * 2.0 - 1.0
            vec = vec.astype(np.float32)
            norm = float(np.linalg.norm(vec))
            if norm > 0.0:
                vec /= norm
            out[i] = vec
        return out


class MiniLMEmbedder:
    """``sentence-transformers/all-MiniLM-L6-v2`` wrapper (D-004).

    Model is lazy-loaded on first :meth:`encode` to keep import time
    cheap and to allow projects without the ``[ml]`` extra installed to
    import :mod:`trade_intel.ml.embeddings` (e.g. for the
    :class:`Embedder` protocol or :class:`HashEmbedder`) without an
    ``ImportError``.

    Output vectors are L2-normalized so cosine similarity collapses to
    a plain inner product (used by :class:`FaissFlatIndex` /
    :class:`NumpyIndex`).
    """

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        self._model_name = model_name
        self._model: object | None = None

    @property
    def dim(self) -> int:
        # MiniLM-L6 is fixed at 384.
        return DEFAULT_DIM

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load(self) -> object:
        if self._model is None:
            try:
                from sentence_transformers import (  # type: ignore[import-not-found]
                    SentenceTransformer,
                )
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    "MiniLMEmbedder requires sentence-transformers. "
                    "Install with `pip install -e .[ml]`."
                ) from exc
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        model = self._load()
        vecs = model.encode(  # type: ignore[attr-defined]
            list(texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return np.asarray(vecs, dtype=np.float32)


__all__ = [
    "DEFAULT_DIM",
    "DEFAULT_MODEL_NAME",
    "Embedder",
    "HashEmbedder",
    "MiniLMEmbedder",
]
