"""Unit tests for the embedding service (P4.1)."""

from __future__ import annotations

import numpy as np
import pytest

from trade_intel.ml.embeddings import (
    DEFAULT_DIM,
    DEFAULT_MODEL_NAME,
    Embedder,
    HashEmbedder,
    MiniLMEmbedder,
)

# ---------- HashEmbedder ----------


def test_hash_embedder_default_dim() -> None:
    e = HashEmbedder()
    assert e.dim == DEFAULT_DIM


def test_hash_embedder_custom_dim() -> None:
    e = HashEmbedder(dim=16)
    assert e.dim == 16


def test_hash_embedder_rejects_nonpositive_dim() -> None:
    with pytest.raises(ValueError):
        HashEmbedder(dim=0)
    with pytest.raises(ValueError):
        HashEmbedder(dim=-1)


def test_hash_embedder_model_name_includes_dim() -> None:
    assert HashEmbedder(dim=32).model_name == "hash-32"


def test_hash_embedder_encode_shape() -> None:
    e = HashEmbedder(dim=64)
    out = e.encode(["a", "b", "c"])
    assert out.shape == (3, 64)
    assert out.dtype == np.float32


def test_hash_embedder_empty_input() -> None:
    out = HashEmbedder(dim=8).encode([])
    assert out.shape == (0, 8)
    assert out.dtype == np.float32


def test_hash_embedder_deterministic_across_calls() -> None:
    e = HashEmbedder(dim=64)
    a = e.encode(["hello"])
    b = e.encode(["hello"])
    np.testing.assert_array_equal(a, b)


def test_hash_embedder_deterministic_across_instances() -> None:
    a = HashEmbedder(dim=32).encode(["world"])
    b = HashEmbedder(dim=32).encode(["world"])
    np.testing.assert_array_equal(a, b)


def test_hash_embedder_different_text_yields_different_vector() -> None:
    e = HashEmbedder(dim=64)
    a = e.encode(["apple"])
    b = e.encode(["banana"])
    assert not np.array_equal(a, b)


def test_hash_embedder_vectors_l2_normalized() -> None:
    e = HashEmbedder(dim=64)
    vecs = e.encode(["x", "y", "z" * 100])
    norms = np.linalg.norm(vecs, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-5)


def test_hash_embedder_batch_matches_individual() -> None:
    e = HashEmbedder(dim=16)
    batch = e.encode(["a", "b", "c"])
    indiv = np.vstack([e.encode([t]) for t in ("a", "b", "c")])
    np.testing.assert_array_equal(batch, indiv)


def test_hash_embedder_handles_unicode() -> None:
    e = HashEmbedder(dim=16)
    out = e.encode(["محمد", "iPhone 12 Pro Max", "🚀"])
    assert out.shape == (3, 16)
    norms = np.linalg.norm(out, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-5)


def test_hash_embedder_satisfies_embedder_protocol() -> None:
    e = HashEmbedder(dim=8)
    assert isinstance(e, Embedder)


# ---------- MiniLMEmbedder (no model load) ----------


def test_minilm_embedder_default_model_name() -> None:
    e = MiniLMEmbedder()
    assert e.model_name == DEFAULT_MODEL_NAME
    assert e.dim == DEFAULT_DIM


def test_minilm_embedder_custom_model_name() -> None:
    e = MiniLMEmbedder("some/other-model")
    assert e.model_name == "some/other-model"


def test_minilm_embedder_satisfies_embedder_protocol() -> None:
    e = MiniLMEmbedder()
    assert isinstance(e, Embedder)


# The actual model load (.encode) is skipped here because it pulls in
# sentence-transformers + torch (~700 MB). Covered by an opt-in integration
# test or by installing the [ml] extra and running encode() manually.
