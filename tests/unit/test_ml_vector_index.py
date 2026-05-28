"""Unit tests for vector indexes (P4.2)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from trade_intel.ml.embeddings import HashEmbedder
from trade_intel.ml.faiss_index import NumpyIndex, VectorIndex

# ---------- construction ----------


def test_numpy_index_default_empty() -> None:
    idx = NumpyIndex(8)
    assert idx.dim == 8
    assert idx.size == 0
    assert idx.search(np.zeros(8, dtype=np.float32), k=5) == []


def test_numpy_index_rejects_nonpositive_dim() -> None:
    with pytest.raises(ValueError):
        NumpyIndex(0)
    with pytest.raises(ValueError):
        NumpyIndex(-3)


def test_numpy_index_satisfies_protocol() -> None:
    assert isinstance(NumpyIndex(8), VectorIndex)


# ---------- add ----------


def test_numpy_index_add_single_batch() -> None:
    idx = NumpyIndex(4)
    vecs = np.array(
        [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]], dtype=np.float32
    )
    idx.add(vecs, [10, 20, 30])
    assert idx.size == 3


def test_numpy_index_add_multiple_batches_concatenates() -> None:
    idx = NumpyIndex(4)
    idx.add(np.eye(4, dtype=np.float32)[:2], [1, 2])
    idx.add(np.eye(4, dtype=np.float32)[2:], [3, 4])
    assert idx.size == 4


def test_numpy_index_add_rejects_dim_mismatch() -> None:
    idx = NumpyIndex(4)
    with pytest.raises(ValueError, match="dim mismatch"):
        idx.add(np.zeros((1, 8), dtype=np.float32), [1])


def test_numpy_index_add_rejects_id_count_mismatch() -> None:
    idx = NumpyIndex(4)
    with pytest.raises(ValueError, match="row count"):
        idx.add(np.zeros((2, 4), dtype=np.float32), [1, 2, 3])


def test_numpy_index_add_rejects_1d_input() -> None:
    idx = NumpyIndex(4)
    with pytest.raises(ValueError, match="2-D"):
        idx.add(np.zeros(4, dtype=np.float32), [1])


# ---------- search ----------


def test_numpy_index_search_finds_self() -> None:
    idx = NumpyIndex(4)
    v = np.array([[1.0, 0, 0, 0]], dtype=np.float32)
    idx.add(v, [42])
    result = idx.search(v[0], k=1)
    assert len(result) == 1
    assert result[0][0] == 42
    assert result[0][1] == pytest.approx(1.0, abs=1e-5)


def test_numpy_index_search_ranks_by_inner_product() -> None:
    idx = NumpyIndex(2)
    # Three vectors at angles 0°, 60°, 90° from query.
    vecs = np.array(
        [
            [1.0, 0.0],          # aligned
            [0.5, 0.8660254],    # 60°
            [0.0, 1.0],          # 90°
        ],
        dtype=np.float32,
    )
    idx.add(vecs, [1, 2, 3])
    result = idx.search(np.array([1.0, 0.0], dtype=np.float32), k=3)
    assert [r[0] for r in result] == [1, 2, 3]
    assert result[0][1] > result[1][1] > result[2][1]


def test_numpy_index_search_k_caps_at_size() -> None:
    idx = NumpyIndex(2)
    idx.add(np.eye(2, dtype=np.float32), [1, 2])
    assert len(idx.search(np.array([1.0, 0.0], dtype=np.float32), k=99)) == 2


def test_numpy_index_search_1d_or_2d_query() -> None:
    idx = NumpyIndex(2)
    idx.add(np.eye(2, dtype=np.float32), [1, 2])
    q1d = np.array([1.0, 0.0], dtype=np.float32)
    q2d = q1d.reshape(1, -1)
    r1 = idx.search(q1d, k=1)
    r2 = idx.search(q2d, k=1)
    assert r1[0][0] == r2[0][0] == 1


def test_numpy_index_search_rejects_wrong_dim() -> None:
    idx = NumpyIndex(4)
    idx.add(np.eye(4, dtype=np.float32), [1, 2, 3, 4])
    with pytest.raises(ValueError, match="query shape"):
        idx.search(np.zeros(8, dtype=np.float32), k=1)


def test_numpy_index_real_embedder_integration() -> None:
    """End-to-end with HashEmbedder: identical texts rank at the top."""
    e = HashEmbedder(dim=32)
    texts = ["iPhone 12 Pro Max", "iPad Mini 5", "iPad Air 5"]
    vecs = e.encode(texts)
    idx = NumpyIndex(e.dim)
    idx.add(vecs, [101, 102, 103])
    # Query with the same first text → should rank 101 top.
    q = e.encode(["iPhone 12 Pro Max"])[0]
    top = idx.search(q, k=3)
    assert top[0][0] == 101


# ---------- persistence ----------


def test_numpy_index_persist_and_load_roundtrip(tmp_path: Path) -> None:
    e = HashEmbedder(dim=16)
    vecs = e.encode(["alpha", "beta", "gamma"])
    idx = NumpyIndex(e.dim)
    idx.add(vecs, [1, 2, 3])
    idx.persist(tmp_path / "idx")

    loaded = NumpyIndex.load(tmp_path / "idx")
    assert loaded.dim == idx.dim
    assert loaded.size == idx.size
    # Search results identical pre/post round-trip.
    q = vecs[0]
    a = idx.search(q, k=3)
    b = loaded.search(q, k=3)
    assert a == b


def test_numpy_index_empty_index_round_trip(tmp_path: Path) -> None:
    idx = NumpyIndex(8)
    idx.persist(tmp_path / "empty")
    loaded = NumpyIndex.load(tmp_path / "empty")
    assert loaded.size == 0
    assert loaded.dim == 8


def test_numpy_index_persist_writes_expected_files(tmp_path: Path) -> None:
    e = HashEmbedder(dim=8)
    idx = NumpyIndex(e.dim)
    idx.add(e.encode(["x"]), [1])
    idx.persist(tmp_path / "files")
    assert (tmp_path / "files" / "meta.json").exists()
    assert (tmp_path / "files" / "vectors.npy").exists()


def test_numpy_index_meta_records_kind(tmp_path: Path) -> None:
    import json

    NumpyIndex(8).persist(tmp_path / "k")
    meta = json.loads((tmp_path / "k" / "meta.json").read_text())
    assert meta["kind"] == "numpy"
    assert meta["dim"] == 8
    assert meta["ids"] == []


# ---------- FaissFlatIndex constructed but lazy ----------


def test_faiss_flat_index_constructible_without_faiss() -> None:
    from trade_intel.ml.faiss_index import FaissFlatIndex

    # Construction alone must not import faiss.
    idx = FaissFlatIndex(8)
    assert idx.dim == 8
    assert idx.size == 0
    assert idx.search(np.zeros(8, dtype=np.float32), k=5) == []
