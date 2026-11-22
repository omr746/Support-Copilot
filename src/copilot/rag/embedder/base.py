"""Synchronous Embedder port protocol and BaseEmbedder implementation (Step 2.2)."""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections.abc import Sequence
from typing import Protocol, cast, runtime_checkable

import numpy as np

from copilot.core.errors import InvalidInputError
from copilot.rag.embedder.inputs import prepare_query_text
from copilot.rag.embedder.validation import (
    compute_effective_document_budget,
    compute_effective_query_budget,
    validate_document_text,
    validate_query_max_tokens,
)
from copilot.rag.fingerprint import (
    EmbedderConfig,
    compute_embedder_fingerprint,
)
from copilot.rag.tokens import TokenCounter, WhitespaceTokenCounter

logger = logging.getLogger(__name__)


@runtime_checkable
class Embedder(Protocol):
    """Synchronous contract for embedding models and adapters (Step 2.2)."""

    @property
    def config(self) -> EmbedderConfig:
        """The immutable EmbedderConfig defining model, dimension, prefixes, and runtime."""
        ...

    @property
    def fingerprint(self) -> str:
        """Deterministic fingerprint of this embedder configuration."""
        ...

    @property
    def tokenizer(self) -> TokenCounter:
        """TokenCounter built from the model's tokenizer."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        """Embed a batch of document texts, returning float32 array of shape (n, d)."""
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single search query, returning float32 array of shape (d,)."""
        ...


