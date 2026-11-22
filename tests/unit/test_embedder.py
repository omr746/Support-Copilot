"""Unit tests for Embedder components, vector rules, fingerprinting, and Query Cache (Step 2.2)."""

from __future__ import annotations

import concurrent.futures
import dataclasses
import time
import unittest

import numpy as np

from copilot.core.errors import (
    InvalidInputError,
    ProviderTimeout,
)
from copilot.rag.chunker import CHUNKER_SETTINGS
from copilot.rag.embedder import (
    CachedQueryEmbedder,
    InMemoryQueryCache,
    build_embedding_input,
    compute_effective_document_budget,
    compute_effective_query_budget,
    compute_embedding_input_hash,
    compute_query_cache_key,
    detect_pii,
    is_finite_and_normalized,
    normalize_l2,
    validate_chunker_embedder_budget,
    verify_embedder_fingerprint_readiness,
)
from copilot.rag.fingerprint import (
    ChunkerConfig,
    EmbedderConfig,
    compute_embedder_fingerprint,
)
from copilot.rag.models import Chunk, ChunkInput
from copilot.rag.tokens import WhitespaceTokenCounter
from tests.fakes.fake_embedder import FakeEmbedder


class TestFakeEmbedderSemanticBehavior(unittest.TestCase):
    """Verifies that FakeEmbedder produces meaningful similarities (paraphrases score higher than unrelated)."""

    def setUp(self) -> None:
        self.embedder = FakeEmbedder()

    def test_arabic_paraphrase_scores_higher_than_unrelated(self) -> None:
        query = "طريقة سداد فاتورة التليفون الأرضي"
        paraphrase = "كيفية دفع واستعلام فواتير الخط الارضي"
        unrelated = "طريقة عمل كعكة الشوكولاتة في المنزل بالفرن"

        q_vec = self.embedder.embed_query(query)
        p_vec = self.embedder.embed_documents([paraphrase])[0]
        u_vec = self.embedder.embed_documents([unrelated])[0]

        sim_para = float(np.dot(q_vec, p_vec))
        sim_unrel = float(np.dot(q_vec, u_vec))

        self.assertGreater(
            sim_para,
            sim_unrel + 0.15,
            f"Expected paraphrase ({sim_para:.3f}) to score significantly higher than unrelated ({sim_unrel:.3f})",
        )

    def test_english_paraphrase_scores_higher_than_unrelated(self) -> None:
        query = "how to recharge mobile balance"
        paraphrase = "topup phone account balance with recharge card"
        unrelated = "playing tennis in the morning at the sports club"

        q_vec = self.embedder.embed_query(query)
        p_vec = self.embedder.embed_documents([paraphrase])[0]
        u_vec = self.embedder.embed_documents([unrelated])[0]

        sim_para = float(np.dot(q_vec, p_vec))
        sim_unrel = float(np.dot(q_vec, u_vec))

        self.assertGreater(
            sim_para,
            sim_unrel + 0.15,
            f"Expected paraphrase ({sim_para:.3f}) to score higher than unrelated ({sim_unrel:.3f})",
        )

    def test_cross_lingual_concept_scores_higher_than_unrelated(self) -> None:
        """Cross-lingual query (English) against Arabic document scores higher than unrelated English."""
        query_en = "internet package pricing and subscription"
        doc_ar = "أسعار باقات انترنت وي وطريقة الاشتراك"
        unrelated_ar = "حالة الطقس ودرجات الحرارة المتوقعة غداً في القاهرة"

        q_vec = self.embedder.embed_query(query_en)
        doc_vec = self.embedder.embed_documents([doc_ar])[0]
        unrel_vec = self.embedder.embed_documents([unrelated_ar])[0]

        sim_cross = float(np.dot(q_vec, doc_vec))
        sim_unrel = float(np.dot(q_vec, unrel_vec))

        self.assertGreater(
            sim_cross,
            sim_unrel,
            f"Expected cross-lingual doc ({sim_cross:.3f}) to score higher than unrelated ({sim_unrel:.3f})",
        )

    def test_counters_and_metrics_increment(self) -> None:
        initial_calls = self.embedder.call_count
        initial_docs = self.embedder.document_text_count
        initial_queries = self.embedder.query_text_count

        self.embedder.embed_query("استعلام عن الرصيد")
        self.assertEqual(self.embedder.call_count, initial_calls + 1)
        self.assertEqual(self.embedder.query_text_count, initial_queries + 1)

        self.embedder.embed_documents(["doc 1", "doc 2", "doc 3"])
        self.assertEqual(self.embedder.call_count, initial_calls + 2)
        self.assertEqual(self.embedder.document_text_count, initial_docs + 3)

    def test_fault_injection_and_delay(self) -> None:
        failing_embedder = FakeEmbedder(
            fail_with=ProviderTimeout("Simulated gateway timeout", "test-provider")
        )
        with self.assertRaises(ProviderTimeout):
            failing_embedder.embed_query("test query")

        slow_embedder = FakeEmbedder(delay_seconds=0.05)
        start = time.monotonic()
        slow_embedder.embed_query("fast query")
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.04)


