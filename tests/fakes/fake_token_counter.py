"""Fake token counters for deterministic unit testing of chunking boundaries (Step 2.1).

Provides separate implementations for fixed-character and word-based token simulation:
- FakeCharTokenCounter: strictly monotonic character-based tokenization.
- FakeWordTokenCounter: whitespace-delimited word tokenization.
"""

from __future__ import annotations

import re

_WORD_PATTERN = re.compile(r"\S+")


class FakeCharTokenCounter:
    """Deterministic token counter grouping text into fixed-size character slices.

    Guarantees strict monotonicity:
    len(a) <= len(b) ==> count_tokens(a) <= count_tokens(b)
    """

    def __init__(
        self,
        tokenizer_id: str = "fake-char@v1",
        chars_per_token: int = 4,
        max_input_tokens: int = 512,
    ) -> None:
        if chars_per_token < 1:
            raise ValueError(f"chars_per_token must be >= 1, got {chars_per_token}")
        self.tokenizer_id = tokenizer_id
        self.chars_per_token = chars_per_token
        self.max_input_tokens = max_input_tokens

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        """Return contiguous character slices of length chars_per_token."""
        if not text:
            return []
        n = len(text)
        return [(i, min(i + self.chars_per_token, n)) for i in range(0, n, self.chars_per_token)]

    def count_tokens(self, text: str) -> int:
        """Count fixed-character tokens."""
        if not text:
            return 0
        return len(self.token_spans(text))


class FakeWordTokenCounter:
    """Deterministic token counter splitting text by non-whitespace word boundaries."""

    def __init__(
        self,
        tokenizer_id: str = "fake-word@v1",
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
        """Count non-whitespace words."""
        if not text or not text.strip():
            return 0
        return len(self.token_spans(text))
