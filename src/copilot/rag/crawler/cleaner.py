"""HTML text cleaning, language detection, boilerplate removal, and content hashing.

Supports Trafilatura for primary extraction with an Arabic-aware BeautifulSoup
fallback engine for robust zero-dependency text extraction and normalization.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment

from copilot.rag.crawler.url_utils import is_allowed_url, normalize_url

if TYPE_CHECKING:
    from copilot.rag.crawler.config import CrawlConfig

logger = logging.getLogger(__name__)

try:
    import trafilatura

    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False


def _resolve_cleaner_version() -> str:
    """Resolve the true cleaner version string reflecting library and rule revisions."""
    bs4_rule_version = "bs4-v1.0"
    if HAS_TRAFILATURA:
        try:
            traf_ver = version("trafilatura")
        except (PackageNotFoundError, Exception):
            traf_ver = "unknown"
        return f"trafilatura-{traf_ver}+{bs4_rule_version}"
    return f"bs4-only+{bs4_rule_version}"


CLEANER_VERSION = _resolve_cleaner_version()

# Arabic Unicode range regex (covers basic Arabic, supplement, extended, and presentation forms)
ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
# Latin characters including standard, Latin-1 Supplement, and Latin Extended
LATIN_RE = re.compile(r"[a-zA-Z\u00C0-\u024F]")

# Tags that represent pure boilerplate, scripting, or navigation
BOILERPLATE_TAGS = {
    "nav",
    "footer",
    "header",
    "script",
    "style",
    "aside",
    "noscript",
    "iframe",
    "svg",
    "button",
    "form",
}

# CSS class or id keywords that represent cookie banners, popups, and boilerplate
BOILERPLATE_PATTERNS = re.compile(
    r"(cookie|banner|consent|popup|modal|advert|adsense|nav|menu|footer|social|share|widget|copyright|timestamp)",
    re.IGNORECASE,
)

# SPA mount elements and client-side app indicators
SPA_MOUNT_PATTERNS = re.compile(
    r'id=[\'"](?:root|app|__next|__nuxt|main-view|application)[\'"]',
    re.IGNORECASE,
)
JS_REQUIRED_PHRASES = (
    "you need to enable javascript",
    "javascript is required",
    "enable javascript to run this app",
    "please enable javascript",
    "javascript must be enabled",
    "يتطلب هذا الموقع تفعيل الجافا سكريبت",
    "يرجى تفعيل الجافاسكريبت",
)
SPA_GENERIC_LOADERS = (
    "loading",
    "loading...",
    "loading application",
    "please wait",
    "جاري التحميل",
    "يرجى الانتظار",
)

# Common sensitive/private path prefixes to reject when config allowlist is not provided
DEFAULT_DENIED_CANONICAL_PATHS = re.compile(
    r"^/(login|cart|checkout|admin|account|auth|signin|signup|portal|dashboard)(/|\?|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CleanedPage:
    """Output of HTML cleaning."""

    cleaned_text: str
    canonical_url: str
    title: str
    language: str
    content_hash: str
    cleaner_version: str
    needs_js: bool

    @property
    def text(self) -> str:
        """Alias for cleaned_text."""
        return self.cleaned_text


# Backward compatibility alias
CleanedDocument = CleanedPage


def normalize_whitespace(text: str) -> str:
    """Normalize repeated whitespace, tabs, and newlines to a single clean space."""
    return re.sub(r"\s+", " ", text).strip()


def compute_content_hash(cleaned_text: str) -> str:
    """Compute SHA-256 content hash strictly on cleaned main text after whitespace normalization.

    Rule: Never hash raw HTML. Only hash normalized cleaned text.
    """
    normalized = normalize_whitespace(cleaned_text)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def detect_language(text: str, html_lang: str | None = None) -> str:
    """Classify the dominant language of the cleaned text into 'ar', 'en', or 'und'.

    Uses a hybrid approach:
    1. If an explicit `<html lang="...">` tag is present and aligns with content, it provides a strong prior.
    2. Character distribution: counts Arabic vs Latin characters with Arabic density weighting
       (Arabic text conveys more content per character than Latin text).
    """
    cleaned = text.strip()
    if not cleaned:
        return "und"

    arabic_chars = len(ARABIC_RE.findall(cleaned))
    latin_chars = len(LATIN_RE.findall(cleaned))

    if arabic_chars == 0 and latin_chars == 0:
        return "und"

    # Prioritize HTML lang hint if valid and supported by text content
    if html_lang:
        primary_lang = html_lang.split("-")[0].strip().lower()
        if primary_lang == "ar" and arabic_chars > 0:
            return "ar"
        if primary_lang == "en" and arabic_chars == 0:
            return "en"

    # In bilingual Arabic technical pages (e.g. telecom with "Wi-Fi", "Router", "Gigabytes"),
    # Latin technical terms appear frequently. If Arabic characters represent >= 25% of letters,
    # or >= latin_chars, the page is classified as Arabic.
    total_letters = arabic_chars + latin_chars
    if arabic_chars >= latin_chars or (
        total_letters > 0 and (arabic_chars / total_letters) >= 0.25
    ):
        return "ar"

    return "en"


def is_javascript_shell(
    cleaned_text: str,
    raw_html: str,
    *,
    body_text: str | None = None,
) -> bool:
    """Check if the HTML is a JavaScript-only shell (empty without JS).

    Multi-signal detection:
    - Checks for SPA mount points (`id="root"`, `id="app"`, etc.) with minimal initial text.
    - Checks for explicit noscript warnings ("You need to enable JavaScript...").
    - Distinguishes real short pages (e.g. "Contact us") from empty SPA shells.
    """
    normalized = normalize_whitespace(cleaned_text)
    raw_lower = raw_html.lower()

    has_js_required_notice = any(phrase in raw_lower for phrase in JS_REQUIRED_PHRASES)
    has_spa_mount = bool(SPA_MOUNT_PATTERNS.search(raw_html))

    # Signal 1: Explicit noscript or JS required warning with no substantial content
    if has_js_required_notice and len(normalized) < 80:
        return True

    # Signal 2: SPA mount element with practically no content or only generic loader string
    if has_spa_mount:
        if len(normalized) == 0:
            return True
        if len(normalized) < 40:
            norm_lower = normalized.lower()
            if any(loader in norm_lower for loader in SPA_GENERIC_LOADERS):
                return True
            if body_text is not None and len(body_text.strip()) < 30:
                return True

    # Signal 3: If practically empty text survived and body text is trivial
    if len(normalized) < 15:
        if body_text is not None and len(body_text.strip()) < 15:
            if "<script" in raw_lower:
                return True

    return False


def _extract_canonical_and_title(
    soup: BeautifulSoup,
    fallback_url: str,
    config: CrawlConfig | None = None,
) -> tuple[str, str]:
    """Extract canonical URL and page title from HTML `<head>` with allowlist validation."""
    canonical_url = fallback_url
    title = ""

    # 1. Canonical tag
    canonical_tag = soup.find("link", rel=lambda val: val and "canonical" in val.lower())  # type: ignore[arg-type]
    if canonical_tag and canonical_tag.get("href"):  # type: ignore[union-attr]
        raw_canonical = str(canonical_tag.get("href")).strip()  # type: ignore[union-attr]
        # Resolve relative canonical URLs
        resolved = urljoin(fallback_url, raw_canonical)
        norm_canonical = normalize_url(resolved)

        # Validate canonical URL against allowlist if config is provided
        if config is not None:
            allowed, reason = is_allowed_url(norm_canonical, config)
            if allowed:
                canonical_url = norm_canonical
            else:
                logger.debug(
                    "Extracted canonical URL '%s' rejected by allowlist (%s); falling back to '%s'",
                    norm_canonical,
                    reason,
                    fallback_url,
                )
                canonical_url = fallback_url
        else:
            # Fallback validation: ensure scheme is http/https, same host, and not a sensitive path
            norm_parts = urlsplit(norm_canonical)
            fall_parts = urlsplit(fallback_url)
            same_host = (
                norm_parts.hostname
                and norm_parts.hostname.lower() == (fall_parts.hostname or "").lower()
            )
            valid_scheme = norm_parts.scheme in ("http", "https")
            denied_path = bool(DEFAULT_DENIED_CANONICAL_PATHS.search(norm_parts.path or "/"))

            if valid_scheme and same_host and not denied_path:
                canonical_url = norm_canonical
            else:
                logger.debug(
                    "Extracted canonical URL '%s' rejected by fallback validation; falling back to '%s'",
                    norm_canonical,
                    fallback_url,
                )
                canonical_url = fallback_url

    # 2. Title tag
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        title = normalize_whitespace(title_tag.string)
    elif soup.find("h1"):
        h1 = soup.find("h1")
        if h1 and h1.get_text():
            title = normalize_whitespace(h1.get_text())

    return canonical_url, title


def _clean_with_beautifulsoup(soup: BeautifulSoup) -> str:
    """Arabic-aware BeautifulSoup fallback text extractor."""
    # 1. Remove comments
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    # 2. Remove boilerplate tags
    for tag in soup.find_all(list(BOILERPLATE_TAGS)):
        tag.decompose()

    # 3. Remove elements matching boilerplate classes or IDs
    for el in soup.find_all(class_=BOILERPLATE_PATTERNS) + soup.find_all(id=BOILERPLATE_PATTERNS):
        el.decompose()

    # 4. Target main content area if available
    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(role="main")
        or soup.find(id=re.compile(r"content|main", re.I))
        or soup.find("body")
        or soup
    )

    # 5. Extract structural blocks (headings, paragraphs, lists, tables)
    blocks: list[str] = []

    for elem in main_content.find_all(  # type: ignore[union-attr]
        ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr"]
    ):
        text = elem.get_text(separator=" ", strip=True)
        if text:
            blocks.append(text)

    if blocks:
        return "\n\n".join(blocks)

    return main_content.get_text(separator="\n", strip=True)  # type: ignore[union-attr]


def clean_html(
    raw_html: str,
    url: str = "",
    *,
    base_url: str = "",
    config: CrawlConfig | None = None,
) -> CleanedPage:
    """Clean HTML and extract main text, canonical URL, title, and language.

    Performance & Safety:
    - Parses HTML with BeautifulSoup exactly ONCE (zero repeated parsing allocations).
    - Drops boilerplate (navigation, footers, headers, scripts, styles, cookies, banners).
    - Preserves headings, lists, tables as text.
    - Preserves Arabic text and digits (e.g. ١٢٠).
    - Validates canonical URLs against allowlist and default sensitive path guards.
    - Multi-signal identification of JavaScript shells as needs_js=True.
    - Computes content hash on cleaned text (never raw HTML).
    """
    target_url = url or base_url or "https://www.te.eg"

    # Single-pass HTML parsing
    soup = BeautifulSoup(raw_html, "html.parser")

    # 1. Extract lang hint from <html lang="...">
    html_tag = soup.find("html")
    html_lang = (
        str(html_tag.get("lang"))
        if html_tag and hasattr(html_tag, "get") and html_tag.get("lang")
        else None
    )

    # 2. Extract canonical and title (does NOT mutate soup)
    canonical_url, title = _extract_canonical_and_title(soup, target_url, config=config)

    # 3. Capture body text statistics before any mutation
    body = soup.find("body")
    body_text = body.get_text(separator=" ", strip=True) if body else ""

    cleaned_text = ""

    # 4. Primary extraction via Trafilatura if available
    if HAS_TRAFILATURA:
        try:
            extracted = trafilatura.extract(
                raw_html,
                url=canonical_url,
                include_tables=True,
                include_links=False,
                favor_recall=True,
                output_format="txt",
            )
            if extracted and len(extracted.strip()) > 30:
                cleaned_text = extracted.strip()
        except Exception as err:
            logger.debug(
                "Trafilatura extraction failed on %s: %s. Using BeautifulSoup fallback.",
                url,
                err,
            )

    # 5. Fallback to BeautifulSoup extractor
    # Mutates the existing `soup` directly WITHOUT reparsing raw_html
    if not cleaned_text:
        cleaned_text = _clean_with_beautifulsoup(soup)

    # 6. Check for JavaScript-only shell using multi-signal analysis (zero reparsing)
    needs_js = is_javascript_shell(cleaned_text, raw_html, body_text=body_text)

    # 7. Language classification with HTML lang tag hint and Arabic density weighting
    language = detect_language(cleaned_text, html_lang=html_lang)

    # 8. Compute content hash on normalized cleaned text (empty for needs_js)
    content_hash = "" if needs_js else compute_content_hash(cleaned_text)

    return CleanedPage(
        cleaned_text=cleaned_text if not needs_js else "",
        canonical_url=canonical_url,
        title=title,
        language=language,
        content_hash=content_hash,
        cleaner_version=CLEANER_VERSION,
        needs_js=needs_js,
    )