class TestFingerprintAndVectorSpaceRules(unittest.TestCase):
    """Verifies vector space rules, fingerprint sensitivity, and readiness check."""

    def test_fingerprint_changes_on_any_config_field(self) -> None:
        base = EmbedderConfig(
            model_id="intfloat/multilingual-e5-small",
            revision="e4ce9877abf3ed58b09337442111c1d054d45d31",
            dimension=384,
            max_length=512,
            pooling="mean",
            normalize_embeddings=True,
            query_prefix="query: ",
            passage_prefix="passage: ",
            quantization="none",
            runtime="onnx",
            embed_text_source="original",
        )
        base_fp = compute_embedder_fingerprint(base)

        # Mutate each field and assert fingerprint changes
        variations = [
            dataclasses.replace(base, model_id="intfloat/multilingual-e5-base"),
            dataclasses.replace(base, revision="1111111111111111111111111111111111111111"),
            dataclasses.replace(base, dimension=512),
            dataclasses.replace(base, max_length=256),
            dataclasses.replace(base, pooling="cls"),
            dataclasses.replace(base, normalize_embeddings=False),
            dataclasses.replace(base, query_prefix=""),
            dataclasses.replace(base, passage_prefix="doc: "),
            dataclasses.replace(base, quantization="int8"),
            dataclasses.replace(base, runtime="torch"),
            dataclasses.replace(base, embed_text_source="normalized"),
            dataclasses.replace(base, truncate_dim=256),
            dataclasses.replace(base, special_tokens=4),
            dataclasses.replace(base, tokenizer_id="custom-tokenizer@v2"),
        ]

        for var in variations:
            new_fp = compute_embedder_fingerprint(var)
            self.assertNotEqual(base_fp, new_fp, f"Fingerprint did not change for {var}")

    def test_readiness_fails_on_fingerprint_mismatch(self) -> None:
        embedder = FakeEmbedder()
        active_fp = embedder.fingerprint

        # Matching fingerprint -> ready
        self.assertTrue(verify_embedder_fingerprint_readiness(active_fp, embedder))

        # Mismatched fingerprint -> not ready
        self.assertFalse(verify_embedder_fingerprint_readiness("wrong_fingerprint_hash", embedder))
        self.assertFalse(verify_embedder_fingerprint_readiness("", embedder))

    def test_no_fallback_embedder_exists(self) -> None:
        """Rule 6: Vectors from different models cannot be compared, so no FallbackEmbedder must exist."""
        import copilot.rag

        # Verify no FallbackEmbedder in copilot.rag module or its submodules
        self.assertFalse(hasattr(copilot.rag, "FallbackEmbedder"))
        with self.assertRaises(AttributeError):
            _ = copilot.rag.FallbackEmbedder