class BaseEmbedder:
    """Base embedder implementing batching, length-sorting, input validation, and spans."""

    def __init__(
        self,
        config: EmbedderConfig,
        tokenizer: TokenCounter | None = None,
        max_batch_size: int = 32,
    ) -> None:
        self._tokenizer = tokenizer or WhitespaceTokenCounter(max_input_tokens=config.max_length)

        # Ensure tokenizer identity is bound to config and fingerprint (Finding 10)
        tok_id = getattr(self._tokenizer, "tokenizer_id", None)
        if config.tokenizer_id is not None and tok_id is not None and config.tokenizer_id != tok_id:
            raise ValueError(
                f"Config tokenizer_id ({config.tokenizer_id}) does not match "
                f"tokenizer tokenizer_id ({tok_id})"
            )
        if config.tokenizer_id is None and tok_id is not None:
            config = dataclasses.replace(config, tokenizer_id=tok_id)

        self._config = config
        self._fingerprint = compute_embedder_fingerprint(config)
        self._max_batch_size = max(1, min(64, max_batch_size))
        self._lock = threading.Lock()

        # Precompute prefix tokens and budget once at initialization (Finding 11 & Finding 8)
        self._special_tokens = config.special_tokens
        self._passage_prefix_tokens = (
            self._tokenizer.count_tokens(config.passage_prefix) if config.passage_prefix else 0
        )
        self._query_prefix_tokens = (
            self._tokenizer.count_tokens(config.query_prefix) if config.query_prefix else 0
        )
        # Unified effective token budget calculation (Items 1 & 2)
        self._allowed_document_tokens = compute_effective_document_budget(
            max_length=config.max_length,
            passage_prefix_tokens=self._passage_prefix_tokens,
            special_tokens=self._special_tokens,
        )
        self._allowed_query_tokens = compute_effective_query_budget(
            max_length=config.max_length,
            query_prefix_tokens=self._query_prefix_tokens,
            special_tokens=self._special_tokens,
        )

        # Operational metrics counters (thread-safe, Finding 9 & Finding 12)
        self.call_count = 0
        self.document_text_count = 0
        self.query_text_count = 0
        self.total_tokens_count = 0
        self.input_tokens_received = 0
        self.input_tokens_accepted = 0
        self.input_tokens_embedded = 0
        self.truncation_count = 0

    @property
    def config(self) -> EmbedderConfig:
        return self._config

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    @property
    def tokenizer(self) -> TokenCounter:
        return self._tokenizer

    def _validate_document_text(self, text: str) -> int:
        """Validate document text length and emptiness without silent truncation.

        Delegates to validation.validate_document_text. Does NOT update metrics on failure (Finding 9).
        """
        return validate_document_text(
            text=text,
            tokenizer=self._tokenizer,
            allowed_tokens=self._allowed_document_tokens,
            max_length=self._config.max_length,
            passage_prefix_tokens=self._passage_prefix_tokens,
            special_tokens=self._special_tokens,
        )

    def _prepare_query_with_truncation(
        self, query: str, max_query_tokens: int = 256
    ) -> tuple[str, bool]:
        """Validate query and truncate if it exceeds effective query tokens (records truncation).

        Guarantees:
        - Validates max_query_tokens is positive int via validation.validate_query_max_tokens (Finding 6).
        - Clamps limit to model max_length minus prefix and special tokens (Finding 7).
        """
        validate_query_max_tokens(max_query_tokens)

        if not isinstance(query, str) or not query.strip():
            raise InvalidInputError(
                "Query text to embed cannot be empty or whitespace only", "embedder"
            )

        # Effective limit is the tighter of requested max_query_tokens and model capacity (Finding 7)
        effective_limit = min(max_query_tokens, self._allowed_query_tokens)

        prepared = prepare_query_text(query, self._config.embed_text_source)
        tokens = self._tokenizer.count_tokens(prepared)

        with self._lock:
            self.input_tokens_received += tokens

        truncated = False
        if tokens > effective_limit:
            truncated = True
            with self._lock:
                self.truncation_count += 1
            spans = self._tokenizer.token_spans(prepared)
            if len(spans) > effective_limit:
                cutoff_char = spans[effective_limit - 1][1]
                prepared = prepared[:cutoff_char].strip()

        used_tokens = min(tokens, effective_limit)
        with self._lock:
            self.input_tokens_accepted += used_tokens
            self.total_tokens_count += used_tokens
            self.input_tokens_embedded += used_tokens + self._query_prefix_tokens

        return prepared, truncated

    def _raw_embed_batch(self, prefixed_texts: Sequence[str]) -> np.ndarray:
        """Subclasses implement actual model inference on prefixed texts, returning (n, d) float32."""
        raise NotImplementedError

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        """Embed a sequence of document texts with batching, length-sorting, and order restoration (Findings 13 & 14)."""
        if len(texts) == 0:
            dim = self._config.truncate_dim or self._config.dimension
            return np.zeros((0, dim), dtype=np.float32)

        # 1. Validate all document texts first (atomic check, Finding 9)
        doc_tokens: list[int] = []
        for t in texts:
            tok = self._validate_document_text(t)
            doc_tokens.append(tok)

        accepted_tokens = sum(doc_tokens)
        with self._lock:
            self.call_count += 1
            self.document_text_count += len(texts)
            self.input_tokens_received += accepted_tokens
            self.input_tokens_accepted += accepted_tokens
            self.total_tokens_count += accepted_tokens
            self.input_tokens_embedded += accepted_tokens + (
                len(texts) * self._passage_prefix_tokens
            )

        # 2. Add passage prefix
        prefix = self._config.passage_prefix or ""
        prefixed_inputs = [f"{prefix}{t}" for t in texts]

        n = len(prefixed_inputs)
        dim = self._config.truncate_dim or self._config.dimension
        result = np.zeros((n, dim), dtype=np.float32)

        # 3. Process in batches of max_batch_size (Finding 13)
        for batch_start in range(0, n, self._max_batch_size):
            batch_end = min(n, batch_start + self._max_batch_size)
            batch_indices = list(range(batch_start, batch_end))

            # 4. Sort by text length inside batch for inference efficiency (Finding 14)
            sorted_indices = sorted(batch_indices, key=lambda idx: len(prefixed_inputs[idx]))
            sorted_texts = [prefixed_inputs[idx] for idx in sorted_indices]

            # 5. Raw model inference on sorted batch
            batch_vectors = self._raw_embed_batch(sorted_texts)
            if not isinstance(batch_vectors, np.ndarray) or batch_vectors.shape != (
                len(sorted_indices),
                dim,
            ):
                raise ValueError(
                    f"Model returned invalid vector shape {getattr(batch_vectors, 'shape', None)}, "
                    f"expected ({len(sorted_indices)}, {dim})"
                )

            # 6. Restore original document ordering (Finding 14)
            for sorted_pos, orig_idx in enumerate(sorted_indices):
                result[orig_idx] = batch_vectors[sorted_pos]

        return result

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a search query with query prefix, query length truncation, and metrics."""
        prepared_query, _ = self._prepare_query_with_truncation(text)

        with self._lock:
            self.call_count += 1
            self.query_text_count += 1

        prefix = self._config.query_prefix or ""
        prefixed_query = f"{prefix}{prepared_query}"
        batch_vec = self._raw_embed_batch([prefixed_query])
        return cast(np.ndarray, batch_vec[0])
