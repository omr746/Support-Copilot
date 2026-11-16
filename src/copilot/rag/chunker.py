"""Deterministic text chunking with heading preservation, verbatim slicing, and overlap (Step 2.1).

Splits documents by Markdown headings first, preserving sections as hard boundaries.
Within each section, applies recursive slicing with sentence and boundary awareness,
maintaining an overlap window without splitting across headings.
Chunks are exact verbatim character slices of the source text (never synthesized by
dropping separators and re-joining).
Normalizes text per chunk for retrieval while preserving original text for user display.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from typing import Any, Sequence

from copilot.rag.fingerprint import ChunkerConfig, EmbedderConfig, PipelineFingerprint, canonical_json
from copilot.rag.models import Chunk, ChunkInput, PageVersion
from copilot.rag.normalize import NORMALIZER_VERSION, normalize_search_text
from copilot.rag.tokens import TokenCounter

# Default chunker configurations per Step 2.1 specification
CHUNKER_SETTINGS: dict[str, Any] = {
    "splitter": "heading+recursive-v1",
    "max_tokens": 800,
    "target_tokens": 600,
    "overlap_tokens": 80,
    "tokenizer_id": "whitespace@v1",
    "separators": [
        "\n\n",
        r"(?<=\n)(?=[-*+] |\d+\. |\|)",
        "\n",
        r"(?<=[.!?؟])\s+",
        r"(?<=[،;,؛])\s+",
        " ",
    ],
    "section_path_in_embedding": True,
}

_HEADING_REGEX = re.compile(r"^(#{1,6})\s+(.+)$")


@dataclasses.dataclass(frozen=True)
class Section:
    """Logical document section bounded by Markdown headings."""

    ordinal: int
    path: str
    body: str
    char_offset: int = 0


@dataclasses.dataclass(frozen=True)
class ChunkSlice:
    """A verbatim character slice of a document or section."""

    text: str
    char_start: int
    char_end: int
    overlap_chars: int


def format_embedding_input(
    chunk_text: str,
    section_path: str = "",
    passage_prefix: str = "",
    include_section_path: bool = True,
) -> str:
    """Format the exact string that will be passed to the embedder.

    Constructs: [passage_prefix][section_path\\n\\n][chunk_text].
    Used to calculate actual token counts and guarantee no silent model truncation.
    """
    parts: list[str] = []
    if passage_prefix:
        parts.append(passage_prefix)
    if include_section_path and section_path:
        parts.append(f"{section_path}\n\n")
    parts.append(chunk_text)
    return "".join(parts)


def chunk_id(
    *args: Any,
    url_or_filename: str | None = None,
    page_version_id: str | None = None,
    section_ordinal: int | None = None,
    section_path: str | None = None,
    index: int | None = None,
) -> str:
    """Compute deterministic chunk ID derived from URL/filename, version, section ordinal, path, and index.

    Supports 5 args: (url_or_filename, page_version_id, section_ordinal, section_path, index)
    or legacy 4 args: (page_version_id, section_ordinal, section_path, index).
    """
    if len(args) == 5:
        url_or_filename, page_version_id, section_ordinal, section_path, index = args
    elif len(args) == 4:
        url_or_filename = ""
        page_version_id, section_ordinal, section_path, index = args
    elif (
        url_or_filename is not None
        and page_version_id is not None
        and section_ordinal is not None
        and section_path is not None
        and index is not None
    ):
        pass
    else:
        raise TypeError("chunk_id requires either 5 arguments or legacy 4 arguments")

    key = f"{url_or_filename}|{page_version_id}|{section_ordinal}|{section_path}|{index}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def chunk_hash(normalized_text: str, section_path: str) -> str:
    """Compute deterministic chunk content hash over section path and normalized search text."""
    payload = f"{section_path}\n{normalized_text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_sections(text: str, default_title: str = "") -> list[Section]:
    """Parse Markdown headings into sections with hierarchical paths, ordinals, and char offsets.

    Rules:
    - Cleaner marks headings as # to ###### title lines.
    - Heading stack forms 'H1 > H2 > H3' hierarchy.
    - Content before the first heading uses the page title as its path.
    - Headings with no body text produce no section of their own (path remains in stack).
    - Never merge sections. A short section is retained as-is.
    - Preserves verbatim character offsets into the source text.
    """
    if not text or not text.strip():
        return []

    lines = text.split("\n")
    sections: list[Section] = []
    heading_stack: list[tuple[int, str]] = []
    current_path: str = default_title.strip() or "Untitled"
    section_ordinal = 0

    # Calculate exact character start offset for each line
    line_starts: list[int] = []
    pos = 0
    for line in lines:
        line_starts.append(pos)
        pos += len(line) + 1  # accounts for '\n'

    body_line_indices: list[int] = []

    def _flush_section() -> None:
        nonlocal section_ordinal, body_line_indices
        if not body_line_indices:
            return

        start_line_idx = body_line_indices[0]
        end_line_idx = body_line_indices[-1]
        raw_start = line_starts[start_line_idx]
        raw_end = line_starts[end_line_idx] + len(lines[end_line_idx])
        raw_body = text[raw_start:raw_end]

        l_stripped = raw_body.lstrip()
        leading_spaces = len(raw_body) - len(l_stripped)
        stripped = l_stripped.rstrip()

        if stripped:
            body_char_offset = raw_start + leading_spaces
            sections.append(
                Section(
                    ordinal=section_ordinal,
                    path=current_path,
                    body=stripped,
                    char_offset=body_char_offset,
                )
            )
            section_ordinal += 1

        body_line_indices = []

    for idx, line in enumerate(lines):
        match = _HEADING_REGEX.match(line)
        if match:
            _flush_section()

            level = len(match.group(1))
            heading_title = match.group(2).strip()

            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading_title))

            current_path = " > ".join(t for _, t in heading_stack)
        else:
            body_line_indices.append(idx)

    _flush_section()
    return sections


def extract_overlap_tail(text: str, max_overlap_tokens: int, token_counter: TokenCounter) -> str:
    """Extract a clean tail slice from text up to max_overlap_tokens cut at sentence, clause, or whitespace boundaries.

    Guarantees:
    - Never cuts mid-word (snaps to whitespace boundaries).
    - Avoids tiny 1-2 word overlaps by enforcing a minimum boundary threshold before sentence/clause splitting.
    - Never exceeds max_overlap_tokens.
    """
    if not text or max_overlap_tokens <= 0:
        return ""

    spans = token_counter.token_spans(text)
    if not spans:
        return ""

    if len(spans) <= max_overlap_tokens:
        candidate_text = text
        start_char = 0
    else:
        start_char = spans[len(spans) - max_overlap_tokens][0]
        # Snap start_char forward to clean word boundary if starting mid-word
        if start_char > 0 and not text[start_char - 1].isspace() and not text[start_char].isspace():
            next_space = text.find(" ", start_char)
            if next_space != -1 and next_space < len(text) - 1:
                while next_space < len(text) and text[next_space].isspace():
                    next_space += 1
                start_char = next_space
        candidate_text = text[start_char:]

    if not candidate_text:
        return ""

    # Check for sentence boundary (. ! ? ؟ followed by whitespace)
    # Only cut at sentence boundary if remaining tail has sufficient tokens (>= max_overlap_tokens // 2)
    # to avoid collapsing overlap into 1-2 words
    min_tokens = max(1, max_overlap_tokens // 2)

    for m in re.finditer(r"[.!?؟]\s+", candidate_text):
        tail = candidate_text[m.end():]
        tail_tokens = token_counter.count_tokens(tail)
        if min_tokens <= tail_tokens <= max_overlap_tokens:
            return tail

    # Check for clause boundary (، ; ؛ , followed by whitespace)
    for m in re.finditer(r"[،;,؛]\s+", candidate_text):
        tail = candidate_text[m.end():]
        tail_tokens = token_counter.count_tokens(tail)
        if min_tokens <= tail_tokens <= max_overlap_tokens:
            return tail

    # Fallback: candidate_text snapped to word start
    l_strip = len(candidate_text) - len(candidate_text.lstrip())
    clean_tail = candidate_text[l_strip:]

    # Recount check: ensure clean_tail fits within max_overlap_tokens
    while clean_tail and token_counter.count_tokens(clean_tail) > max_overlap_tokens:
        spans_tail = token_counter.token_spans(clean_tail)
        if len(spans_tail) <= 1:
            break
        cut = spans_tail[1][0]
        if cut < len(clean_tail) and not clean_tail[cut - 1].isspace() and not clean_tail[cut].isspace():
            nxt = clean_tail.find(" ", cut)
            if nxt != -1:
                while nxt < len(clean_tail) and clean_tail[nxt].isspace():
                    nxt += 1
                cut = nxt
        clean_tail = clean_tail[cut:]

    return clean_tail


def _split_unbroken_spans(
    text: str,
    start: int,
    end: int,
    max_tokens: int,
    token_counter: TokenCounter,
) -> list[tuple[int, int]]:
    """Slice an unbroken piece (URL, continuous string) strictly by token spans."""
    piece_text = text[start:end]
    spans = token_counter.token_spans(piece_text)
    if not spans or len(spans) <= max_tokens:
        return [(start, end)]

    slices: list[tuple[int, int]] = []
    total_tokens = len(spans)
    start_idx = 0

    while start_idx < total_tokens:
        end_idx = min(start_idx + max_tokens, total_tokens)
        c_start = start + spans[start_idx][0]
        c_end = start + spans[end_idx - 1][1]
        if end_idx == total_tokens:
            c_end = end
        if c_end > c_start:
            slices.append((c_start, c_end))
        start_idx = end_idx

    return slices


def _recursive_slice_boundaries(
    text: str,
    start: int,
    end: int,
    sep_idx: int,
    max_tokens: int,
    token_counter: TokenCounter,
    separators: Sequence[str],
) -> list[tuple[int, int]]:
    """Recursively partition text[start:end] into contiguous sub-slices fitting max_tokens without dropping characters."""
    if start >= end:
        return []

    if token_counter.count_tokens(text[start:end]) <= max_tokens:
        return [(start, end)]

    if sep_idx >= len(separators):
        return _split_unbroken_spans(text, start, end, max_tokens, token_counter)

    current_sep = separators[sep_idx]
    sub_text = text[start:end]
    cut_points = [start]

    if current_sep.startswith("(?<="):
        # Lookaround assertion pattern e.g. (?<=\n)(?=[-*+] |\d+\. |\|) or sentence/clause endings
        for m in re.finditer(current_sep, sub_text):
            cut = start + m.start()
            if cut > cut_points[-1] and cut < end:
                cut_points.append(cut)
    else:
        # Literal string delimiter e.g. "\n\n", "\n", " "
        curr = 0
        while True:
            pos = sub_text.find(current_sep, curr)
            if pos == -1:
                break
            # Cut immediately after the separator so it stays attached to the preceding block
            cut = start + pos + len(current_sep)
            if cut > cut_points[-1] and cut < end:
                cut_points.append(cut)
            curr = pos + len(current_sep)

    cut_points.append(end)

    if len(cut_points) <= 2:
        # Separator didn't match inside, try the next separator
        return _recursive_slice_boundaries(
            text, start, end, sep_idx + 1, max_tokens, token_counter, separators
        )

    result: list[tuple[int, int]] = []
    for i in range(len(cut_points) - 1):
        p_start = cut_points[i]
        p_end = cut_points[i + 1]
        if p_start >= p_end:
            continue
        if token_counter.count_tokens(text[p_start:p_end]) <= max_tokens:
            result.append((p_start, p_end))
        else:
            result.extend(
                _recursive_slice_boundaries(
                    text, p_start, p_end, sep_idx + 1, max_tokens, token_counter, separators
                )
            )

    return result


def _pack_slice_segments(
    text: str,
    spans: list[tuple[int, int]],
    target_tokens: int,
    max_segment_tokens: int,
    token_counter: TokenCounter,
) -> list[tuple[int, int]]:
    """Greedily pack contiguous atomic spans into primary non-overlapping segments up to target_tokens."""
    if not spans:
        return []

    segments: list[tuple[int, int]] = []
    seg_start = spans[0][0]
    seg_end = spans[0][1]

    for next_start, next_end in spans[1:]:
        candidate_tokens = token_counter.count_tokens(text[seg_start:next_end])
        # Strict packing: pack up to target_tokens only
        if candidate_tokens <= target_tokens:
            seg_end = next_end
        else:
            segments.append((seg_start, seg_end))
            seg_start = next_start
            seg_end = next_end

    segments.append((seg_start, seg_end))
    return segments


def chunk_section_slices(
    section: Section,
    token_counter: TokenCounter,
    chunker_config: ChunkerConfig | dict[str, Any] | None = None,
    embedder_config: EmbedderConfig | None = None,
) -> list[ChunkSlice]:
    """Produce verbatim ChunkSlices for a single section with overlap respecting limits.

    Guarantees:
    - Never crosses headings.
    - Chunks are exact verbatim slices of section.body.
    - Exact embedding budget: accounts for embedder_config.passage_prefix and section_path.
    - First chunk has overlap_chars=0.
    - Subsequent chunks contain overlap from previous chunk's tail.
    - Recounts formatted embedding input to guarantee token_counter capacity is strictly respected.
    """
    body = section.body
    if not body or not body.strip():
        return []

    raw_max = (
        chunker_config.get("max_tokens", CHUNKER_SETTINGS["max_tokens"])
        if isinstance(chunker_config, dict)
        else (chunker_config.max_tokens if chunker_config else CHUNKER_SETTINGS["max_tokens"])
    )
    if raw_max > token_counter.max_input_tokens:
        raise ValueError(
            f"Chunker max_tokens ({raw_max}) exceeds token counter max_input_tokens "
            f"({token_counter.max_input_tokens}). Text would be silently truncated by the embedder."
        )

    if isinstance(chunker_config, ChunkerConfig):
        cfg = chunker_config
    elif isinstance(chunker_config, dict):
        merged = dict(CHUNKER_SETTINGS)
        merged.update(chunker_config)
        merged["tokenizer_id"] = token_counter.tokenizer_id
        # If max_tokens was lowered without adjusting target/overlap, adjust them to maintain invariant
        if merged["target_tokens"] + merged["overlap_tokens"] > merged["max_tokens"]:
            merged["target_tokens"] = max(1, int(merged["max_tokens"] * 0.75))
            merged["overlap_tokens"] = max(0, merged["max_tokens"] - merged["target_tokens"] - 1)
        cfg = ChunkerConfig.from_dict(merged)
    else:
        merged = dict(CHUNKER_SETTINGS)
        merged["tokenizer_id"] = token_counter.tokenizer_id
        cfg = ChunkerConfig.from_dict(merged)

    # Cross-check tokenizer
    if cfg.tokenizer_id != token_counter.tokenizer_id:
        raise ValueError(
            f"ChunkerConfig tokenizer_id ({cfg.tokenizer_id}) does not match "
            f"TokenCounter tokenizer_id ({token_counter.tokenizer_id})"
        )

    if cfg.max_tokens > token_counter.max_input_tokens:
        raise ValueError(
            f"Chunker max_tokens ({cfg.max_tokens}) exceeds token counter max_input_tokens "
            f"({token_counter.max_input_tokens}). Text would be silently truncated by the embedder."
        )

    # 1. Resolve limits and prefixes for actual embedding input
    passage_prefix = ""
    if embedder_config is not None:
        passage_prefix = embedder_config.passage_prefix
    elif isinstance(chunker_config, dict) and "passage_prefix" in chunker_config:
        passage_prefix = str(chunker_config["passage_prefix"])

    max_limit = token_counter.max_input_tokens
    if embedder_config is not None:
        max_limit = min(max_limit, embedder_config.max_length)
    max_limit = min(cfg.max_tokens, max_limit)

    def _embedding_text(chunk_text: str) -> str:
        return format_embedding_input(
            chunk_text,
            section_path=section.path,
            passage_prefix=passage_prefix,
            include_section_path=cfg.section_path_in_embedding,
        )

    def _count_embedding_tokens(chunk_text: str) -> int:
        return token_counter.count_tokens(_embedding_text(chunk_text))

    # 2. Section path and passage prefix budgeting
    overhead_tokens = token_counter.count_tokens(_embedding_text(""))
    effective_max_tokens = max_limit - overhead_tokens
    if effective_max_tokens <= cfg.overlap_tokens:
        raise ValueError(
            f"Embedding overhead (prefix={passage_prefix!r}, path={section.path!r}) consumes "
            f"{overhead_tokens} tokens, leaving {effective_max_tokens} which is <= overlap_tokens ({cfg.overlap_tokens}) "
            f"within limit {max_limit}."
        )

    # If section body fits entirely within effective_max_tokens when embedded, emit single chunk
    if _count_embedding_tokens(body) <= max_limit:
        return [
            ChunkSlice(
                text=body,
                char_start=section.char_offset,
                char_end=section.char_offset + len(body),
                overlap_chars=0,
            )
        ]

    max_segment_tokens = max(1, effective_max_tokens - cfg.overlap_tokens)
    target_pack_tokens = min(cfg.target_tokens, max_segment_tokens)

    # 1. Recursively partition body into contiguous atomic spans
    atomic_spans = _recursive_slice_boundaries(
        body, 0, len(body), 0, max_segment_tokens, token_counter, cfg.separators
    )

    # 2. Greedily pack atomic spans into primary non-overlapping segments
    primary_segments = _pack_slice_segments(
        body, atomic_spans, target_pack_tokens, max_segment_tokens, token_counter
    )

    if not primary_segments:
        return []

    slices: list[ChunkSlice] = []
    for i, (p_start, p_end) in enumerate(primary_segments):
        if i == 0:
            seg_text = body[p_start:p_end]
            if _count_embedding_tokens(seg_text) > max_limit:
                unbroken = _split_unbroken_spans(
                    body, p_start, p_end, effective_max_tokens, token_counter
                )
                for u_start, u_end in unbroken:
                    slices.append(
                        ChunkSlice(
                            text=body[u_start:u_end],
                            char_start=section.char_offset + u_start,
                            char_end=section.char_offset + u_end,
                            overlap_chars=0,
                        )
                    )
            else:
                slices.append(
                    ChunkSlice(
                        text=seg_text,
                        char_start=section.char_offset + p_start,
                        char_end=section.char_offset + p_end,
                        overlap_chars=0,
                    )
                )
        else:
            prev_start, prev_end = primary_segments[i - 1]
            prev_seg_text = body[prev_start:prev_end]
            overlap_tail = extract_overlap_tail(prev_seg_text, cfg.overlap_tokens, token_counter)

            if overlap_tail:
                overlap_start = prev_end - len(overlap_tail)
                overlap_chars = len(overlap_tail)
                candidate_text = body[overlap_start:p_end]

                # Recount check on full embedding input: non-additive tokenizers shrink overlap if over budget
                while (
                    overlap_start < p_start
                    and _count_embedding_tokens(candidate_text) > max_limit
                ):
                    tail_spans = token_counter.token_spans(body[overlap_start:prev_end])
                    if len(tail_spans) <= 1:
                        overlap_start = p_start
                        overlap_chars = 0
                        break
                    cut = tail_spans[1][0]
                    overlap_start += cut
                    overlap_chars = max(0, prev_end - overlap_start)
                    candidate_text = body[overlap_start:p_end]

                if _count_embedding_tokens(candidate_text) > max_limit:
                    unbroken = _split_unbroken_spans(
                        body, overlap_start, p_end, effective_max_tokens, token_counter
                    )
                    for idx_u, (u_start, u_end) in enumerate(unbroken):
                        u_overlap = overlap_chars if idx_u == 0 else 0
                        slices.append(
                            ChunkSlice(
                                text=body[u_start:u_end],
                                char_start=section.char_offset + u_start,
                                char_end=section.char_offset + u_end,
                                overlap_chars=u_overlap,
                            )
                        )
                else:
                    slices.append(
                        ChunkSlice(
                            text=candidate_text,
                            char_start=section.char_offset + overlap_start,
                            char_end=section.char_offset + p_end,
                            overlap_chars=overlap_chars,
                        )
                    )
            else:
                seg_text = body[p_start:p_end]
                if _count_embedding_tokens(seg_text) > max_limit:
                    unbroken = _split_unbroken_spans(
                        body, p_start, p_end, effective_max_tokens, token_counter
                    )
                    for u_start, u_end in unbroken:
                        slices.append(
                            ChunkSlice(
                                text=body[u_start:u_end],
                                char_start=section.char_offset + u_start,
                                char_end=section.char_offset + u_end,
                                overlap_chars=0,
                            )
                        )
                else:
                    slices.append(
                        ChunkSlice(
                            text=seg_text,
                            char_start=section.char_offset + p_start,
                            char_end=section.char_offset + p_end,
                            overlap_chars=0,
                        )
                    )

    return slices


def chunk_section(
    section: Section,
    token_counter: TokenCounter,
    chunker_settings: ChunkerConfig | dict[str, Any] | None = None,
    embedder_config: EmbedderConfig | None = None,
) -> list[str]:
    """Produce raw text chunks for a single section with overlap respecting limits.

    Convenience wrapper over chunk_section_slices returning list[str] for backward compatibility.
    """
    slices = chunk_section_slices(
        section, token_counter, chunker_settings, embedder_config=embedder_config
    )
    return [s.text for s in slices]


def chunk_page(
    page: PageVersion | ChunkInput | Any,
    token_counter: TokenCounter,
    chunker_settings: ChunkerConfig | dict[str, Any] | None = None,
    source_type: str = "builtin",
    owner_id: str | None = None,
    source_id: str = "",
    embedder_config: EmbedderConfig | None = None,
) -> list[Chunk]:
    """Chunk a document page into deterministic, normalized Chunk records (Step 2.1).

    Workflow:
    1. Validate token_counter presence and verify max_tokens <= max_input_tokens.
    2. Convert PageVersion to ChunkInput; skip removed, JS-required, or non-200 pages.
    3. Validate source_type and owner_id invariants.
    4. Parse Markdown headings into sections with hierarchical paths and offsets.
    5. Chunk original text verbatim (never normalize before chunking).
    6. Normalize each chunk independently for search matching; skip empty normalized text.
    7. Formats embedding input (prefix + section_path + text) and strictly validates actual tokens.
    8. Derive deterministic chunk IDs and hashes.
    """
    if token_counter is None:
        raise ValueError(
            "token_counter is required. In unit tests pass WhitespaceTokenCounter() explicitly. "
            "In production pass the real embedder tokenizer. Never silently fallback to whitespace counting."
        )

    raw_max = (
        chunker_settings.get("max_tokens", CHUNKER_SETTINGS["max_tokens"])
        if isinstance(chunker_settings, dict)
        else (chunker_settings.max_tokens if chunker_settings else CHUNKER_SETTINGS["max_tokens"])
    )
    if raw_max > token_counter.max_input_tokens:
        raise ValueError(
            f"Chunker max_tokens ({raw_max}) exceeds token counter max_input_tokens "
            f"({token_counter.max_input_tokens}). Text would be silently truncated by the embedder."
        )

    # 1. Resolve typed ChunkInput
    if isinstance(page, PageVersion):
        chunk_input = ChunkInput.from_page_version(page)
        if chunk_input is None:
            return []
        chunk_input = dataclasses.replace(
            chunk_input,
            source_type=source_type,
            owner_id=owner_id,
            source_id=source_id or chunk_input.source_id,
        )
    elif isinstance(page, ChunkInput):
        chunk_input = page
    else:
        # Support duck typing
        is_removed = getattr(page, "is_removed", False)
        needs_js = getattr(page, "needs_js", False)
        http_status = getattr(page, "http_status", 200)
        if is_removed or needs_js or http_status != 200:
            return []

        url_or_filename = (
            getattr(page, "url", "")
            or getattr(page, "canonical_url", "")
            or getattr(page, "url_or_filename", "")
        )
        content_hash = getattr(page, "content_hash", "")
        pvid = getattr(page, "page_version_id", "") or content_hash or url_or_filename
        text = getattr(page, "cleaned_text", "") or getattr(page, "text", "")

        chunk_input = ChunkInput(
            source_type=source_type,
            owner_id=owner_id,
            source_id=source_id or url_or_filename,
            page_version_id=pvid,
            url_or_filename=url_or_filename,
            title=getattr(page, "title", "") or "",
            language=getattr(page, "language", "") or "und",
            fetched_at=getattr(page, "fetched_at", "") or "",
            text=text,
        )

    # 2. Source type and owner validation
    if chunk_input.source_type == "builtin":
        if chunk_input.owner_id is not None:
            raise ValueError("owner_id must be None for 'builtin' source type")
    elif chunk_input.source_type == "upload":
        if chunk_input.owner_id is None:
            raise ValueError(f"owner_id is required for source type {chunk_input.source_type!r}")

    # 3. Resolve ChunkerConfig
    if isinstance(chunker_settings, ChunkerConfig):
        cfg = chunker_settings
    elif isinstance(chunker_settings, dict):
        merged = dict(CHUNKER_SETTINGS)
        merged.update(chunker_settings)
        merged["tokenizer_id"] = token_counter.tokenizer_id
        if merged["target_tokens"] + merged["overlap_tokens"] > merged["max_tokens"]:
            merged["target_tokens"] = max(1, int(merged["max_tokens"] * 0.75))
            merged["overlap_tokens"] = max(0, merged["max_tokens"] - merged["target_tokens"] - 1)
        cfg = ChunkerConfig.from_dict(merged)
    else:
        merged = dict(CHUNKER_SETTINGS)
        merged["tokenizer_id"] = token_counter.tokenizer_id
        cfg = ChunkerConfig.from_dict(merged)

    if cfg.tokenizer_id != token_counter.tokenizer_id:
        raise ValueError(
            f"ChunkerConfig tokenizer_id ({cfg.tokenizer_id}) does not match "
            f"TokenCounter tokenizer_id ({token_counter.tokenizer_id})"
        )

    # Resolve limits and passage prefix
    passage_prefix = ""
    if embedder_config is not None:
        passage_prefix = embedder_config.passage_prefix
    elif isinstance(chunker_settings, dict) and "passage_prefix" in chunker_settings:
        passage_prefix = str(chunker_settings["passage_prefix"])

    max_limit = token_counter.max_input_tokens
    if embedder_config is not None:
        max_limit = min(max_limit, embedder_config.max_length)
    max_limit = min(cfg.max_tokens, max_limit)

    raw_text = chunk_input.text
    if not raw_text or not raw_text.strip():
        return []

    # 4. Parse sections bounded by headings
    sections = parse_sections(raw_text, default_title=chunk_input.title)
    if not sections:
        return []

    # 5. Compute unified chunking fingerprint digest
    cleaner_version = getattr(page, "cleaner_version", "v1") or "v1"
    fp = PipelineFingerprint(
        cleaner_version=cleaner_version,
        normalizer_version=NORMALIZER_VERSION,
        chunker_config=cfg,
        injection_pattern_version="v1",
        embedder_config=embedder_config,
    )
    chunking_digest = fp.chunking_digest

    all_chunks: list[Chunk] = []
    global_index = 0

    for section in sections:
        slices = chunk_section_slices(
            section, token_counter, cfg, embedder_config=embedder_config
        )
        for s in slices:
            norm_text = normalize_search_text(s.text)
            # Skip chunks where normalized search text is empty (e.g. diacritics/whitespace only)
            if not norm_text.strip():
                continue

            # Exact runtime calculation & verification: embedding_input = passage_prefix + section_path + chunk.text
            embedding_input = format_embedding_input(
                s.text,
                section_path=section.path,
                passage_prefix=passage_prefix,
                include_section_path=cfg.section_path_in_embedding,
            )
            actual_tokens = token_counter.count_tokens(embedding_input)
            if actual_tokens > max_limit:
                raise ValueError(
                    f"Embedding input ({actual_tokens} tokens) exceeds model token limit ({max_limit})"
                )

            chunk_tokens = token_counter.count_tokens(s.text)
            cid = chunk_id(
                chunk_input.url_or_filename,
                chunk_input.page_version_id,
                section.ordinal,
                section.path,
                global_index,
            )
            chash = chunk_hash(
                normalized_text=norm_text,
                section_path=section.path,
            )

            all_chunks.append(
                Chunk(
                    id=cid,
                    chunk_hash=chash,
                    index=global_index,
                    source_type=chunk_input.source_type,
                    owner_id=chunk_input.owner_id,
                    source_id=chunk_input.source_id,
                    page_version_id=chunk_input.page_version_id,
                    url_or_filename=chunk_input.url_or_filename,
                    title=chunk_input.title,
                    fetched_at=chunk_input.fetched_at,
                    section_path=section.path,
                    language=chunk_input.language or "und",
                    text=s.text,
                    normalized_text=norm_text,
                    token_count=chunk_tokens,
                    normalizer_version=NORMALIZER_VERSION,
                    chunking_digest=chunking_digest,
                    char_start=s.char_start,
                    char_end=s.char_end,
                    overlap_chars=s.overlap_chars,
                )
            )
            global_index += 1

    return all_chunks
