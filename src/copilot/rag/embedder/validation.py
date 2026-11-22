"""Validation rules and guards for embedders, documents, and budgets (Step 2.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from copilot.core.errors import InvalidInputError
from copilot.rag.fingerprint import ChunkerConfig, EmbedderConfig
from copilot.rag.tokens import TokenCounter

if TYPE_CHECKING:
    from copilot.rag.embedder.base import Embedder

logger = structlog.get_logger(__name__)


def compute_effective_document_budget(
    max_length: int,
    passage_prefix_tokens: int,
    special_tokens: int,
) -> int:
    """Compute effective token budget for document text, strictly failing if budget <= 0 (Item 1 & 2).

    Formula: max_length - passage_prefix_tokens - special_tokens.
    Raises ValueError if available budget <= 0.
    """
    available = max_length - passage_prefix_tokens - special_tokens
    if available <= 0:
        raise ValueError(
            f"Invalid embedder token budget: available document budget is {available} <= 0 "
            f"(max_length={max_length}, passage_prefix={passage_prefix_tokens}, "
            f"special_tokens={special_tokens}). No capacity remains for document text!"
        )
    return available


def compute_effective_query_budget(
    max_length: int,
    query_prefix_tokens: int,
    special_tokens: int,
) -> int:
    """Compute effective token budget for query text, strictly failing if budget <= 0 (Item 1 & 2).

    Formula: max_length - query_prefix_tokens - special_tokens.
    Raises ValueError if available budget <= 0.
    """
    available = max_length - query_prefix_tokens - special_tokens
    if available <= 0:
        raise ValueError(
            f"Invalid embedder token budget: available query budget is {available} <= 0 "
            f"(max_length={max_length}, query_prefix={query_prefix_tokens}, "
            f"special_tokens={special_tokens}). No capacity remains for query text!"
        )
    return available


def validate_document_text(
    text: str,
    tokenizer: TokenCounter,
    allowed_tokens: int,
    max_length: int,
    passage_prefix_tokens: int,
    special_tokens: int,
) -> int:
    """Validate document text is non-empty and fits within embedder token limits without silent truncation.

    Returns the counted tokens on success.
    Raises InvalidInputError if text is empty/whitespace or exceeds allowed_tokens.
    """
    if not isinstance(text, str) or not text.strip():
        raise InvalidInputError(
            "Document text to embed cannot be empty or whitespace only", "embedder"
        )

    tokens = tokenizer.count_tokens(text)
    if tokens > allowed_tokens:
        raise InvalidInputError(
            f"Document text token count ({tokens}) exceeds embedder limit "
            f"({allowed_tokens} tokens = max_length {max_length} "
            f"- prefix {passage_prefix_tokens} - special {special_tokens}). "
            "Document chunk must be split by chunker before embedding.",
            "embedder",
        )
    return tokens


def validate_query_max_tokens(max_query_tokens: Any) -> int:
    """Validate that max_query_tokens is a positive integer, strictly rejecting bool and <= 0."""
    if (
        not isinstance(max_query_tokens, int)
        or isinstance(max_query_tokens, bool)
        or max_query_tokens <= 0
    ):
        raise InvalidInputError("max_query_tokens must be a positive integer", "embedder")
    return max_query_tokens


def validate_chunker_embedder_budget(
    chunker_config: ChunkerConfig,
    embedder_config: EmbedderConfig,
    token_counter: TokenCounter | None = None,
) -> bool:
    """Startup validation guard: ensure chunker limit does not exceed embedder max_length.

    Leaves headroom for passage prefix and special tokens using unified budget calculation (Item 1).
    """
    prefix_tokens = 0
    if embedder_config.passage_prefix and token_counter:
        prefix_tokens = token_counter.count_tokens(embedder_config.passage_prefix)
    elif embedder_config.passage_prefix:
        prefix_tokens = len(embedder_config.passage_prefix.split())

    # Unified budget calculation: strictly raises if available <= 0
    available_budget = compute_effective_document_budget(
        max_length=embedder_config.max_length,
        passage_prefix_tokens=prefix_tokens,
        special_tokens=embedder_config.special_tokens,
    )

    if chunker_config.max_tokens > available_budget:
        raise ValueError(
            f"Chunker max_tokens ({chunker_config.max_tokens}) exceeds embedder available budget "
            f"({available_budget} = max_length {embedder_config.max_length} - prefix {prefix_tokens} "
            f"- special {embedder_config.special_tokens}). Adjust chunker settings or select a larger embedding model."
        )
    return True


def verify_embedder_fingerprint_readiness(
    active_index_fingerprint: str,
    loaded_embedder: Embedder,
) -> bool:
    """Verify that active index's embedder fingerprint matches the loaded embedder.

    Guarantees:
    - Strictly blocks index loading or retrieval on any mismatch (Item 3).
    - Emits structured error logs explaining exact mismatch to prevent vector space contamination.
    """
    if not active_index_fingerprint or not active_index_fingerprint.strip():
        logger.error(
            "embedder_fingerprint_readiness_failed",
            reason="active_index_fingerprint_empty",
            loaded_embedder_fingerprint=getattr(loaded_embedder, "fingerprint", None),
        )
        return False

    if not loaded_embedder:
        logger.error(
            "embedder_fingerprint_readiness_failed",
            reason="loaded_embedder_missing",
            active_index_fingerprint=active_index_fingerprint,
        )
        return False

    loaded_fp = loaded_embedder.fingerprint.strip()
    index_fp = active_index_fingerprint.strip()

    if loaded_fp != index_fp:
        logger.error(
            "embedder_fingerprint_readiness_failed",
            reason="fingerprint_mismatch",
            active_index_fingerprint=index_fp,
            loaded_embedder_fingerprint=loaded_fp,
            model_id=loaded_embedder.config.model_id,
            revision=loaded_embedder.config.revision,
            action="blocking_index_load_and_retrieval",
            msg=(
                f"Active index embedder fingerprint ({index_fp}) does not match loaded "
                f"embedder fingerprint ({loaded_fp}). Retrieval blocked to prevent "
                "vector space corruption across differing models/configurations."
            ),
        )
        return False

    logger.info(
        "embedder_fingerprint_readiness_verified",
        fingerprint=index_fp,
        model_id=loaded_embedder.config.model_id,
    )
    return True
