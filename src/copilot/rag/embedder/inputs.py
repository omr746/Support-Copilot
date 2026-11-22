"""Standardized input generation, query preparation, hashing, and PII checks (Step 2.2)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from copilot.rag.models import Chunk, ChunkInput
from copilot.rag.normalize import normalize_search_text

# Basic PII detection patterns for query caching exemption
PII_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PII_PHONE_PATTERN = re.compile(r"(?:\+?20|0)?1[0125]\d{8}\b")
PII_CARD_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
PII_NATIONAL_ID_PATTERN = re.compile(r"\b[23]\d{13}\b")


def build_embedding_input(
    chunk: Chunk | ChunkInput | dict[str, Any],
    embed_text_source: str = "original",
) -> str:
    """Build standardized embedding input string: section path, blank line, and chunk text.

    Args:
        chunk: Chunk, ChunkInput, or dict containing section_path and text.
        embed_text_source: 'original' (raw text with NFC normalization and whitespace cleanup)
                           or 'normalized' (search-normalized text).

    Note: Prefixes (e.g. 'passage: ') are added inside the adapter, NEVER here.
    """
    if isinstance(chunk, Chunk):
        raw_text = chunk.text if embed_text_source == "original" else chunk.normalized_text
        section_path = chunk.section_path or ""
    elif isinstance(chunk, ChunkInput):
        raw_text = chunk.text
        if embed_text_source == "normalized":
            raw_text = normalize_search_text(raw_text)
        section_path = ""
    elif isinstance(chunk, dict):
        raw_text = chunk.get("text", "")
        if embed_text_source == "normalized":
            raw_text = chunk.get("normalized_text") or normalize_search_text(raw_text)
        section_path = chunk.get("section_path", "") or ""
    else:
        raw_text = getattr(chunk, "text", "")
        section_path = getattr(chunk, "section_path", "") or ""
        if embed_text_source == "normalized":
            raw_text = getattr(chunk, "normalized_text", "") or normalize_search_text(raw_text)

    # Basic Unicode normalization (NFC) and whitespace strip for original text
    text_clean = unicodedata.normalize("NFC", raw_text).strip()
    sec_clean = unicodedata.normalize("NFC", section_path).strip()

    if sec_clean:
        return f"{sec_clean}\n\n{text_clean}"
    return text_clean


def prepare_query_text(query: str, embed_text_source: str = "original") -> str:
    """Prepare query text applying identical transforms as documents before embedding."""
    clean = unicodedata.normalize("NFC", query).strip()
    if embed_text_source == "normalized":
        return normalize_search_text(clean)
    return clean


def compute_embedding_input_hash(embedding_input: str) -> str:
    """Compute SHA-256 hash of exact text passed to embedder (before adapter adds prefix)."""
    return hashlib.sha256(embedding_input.encode("utf-8")).hexdigest()


def detect_pii(text: str) -> bool:
    """Detect whether query text contains obvious PII (email, card, phone, national ID)."""
    if PII_EMAIL_PATTERN.search(text):
        return True
    if PII_PHONE_PATTERN.search(text):
        return True
    if PII_CARD_PATTERN.search(text):
        return True
    if PII_NATIONAL_ID_PATTERN.search(text):
        return True
    return False