class TestInputsAndHashes(unittest.TestCase):
    """Verifies build_embedding_input, embedding_input_hash vs chunk_hash, and budget validation."""

    def test_build_embedding_input_with_section_path(self) -> None:
        chunk = Chunk(
            id="p1_0001",
            chunk_hash="h1",
            index=0,
            source_type="website",
            owner_id=None,
            source_id="https://te.eg/fiber",
            page_version_id="pv1",
            url_or_filename="https://te.eg/fiber",
            title="باقات الفايبر",
            fetched_at="2026-11-17T00:00:00Z",
            section_path="باقات الفايبر > الشروط",
            language="ar",
            text="الأسعار غير شاملة ضريبة القيمة المضافة ١٤٪.",
            normalized_text="الاسعار غير شامله ضريبه القيمه المضافه 14%.",
            token_count=10,
            normalizer_version="v1.0",
            chunking_digest="d1",
        )

        # Original source uses raw text
        inp_orig = build_embedding_input(chunk, embed_text_source="original")
        self.assertEqual(
            inp_orig, "باقات الفايبر > الشروط\n\nالأسعار غير شاملة ضريبة القيمة المضافة ١٤٪."
        )

        # Normalized source uses normalized text
        inp_norm = build_embedding_input(chunk, embed_text_source="normalized")
        self.assertEqual(
            inp_norm, "باقات الفايبر > الشروط\n\nالاسعار غير شامله ضريبه القيمه المضافه 14%."
        )

    def test_two_hashes_two_jobs(self) -> None:
        """Two chunks with same normalized text but different original diacritics/digits:

        - chunk_hash (for identity/dedup) is identical.
        - embedding_input_hash with embed_text_source='original' is different.
        - embedding_input_hash with embed_text_source='normalized' is identical.
        """
        text_a = "السعر: ١٢٠ جنيهاً مصرياً"
        text_b = "السعر: 120 جنيها مصريا"

        chunk_a = ChunkInput(
            source_type="website",
            owner_id=None,
            source_id="s1",
            page_version_id="pv1",
            url_or_filename="u1",
            title="T",
            language="ar",
            fetched_at="2026-11-17",
            text=text_a,
        )
        chunk_b = ChunkInput(
            source_type="website",
            owner_id=None,
            source_id="s1",
            page_version_id="pv1",
            url_or_filename="u1",
            title="T",
            language="ar",
            fetched_at="2026-11-17",
            text=text_b,
        )

        inp_a_orig = build_embedding_input(chunk_a, embed_text_source="original")
        inp_b_orig = build_embedding_input(chunk_b, embed_text_source="original")
        hash_a_orig = compute_embedding_input_hash(inp_a_orig)
        hash_b_orig = compute_embedding_input_hash(inp_b_orig)
        self.assertNotEqual(hash_a_orig, hash_b_orig)

        inp_a_norm = build_embedding_input(chunk_a, embed_text_source="normalized")
        inp_b_norm = build_embedding_input(chunk_b, embed_text_source="normalized")
        hash_a_norm = compute_embedding_input_hash(inp_a_norm)
        hash_b_norm = compute_embedding_input_hash(inp_b_norm)
        self.assertEqual(hash_a_norm, hash_b_norm)

    def test_validate_chunker_embedder_budget(self) -> None:
        token_counter = WhitespaceTokenCounter()
        chunker_cfg = ChunkerConfig.from_dict(CHUNKER_SETTINGS)

        # Valid budget
        embedder_cfg = EmbedderConfig(
            model_id="m1",
            revision="rev1",
            dimension=384,
            max_length=1024,
            pooling="mean",
            normalize_embeddings=True,
            passage_prefix="passage: ",
        )
        self.assertTrue(validate_chunker_embedder_budget(chunker_cfg, embedder_cfg, token_counter))

        # Too small embedder max_length -> raises ValueError
        small_embedder = EmbedderConfig(
            model_id="m1",
            revision="rev1",
            dimension=384,
            max_length=256,
            pooling="mean",
            normalize_embeddings=True,
            passage_prefix="passage: ",
        )
        with self.assertRaises(ValueError) as ctx:
            validate_chunker_embedder_budget(chunker_cfg, small_embedder, token_counter)
        self.assertIn("exceeds embedder available budget", str(ctx.exception))


