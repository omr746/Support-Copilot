"""Text normalizer for search and retrieval matching (Step 2.1).

Rules applied in strict deterministic order:
1. Normalize line separators (\r\n, \r, \u2028, \u2029, \v, \f -> \n) and apply Unicode NFKC.
2. Delete invisible Unicode characters (BOM, zero-width spaces/joiners, bidi marks/embeddings/isolates).
3. Delete Arabic diacritics (tashkeel U+064B-U+065F), Quranic annotation marks (U+0610-U+061A, U+06D6-U+06ED),
   superscript alef (U+0670), and tatweel (kashida U+0640).
4. Map alef variants (أ, إ, آ, ٱ) to bare alef (ا).
5. Map alef maksura (ى) and Persian yeh (ی) to yaa (ي), and Persian keheh (ک) to kaf (ك).
6. Map taa marbuta (ة) to haa (ه).
7. Map Arabic-Indic (٠-٩) and Persian (۰-۹) digits to ASCII (0-9).
8. Map Arabic punctuation and separators to ASCII (؟ -> ?, ، -> ,, ؛ -> ;, ٫ -> ., ٬ -> ,, ٪ -> %, ۔ -> .).
9. Casefold characters and re-apply Unicode NFKC for full idempotence.
10. Collapse runs of spaces and tabs to one space and strip each line, preserving line breaks.

Guarantees:
- Idempotence: normalize(normalize(x)) == normalize(x) across all Unicode inputs.
- Preserves hamza on waw (ؤ) and yaa (ئ).
- Pure function with constant pre-compiled lookup tables.
"""

from __future__ import annotations

import re
import unicodedata

NORMALIZER_VERSION: str = "ar-normalizer-v1.1"

# Rule 1: Line separators (CRLF, CR, LSEP, PSEP, vertical tab, form feed)
_NEWLINES_RE = re.compile(r"\r\n|[\r\v\f\u2028\u2029]")

# Rule 2: Invisible characters
# U+200B-U+200F: Zero-width space, joiners, directional marks
# U+202A-U+202E: Directional embeddings, overrides, pop directional
# U+2066-U+2069: Directional isolates
# U+FEFF: Byte Order Mark / Zero-width no-break space
_INVISIBLE_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2066-\u2069\uFEFF]")

# Rule 3: Arabic diacritics, combining marks, Quranic annotations & tatweel
# U+0610-U+061A: Quranic annotation signs (sallallahou alayhi wasallam, etc.)
# U+064B-U+0652: Tashkeel (fathatan, dammatan, kasratan, fatha, damma, kasra, shadda, sukun)
# U+0653-U+065F: Combining marks (maddah, hamza above/below, inverted damma, etc.)
# U+0670: Superscript Alef (dagger alef)
# U+06D6-U+06ED: Quranic annotations and ligatures
# U+0640: Tatweel (kashida)
_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")

# Rule 4: Alef variants -> bare alef (ا U+0627)
# أ (U+0623), إ (U+0625), آ (U+0622), ٱ (U+0671)
_ALEF_RE = re.compile(r"[\u0622\u0623\u0625\u0671]")

# Rule 5 & 6: Alef maksura (ى), Persian yeh (ی) -> yaa (ي); Persian keheh (ک) -> kaf (ك); taa marbuta (ة) -> haa (ه)
_LETTERS_TRANS = str.maketrans({
    "ى": "ي",
    "ی": "ي",
    "ک": "ك",
    "ة": "ه",
})

# Rule 7: Arabic-Indic & Persian digits -> ASCII digits
_DIGITS_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Rule 8: Arabic punctuation, decimal, thousands separators & percent signs -> ASCII
_PUNCT_TRANS = str.maketrans({
    "؟": "?",
    "،": ",",
    "؛": ";",
    "٫": ".",  # Arabic decimal separator (U+066B)
    "٬": ",",  # Arabic thousands separator (U+066C)
    "٪": "%",  # Arabic percent sign (U+066A)
    "۔": ".",  # Arabic full stop (U+06D4)
})

# Rule 10: Horizontal whitespace runs (compiled once at module level for optimal performance)
_SPACES_RE = re.compile(r"[ \t]+")


def normalize_search_text(text: str) -> str:
    """Normalize text for search indexing and retrieval matching.

    Operates strictly on search/embedding representations. The original
    display text is never modified.
    """
    if not text:
        return ""

    # Rule 1: Normalize all line separators to \n and apply Unicode NFKC
    t = _NEWLINES_RE.sub("\n", text)
    t = unicodedata.normalize("NFKC", t)

    # Rule 2: Delete invisible characters
    t = _INVISIBLE_RE.sub("", t)

    # Rule 3: Delete Arabic diacritics, combining marks, Quranic signs, and tatweel
    t = _DIACRITICS_RE.sub("", t)

    # Rule 4: Map alef variants to bare alef
    t = _ALEF_RE.sub("ا", t)

    # Rule 5 & 6: Map alef maksura / Persian yeh, Persian keheh, and taa marbuta
    t = t.translate(_LETTERS_TRANS)

    # Rule 7: Map Arabic-Indic and Persian digits to ASCII
    t = t.translate(_DIGITS_TRANS)

    # Rule 8: Map Arabic punctuation, decimals, thousands separators, and percent signs to ASCII
    t = t.translate(_PUNCT_TRANS)

    # Rule 9: Casefold characters, followed by NFKC to guarantee idempotence across all Unicode
    t = unicodedata.normalize("NFKC", t.casefold())

    # Rule 10: Collapse runs of spaces/tabs to one space and strip each line. Keep line breaks.
    lines = [_SPACES_RE.sub(" ", line).strip() for line in t.split("\n")]
    return "\n".join(lines)
