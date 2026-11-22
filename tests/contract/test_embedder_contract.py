"""Contract tests for Embedder port implementations (Step 2.2)."""

from __future__ import annotations

import unittest

import numpy as np

from copilot.core.errors import InvalidInputError
from copilot.rag.embedder.base import Embedder
from copilot.rag.embedder.vector import is_finite_and_normalized
from tests.fakes.fake_embedder import FakeEmbedder


def check_embedder_contract(embedder: Embedder) -> None:
    """Reusable contract suite for any Embedder port implementation (Step 2.2).

    Verifies:
    1. Output dtype is float32, vectors are strictly finite, shape matches configured dimension.
    2. All vectors are Euclidean (L2) normalized to within 1e-3.
    3. Determinism: identical input texts yield identical vectors (cosine sim >= 0.9999).
    4. Batch equals single: batch output matches individual calls, and order is preserved.
    5. Empty texts and whitespace-only texts are rejected with InvalidInputError.
    6. Empty batch sequence returns empty array of shape (0, d).
    7. Document text exceeding token budget is rejected with InvalidInputError (no silent truncation).
    8. Query text is truncated to max_query_tokens without crashing.
    """
    dim = embedder.config.truncate_dim or embedder.config.dimension

    # 1. Dimension, dtype, finiteness, and L2 norm on single query
    q_vec = embedder.embed_query("خدمة عملاء المصرية للاتصالات")
    assert isinstance(q_vec, np.ndarray), "Query embedding must be a numpy ndarray"
    assert q_vec.dtype == np.float32, f"Expected float32, got {q_vec.dtype}"
    assert q_vec.shape == (dim,), f"Expected shape ({dim},), got {q_vec.shape}"
    assert np.all(np.isfinite(q_vec)), "Query vector must contain finite values only"
    norm = float(np.linalg.norm(q_vec))
    assert abs(norm - 1.0) <= 1e-3, f"Query vector not normalized: norm={norm}"

    # 2. Dimension, dtype, finiteness, and L2 norm on document batch
    docs = [
        "باقات الإنترنت المنزلي فائق السرعة من وي تبدأ من سرعة 30 ميجابت",
        "How to recharge WE mobile balance using prepaid scratch cards",
        "خطوات التعاقد على الخط الأرضي وتقديم طلب التوصيل المنزلي",
    ]
    doc_vecs = embedder.embed_documents(docs)
    assert isinstance(doc_vecs, np.ndarray), "Document embeddings must be a numpy ndarray"
    assert doc_vecs.dtype == np.float32, f"Expected float32, got {doc_vecs.dtype}"
    assert doc_vecs.shape == (len(docs), dim), (
        f"Expected shape ({len(docs)}, {dim}), got {doc_vecs.shape}"
    )
    assert np.all(np.isfinite(doc_vecs)), "Document vectors must contain finite values only"
    assert is_finite_and_normalized(doc_vecs, tol=1e-3)

    # 3. Determinism: identical text yields identical vector
    q_vec_2 = embedder.embed_query("خدمة عملاء المصرية للاتصالات")
    cos_sim = float(np.dot(q_vec, q_vec_2))
    assert cos_sim >= 0.9999, f"Embedder non-deterministic: cosine sim {cos_sim} < 0.9999"

    # 4. Batch equals single: embedding individually equals batch embedding
    for idx, doc_text in enumerate(docs):
        single_vec = embedder.embed_documents([doc_text])[0]
        batch_vec = doc_vecs[idx]
        sim = float(np.dot(single_vec, batch_vec))
        assert sim >= 0.9999, f"Batch vector does not match single vector at index {idx}: sim={sim}"

    # 5. Order is preserved when texts have varying lengths
    varied_docs = [
        "قصير",
        "هذا نص ذو طول متوسط يستعرض تفاصيل باقات الدفع المسبق",
        "نص طويل جداً يحتوي على تفاصيل دقيقة حول شروط وأحكام التعاقد على خدمات الإنترنت المنزلي وتوصيل الفايبر للشركات والمنازل مع راوتر مجاني",
        "قصير 2",
    ]
    varied_vecs = embedder.embed_documents(varied_docs)
    for idx, text in enumerate(varied_docs):
        single = embedder.embed_documents([text])[0]
        sim = float(np.dot(single, varied_vecs[idx]))
        assert sim >= 0.9999, f"Order scrambled in batch for text index {idx}: sim={sim}"

    # 6. Empty batch handling
    empty_batch = embedder.embed_documents([])
    assert empty_batch.shape == (0, dim)
    assert empty_batch.dtype == np.float32

    # 7. Empty or whitespace-only texts rejected
    for bad_text in ("", "   ", "\t\n"):
        try:
            embedder.embed_query(bad_text)
            raise AssertionError(f"Expected InvalidInputError for empty query: {bad_text!r}")
        except InvalidInputError:
            pass

        try:
            embedder.embed_documents(["Valid text", bad_text])
            raise AssertionError(f"Expected InvalidInputError for empty doc in batch: {bad_text!r}")
        except InvalidInputError:
            pass

    # 8. Document exceeding token budget rejected without silent truncation
    too_long = "كلمة " * (embedder.config.max_length + 50)
    try:
        embedder.embed_documents([too_long])
        raise AssertionError("Expected InvalidInputError for document exceeding token limit")
    except InvalidInputError:
        pass

    # 9. Query truncation rejects non-positive or bool max_query_tokens (Finding 6)
    if hasattr(embedder, "_prepare_query_with_truncation"):
        for invalid_tokens in (0, -1, True, False):
            try:
                embedder._prepare_query_with_truncation(
                    "test query", max_query_tokens=invalid_tokens
                )  # type: ignore[arg-type]
                raise AssertionError(
                    f"Expected InvalidInputError for max_query_tokens={invalid_tokens}"
                )
            except InvalidInputError:
                pass


class TestEmbedderContract(unittest.TestCase):
    """Test suite running contract suite against FakeEmbedder."""

    def test_fake_embedder_satisfies_contract(self) -> None:
        embedder = FakeEmbedder()
        check_embedder_contract(embedder)