class TestQueryEmbeddingCache(unittest.TestCase):
    """Test suite for CachedQueryEmbedder, QueryCacheBackend, bounded memory, single-flight, and HMAC privacy."""

    def test_cache_hit_avoids_embedder_call(self) -> None:
        base_embedder = FakeEmbedder()
        cached = CachedQueryEmbedder(base_embedder, ttl_seconds=300)

        query = "كيف يمكنني تجديد باقة الإنترنت المنزلي؟"

        # First call: cache miss, increments embedder call count
        v1 = cached.embed_query(query)
        self.assertEqual(cached.cache_misses, 1)
        self.assertEqual(cached.cache_hits, 0)
        self.assertEqual(base_embedder.call_count, 1)

        # Second identical call: cache hit, does NOT call embedder
        v2 = cached.embed_query(query)
        self.assertEqual(cached.cache_misses, 1)
        self.assertEqual(cached.cache_hits, 1)
        self.assertEqual(base_embedder.call_count, 1)
        np.testing.assert_array_equal(v1, v2)

    def test_different_query_or_fingerprint_is_miss(self) -> None:
        base_embedder = FakeEmbedder()
        cached = CachedQueryEmbedder(base_embedder)

        _ = cached.embed_query("استعلام عن الفاتورة")
        self.assertEqual(cached.cache_misses, 1)

        _ = cached.embed_query("طريقة شحن الرصيد")
        self.assertEqual(cached.cache_misses, 2)
        self.assertEqual(cached.cache_hits, 0)

    def test_ttl_expiration(self) -> None:
        current_time = 1000.0

        def fake_clock() -> float:
            return current_time

        cache_backend = InMemoryQueryCache(clock=fake_clock)
        base_embedder = FakeEmbedder()
        cached = CachedQueryEmbedder(base_embedder, cache=cache_backend, ttl_seconds=60)

        query = "خدمة العملاء والدعم الفني"
        _ = cached.embed_query(query)
        self.assertEqual(cached.cache_misses, 1)

        # Still within TTL: hit
        current_time += 30.0
        _ = cached.embed_query(query)
        self.assertEqual(cached.cache_hits, 1)

        # Expired: miss and re-call
        current_time += 40.0  # total 70s > 60s TTL
        _ = cached.embed_query(query)
        self.assertEqual(cached.cache_misses, 2)

    def test_pii_query_skips_cache(self) -> None:
        """Queries containing emails, cards, phones, or has_pii=True skip the cache."""
        base_embedder = FakeEmbedder()
        cached = CachedQueryEmbedder(base_embedder)

        pii_query = "رقم هاتفي هو 01012345678 وأريد معرفة الرصيد"
        self.assertTrue(detect_pii(pii_query))

        _ = cached.embed_query(pii_query)
        self.assertEqual(cached.pii_skipped, 1)
        self.assertEqual(cached.cache_hits, 0)
        self.assertEqual(cached.cache_misses, 0)

        # Second call still skips cache
        _ = cached.embed_query(pii_query)
        self.assertEqual(cached.pii_skipped, 2)
        self.assertEqual(cached.cache_hits, 0)

        # Explicit has_pii flag
        _ = cached.embed_query("استعلام عادي", has_pii=True)
        self.assertEqual(cached.pii_skipped, 3)
        self.assertEqual(cached.cache_hits, 0)

    def test_max_size_and_ttl_validation(self) -> None:
        """Finding P1: max_size must be a strictly positive integer, rejecting 0, negatives, bools, and floats."""
        for invalid_size in (0, -1, -50, True, False, "100", 10.5):
            with self.assertRaises(InvalidInputError) as ctx:
                InMemoryQueryCache(max_size=invalid_size)  # type: ignore[arg-type]
            self.assertIn("max_size must be a positive integer", str(ctx.exception))

        cache = InMemoryQueryCache(max_size=5)
        for invalid_ttl in (0, -10, True, False, "60"):
            with self.assertRaises(InvalidInputError) as ctx:
                cache.set("k1", np.ones(384, dtype=np.float32), ttl_seconds=invalid_ttl)  # type: ignore[arg-type]
            self.assertIn("ttl_seconds must be a positive number", str(ctx.exception))

    def test_lru_eviction_when_capacity_reached(self) -> None:
        """Finding P1: When cache is at capacity, least recently used entries are evicted."""
        cache = InMemoryQueryCache(max_size=3)
        v1 = np.full(384, 1.0, dtype=np.float32)
        v2 = np.full(384, 2.0, dtype=np.float32)
        v3 = np.full(384, 3.0, dtype=np.float32)
        v4 = np.full(384, 4.0, dtype=np.float32)

        cache.set("k1", v1, ttl_seconds=3600)
        cache.set("k2", v2, ttl_seconds=3600)
        cache.set("k3", v3, ttl_seconds=3600)
        self.assertEqual(len(cache), 3)

        # Access k1 to make it most recently used (k2 becomes oldest / LRU)
        _ = cache.get("k1")

        # Insert k4: capacity is reached, so k2 must be evicted
        cache.set("k4", v4, ttl_seconds=3600)
        self.assertEqual(len(cache), 3)
        self.assertEqual(cache.eviction_count, 1)

        self.assertIsNone(cache.get("k2"))
        self.assertIsNotNone(cache.get("k1"))
        self.assertIsNotNone(cache.get("k3"))
        self.assertIsNotNone(cache.get("k4"))

    def test_expired_entries_evicted_before_active_entries(self) -> None:
        """Finding P1: On insertion into full cache, expired entries are cleaned up first before active LRU."""
        current_time = 100.0

        def fake_clock() -> float:
            return current_time

        cache = InMemoryQueryCache(max_size=3, clock=fake_clock)
        vec = np.ones(384, dtype=np.float32)

        # k1 has short TTL (10s), k2 and k3 have long TTL (100s)
        cache.set("k1", vec, ttl_seconds=10)
        cache.set("k2", vec, ttl_seconds=100)
        cache.set("k3", vec, ttl_seconds=100)
        self.assertEqual(len(cache), 3)

        # Advance time so k1 expires
        current_time += 15.0

        # Insert k4: k1 is expired, so it is purged, and active entries k2 and k3 remain!
        cache.set("k4", vec, ttl_seconds=100)
        self.assertEqual(len(cache), 3)
        self.assertEqual(
            cache.eviction_count, 0
        )  # Eviction count tracks LRU force-evictions, not expired cleanups

        self.assertIsNone(cache.get("k1"))
        self.assertIsNotNone(cache.get("k2"))
        self.assertIsNotNone(cache.get("k3"))
        self.assertIsNotNone(cache.get("k4"))

    def test_explicit_cleanup_expired(self) -> None:
        """Finding P1: cleanup_expired() actively purges expired entries and returns their count."""
        current_time = 200.0

        def fake_clock() -> float:
            return current_time

        cache = InMemoryQueryCache(clock=fake_clock)
        vec = np.ones(384, dtype=np.float32)

        cache.set("exp1", vec, ttl_seconds=10)
        cache.set("exp2", vec, ttl_seconds=20)
        cache.set("live", vec, ttl_seconds=100)
        self.assertEqual(len(cache), 3)

        # Advance time past exp1 only
        current_time += 15.0
        purged = cache.cleanup_expired()
        self.assertEqual(purged, 1)
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get("exp1"))

        # Advance time past exp2
        current_time += 10.0
        purged = cache.cleanup_expired()
        self.assertEqual(purged, 1)
        self.assertEqual(len(cache), 1)
        self.assertIsNotNone(cache.get("live"))

    def test_single_flight_coalesces_concurrent_duplicate_queries(self) -> None:
        """Finding P1: Concurrent identical queries coalesce via SingleFlight, executing model inference once."""
        base_embedder = FakeEmbedder(delay_seconds=0.05)
        cached = CachedQueryEmbedder(base_embedder)

        query = "ما هي شروط الاشتراك في خدمة الإنترنت الفايبر؟"
        concurrency = 8
        results: list[np.ndarray] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(cached.embed_query, query) for _ in range(concurrency)]
            for fut in concurrent.futures.as_completed(futures):
                results.append(fut.result())

        # All 8 concurrent callers received identical vectors
        self.assertEqual(len(results), concurrency)
        for r in results[1:]:
            np.testing.assert_array_equal(results[0], r)

        # Single-flight ensured model inference was invoked exactly ONCE!
        self.assertEqual(base_embedder.call_count, 1)
        self.assertEqual(cached.cache_misses, 1)
        self.assertEqual(cached.coalesced_requests, concurrency - 1)
        self.assertEqual(cached.cache_hits, 0)

        # A subsequent query after completion hits the populated cache
        v_next = cached.embed_query(query)
        self.assertEqual(cached.cache_hits, 1)
        self.assertEqual(base_embedder.call_count, 1)
        np.testing.assert_array_equal(results[0], v_next)

    def test_single_flight_propagates_exception(self) -> None:
        """Finding P1: If leader fails, the exception is cleanly propagated to all waiters without hanging."""
        base_embedder = FakeEmbedder(
            fail_with=RuntimeError("Simulated embedder fault"),
            delay_seconds=0.05,
        )
        cached = CachedQueryEmbedder(base_embedder)

        query = "استعلام يسبب خطأ"
        concurrency = 4
        exceptions: list[BaseException] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(cached.embed_query, query) for _ in range(concurrency)]
            for fut in futures:
                try:
                    fut.result()
                except BaseException as exc:
                    exceptions.append(exc)

        self.assertEqual(len(exceptions), concurrency)
        for exc in exceptions:
            self.assertIn("Simulated embedder fault", str(exc))

    def test_hash_privacy_hmac_vs_sha256(self) -> None:
        """Finding P1/P2: Privacy hardening uses HMAC-SHA256 to prevent dictionary/rainbow table recovery."""
        query = "استعلام سري عن حساب العميل"
        fp = "fp_abc123"

        # Plain SHA-256 (default)
        key_plain = compute_query_cache_key(query, fp)
        self.assertEqual(len(key_plain), 64)

        # HMAC with string secret
        secret = "corp_secure_cache_salt_2026"
        key_hmac_str = compute_query_cache_key(query, fp, secret=secret)
        self.assertEqual(len(key_hmac_str), 64)
        self.assertNotEqual(key_plain, key_hmac_str)

        # HMAC with bytes secret produces exact same result as string
        key_hmac_bytes = compute_query_cache_key(query, fp, secret=secret.encode("utf-8"))
        self.assertEqual(key_hmac_str, key_hmac_bytes)

        # Different secret -> different key
        key_diff_secret = compute_query_cache_key(query, fp, secret="another_secret")
        self.assertNotEqual(key_hmac_str, key_diff_secret)

        # Invalid secret validation
        for bad_secret in ("", b"", 12345, True, [1, 2]):
            with self.assertRaises(InvalidInputError):
                compute_query_cache_key(query, fp, secret=bad_secret)  # type: ignore[arg-type]

        # CachedQueryEmbedder configured with HMAC secret
        base_embedder = FakeEmbedder()
        cached = CachedQueryEmbedder(base_embedder, cache_key_secret=secret)
        _ = cached.embed_query(query)
        self.assertEqual(cached.cache_misses, 1)
        _ = cached.embed_query(query)
        self.assertEqual(cached.cache_hits, 1)


