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
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from copilot.rag.crawler.url_utils import is_allowed_url, normalize_url

if TYPE_CHECKING:
    from copilot.rag.crawler.config import CrawlConfig

logger = logging.getLogger(__name__)

try:
    import trafilatura

    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

# Extraction rules versioning separated from library versions
TRAFILATURA_RULE_VERSION = "traf-rules-v1.1"
BS4_RULE_VERSION = "bs4-rules-v1.1"


def _resolve_cleaner_version() -> str:
    """Resolve cleaner version from library and extraction rule versions."""
    if HAS_TRAFILATURA:
        try:
            traf_ver = version("trafilatura")
        except (PackageNotFoundError, Exception):
            traf_ver = "unknown"
        return (
            f"trafilatura-{traf_ver}"
            f"+{TRAFILATURA_RULE_VERSION}"
            f"+{BS4_RULE_VERSION}"
        )
    return f"bs4-only+{BS4_RULE_VERSION}"


CLEANER_VERSION = _resolve_cleaner_version()

# Arabic Unicode range regex (covers basic Arabic, supplement, extended, and presentation forms)
ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
# Latin characters including standard, Latin-1 Supplement, and Latin Extended
LATIN_RE = re.compile(r"[a-zA-Z\u00C0-\u024F]")

# Tags that represent pure non-content boilerplate, scripting, or embed media
BOILERPLATE_TAGS = {
    "nav",
    "footer",
    "script",
    "style",
    "aside",
    "noscript",
    "iframe",
    "svg",
    "canvas",
}

# Exact IDs of known modals, banners, and comparison widgets
EXACT_BOILERPLATE_IDS = {
    "resetComparableWarning",
    "compareModal",
    "comparisonModal",
    "cookieConsent",
    "cookie-notice",
    "cookie-banner",
    "site-header",
    "site-footer",
    "navbar",
    "main-nav",
    "back-to-top",
    "scroll-to-top",
    "sbst_quickAccessNav",
    "sbst_quickaccessnav",
    "quick-access-nav",
    "quickAccessNav",
}

# Exact class tokens of known boilerplate components
EXACT_BOILERPLATE_CLASSES = {
    "modal",
    "modal-backdrop",
    "modal-dialog",
    "modal-content",
    "modal-header",
    "modal-footer",
    "modal-fade",
    "compare-basket",
    "compare-widget",
    "product-comparison",
    "cookie-banner",
    "cookie-consent",
    "cookie-notice",
    "site-footer",
    "footer-bottom",
    "main-footer",
    "header-nav",
    "navbar",
    "sidebar-ad",
    "breadcrumb",
    "breadcrumbs",
    "pagination",
    "share-buttons",
    "social-share",
    "social-links",
    "back-to-top",
    "scroll-top",
    "scroll-to-top",
    "quick-access-nav",
    "skip-link",
    "main-header",
    "secondary-header",
    "overlay",
    "hover-overlay",
    "card-overlay",
}

# ARIA roles for boilerplate components
EXACT_BOILERPLATE_ROLES = {
    "dialog",
    "alertdialog",
    "banner",
    "contentinfo",
}

# Known boilerplate phrases (comparison widgets, nav prompts, back-to-top, quick links) that pollute content
KNOWN_BOILERPLATE_LINES = re.compile(
    r"^(products in comparison.*|سيتم محو المنتجات.*|sorry\.? there are no products.*|عفوا[،,]? لا يوجد منتجات.*|"
    r"different types of products.*|قائمة منتجات مختلفة.*|please add products.*|يرجى إضافة منتجات.*|"
    r"full comparing list.*|قائمة مقارنة مكتملة.*|the comparison list contains.*|قائمة المقارنة تحتوي.*|"
    r"add up to 3 products.*|أضف 3 منتجات.*|add (another )?products?.*|أضف منتج( آخر)?.*|compare|المقارنة|"
    r"return to top.*|الرجوع الي الأعل[ىي].*|main menu.*|الصفحة الرئيسية.*|تخطي إلى المحتوى.*|"
    r"روابط سريعة.*|quick access.*|quick links.*|رجوع|back|حذف الكل|clear all|"
    r"no current (services|promotions|products|offers).*|لا توجد (خدمات|عروض|منتجات).*|"
    r"all rights reserved.*|جميع حقوق النشر محفوظة.*|telecom egypt|المصرية للاتصالات)$",
    re.IGNORECASE,
)

