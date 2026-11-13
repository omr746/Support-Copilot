"""HTML text cleaning, language detection, boilerplate removal, and content hashing.

Note: Canonical implementation is now in `copilot.rag.crawler.cleaner`.
This module re-exports symbols for backward compatibility.
"""

from copilot.rag.crawler.cleaner import (
    ARABIC_RE,
    BOILERPLATE_PATTERNS,
    BOILERPLATE_TAGS,
    CLEANER_VERSION,
    HAS_TRAFILATURA,
    LATIN_RE,
    CleanedPage,
    clean_html,
    compute_content_hash,
    detect_language,
    is_javascript_shell,
    normalize_whitespace,
)

__all__ = [
    "ARABIC_RE",
    "BOILERPLATE_PATTERNS",
    "BOILERPLATE_TAGS",
    "CLEANER_VERSION",
    "HAS_TRAFILATURA",
    "LATIN_RE",
    "CleanedPage",
    "clean_html",
    "compute_content_hash",
    "detect_language",
    "is_javascript_shell",
    "normalize_whitespace",
]
