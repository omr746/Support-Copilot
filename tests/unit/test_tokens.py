"""Unit tests for token counter protocol, whitespace baseline, and test fakes (Step 2.1).

Verifies protocol contract adherence, token span accuracy, monotonicity,
startup limit validation, and empty/whitespace handling.
"""

import unittest

from copilot.rag.chunker import CHUNKER_SETTINGS, Section, chunk_page, chunk_section
from copilot.rag.models import PageVersion
from copilot.rag.tokens import TokenCounter, WhitespaceTokenCounter
from tests.fakes.fake_token_counter import FakeCharTokenCounter, FakeWordTokenCounter


class TestTokens(unittest.TestCase):
    """Test suite for token counters and protocol contracts."""

    def test_protocol_runtime_check(self) -> None:
        """Verify counters satisfy runtime-checkable TokenCounter Protocol."""
        ws_counter = WhitespaceTokenCounter()
        char_counter = FakeCharTokenCounter()
        word_counter = FakeWordTokenCounter()

        self.assertIsInstance(ws_counter, TokenCounter)
        self.assertIsInstance(char_counter, TokenCounter)
        self.assertIsInstance(word_counter, TokenCounter)

    def test_whitespace_counter_spans_and_counting(self) -> None:
        """Whitespace counter extracts exact word spans and counts accurately."""
        counter = WhitespaceTokenCounter()
        self.assertEqual(counter.tokenizer_id, "whitespace@v1")
        self.assertGreaterEqual(counter.max_input_tokens, 512)

        self.assertEqual(counter.count_tokens(""), 0)
        self.assertEqual(counter.count_tokens("   \t\n  "), 0)
        self.assertEqual(counter.token_spans(""), [])
        self.assertEqual(counter.token_spans("   \t  "), [])

        text = "Hello, world!  This is a test."
        spans = counter.token_spans(text)
        self.assertEqual(len(spans), 6)
        words = [text[s:e] for s, e in spans]
        self.assertEqual(words, ["Hello,", "world!", "This", "is", "a", "test."])
        self.assertEqual(counter.count_tokens(text), 6)

    def test_fake_char_counter_monotonicity_and_partition(self) -> None:
        """FakeCharTokenCounter is strictly monotonic and partitions text without gaps."""
        counter = FakeCharTokenCounter(chars_per_token=4, max_input_tokens=512)
        self.assertEqual(counter.tokenizer_id, "fake-char@v1")

        self.assertEqual(counter.count_tokens(""), 0)
        self.assertEqual(counter.token_spans(""), [])

        # Monotonicity test across arbitrary lengths
        for length in range(150):
            t1 = "a" * length
            t2 = "a" * (length + 1)
            self.assertLessEqual(
                counter.count_tokens(t1),
                counter.count_tokens(t2),
                f"Monotonicity failed at length {length}",
            )

        # Partition test: spans reconstruct the original text verbatim
        sample = "Arbitrary sample text with 123456789 and punctuation!؟"
        spans = counter.token_spans(sample)
        reconstructed = "".join(sample[s:e] for s, e in spans)
        self.assertEqual(reconstructed, sample)
        self.assertEqual(counter.count_tokens(sample), len(spans))

    def test_fake_word_counter_behavior(self) -> None:
        """FakeWordTokenCounter extracts exact word slices and ignores whitespace."""
        counter = FakeWordTokenCounter()
        self.assertEqual(counter.tokenizer_id, "fake-word@v1")

        self.assertEqual(counter.count_tokens(""), 0)
        self.assertEqual(counter.count_tokens("   "), 0)

        arabic_text = "الدعم الفني لشركة الاتصالات"
        spans = counter.token_spans(arabic_text)
        self.assertEqual(len(spans), 4)
        self.assertEqual(
            [arabic_text[s:e] for s, e in spans], ["الدعم", "الفني", "لشركة", "الاتصالات"]
        )

    def test_startup_limit_validation_exceeds_max_input_tokens(self) -> None:
        """Chunker must fail at startup if max_tokens exceeds token_counter.max_input_tokens."""
        small_counter = FakeCharTokenCounter(max_input_tokens=100)
        settings = dict(CHUNKER_SETTINGS)
        settings["max_tokens"] = 200  # Exceeds counter limit 100

        sec = Section(ordinal=0, path="Test", body="Some text body")
        with self.assertRaises(ValueError) as ctx:
            chunk_section(sec, small_counter, settings)
        self.assertIn("exceeds token counter max_input_tokens", str(ctx.exception))

        page = PageVersion(
            url="https://example.com",
            canonical_url="https://example.com",
            title="T",
            language="en",
            fetched_at="2026-11-16T00:00:00Z",
            http_status=200,
            content_hash="h1",
            cleaner_version="v1",
            cleaned_text="# Heading\nBody text",
        )
        with self.assertRaises(ValueError) as ctx:
            chunk_page(page, small_counter, settings)
        self.assertIn("exceeds token counter max_input_tokens", str(ctx.exception))

    def test_chunk_page_fails_if_counter_is_none(self) -> None:
        """Fails loudly if token_counter is None (never silently fallback to whitespace in prod)."""
        page = PageVersion(
            url="https://example.com",
            canonical_url="https://example.com",
            title="T",
            language="en",
            fetched_at="2026-11-16T00:00:00Z",
            http_status=200,
            content_hash="h1",
            cleaner_version="v1",
            cleaned_text="# Heading\nBody text",
        )
        with self.assertRaises(ValueError) as ctx:
            chunk_page(page, None)  # type: ignore[arg-type]
        self.assertIn("token_counter is required", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
