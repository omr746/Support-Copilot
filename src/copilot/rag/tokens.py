"""Token counting abstractions and token position mapping for chunking (Step 2.1).

Defines the TokenCounter protocol satisfied by embedder tokenizers and test counters.

Special tokens policy:
- count_tokens and token_spans operate on raw text and strictly exclude model special
  tokens (e.g. [CLS], [SEP], <s>, </s>).
- max_input_tokens represents the maximum effective text token budget accepted by the
  underlying embedder, already accounting for and subtracting any reserved special tokens
  from the model's raw context window.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

_WORD_PATTERN = re.compile(r"\S+")


@runtime_checkable
class TokenCounter(Protocol):
    """Protocol satisfied by embedder tokenizers and test counters.

    Guarantees:
    - tokenizer_id: Exact identifier including name, revision, or file checksum (feeds fingerprint).
    - max_input_tokens: Upper bound on tokens accepted by the embedder (excluding special tokens).
    - count_tokens(text): Number of text tokens in text.
    - token_spans(text): Ordered list of (start_char_idx, end_char_idx) character slices for each token.
    """

    tokenizer_id: str
    max_input_tokens: int

    def count_tokens(self, text: str) -> int:
        """Count the number of text tokens in the given text string (excluding special tokens)."""
        ...

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        """Return the character span (start, end) of each token in text."""
        ...


class WhitespaceTokenCounter:
    """Baseline whitespace token counter strictly for unit testing and offline fixtures.

    WARNING: Never use in production. Whitespace counting severely undercounts Arabic text
    (by a factor of ~1.5x to 3x compared to BPE/WordPiece tokenizers), causing chunks to
    exceed embedder limits in real workloads. If the production tokenizer cannot load,
    the system must fail loudly rather than falling back to whitespace counting.
    """

    def __init__(
        self,
        tokenizer_id: str = "whitespace@v1",
        max_input_tokens: int = 8192,
    ) -> None:
        self.tokenizer_id = tokenizer_id
        self.max_input_tokens = max_input_tokens

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        """Return (start, end) character offsets for each non-whitespace word."""
        if not text:
            return []
        return [(m.start(), m.end()) for m in _WORD_PATTERN.finditer(text)]

    def count_tokens(self, text: str) -> int:
        """Count non-whitespace words in text."""
        if not text or not text.strip():
            return 0
        return len(self.token_spans(text))