class TestBaseEmbedderRules(unittest.TestCase):
    """Verifies Findings 6 through 14: truncation validation, limits, token metrics, thread safety, and sorting."""

    def test_query_truncation_validation_rejects_non_positive_and_bool(self) -> None:
        """Finding 6: max_query_tokens must be positive int, strictly rejecting 0, negatives, and booleans."""
        embedder = FakeEmbedder()
        for invalid_val in (0, -1, -50, True, False, "256", 2.5):
            with self.assertRaises(InvalidInputError) as ctx:
                embedder._prepare_query_with_truncation("test query", max_query_tokens=invalid_val)  # type: ignore[arg-type]
            self.assertIn("max_query_tokens must be a positive integer", str(ctx.exception))

        # Valid positive int works
        text, truncated = embedder._prepare_query_with_truncation("test query", max_query_tokens=10)
        self.assertFalse(truncated)
        self.assertEqual(text, "test query")

    def test_query_truncation_bounded_by_model_length(self) -> None:
        """Finding 7: effective query limit must be bounded by model max_length minus prefix and special tokens."""
        cfg = EmbedderConfig(
            model_id="tiny-model",
            revision="rev1",
            dimension=64,
            max_length=20,
            pooling="mean",
            normalize_embeddings=True,
            query_prefix="query: ",
            special_tokens=2,
        )
        embedder = FakeEmbedder(config=cfg)
        # Model max_length=20, query_prefix="query: " (1 word token), special=2 -> allowed query tokens = 17
        long_query = "word " * 50
        prepared, truncated = embedder._prepare_query_with_truncation(
            long_query, max_query_tokens=256
        )
        self.assertTrue(truncated)
        tokens = embedder.tokenizer.count_tokens(prepared)
        self.assertLessEqual(tokens, 17)

    def test_special_tokens_configurable(self) -> None:
        """Finding 8: special_tokens is configurable on EmbedderConfig and affects budget calculation."""
        cfg2 = EmbedderConfig(
            model_id="m1",
            revision="rev1",
            dimension=64,
            max_length=100,
            pooling="mean",
            normalize_embeddings=True,
            special_tokens=2,
        )
        cfg4 = EmbedderConfig(
            model_id="m1",
            revision="rev1",
            dimension=64,
            max_length=100,
            pooling="mean",
            normalize_embeddings=True,
            special_tokens=4,
        )
        e2 = FakeEmbedder(config=cfg2)
        e4 = FakeEmbedder(config=cfg4)
        self.assertEqual(e2._allowed_document_tokens, 98)
        self.assertEqual(e4._allowed_document_tokens, 96)

    def test_total_tokens_count_not_incremented_on_validation_failure(self) -> None:
        """Finding 9: rejected document does NOT increment total_tokens_count or accepted tokens."""
        embedder = FakeEmbedder()
        initial_tokens = embedder.total_tokens_count
        initial_accepted = embedder.input_tokens_accepted

        too_long = "كلمة " * (embedder.config.max_length + 100)
        with self.assertRaises(InvalidInputError):
            embedder.embed_documents([too_long])

        self.assertEqual(embedder.total_tokens_count, initial_tokens)
        self.assertEqual(embedder.input_tokens_accepted, initial_accepted)

    def test_tokenizer_id_in_fingerprint_and_validation(self) -> None:
        """Finding 10: Tokenizer identity is bound to config and affects fingerprint."""
        cfg_a = EmbedderConfig(
            model_id="m1",
            revision="rev1",
            dimension=64,
            max_length=100,
            pooling="mean",
            normalize_embeddings=True,
            tokenizer_id="tok-a@v1",
        )
        cfg_b = EmbedderConfig(
            model_id="m1",
            revision="rev1",
            dimension=64,
            max_length=100,
            pooling="mean",
            normalize_embeddings=True,
            tokenizer_id="tok-b@v1",
        )
        self.assertNotEqual(
            compute_embedder_fingerprint(cfg_a), compute_embedder_fingerprint(cfg_b)
        )

        # Mismatch between config.tokenizer_id and injected tokenizer raises ValueError
        tok = WhitespaceTokenCounter(tokenizer_id="tok-c@v1")
        with self.assertRaises(ValueError) as ctx:
            FakeEmbedder(config=cfg_a, tokenizer=tok)
        self.assertIn("does not match tokenizer", str(ctx.exception))

    def test_prefix_tokens_precomputed(self) -> None:
        """Finding 11: prefix tokens are precomputed once on embedder initialization."""
        cfg = EmbedderConfig(
            model_id="m1",
            revision="rev1",
            dimension=64,
            max_length=100,
            pooling="mean",
            normalize_embeddings=True,
            passage_prefix="passage: ",
            query_prefix="query: ",
        )
        embedder = FakeEmbedder(config=cfg)
        self.assertEqual(embedder._passage_prefix_tokens, 1)
        self.assertEqual(embedder._query_prefix_tokens, 1)

    def test_base_embedder_batching_and_length_sorting_preserves_order(self) -> None:
        """Findings 13 & 14: BaseEmbedder splits batches by max_batch_size, sorts by length, and restores original ordering."""
        cfg = EmbedderConfig(
            model_id="m1",
            revision="rev1",
            dimension=64,
            max_length=512,
            pooling="mean",
            normalize_embeddings=True,
        )
        embedder = FakeEmbedder(config=cfg)
        embedder._max_batch_size = 2

        texts = [
            "short text",
            "a significantly longer sentence with multiple words for length comparison",
            "mid text here",
            "tiny",
            "another very long paragraph describing technical specifications for telecommunications",
        ]
        # Embed via batching (split into 3 batches: 2, 2, 1)
        vecs_batch = embedder.embed_documents(texts)
        self.assertEqual(vecs_batch.shape, (5, 64))

        # Compare each with individual single call
        for idx, text in enumerate(texts):
            single_vec = embedder.embed_documents([text])[0]
            sim = float(np.dot(vecs_batch[idx], single_vec))
            self.assertGreaterEqual(sim, 0.9999)

    def test_thread_safety_under_concurrent_access(self) -> None:
        """Finding 12: metrics counters and batch execution are thread-safe under concurrent access."""
        import concurrent.futures

        embedder = FakeEmbedder()
        queries = [f"استعلام رقم {i}" for i in range(50)]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(embedder.embed_query, q) for q in queries]
            concurrent.futures.wait(futures)

        self.assertEqual(embedder.query_text_count, 50)
        self.assertEqual(embedder.call_count, 50)