# Backward-compatible alias
BOILERPLATE_PATTERNS = KNOWN_BOILERPLATE_LINES

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
    extractor: str = ""

    @property
    def text(self) -> str:
        """Alias for cleaned_text."""
        return self.cleaned_text


# Backward compatibility alias
CleanedDocument = CleanedPage


def normalize_whitespace(text: str) -> str:
    """Normalize repeated whitespace, tabs, and newlines to a single clean space."""
    return re.sub(r"\s+", " ", text).strip()


def normalize_content_lines(text: str) -> str:
    """Normalize whitespace within each line while strictly preserving line breaks and structure."""
    if not text:
        return ""
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.splitlines()]
    normalized = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", normalized)


def compute_content_hash(cleaned_text: str) -> str:
    """Compute SHA-256 content hash strictly on cleaned main text preserving line breaks and structure.

    Rule: Never hash raw HTML. Only hash cleaned text with line structure intact.
    """
    normalized = normalize_content_lines(cleaned_text)
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


def passes_quality_gate(text: str) -> bool:
    """Evaluate whether extracted text passes the quality gate.

    Quality Gate Rules:
    - Minimum length: >= 20 characters
    - Minimum content words: >= 5 words
    - Maximum boilerplate line ratio: <= 40% of non-empty lines
    - Must contain substantive headings or content lines
    - Rejects text dominated by comparison modals and shopping basket noise
    """
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < 20:
        return False

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if not lines:
        return False

    content_lines = [line for line in lines if not KNOWN_BOILERPLATE_LINES.match(line)]
    if not content_lines:
        return False

    # Check boilerplate line pollution ratio
    boilerplate_ratio = (len(lines) - len(content_lines)) / len(lines)
    if boilerplate_ratio > 0.40:
        return False

    all_words = (" ".join(content_lines)).split()
    if len(all_words) < 5:
        return False

    # Rejection of comparison widget noise
    low = (" ".join(content_lines)).lower()
    if ("product" in low or "منتج" in low) and ("compar" in low or "مقارن" in low):
        words_set = set(re.findall(r"[\u0600-\u06FF\w]+", low))
        comparison_junk = {
            "products", "product", "comparison", "compare", "cleared", "sorry",
            "منتجات", "منتج", "محو", "مقارنة", "المقارنة", "يوجد", "لا", "التي", "يتم",
            "are", "there", "no", "to", "in", "will", "be"
        }
        if len(words_set - comparison_junk) <= 3:
            return False

    return True


