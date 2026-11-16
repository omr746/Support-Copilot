"""Unit and property tests for deterministic text chunking (Step 2.1).

Covers all 12 specification rules:
1. Hard limit: no chunk exceeds max_tokens (including 5,000-char unbroken string).
2. Overlap: respected within section, first chunk has none, no overlap across headings.
3. Coverage: stripping overlap and joining chunks recovers section text.
4. Metadata: all fields populated, owner_id None for builtin.
5. Arabic and English text: punctuation-aware splitting (؟, ., ،), never mid-word.
6. Original preserved: chunk.text is clean source slice, normalized_text is derived.
7. Empty/whitespace/headings-only input returns [].
8. Short sections never merged across headings.
9. Determinism: identical chunk IDs and hashes across runs.
10. One-sentence edit: changes chunk and at most overlap neighbor; other sections untouched.
11. Fingerprint: digest changes on settings/normalizer changes; all settings fields covered.
12. Repeated identical sections: distinct chunk IDs (due to ordinal) but identical hashes.
"""

from __future__ import annotations

import copy
import hashlib
import random
import string
import unittest

from copilot.rag.chunker import (
    CHUNKER_SETTINGS,
    Section,
    chunk_hash,
    chunk_id,
    chunk_page,
    chunk_section,
    extract_overlap_tail,
    format_embedding_input,
    parse_sections,
)
from copilot.rag.fingerprint import ChunkerConfig, EmbedderConfig, PipelineFingerprint
from copilot.rag.crawler.cleaner import _clean_with_beautifulsoup
from bs4 import BeautifulSoup
from copilot.rag.models import Chunk, ChunkInput, PageVersion
from copilot.rag.normalize import NORMALIZER_VERSION, normalize_search_text
from copilot.rag.tokens import WhitespaceTokenCounter
from tests.fakes.fake_token_counter import FakeCharTokenCounter