class TestVectorOperationsAndInvariants(unittest.TestCase):
    """Verifies vector operations hardening: NaN/Inf rejection, zero vector rejection, shape validation, eps/tol."""

    def test_normalize_l2_rejects_nan_and_infinity(self) -> None:
        """P1: normalize_l2 strictly rejects inputs containing NaN or Infinity."""
        nan_vec = np.array([np.nan, 1.0, 2.0])
        with self.assertRaises(ValueError) as ctx:
            normalize_l2(nan_vec)
        self.assertIn("NaN or Infinity", str(ctx.exception))

        inf_batch = np.array([[1.0, 2.0], [np.inf, 3.0]])
        with self.assertRaises(ValueError) as ctx:
            normalize_l2(inf_batch)
        self.assertIn("NaN or Infinity", str(ctx.exception))

    def test_normalize_l2_rejects_zero_vector(self) -> None:
        """P1: normalize_l2 rejects vectors with norm < eps (zero direction on unit sphere)."""
        zero_1d = np.zeros(128)
        with self.assertRaises(ValueError) as ctx:
            normalize_l2(zero_1d)
        self.assertIn("zero vector", str(ctx.exception))

        zero_in_batch = np.array([[1.0, 2.0, 3.0], [0.0, 0.0, 0.0]])
        with self.assertRaises(ValueError) as ctx:
            normalize_l2(zero_in_batch)
        self.assertIn("zero vector", str(ctx.exception))

    def test_normalize_l2_validates_shape(self) -> None:
        """P1: normalize_l2 rejects 3D arrays or zero dimensions."""
        tensor_3d = np.ones((2, 3, 4))
        with self.assertRaises(ValueError) as ctx:
            normalize_l2(tensor_3d)
        self.assertIn("Expected 1D vector (d,) or 2D batch (n, d)", str(ctx.exception))

        zero_d = np.zeros((5, 0))
        with self.assertRaises(ValueError) as ctx:
            normalize_l2(zero_d)
        self.assertIn("embedding dimension 0", str(ctx.exception))

    def test_normalize_l2_handles_empty_batch_gracefully(self) -> None:
        """P2: empty 2D batch (0, d) returns valid float32 (0, d) array without division error."""
        empty_batch = np.zeros((0, 64), dtype=np.float32)
        norm_batch = normalize_l2(empty_batch)
        self.assertEqual(norm_batch.shape, (0, 64))
        self.assertEqual(norm_batch.dtype, np.float32)

        # Empty 1D vector of shape (0,) is rejected
        with self.assertRaises(ValueError):
            normalize_l2(np.array([]))

    def test_normalize_l2_validates_eps(self) -> None:
        """P2: eps must be a positive finite float."""
        v = np.array([1.0, 2.0, 3.0])
        for invalid_eps in (0, -1.0, np.nan, np.inf, True, False):
            with self.assertRaises(ValueError) as ctx:
                normalize_l2(v, eps=invalid_eps)  # type: ignore[arg-type]
            self.assertIn("eps must be a positive finite float", str(ctx.exception))

    def test_is_finite_and_normalized_validates_tol_and_rejects_empty(self) -> None:
        """P2: is_finite_and_normalized rejects empty 1D, validates tol, and rejects zero/non-unit vectors."""
        self.assertFalse(is_finite_and_normalized(np.array([])))
        self.assertFalse(is_finite_and_normalized(np.zeros(128)))

        unit_vec = normalize_l2(np.array([3.0, 4.0]))
        self.assertTrue(is_finite_and_normalized(unit_vec))

        for invalid_tol in (-0.01, -1.0, np.nan, np.inf, True, False):
            with self.assertRaises(ValueError) as ctx:
                is_finite_and_normalized(unit_vec, tol=invalid_tol)  # type: ignore[arg-type]
            self.assertIn("tol must be a non-negative finite float", str(ctx.exception))


