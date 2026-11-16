"""Unit tests for search text normalization (Step 2.1).

Verifies strict ordered application of all 10 rules, idempotence across Unicode,
extended combining marks, Arabic decimal/thousands separators, Persian keyboard letters,
and preservation of semantically vital characters (ؤ, ئ).
"""

from __future__ import annotations

import re
import unittest

from copilot.rag.normalize import (
    _SPACES_RE,
    NORMALIZER_VERSION,
    normalize_search_text,
)

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False


class TestNormalizeSearchText(unittest.TestCase):
    """Test suite for normalize_search_text."""

    def test_normalizer_version(self) -> None:
        self.assertEqual(NORMALIZER_VERSION, "ar-normalizer-v1.1")

    def test_spaces_regex_precompiled(self) -> None:
        """Rule 10 regex must be pre-compiled once as a module constant."""
        self.assertIsInstance(_SPACES_RE, re.Pattern)
        self.assertEqual(_SPACES_RE.pattern, r"[ \t]+")

    def test_empty_and_whitespace(self) -> None:
        self.assertEqual(normalize_search_text(""), "")
        self.assertEqual(normalize_search_text("   "), "")
        self.assertEqual(normalize_search_text("\n\n\t  \n"), "\n\n\n")

    def test_rule_1_nfkc_and_newlines(self) -> None:
        # Full-width Latin and \r\n conversion
        text = "Ｈｅｌｌｏ\r\nＷｏｒｌｄ\r!"
        expected = "hello\nworld\n!"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_1_other_line_separators(self) -> None:
        # U+2028 (LSEP), U+2029 (PSEP), \v (VT), \f (FF)
        text = "سطر أول\u2028سطر ثاني\u2029فقرة جديدة\vتفاصيل إضافية\fصفحة جديدة"
        expected = "سطر اول\nسطر ثاني\nفقره جديده\nتفاصيل اضافيه\nصفحه جديده"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_2_invisible_characters(self) -> None:
        # U+FEFF (BOM), U+200B (ZWSP), U+200C (ZWNJ), U+200E (LRM)
        text = "\ufeffمرحبا\u200b\u200c \u200eالعالم"
        expected = "مرحبا العالم"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_3_diacritics_and_tatweel(self) -> None:
        # Tashkeel: fatha, damma, kasra, shadda, sukun, tanween, dagger alef, tatweel
        # كَتَبَ، حُـسْن، هٰذَا
        text = "كَتَبَ، حُــــسْنٌ، هٰذَا"
        expected = "كتب, حسن, هذا"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_3_extended_combining_marks_and_quranic(self) -> None:
        # U+0653-U+065F (maddah, hamza above/below), Quranic marks (U+0610, U+06D6, U+06DF)
        # Stray combining marks on letters
        text = "ك\u0654ت\u0653ب\u0655 م\u0610ح\u06d6م\u06dfد"
        expected = "كتب محمد"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_4_alef_variants(self) -> None:
        # أ, إ, آ, ٱ -> ا
        text = "أحمد وإبراهيم في ألمانيا مع ٱلناس"
        expected = "احمد وابراهيم في المانيا مع الناس"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_5_alef_maksura(self) -> None:
        # ى -> ي
        text = "مستشفى على"
        expected = "مستشفي علي"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_5_persian_keyboard_letters(self) -> None:
        # Persian yeh (ی U+06CC) -> ي (U+064A)
        # Persian keheh (ک U+06A9) -> ك (U+0643)
        text = "کمک به زبان فارسی"
        expected = "كمك به زبان فارسي"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_6_taa_marbuta(self) -> None:
        # ة -> ه
        text = "شركة تقنية جديدة"
        expected = "شركه تقنيه جديده"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_7_digits_arabic_indic_and_persian(self) -> None:
        # Arabic-Indic ٠١٢٣٤ and Persian ۵۶۷۸۹
        text = "سعر ٠١٢٣٤ و ۵۶۷۸۹"
        expected = "سعر 01234 و 56789"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_8_arabic_punctuation_and_separators(self) -> None:
        # ؟ -> ?, ، -> ,, ؛ -> ;
        # Arabic decimal: ٫ (U+066B) -> .
        # Arabic thousands: ٬ (U+066C) -> ,
        # Arabic percent: ٪ (U+066A) -> %
        # Arabic full stop: ۔ (U+06D4) -> .
        text = "سعر الباقة ١٢٠٫٥٠ ج.م، مع خصم ٪١٠، والحد الأقصى ١٬٠٠٠ دقيقة۔ هل توافق؟"
        expected = "سعر الباقه 120.50 ج.م, مع خصم %10, والحد الاقصي 1,000 دقيقه. هل توافق?"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_9_casefold_multilingual(self) -> None:
        # Latin, German ß -> ss, Greek Σ/σ
        text = "Support Copilot In STRAßE With Σύστημα"
        expected = "support copilot in strasse with σύστημα"
        self.assertEqual(normalize_search_text(text), expected)

    def test_rule_10_whitespace_collapse_and_linebreaks(self) -> None:
        text = "  سطر    أول   \n   \n\t\tسطر    ثاني\t\t"
        expected = "سطر اول\n\nسطر ثاني"
        self.assertEqual(normalize_search_text(text), expected)

    def test_hamza_preservation_waw_and_yaa(self) -> None:
        # Rule explicitly: Do not touch ؤ and ئ, and do not remove hamza broadly.
        text = "مؤتمر رئيسي وسؤال وهيئة شؤون"
        expected = "مؤتمر رئيسي وسؤال وهيئه شؤون"
        self.assertEqual(normalize_search_text(text), expected)

    def test_idempotence(self) -> None:
        # normalize(normalize(x)) == normalize(x)
        samples = [
            "أهلاً بِكُمْ فِي Support Copilot! السعر: ١٢٠٫٥٠$؟ ٪١٠",
            "شركة التقنية المتقدمة؛ 2026",
            "مؤتمر ومسؤولية ورئيس\r\n\t\tخطوات عمل جديدة\n",
            "کمک به فارسی و عربی ١٬٠٠٠۔",
            "STRAßE Ωμέγα \u0390 \u1f56 \u2126",
            "line1\u2028line2\u2029line3\vline4\fline5",
            "\ufeffك\u0654ت\u0653ب\u0655 \u200b\u200cم\u0610ح\u06d6م\u06dfد",
        ]
        for s in samples:
            norm1 = normalize_search_text(s)
            norm2 = normalize_search_text(norm1)
            self.assertEqual(norm1, norm2, f"Idempotence failed for sample: {s!r}")

    def test_idempotence_unicode_troublemakers(self) -> None:
        """Verify idempotence on characters whose casefold alters NFKC decomposition."""
        troublemakers = [
            "\xa0",
            "¨",
            "ª",
            "¯",
            "²",
            "³",
            "´",
            "µ",
            "¹",
            "º",
            "¼",
            "½",
            "¾",
            "\u0390",
            "\u1f56",
            "\u2126",
            "\ufb01",
            "\ufb02",
            "\u1e9e",
        ]
        for ch in troublemakers:
            norm1 = normalize_search_text(ch)
            norm2 = normalize_search_text(norm1)
            self.assertEqual(norm1, norm2, f"Idempotence failed for character: {hex(ord(ch))}")

    def test_latin_only_preserves_text_except_casing_and_whitespace(self) -> None:
        text = "  Hello   WORLD   123!   "
        self.assertEqual(normalize_search_text(text), "hello world 123!")


# If hypothesis is installed in the test environment, run property-based idempotence test
if HAS_HYPOTHESIS:

    class TestHypothesisNormalize(unittest.TestCase):
        """Hypothesis property tests for normalization idempotence."""

        @settings(max_examples=200)
        @given(st.text())
        def test_hypothesis_idempotence(self, text: str) -> None:
            norm1 = normalize_search_text(text)
            norm2 = normalize_search_text(norm1)
            self.assertEqual(norm1, norm2)


if __name__ == "__main__":
    unittest.main()
