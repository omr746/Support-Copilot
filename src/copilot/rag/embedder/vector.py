"""Vector operations and validation for embedding models (Step 2.2)."""

from __future__ import annotations

import math
from typing import cast

import numpy as np


def normalize_l2(vectors: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    """Normalize vectors to unit Euclidean (L2) norm so dot product equals cosine similarity.

    Guarantees:
    - Accepts 1D vector of shape (d,) or 2D batch of shape (n, d) with d > 0.
    - Validates that eps is a positive finite float.
    - Strictly rejects inputs containing NaN or Infinity before normalization.
    - Rejects zero vectors (norm < eps) because a zero vector has no direction on the unit sphere.
    - Handles empty batches of shape (0, d) gracefully by returning empty float32 array of shape (0, d).
    - Verifies output is strictly finite after normalization and float32 conversion (preventing overflow).
    """
    if not isinstance(vectors, np.ndarray):
        raise TypeError(f"Expected numpy ndarray, got {type(vectors)}")

    if (
        not isinstance(eps, (int, float))
        or isinstance(eps, bool)
        or not math.isfinite(eps)
        or eps <= 0
    ):
        raise ValueError(f"eps must be a positive finite float, got {eps!r}")

    if vectors.ndim not in (1, 2):
        raise ValueError(
            f"Expected 1D vector (d,) or 2D batch (n, d), got shape {vectors.shape} (ndim={vectors.ndim})"
        )

    # 1D vector: shape (d,)
    if vectors.ndim == 1:
        if vectors.size == 0:
            raise ValueError("Cannot normalize empty 1D vector of shape (0,)")
        if not np.all(np.isfinite(vectors)):
            raise ValueError("Cannot normalize vector containing NaN or Infinity")

        norm = float(np.linalg.norm(vectors))
        if norm < eps:
            raise ValueError(
                f"Cannot L2-normalize zero vector: norm ({norm:.2e}) is less than eps ({eps:.2e})"
            )

        res = (vectors / norm).astype(np.float32)
        if not np.all(np.isfinite(res)):
            raise ValueError("L2 normalization produced non-finite values (numeric overflow)")
        return cast(np.ndarray, res)

    # 2D batch: shape (n, d)
    n, d = vectors.shape
    if d == 0:
        raise ValueError(
            f"Cannot normalize batch with embedding dimension 0, shape {vectors.shape}"
        )

    # Empty batch of shape (0, d) is a valid empty document batch
    if n == 0:
        return np.zeros((0, d), dtype=np.float32)

    if not np.all(np.isfinite(vectors)):
        raise ValueError("Cannot normalize batch containing NaN or Infinity")

    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    min_norm = float(np.min(norms))
    if min_norm < eps:
        raise ValueError(
            f"Cannot L2-normalize batch containing zero vector: minimum norm ({min_norm:.2e}) < eps ({eps:.2e})"
        )

    res = (vectors / norms).astype(np.float32)
    if not np.all(np.isfinite(res)):
        raise ValueError("L2 normalization produced non-finite values (numeric overflow)")
    return cast(np.ndarray, res)


def is_finite_and_normalized(vectors: np.ndarray, tol: float = 1e-3) -> bool:
    """Verify that vector(s) are strictly finite and normalized to within tolerance.

    Guarantees:
    - Rejects empty 1D vectors or empty dimensions.
    - Validates tol is a non-negative finite float.
    - Requires 1D shape (d,) with d > 0 or 2D shape (n, d) with n > 0, d > 0.
    - Empty 2D batch (0, d) returns True only if d > 0.
    """
    if not isinstance(vectors, np.ndarray):
        return False

    if (
        not isinstance(tol, (int, float))
        or isinstance(tol, bool)
        or not math.isfinite(tol)
        or tol < 0
    ):
        raise ValueError(f"tol must be a non-negative finite float, got {tol!r}")

    if vectors.ndim not in (1, 2):
        return False

    if vectors.ndim == 1:
        if vectors.size == 0 or not np.all(np.isfinite(vectors)):
            return False
        norm = float(np.linalg.norm(vectors))
        return abs(norm - 1.0) <= tol

    # 2D batch: (n, d)
    n, d = vectors.shape
    if d == 0:
        return False
    if n == 0:
        return True  # Valid empty batch
    if not np.all(np.isfinite(vectors)):
        return False

    norms = np.linalg.norm(vectors, axis=-1)
    return bool(np.all(np.abs(norms - 1.0) <= tol))