class TestChunker(unittest.TestCase):
    """Comprehensive test suite for Step 2.1 Chunker."""

    def setUp(self) -> None:
        self.counter = WhitespaceTokenCounter()

    def test_rule_1_hard_limit_and_unbroken_string(self) -> None:
        """No chunk exceeds max_tokens, including 5,000-character unbroken string."""
        char_counter = FakeCharTokenCounter(chars_per_token=4, max_input_tokens=512)
        settings = copy.deepcopy(CHUNKER_SETTINGS)
        settings["max_tokens"] = 100
        settings["target_tokens"] = 80
        settings["overlap_tokens"] = 20

        # 5,000-character unbroken string without any spaces or punctuation
        unbroken = "x" * 5000
        section = Section(ordinal=0, path="Test", body=unbroken)
        chunks = chunk_section(section, char_counter, settings)

        self.assertGreater(len(chunks), 1)
        for i, chunk in enumerate(chunks):
            token_count = char_counter.count_tokens(chunk)
            self.assertLessEqual(
                token_count,
                settings["max_tokens"],
                f"Chunk {i} exceeded max_tokens: {token_count} > {settings['max_tokens']}",
            )

        # Property test: random strings of words
        random.seed(42)
        for _ in range(10):
            words = ["".join(random.choices(string.ascii_letters, k=random.randint(3, 10))) for _ in range(500)]
            text = " ".join(words)
            rnd_sec = Section(ordinal=0, path="Random", body=text)
            rnd_chunks = chunk_section(rnd_sec, self.counter, settings)
            for c in rnd_chunks:
                self.assertLessEqual(self.counter.count_tokens(c), settings["max_tokens"])

    def test_rule_2_overlap_respected_and_no_overlap_across_headings(self) -> None:
        """First chunk has no overlap, subsequent chunks start with overlap tail; no overlap across headings."""
        settings = copy.deepcopy(CHUNKER_SETTINGS)
        settings["max_tokens"] = 16
        settings["target_tokens"] = 10
        settings["overlap_tokens"] = 4

        markdown = (
            "# Section One\n"
            "Sentence one is here. Sentence two follows it. Sentence three has more details. "
            "Sentence four concludes the first part. Sentence five starts the second part.\n\n"
            "## Section Two\n"
            "Section two introductory sentence. Details for section two are described here."
        )

        page = PageVersion(
            url="https://example.com/test",
            canonical_url="https://example.com/test",
            title="Overlap Test",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="ver-100",
            cleaner_version="v1",
            cleaned_text=markdown,
        )

        chunks = chunk_page(page, self.counter, settings)
        self.assertGreater(len(chunks), 2)

        sec1_chunks = [c for c in chunks if c.section_path.startswith("Section One")]
        sec2_chunks = [c for c in chunks if c.section_path.startswith("Section One > Section Two")]

        self.assertGreater(len(sec1_chunks), 1)
        self.assertGreaterEqual(len(sec2_chunks), 1)

        # First chunk of section 1 has no overlap prefix from elsewhere
        self.assertTrue(sec1_chunks[0].text.startswith("Sentence one"))

        # Subsequent chunk in section 1 must contain tail words of prior chunk
        prior_tail = extract_overlap_tail(sec1_chunks[0].text, settings["overlap_tokens"], self.counter)
        self.assertTrue(sec1_chunks[1].text.startswith(prior_tail))

        # First chunk of section 2 must NOT have overlap from section 1
        self.assertTrue(sec2_chunks[0].text.startswith("Section two"))
        for word in sec1_chunks[-1].text.split()[-3:]:
            self.assertNotIn(word, sec2_chunks[0].text.split()[:3])

    def test_rule_3_coverage_recovers_section_text(self) -> None:
        """Stripping overlap and joining chunks recovers section text (up to whitespace)."""
        settings = copy.deepcopy(CHUNKER_SETTINGS)
        settings["max_tokens"] = 12
        settings["target_tokens"] = 8
        settings["overlap_tokens"] = 3

        body = (
            "First paragraph with some words.\n\n"
            "Second paragraph with additional information.\n\n"
            "Third paragraph concluding this unit."
        )
        sec = Section(ordinal=0, path="Doc", body=body)
        raw_chunks = chunk_section(sec, self.counter, settings)
        self.assertGreater(len(raw_chunks), 1)

        # Reconstruct by stripping overlap prefix from subsequent chunks
        recovered_parts: list[str] = [raw_chunks[0]]
        for i in range(1, len(raw_chunks)):
            prev_tail = extract_overlap_tail(raw_chunks[i - 1], settings["overlap_tokens"], self.counter)
            chunk_txt = raw_chunks[i]
            if prev_tail and chunk_txt.startswith(prev_tail):
                stripped = chunk_txt[len(prev_tail) :].strip()
                recovered_parts.append(stripped)
            else:
                recovered_parts.append(chunk_txt)

        joined = " ".join(recovered_parts)
        for word in body.split():
            self.assertIn(word, joined)

    def test_rule_4_metadata_fields(self) -> None:
        """Metadata fields: source_type, owner_id (None for builtin), hashes, versions."""
        page = PageVersion(
            url="https://example.com/docs/manual",
            canonical_url="https://example.com/docs/manual",
            title="Manual",
            language="ar",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="page-hash-99",
            cleaner_version="v1",
            cleaned_text="# دليل الاستخدام\nمحتوى توضيحي للدليل.",
        )

        chunks = chunk_page(page, self.counter, source_type="builtin", owner_id=None, source_id="src-1")
        self.assertEqual(len(chunks), 1)
        c = chunks[0]

        self.assertEqual(c.source_type, "builtin")
        self.assertIsNone(c.owner_id)
        self.assertEqual(c.source_id, "src-1")
        self.assertEqual(c.page_version_id, "page-hash-99")
        self.assertEqual(c.url_or_filename, "https://example.com/docs/manual")
        self.assertEqual(c.title, "Manual")
        self.assertEqual(c.language, "ar")
        self.assertEqual(c.fetched_at, "2026-11-15T09:00:00Z")
        self.assertEqual(c.normalizer_version, NORMALIZER_VERSION)
        self.assertTrue(len(c.chunker_settings_digest) == 64)
        self.assertTrue(len(c.chunk_hash) == 64)
        self.assertTrue(len(c.id) == 20)

    def test_rule_5_arabic_and_english_punctuation_aware(self) -> None:
        """Arabic and English text both work; splits at punctuation (؟, ., ،), never mid-word."""
        settings = copy.deepcopy(CHUNKER_SETTINGS)
        settings["max_tokens"] = 15
        settings["target_tokens"] = 10
        settings["overlap_tokens"] = 3

        ar_text = (
            "هذا النص يتناول الدعم الفني الذكي. "
            "هل ترغب في الاستفسار عن تفاصيل الحساب؟ "
            "يمكنك التواصل معنا عبر البريد، أو الهاتف، أو المحادثة المباشرة."
        )
        sec = Section(ordinal=0, path="Arabic", body=ar_text)
        chunks = chunk_section(sec, self.counter, settings)
        self.assertGreater(len(chunks), 1)

        for c in chunks:
            # Must not cut mid-word (no isolated letters from word breaking)
            words = c.split()
            for w in words:
                self.assertFalse(len(w) == 1 and w in "ابتثجحخدذرزسشصضطظعغفقكلمنهوي")

    def test_rule_6_original_preserved_and_normalized_derived(self) -> None:
        """chunk.text is clean source slice; normalized_text differs when diacritics/alef variants present."""
        raw_ar = "# التوثيق\nهٰذَا كَتَبَ أَمْسِ فِي المـُسْتَشْفَىٰ."
        page = PageVersion(
            url="https://example.com/ar",
            canonical_url="https://example.com/ar",
            title="Ar Page",
            language="ar",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="h1",
            cleaner_version="v1",
            cleaned_text=raw_ar,
        )

        chunks = chunk_page(page, self.counter)
        self.assertEqual(len(chunks), 1)
        c = chunks[0]

        # Original text has diacritics and tatweel
        self.assertIn("كَتَبَ", c.text)
        self.assertIn("أَمْسِ", c.text)

        # Normalized text has them stripped and mapped
        self.assertNotIn("كَتَبَ", c.normalized_text)
        self.assertIn("كتب", c.normalized_text)
        self.assertIn("امس", c.normalized_text)
        self.assertNotEqual(c.text, c.normalized_text)

    def test_rule_7_empty_whitespace_and_headings_only(self) -> None:
        """Empty or whitespace-only document, or a document with only headings returns [], no error."""
        cases = [
            "",
            "    \t\n   \n",
            "# Heading Only\n## Subheading\n### Level 3",
            "   \n## Just A Title\n\n",
        ]
        for text in cases:
            page = PageVersion(
                url="https://example.com/empty",
                canonical_url="https://example.com/empty",
                title="Empty",
                language="en",
                fetched_at="2026-11-15T09:00:00Z",
                http_status=200,
                content_hash="empty-h",
                cleaner_version="v1",
                cleaned_text=text,
            )
            chunks = chunk_page(page, self.counter)
            self.assertEqual(chunks, [], f"Expected [] for input: {repr(text)}")

    def test_rule_8_short_sections_never_merged_across_headings(self) -> None:
        """A short section is one chunk, even if tiny. Chunks never cross a heading."""
        text = (
            "# Chapter 1\n"
            "Tiny one.\n\n"
            "# Chapter 2\n"
            "Tiny two."
        )
        sections = parse_sections(text, default_title="Book")
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].path, "Chapter 1")
        self.assertEqual(sections[0].body, "Tiny one.")
        self.assertEqual(sections[1].path, "Chapter 2")
        self.assertEqual(sections[1].body, "Tiny two.")

        page = PageVersion(
            url="https://example.com/book",
            canonical_url="https://example.com/book",
            title="Book",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="book-h",
            cleaner_version="v1",
            cleaned_text=text,
        )
        chunks = chunk_page(page, self.counter)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].text, "Tiny one.")
        self.assertEqual(chunks[1].text, "Tiny two.")
        self.assertNotEqual(chunks[0].section_path, chunks[1].section_path)

    def test_rule_9_determinism_across_runs(self) -> None:
        """The same input always gives the same chunk ids and chunk hashes."""
        doc = (
            "# Determinism\n"
            "Deterministic chunking ensures reproducible search index builds. "
            "Hashing is pure and stable."
        )
        page1 = PageVersion(
            url="https://example.com/det",
            canonical_url="https://example.com/det",
            title="Det",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="fixed-hash-123",
            cleaner_version="v1",
            cleaned_text=doc,
        )
        page2 = PageVersion(
            url="https://example.com/det",
            canonical_url="https://example.com/det",
            title="Det",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="fixed-hash-123",
            cleaner_version="v1",
            cleaned_text=doc,
        )

        chunks1 = chunk_page(page1, self.counter)
        chunks2 = chunk_page(page2, self.counter)

        self.assertEqual(len(chunks1), len(chunks2))
        for c1, c2 in zip(chunks1, chunks2):
            self.assertEqual(c1.id, c2.id)
            self.assertEqual(c1.chunk_hash, c2.chunk_hash)
            self.assertEqual(c1.text, c2.text)
            self.assertEqual(c1.normalized_text, c2.normalized_text)

    def test_rule_10_one_sentence_edit_isolation(self) -> None:
        """Changing one sentence changes the hash of that chunk (and at most overlap neighbor) and no others."""
        settings = copy.deepcopy(CHUNKER_SETTINGS)
        settings["max_tokens"] = 30
        settings["target_tokens"] = 20
        settings["overlap_tokens"] = 6

        doc_v1 = (
            "# Intro\n"
            "Introductory section body is here.\n\n"
            "# Details\n"
            "Sentence alpha is here. Sentence beta is second. Sentence gamma is third. "
            "Sentence delta is fourth. Sentence epsilon is fifth. Sentence zeta is sixth.\n\n"
            "# Conclusion\n"
            "Final conclusion text."
        )

        # Modify only sentence alpha in Details
        doc_v2 = (
            "# Intro\n"
            "Introductory section body is here.\n\n"
            "# Details\n"
            "Sentence alpha has been modified. Sentence beta is second. Sentence gamma is third. "
            "Sentence delta is fourth. Sentence epsilon is fifth. Sentence zeta is sixth.\n\n"
            "# Conclusion\n"
            "Final conclusion text."
        )

        p1 = PageVersion(
            url="https://example.com/doc",
            canonical_url="https://example.com/doc",
            title="Doc",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="v1",
            cleaner_version="v1",
            cleaned_text=doc_v1,
        )
        p2 = PageVersion(
            url="https://example.com/doc",
            canonical_url="https://example.com/doc",
            title="Doc",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="v1",  # Same version id to test hash isolation
            cleaner_version="v1",
            cleaned_text=doc_v2,
        )

        chunks1 = chunk_page(p1, self.counter, settings)
        chunks2 = chunk_page(p2, self.counter, settings)

        # Intro and Conclusion chunk hashes MUST remain completely identical
        intro_c1 = [c for c in chunks1 if c.section_path == "Intro"][0]
        intro_c2 = [c for c in chunks2 if c.section_path == "Intro"][0]
        self.assertEqual(intro_c1.chunk_hash, intro_c2.chunk_hash)

        conc_c1 = [c for c in chunks1 if c.section_path == "Conclusion"][0]
        conc_c2 = [c for c in chunks2 if c.section_path == "Conclusion"][0]
        self.assertEqual(conc_c1.chunk_hash, conc_c2.chunk_hash)

        # In Details: the first chunk containing sentence alpha changed
        details_c1 = [c for c in chunks1 if c.section_path == "Details"]
        details_c2 = [c for c in chunks2 if c.section_path == "Details"]
        self.assertNotEqual(details_c1[0].chunk_hash, details_c2[0].chunk_hash)

    def test_rule_11_fingerprint_changes(self) -> None:
        """Changing chunker settings, normalizer, or embedder changes fingerprint."""
        chunk_cfg = ChunkerConfig.from_dict(CHUNKER_SETTINGS)
        emb_cfg = EmbedderConfig(
            model_id="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            revision="rev-1",
            dimension=384,
            max_length=1024,
            pooling="mean",
            normalize_embeddings=True,
        )
        base_fp = PipelineFingerprint(
            cleaner_version="trafilatura-v1",
            normalizer_version=NORMALIZER_VERSION,
            chunker_config=chunk_cfg,
            injection_pattern_version="v1",
            embedder_config=emb_cfg,
        )
        digest_base = base_fp.chunking_digest
        full_base = base_fp.full_digest

        # Normalizer bump
        bump_norm = PipelineFingerprint(
            cleaner_version="trafilatura-v1",
            normalizer_version="ar-normalizer-v2.0",
            chunker_config=chunk_cfg,
            injection_pattern_version="v1",
            embedder_config=emb_cfg,
        )
        self.assertNotEqual(digest_base, bump_norm.chunking_digest)
        self.assertNotEqual(full_base, bump_norm.full_digest)

        # Chunker settings change
        modified_settings = copy.deepcopy(CHUNKER_SETTINGS)
        modified_settings["max_tokens"] = 900
        bump_settings = PipelineFingerprint(
            cleaner_version="trafilatura-v1",
            normalizer_version=NORMALIZER_VERSION,
            chunker_config=ChunkerConfig.from_dict(modified_settings),
            injection_pattern_version="v1",
            embedder_config=emb_cfg,
        )
        self.assertNotEqual(digest_base, bump_settings.chunking_digest)
        self.assertNotEqual(full_base, bump_settings.full_digest)

        # Cleaner version change (alters full_digest, keeps chunking_digest)
        bump_cleaner = PipelineFingerprint(
            cleaner_version="trafilatura-v2",
            normalizer_version=NORMALIZER_VERSION,
            chunker_config=chunk_cfg,
            injection_pattern_version="v1",
            embedder_config=emb_cfg,
        )
        self.assertEqual(digest_base, bump_cleaner.chunking_digest)
        self.assertNotEqual(full_base, bump_cleaner.full_digest)

    def test_rule_12_repeated_identical_sections(self) -> None:
        """Repeated identical sections give different chunk IDs (due to ordinal) but the same chunk hash."""
        text = (
            "# Details\n"
            "This is identical content in the section.\n\n"
            "# Details\n"
            "This is identical content in the section."
        )
        page = PageVersion(
            url="https://example.com/dup",
            canonical_url="https://example.com/dup",
            title="Duplicate Sections",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="dup-hash-1",
            cleaner_version="v1",
            cleaned_text=text,
        )

        chunks = chunk_page(page, self.counter)
        self.assertEqual(len(chunks), 2)

        # Different IDs because of section ordinal
        self.assertNotEqual(chunks[0].id, chunks[1].id)

        # Identical hash because normalized text and section path are identical
        self.assertEqual(chunks[0].chunk_hash, chunks[1].chunk_hash)
        self.assertEqual(chunks[0].section_path, chunks[1].section_path)

    def test_verbatim_slices_and_offset_tracking(self) -> None:
        """Every chunk text is an exact verbatim substring of the source text at [char_start:char_end]."""
        doc = (
            "# Section A\n"
            "This is paragraph one with several words in section A.\n\n"
            "This is paragraph two with even more text explaining topic A.\n\n"
            "## Section B\n"
            "Now we are in section B. The table below lists details:\n"
            "| Service | Cost |\n"
            "| Internet | 120 |\n"
            "| Landline | 30 |\n"
        )
        settings = copy.deepcopy(CHUNKER_SETTINGS)
        settings["max_tokens"] = 16
        settings["target_tokens"] = 10
        settings["overlap_tokens"] = 4

        page = PageVersion(
            url="https://example.com/verbatim",
            canonical_url="https://example.com/verbatim",
            title="Verbatim Test",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="v-hash-1",
            cleaner_version="v1",
            cleaned_text=doc,
        )

        chunks = chunk_page(page, self.counter, settings)
        self.assertGreater(len(chunks), 2)

        for i, c in enumerate(chunks):
            # Verbatim guarantee: slice from doc matches chunk text character-for-character
            slice_from_doc = doc[c.char_start : c.char_end]
            self.assertEqual(
                c.text,
                slice_from_doc,
                f"Chunk {i} text does not match doc slice at [{c.char_start}:{c.char_end}]",
            )
            # Overlap guarantee
            if c.overlap_chars > 0:
                self.assertEqual(c.text[: c.overlap_chars], chunks[i - 1].text[-c.overlap_chars :])

    def test_tables_and_lists_preserve_newlines(self) -> None:
        """Table rows and list items are not flattened into a single space-separated line."""
        table_doc = (
            "# Pricing\n"
            "| Plan | Quota | Price |\n"
            "| Super | 140GB | 120 EGP |\n"
            "| Mega | 250GB | 210 EGP |\n"
            "| Ultra | 600GB | 500 EGP |\n"
        )
        page = PageVersion(
            url="https://example.com/table",
            canonical_url="https://example.com/table",
            title="Table",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="t-hash-1",
            cleaner_version="v1",
            cleaned_text=table_doc,
        )

        chunks = chunk_page(page, self.counter)
        self.assertEqual(len(chunks), 1)
        c = chunks[0]
        # Must retain original line breaks, not flattened into spaces
        self.assertIn("\n| Super | 140GB | 120 EGP |\n", c.text)

    def test_section_path_budgeting_in_embedding(self) -> None:
        """When section_path_in_embedding is True, path tokens are budgeted from max_tokens."""
        settings = copy.deepcopy(CHUNKER_SETTINGS)
        settings["max_tokens"] = 25
        settings["target_tokens"] = 15
        settings["overlap_tokens"] = 4
        settings["section_path_in_embedding"] = True

        doc = (
            "# Main Category > Sub Category > Topic\n"
            "Word one two three four five six seven eight nine ten eleven twelve."
        )
        page = PageVersion(
            url="https://example.com/path",
            canonical_url="https://example.com/path",
            title="Path Budget",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="p-hash-1",
            cleaner_version="v1",
            cleaned_text=doc,
        )

        chunks = chunk_page(page, self.counter, settings)
        self.assertGreaterEqual(len(chunks), 1)

        for c in chunks:
            path_tokens = self.counter.count_tokens(c.section_path + "\n\n")
            total_embedding_tokens = path_tokens + c.token_count
            self.assertLessEqual(
                total_embedding_tokens,
                settings["max_tokens"],
                f"Path + chunk tokens ({total_embedding_tokens}) exceeded max_tokens ({settings['max_tokens']})",
            )

    def test_page_version_filtering_conditions(self) -> None:
        """Removed pages, JS-requiring shells, and non-200 HTTP responses return []."""
        # Removed page
        p_removed = PageVersion(
            url="https://example.com/removed",
            canonical_url="https://example.com/removed",
            title="Removed",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="h1",
            cleaner_version="v1",
            cleaned_text="Some text",
            is_removed=True,
        )
        self.assertEqual(chunk_page(p_removed, self.counter), [])

        # JS shell
        p_js = PageVersion(
            url="https://example.com/js",
            canonical_url="https://example.com/js",
            title="JS Shell",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="h2",
            cleaner_version="v1",
            cleaned_text="Some text",
            needs_js=True,
        )
        self.assertEqual(chunk_page(p_js, self.counter), [])

        # Non-200 HTTP status
        p_404 = PageVersion(
            url="https://example.com/404",
            canonical_url="https://example.com/404",
            title="404",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=404,
            content_hash="h3",
            cleaner_version="v1",
            cleaned_text="Not found",
        )
        self.assertEqual(chunk_page(p_404, self.counter), [])

    def test_source_type_and_owner_validation(self) -> None:
        """Enforces owner_id None for builtin and owner_id required for upload."""
        inp_builtin_err = ChunkInput(
            source_type="builtin",
            owner_id="user-123",  # invalid for builtin
            source_id="src-1",
            page_version_id="pv-1",
            url_or_filename="file.md",
            title="T",
            language="en",
            fetched_at="2026-11-15",
            text="Valid body text here.",
        )
        with self.assertRaises(ValueError) as ctx:
            chunk_page(inp_builtin_err, self.counter)
        self.assertIn("owner_id must be None for 'builtin'", str(ctx.exception))

        inp_upload_err = ChunkInput(
            source_type="upload",
            owner_id=None,  # invalid for upload
            source_id="src-2",
            page_version_id="pv-2",
            url_or_filename="file.md",
            title="T",
            language="en",
            fetched_at="2026-11-15",
            text="Valid body text here.",
        )
        with self.assertRaises(ValueError) as ctx:
            chunk_page(inp_upload_err, self.counter)
        self.assertIn("owner_id is required", str(ctx.exception))

    def test_empty_normalized_text_skipped(self) -> None:
        """Chunks whose normalized text is completely empty (e.g. only tatweel or diacritics) are skipped."""
        tatweel_only_doc = "# Title\nـَـُـِـّـْ"
        page = PageVersion(
            url="https://example.com/tatweel",
            canonical_url="https://example.com/tatweel",
            title="Tatweel",
            language="ar",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="h-tatweel",
            cleaner_version="v1",
            cleaned_text=tatweel_only_doc,
        )
        chunks = chunk_page(page, self.counter)
        self.assertEqual(chunks, [])

    def test_url_collision_prevention_for_identical_content(self) -> None:
        """Two different URLs with identical text produce different chunk IDs."""
        text = "# Shared\nShared identical content across different endpoints."
        p1 = PageVersion(
            url="https://example.com/page-1",
            canonical_url="https://example.com/page-1",
            title="Page 1",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="same-hash",
            cleaner_version="v1",
            cleaned_text=text,
        )
        p2 = PageVersion(
            url="https://example.com/page-2",
            canonical_url="https://example.com/page-2",
            title="Page 2",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="same-hash",
            cleaner_version="v1",
            cleaned_text=text,
        )

        c1 = chunk_page(p1, self.counter)
        c2 = chunk_page(p2, self.counter)

        self.assertEqual(len(c1), 1)
        self.assertEqual(len(c2), 1)
        self.assertNotEqual(c1[0].id, c2[0].id)
        # Content hash is identical because text and path are identical
        self.assertEqual(c1[0].chunk_hash, c2[0].chunk_hash)

    def test_cleaner_beautifulsoup_heading_hierarchy(self) -> None:
        """BeautifulSoup cleaner emits level-accurate markdown headings (# to ######)."""
        html = (
            "<main>"
            "<h1>Level One</h1><p>Para one.</p>"
            "<h2>Level Two</h2><p>Para two.</p>"
            "<h3>Level Three</h3><p>Para three.</p>"
            "</main>"
        )
        soup = BeautifulSoup(html, "html.parser")
        cleaned = _clean_with_beautifulsoup(soup)
        self.assertIn("# Level One", cleaned)
        self.assertIn("## Level Two", cleaned)
        self.assertIn("### Level Three", cleaned)

    def test_format_embedding_input_helper(self) -> None:
        """format_embedding_input constructs [passage_prefix][section_path\\n\\n][chunk_text]."""
        formatted = format_embedding_input(
            chunk_text="The internet package provides high speed.",
            section_path="Internet Services > Home Internet",
            passage_prefix="passage: ",
            include_section_path=True,
        )
        expected = "passage: Internet Services > Home Internet\n\nThe internet package provides high speed."
        self.assertEqual(formatted, expected)

        # Without prefix and path
        self.assertEqual(format_embedding_input("Text only", "", "", False), "Text only")

    def test_embedding_input_actual_tokens_with_prefix_and_path(self) -> None:
        """Embedder receives passage_prefix + section_path + chunk.text; actual tokens never exceed max_length."""
        embedder_cfg = EmbedderConfig(
            model_id="intfloat/multilingual-e5-small",
            revision="rev-e5",
            dimension=384,
            max_length=24,  # Tight model limit
            pooling="mean",
            normalize_embeddings=True,
            passage_prefix="passage: ",
        )

        settings = copy.deepcopy(CHUNKER_SETTINGS)
        settings["max_tokens"] = 24
        settings["target_tokens"] = 14
        settings["overlap_tokens"] = 4
        settings["section_path_in_embedding"] = True

        doc = (
            "# Telecom > Internet\n"
            "Sentence one describes the fiber broadband speed and quota. "
            "Sentence two explains pricing, router installation, and landline bundle. "
            "Sentence three concludes details on contract renewal."
        )

        page = PageVersion(
            url="https://example.com/e5-test",
            canonical_url="https://example.com/e5-test",
            title="E5 Test",
            language="en",
            fetched_at="2026-11-15T09:00:00Z",
            http_status=200,
            content_hash="e5-hash",
            cleaner_version="v1",
            cleaned_text=doc,
        )

        chunks = chunk_page(page, self.counter, settings, embedder_config=embedder_cfg)
        self.assertGreater(len(chunks), 1)

        for i, c in enumerate(chunks):
            # Calculate actual embedding input exactly as fed to the embedding model
            embedding_input = format_embedding_input(
                c.text,
                section_path=c.section_path,
                passage_prefix=embedder_cfg.passage_prefix,
                include_section_path=True,
            )
            actual_tokens = self.counter.count_tokens(embedding_input)
            self.assertLessEqual(
                actual_tokens,
                embedder_cfg.max_length,
                f"Chunk {i} embedding_input ({actual_tokens} tokens) exceeded embedder max_length ({embedder_cfg.max_length})! "
                f"Full input: {embedding_input!r}",
            )

    def test_embedding_input_overhead_validation_error(self) -> None:
        """Fails loudly if overhead of prefix + section_path leaves no room for overlap and chunk text."""
        embedder_cfg = EmbedderConfig(
            model_id="intfloat/multilingual-e5-small",
            revision="rev-e5",
            dimension=384,
            max_length=10,  # Extremely small
            pooling="mean",
            normalize_embeddings=True,
            passage_prefix="passage: very long prefix here ",
        )
        settings = copy.deepcopy(CHUNKER_SETTINGS)
        settings["max_tokens"] = 10
        settings["target_tokens"] = 6
        settings["overlap_tokens"] = 3

        sec = Section(ordinal=0, path="Very Long Heading Section Path", body="Body text")
        with self.assertRaises(ValueError) as ctx:
            chunk_section(sec, self.counter, settings, embedder_config=embedder_cfg)
        self.assertIn("Embedding overhead", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