def is_javascript_shell(
    cleaned_text: str,
    raw_html: str,
    *,
    body_text: str | None = None,
) -> bool:
    """Check if the HTML is an actual JavaScript-only shell (empty SPA without JS).

    Multi-signal detection:
    - Checks for explicit noscript warnings ("You need to enable JavaScript...").
    - Checks for SPA mount points (`id="root"`, `id="app"`, etc.) with minimal initial text or generic loaders.
    - Flags empty shells only when no real content was returned.
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

    # Signal 3: Truly empty body text and 0 cleaned text
    if len(normalized) == 0:
        if has_spa_mount or "<noscript>" in raw_lower:
            return True
        if body_text is not None and len(body_text.strip()) < 15:
            return True

    return False


def is_boilerplate_element(el: Any) -> bool:
    """Check if an element matches exact boilerplate IDs, class tokens, or ARIA roles."""
    if not isinstance(el, Tag) or getattr(el, "attrs", None) is None:
        return False

    # 1. Exact ID check and ID keyword check
    el_id = el.get("id")
    if el_id:
        if el_id in EXACT_BOILERPLATE_IDS:
            return True
        id_lower = str(el_id).lower()
        if any(
            k in id_lower
            for k in (
                "resetcomparable",
                "comparemodal",
                "comparisonmodal",
                "quickaccess",
                "backtotop",
                "back-to-top",
                "scrolltop",
                "scroll-to-top",
            )
        ):
            return True

    # 2. Exact class token matching and Liferay structure item checks
    classes = el.get("class")
    if classes:
        if isinstance(classes, str):
            class_list = classes.split()
        else:
            class_list = list(classes)
        class_set = {c.lower() for c in class_list}
        if class_set & EXACT_BOILERPLATE_CLASSES:
            return True
        for c in class_set:
            if "compare" in c and any(w in c for w in ("modal", "basket", "widget", "tray", "cards", "page-cards")):
                return True
            if any(
                k in c
                for k in (
                    "quick-access",
                    "quickaccess",
                    "skip-link",
                    "main-header",
                    "secondary-header",
                    "header-fragment",
                    "back-to-top",
                    "scroll-to-top",
                )
            ):
                return True
            if c.startswith("lfr-layout-structure-item-") and any(
                item in c
                for item in (
                    "main-header",
                    "secondary-header",
                    "breadcrumb",
                    "comparison",
                    "back-to-top",
                )
            ):
                return True

    # 3. Exact ARIA role matching
    role = el.get("role")
    if role and str(role).lower() in EXACT_BOILERPLATE_ROLES:
        return True

    return False


def is_site_level_header(header: Tag) -> bool:
    """Check if a <header> is a site-wide navigation/branding banner rather than an article header."""
    if not isinstance(header, Tag) or getattr(header, "attrs", None) is None:
        return False

    role = header.get("role")
    if role and str(role).lower() == "banner":
        return True

    classes = header.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    classes_set = {c.lower() for c in classes}
    if {"site-header", "main-header", "header-nav", "navbar", "page-header"} & classes_set:
        return True

    # If header contains a nav element and is not nested within an article or main content
    if header.find("nav"):
        parent_names = [p.name for p in header.parents if hasattr(p, "name")]
        if "article" not in parent_names and "main" not in parent_names:
            return True

    return False


def is_boilerplate_form(form: Tag) -> bool:
    """Check if a <form> is purely search, login, or newsletter boilerplate."""
    if not isinstance(form, Tag) or getattr(form, "attrs", None) is None:
        return False

    role = form.get("role")
    if role and str(role).lower() == "search":
        return True

    fid = form.get("id") or ""
    fclasses = form.get("class") or []
    if isinstance(fclasses, str):
        fclasses = fclasses.split()
    identifiers = [fid] + list(fclasses)
    for ident in identifiers:
        ident_low = str(ident).lower()
        if any(keyword in ident_low for keyword in ("search", "login", "auth", "newsletter", "subscribe")):
            return True

    # If form only contains search/submit inputs with no substantive text or tables
    inputs = form.find_all("input")
    if inputs and all(inp.get("type", "").lower() in ("search", "hidden", "submit") for inp in inputs):
        if not form.find(["p", "h1", "h2", "h3", "h4", "h5", "h6", "table", "label", "legend"]):
            return True

    return False


def _extract_list(list_elem: Tag, depth: int = 0) -> list[str]:
    """Extract list items preserving hierarchy and indentation, supporting div/span wrappers."""
    lines: list[str] = []
    indent = "  " * depth

    # Identify direct <li> elements belonging to this list (closest ancestor list is list_elem)
    direct_lis: list[Tag] = []
    for li in list_elem.find_all("li"):
        parent_list = None
        for p in li.parents:
            if isinstance(p, Tag) and p.name in ("ul", "ol"):
                parent_list = p
                break
        if parent_list is list_elem:
            direct_lis.append(li)

    for li in direct_lis:
        # Identify direct nested sub-lists inside this <li> (closest ancestor <li> is li)
        nested_sublists: list[Tag] = []
        for sublist in li.find_all(["ul", "ol"]):
            parent_li = None
            for p in sublist.parents:
                if isinstance(p, Tag) and p.name == "li":
                    parent_li = p
                    break
            if parent_li is li:
                nested_sublists.append(sublist)

        nested_ids = {
            id(sub)
            for sublist in nested_sublists
            for sub in [sublist] + sublist.find_all(True)
        }

        # Extract direct text of this <li> excluding strings inside nested sub-lists
        direct_text_parts: list[str] = []
        for s in li.find_all(string=True):
            if isinstance(s, Comment):
                continue
            if any(id(p) in nested_ids for p in s.parents):
                continue
            t = str(s).strip()
            if t:
                direct_text_parts.append(t)

        item_text = normalize_whitespace(" ".join(direct_text_parts))
        if item_text and not KNOWN_BOILERPLATE_LINES.match(item_text):
            lines.append(f"{indent}- {item_text}")

        # Recurse into nested sub-lists with increased indentation
        for nested in nested_sublists:
            lines.extend(_extract_list(nested, depth + 1))

    return lines


def _extract_table(table_elem: Tag) -> list[str]:
    """Extract table rows as formatted Markdown-style tables with clear cell boundaries.

    Handles nested tables by strictly extracting rows and cells belonging to the current
    table level, preventing rows or cells from being mixed up.
    """
    nested_tables = [t for t in table_elem.find_all("table") if t is not table_elem]
    nested_ids = {id(n) for sub in nested_tables for n in [sub] + sub.find_all(True)}

    direct_trs: list[Tag] = []
    for tr in table_elem.find_all("tr"):
        parent_table = None
        for p in tr.parents:
            if isinstance(p, Tag) and p.name == "table":
                parent_table = p
                break
        if parent_table is table_elem:
            direct_trs.append(tr)

    if not direct_trs:
        return []

    formatted_rows: list[str] = []

    for i, tr in enumerate(direct_trs):
        direct_cells: list[Tag] = []
        for cell in tr.find_all(["th", "td"]):
            parent_tr = None
            for p in cell.parents:
                if isinstance(p, Tag) and p.name == "tr":
                    parent_tr = p
                    break
            if parent_tr is tr:
                direct_cells.append(cell)

        if not direct_cells:
            continue

        cell_texts: list[str] = []
        for c in direct_cells:
            text_parts: list[str] = []
            for s in c.find_all(string=True):
                if isinstance(s, Comment):
                    continue
                if any(id(p) in nested_ids for p in s.parents):
                    continue
                t = str(s).strip()
                if t:
                    text_parts.append(t)
            cell_texts.append(normalize_whitespace(" ".join(text_parts)))

        if not any(cell_texts):
            continue

        row_str = "| " + " | ".join(cell_texts) + " |"
        formatted_rows.append(row_str)

        if i == 0 and any(c.name == "th" for c in direct_cells):
            sep = "| " + " | ".join(["---"] * len(cell_texts)) + " |"
            formatted_rows.append(sep)

    # Format each nested table directly following the parent table
    for nested in nested_tables:
        parent_table = None
        for p in nested.parents:
            if isinstance(p, Tag) and p.name == "table":
                parent_table = p
                break
        if parent_table is table_elem:
            nested_lines = _extract_table(nested)
            if nested_lines:
                formatted_rows.append("")
                formatted_rows.extend(nested_lines)

    return formatted_rows


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
        resolved = urljoin(fallback_url, raw_canonical)
        norm_canonical = normalize_url(resolved)

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
    """Arabic-aware BeautifulSoup fallback text extractor with clean DOM traversal."""
    # 1. Remove comments
    for comment in list(soup.find_all(string=lambda s: isinstance(s, Comment))):
        comment.extract()

    # 2. Remove standard boilerplate tags (nav, footer, script, style, aside, iframe, etc.)
    for tag in list(soup.find_all(list(BOILERPLATE_TAGS))):
        tag.decompose()

    # 3. Decompose exact boilerplate elements (modals, dialogs, cookie banners, compare widgets)
    for el in list(soup.find_all(True)):
        if el.parent is None:
            continue
        if is_boilerplate_element(el):
            el.decompose()

    # 4. Remove site-level header if it's a nav banner or outside article/main
    for header in list(soup.find_all("header")):
        if header.parent is None:
            continue
        if is_site_level_header(header):
            header.decompose()

    # 5. Remove search/login/newsletter forms while preserving informative forms
    for form in list(soup.find_all("form")):
        if form.parent is None:
            continue
        if is_boilerplate_form(form):
            form.decompose()
        else:
            # Strip submit buttons / hidden inputs inside content forms
            for btn in list(form.find_all(["button", "input"], type=re.compile(r"^(submit|hidden)$", re.I))):
                btn.decompose()

    # 6. Target main content area if available
    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(role="main")
        or soup.find(id="main-content")
        or soup.find(id="content")
        or soup.find(class_=re.compile(r"^(main-content|layout-content|entry-content)$", re.I))
        or soup.find("body")
        or soup
    )

    # 7. Extract structural blocks (headings, paragraphs, lists, tables, legends, labels)
    blocks: list[str] = []
    handled_ids: set[int] = set()

    for elem in main_content.find_all(  # type: ignore[union-attr]
        [
            "h1", "h2", "h3", "h4", "h5", "h6",
            "p", "ul", "ol", "table", "blockquote", "pre",
            "legend", "label",
        ]
    ):
        if id(elem) in handled_ids:
            continue

        if elem.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(elem.name[1])
            # Mark all nested descendants as handled so nested <p> or <label> are not processed again
            for sub in elem.find_all(True):
                handled_ids.add(id(sub))
            text = normalize_whitespace(elem.get_text(separator=" ", strip=True))
            if text and not KNOWN_BOILERPLATE_LINES.match(text):
                # Suppress consecutive identical blocks (e.g. heading followed by identical paragraph)
                clean_key = normalize_whitespace(text.lstrip("# ").strip()).lower()
                if blocks:
                    prev_key = normalize_whitespace(blocks[-1].lstrip("# ").strip()).lower()
                    if clean_key == prev_key:
                        continue
                blocks.append(f"{'#' * level} {text}")

        elif elem.name in {"ul", "ol"}:
            for sub in elem.find_all(["ul", "ol", "li"]):
                handled_ids.add(id(sub))
            list_lines = _extract_list(elem)
            if list_lines:
                blocks.append("\n".join(list_lines))

        elif elem.name == "table":
            for sub in elem.find_all(["table", "tr", "th", "td", "tbody", "thead"]):
                handled_ids.add(id(sub))
            table_lines = _extract_table(elem)
            if table_lines:
                blocks.append("\n".join(table_lines))

        elif elem.name in {"p", "blockquote", "pre", "legend", "label"}:
            for sub in elem.find_all(True):
                handled_ids.add(id(sub))
            text = normalize_whitespace(elem.get_text(separator=" ", strip=True))
            if text and not KNOWN_BOILERPLATE_LINES.match(text):
                clean_key = normalize_whitespace(text.lstrip("# ").strip()).lower()
                if blocks:
                    prev_key = normalize_whitespace(blocks[-1].lstrip("# ").strip()).lower()
                    if clean_key == prev_key:
                        continue
                blocks.append(text)

    if blocks:
        return "\n\n".join(blocks)

    raw_text = main_content.get_text(separator="\n", strip=True)  # type: ignore[union-attr]
    clean_lines = [
        line.strip()
        for line in raw_text.splitlines()
        if line.strip() and not KNOWN_BOILERPLATE_LINES.match(line.strip())
    ]
    return "\n".join(clean_lines).strip()


def clean_html(
    raw_html: str,
    url: str = "",
    *,
    base_url: str = "",
    config: CrawlConfig | None = None,
) -> CleanedPage:
    """Clean HTML and extract main text, canonical URL, title, language, and extractor.

    Performance & Safety:
    - Parses HTML with BeautifulSoup exactly ONCE (zero repeated parsing allocations).
    - Drops boilerplate (navigation, footers, headers, scripts, styles, cookies, banners).
    - Preserves headings, lists, tables as structured Markdown.
    - Preserves Arabic text and digits (e.g. ١٢٠).
    - Validates canonical URLs against allowlist and default sensitive path guards.
    - Evaluates extracted text with a real Quality Gate.
    - Multi-signal identification of JavaScript shells as needs_js=True.
    - Computes content hash on cleaned text (never raw HTML).
    - Records which extractor produced the final text.
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
    extractor = "none"

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
            # Apply Real Quality Gate to Trafilatura output
            if extracted and passes_quality_gate(extracted):
                clean_lines = [
                    line
                    for line in extracted.splitlines()
                    if not KNOWN_BOILERPLATE_LINES.match(line.strip())
                ]
                filtered = "\n".join(clean_lines).strip()
                if passes_quality_gate(filtered):
                    cleaned_text = filtered
                    extractor = "trafilatura"
        except Exception as err:
            logger.debug(
                "Trafilatura extraction failed on %s: %s. Using BeautifulSoup fallback.",
                url,
                err,
            )

    # 5. BeautifulSoup extractor with DOM traversal and modal/boilerplate pruning
    bs4_text = _clean_with_beautifulsoup(soup)

    # If Trafilatura was rejected by the Quality Gate or produced no text,
    # or if BeautifulSoup extracted richer heading structure (#) or more complete content,
    # select BeautifulSoup
    if not cleaned_text:
        if passes_quality_gate(bs4_text) or bs4_text.strip():
            cleaned_text = bs4_text
            extractor = "beautifulsoup"
    elif bs4_text and ("#" in bs4_text or len(bs4_text) > len(cleaned_text) * 1.5):
        cleaned_text = bs4_text
        extractor = "beautifulsoup"

    # 6. Check for JavaScript-only shell using multi-signal analysis
    needs_js = is_javascript_shell(cleaned_text, raw_html, body_text=body_text)
    if needs_js and not cleaned_text.strip():
        cleaned_text = ""
        extractor = "none"

    # 7. Language classification with HTML lang tag hint and Arabic density weighting
    language = detect_language(cleaned_text, html_lang=html_lang)

    # 8. Compute content hash on normalized cleaned text (empty for needs_js)
    content_hash = "" if needs_js else compute_content_hash(cleaned_text)

    return CleanedPage(
        cleaned_text=cleaned_text,
        canonical_url=canonical_url,
        title=title,
        language=language,
        content_hash=content_hash,
        cleaner_version=CLEANER_VERSION,
        needs_js=needs_js,
        extractor=extractor,
    )
