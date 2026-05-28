"""Vector-index abstraction (P4.2).

:class:`VectorIndex` is the protocol every index must satisfy.

- :class:`FaissFlatIndex` — production wrapper around
  ``faiss.IndexFlatIP`` (inner product on L2-normalized vectors equals
  cosine similarity, given how :class:`MiniLMEmbedder` / :class:`HashEmbedder`
  emit their vectors). Lazy-imports ``faiss-cpu`` from the ``[ml]``
  extra so the module itself imports cheaply.
- :class:`NumpyIndex` — pure-numpy brute-force fallback for tests. Same
  contract, same persistence layout.

Persistence layout (directory-based, both impls)::

    <path>/
        meta.json     # {"dim", "ids", "kind"}
        vectors.npy   # NumpyIndex only
        faiss.index   # FaissFlatIndex only

Round-trip via :meth:`persist` + :meth:`load` reproduces ``size``,
``dim``, ``ids``, and search results to within float32 precision.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class VectorIndex(Protocol):
    """Index over L2-normalized float32 vectors with int payload IDs."""

    @property
    def dim(self) -> int:
        ...

    @property
    def size(self) -> int:
        ...

    def add(self, vectors: np.ndarray, ids: Sequence[int]) -> None:
        ...

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        ...

    def persist(self, path: str | Path) -> None:
        ...


def _validate_add(
    *, dim: int, vectors: np.ndarray, ids: Sequence[int]
) -> None:
    if vectors.ndim != 2:
        raise ValueError(f"vectors must be 2-D, got shape {vectors.shape}")
    if vectors.shape[1] != dim:
        raise ValueError(
            f"vector dim mismatch: index={dim}, got {vectors.shape[1]}"
        )
    if vectors.shape[0] != len(ids):
        raise ValueError(
            f"row count mismatch: {vectors.shape[0]} vectors vs {len(ids)} ids"
        )


def _normalize_query(query: np.ndarray, dim: int) -> np.ndarray:
    """Promote a 1-D ``(dim,)`` query to ``(1, dim)`` and validate shape."""
    if query.ndim == 1:
        query = query.reshape(1, -1)
    if query.ndim != 2 or query.shape[1] != dim:
        raise ValueError(
            f"query shape must be ({dim},) or (1, {dim}); got {query.shape}"
        )
    return query.astype(np.float32, copy=False)


class NumpyIndex:
    """Pure-numpy brute-force vector index.

    O(N) per search; fine up to ~10k vectors. Vectors are stored as
    float32 contiguous arrays so the search ``vectors @ query.T`` is a
    single BLAS call.
    """

    def __init__(self, dim: int) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self._dim = dim
        self._vectors: np.ndarray | None = None
        self._ids: list[int] = []

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def size(self) -> int:
        return len(self._ids)

    def add(self, vectors: np.ndarray, ids: Sequence[int]) -> None:
        _validate_add(dim=self._dim, vectors=vectors, ids=ids)
        block = vectors.astype(np.float32, copy=False)
        if self._vectors is None:
            self._vectors = block.copy()
        else:
            self._vectors = np.vstack([self._vectors, block])
        self._ids.extend(int(i) for i in ids)

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.size == 0 or self._vectors is None:
            return []
        q = _normalize_query(query, self._dim)
        # Inner product == cosine sim when both sides are L2-normalized.
        scores = (self._vectors @ q[0]).astype(np.float32)
        k = min(k, self.size)
        # argpartition is O(N) vs argsort's O(N log N); sort just the top-k.
        top_part = np.argpartition(-scores, k - 1)[:k]
        top_sorted = top_part[np.argsort(-scores[top_part])]
        return [(self._ids[i], float(scores[i])) for i in top_sorted]

    def persist(self, path: str | Path) -> None:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        if self._vectors is not None:
            np.save(p / "vectors.npy", self._vectors)
        (p / "meta.json").write_text(
            json.dumps(
                {"dim": self._dim, "ids": self._ids, "kind": "numpy"},
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> NumpyIndex:
        p = Path(path)
        meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        idx = cls(int(meta["dim"]))
        idx._ids = [int(i) for i in meta["ids"]]
        vec_path = p / "vectors.npy"
        if vec_path.exists():
            idx._vectors = np.load(vec_path).astype(np.float32, copy=False)
        return idx


class FaissFlatIndex:
    """FAISS ``IndexFlatIP`` wrapper.

    Lazy-imports ``faiss`` on first :meth:`add` / :meth:`search` /
    :meth:`persist` so the class can be referenced (e.g. for type
    hints) without the ``[ml]`` extra installed.
    """

    def __init__(self, dim: int) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self._dim = dim
        self._index: object | None = None  # faiss.IndexFlatIP
        self._ids: list[int] = []

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def size(self) -> int:
        return len(self._ids)

    @staticmethod
    def _faiss() -> object:
        try:
            import faiss  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "FaissFlatIndex requires faiss-cpu. "
                "Install with `pip install -e .[ml]`."
            ) from exc
        return faiss

    def _ensure_index(self) -> object:
        if self._index is None:
            faiss = self._faiss()
            self._index = faiss.IndexFlatIP(self._dim)  # type: ignore[attr-defined]
        return self._index

    def add(self, vectors: np.ndarray, ids: Sequence[int]) -> None:
        _validate_add(dim=self._dim, vectors=vectors, ids=ids)
        idx = self._ensure_index()
        idx.add(vectors.astype(np.float32, copy=False))  # type: ignore[attr-defined]
        self._ids.extend(int(i) for i in ids)

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.size == 0:
            return []
        idx = self._ensure_index()
        q = _normalize_query(query, self._dim)
        distances, faiss_ids = idx.search(q, min(k, self.size))  # type: ignore[attr-defined]
        results: list[tuple[int, float]] = []
        for score, faiss_idx in zip(distances[0], faiss_ids[0], strict=False):
            if faiss_idx < 0:  # FAISS returns -1 for under-filled rows.
                continue
            results.append((self._ids[int(faiss_idx)], float(score)))
        return results

    def persist(self, path: str | Path) -> None:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        if self._index is not None:
            faiss = self._faiss()
            faiss.write_index(self._index, str(p / "faiss.index"))  # type: ignore[attr-defined]
        (p / "meta.json").write_text(
            json.dumps(
                {"dim": self._dim, "ids": self._ids, "kind": "faiss-flat-ip"},
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> FaissFlatIndex:
        p = Path(path)
        meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        idx = cls(int(meta["dim"]))
        idx._ids = [int(i) for i in meta["ids"]]
        faiss_path = p / "faiss.index"
        if faiss_path.exists():
            faiss = cls._faiss()
            idx._index = faiss.read_index(str(faiss_path))  # type: ignore[attr-defined]
        return idx


__all__ = ["FaissFlatIndex", "NumpyIndex", "VectorIndex"]