class TestBudgetAndReadinessHardening(unittest.TestCase):
    """Verifies Items 1, 2, and 3: unified budget calculation, non-positive budget rejection, and blocked readiness logging."""

    def test_compute_effective_budget_rejects_zero_or_negative(self) -> None:
        """Item 2: zero or negative available budget strictly raises ValueError without masking."""
        # max_length = 10, passage_prefix = 8, special = 2 -> available = 0
        with self.assertRaises(ValueError) as ctx:
            compute_effective_document_budget(
                max_length=10, passage_prefix_tokens=8, special_tokens=2
            )
        self.assertIn("available document budget is 0 <= 0", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            compute_effective_query_budget(max_length=10, query_prefix_tokens=10, special_tokens=2)
        self.assertIn("available query budget is -2 <= 0", str(ctx.exception))

    def test_base_embedder_rejects_impossible_budget_at_startup(self) -> None:
        """Item 1 & 2: BaseEmbedder fails fast on initialization if model capacity cannot fit prefixes and special tokens."""
        cfg = EmbedderConfig(
            model_id="m1",
            revision="rev1",
            dimension=64,
            max_length=6,
            pooling="mean",
            normalize_embeddings=True,
            passage_prefix="word word word word word ",  # 5 tokens
            special_tokens=2,  # 5 + 2 = 7 > 6
        )
        with self.assertRaises(ValueError) as ctx:
            FakeEmbedder(config=cfg)
        self.assertIn("available document budget is -1 <= 0", str(ctx.exception))

    def test_readiness_blocks_and_logs_on_mismatch(self) -> None:
        """Item 3: readiness check strictly blocks and logs structured error on fingerprint mismatch."""
        embedder = FakeEmbedder()
        self.assertFalse(verify_embedder_fingerprint_readiness("mismatched_index_digest", embedder))
        self.assertFalse(verify_embedder_fingerprint_readiness("", embedder))
        self.assertTrue(verify_embedder_fingerprint_readiness(embedder.fingerprint, embedder))


if __name__ == "__main__":
    unittest.main()
