"""Embedder port, input formatting, vector math, caching, and validation (Step 2.2)."""

from __future__ import annotations

from copilot.rag.embedder.base import BaseEmbedder, Embedder
from copilot.rag.embedder.cache import (
    CachedQueryEmbedder,
    InMemoryQueryCache,
    QueryCacheBackend,
    SingleFlight,
    compute_query_cache_key,
)
from copilot.rag.embedder.inputs import (
    build_embedding_input,
    compute_embedding_input_hash,
    detect_pii,
    prepare_query_text,
)
from copilot.rag.embedder.validation import (
    compute_effective_document_budget,
    compute_effective_query_budget,
    validate_chunker_embedder_budget,
    validate_document_text,
    validate_query_max_tokens,
    verify_embedder_fingerprint_readiness,
)
from copilot.rag.embedder.vector import is_finite_and_normalized, normalize_l2

__all__ = [
    "BaseEmbedder",
    "CachedQueryEmbedder",
    "Embedder",
    "InMemoryQueryCache",
    "QueryCacheBackend",
    "SingleFlight",
    "build_embedding_input",
    "compute_effective_document_budget",
    "compute_effective_query_budget",
    "compute_embedding_input_hash",
    "compute_query_cache_key",
    "detect_pii",
    "is_finite_and_normalized",
    "normalize_l2",
    "prepare_query_text",
    "validate_chunker_embedder_budget",
    "validate_document_text",
    "validate_query_max_tokens",
    "verify_embedder_fingerprint_readiness",
]
