"""Unit tests for HTML cleaning, Arabic retention, content hashing, and immutable snapshots (Step 2.0b)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from copilot.rag.cleaner import (
    CLEANER_VERSION,
    clean_html,
    detect_language,
)
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.snapshot import SnapshotStore

FIXTURES_DIR = Path("tests/fixtures/html")


@pytest.fixture
def arabic_html() -> str:
    return (FIXTURES_DIR / "arabic_support.html").read_text(encoding="utf-8")


@pytest.fixture
def timestamp_change_html() -> str:
    return (FIXTURES_DIR / "timestamp_change.html").read_text(encoding="utf-8")


@pytest.fixture
def real_change_html() -> str:
    return (FIXTURES_DIR / "real_text_change.html").read_text(encoding="utf-8")


@pytest.fixture
def js_shell_html() -> str:
    return (FIXTURES_DIR / "js_shell.html").read_text(encoding="utf-8")


@pytest.fixture
def english_html() -> str:
    return (FIXTURES_DIR / "english_support.html").read_text(encoding="utf-8")


# --- 1. Cleaner, Arabic Retention & Boilerplate Removal ---


def test_cleaner_keeps_main_text_and_drops_boilerplate(arabic_html: str) -> None:
    """Navigation, footers, scripts, and cookies are dropped; headings, lists, tables, and Arabic survive."""
    cleaned = clean_html(arabic_html, base_url="https://www.te.eg/customer-service")

    # Main content preserved
    assert "الدعم الفني وخدمة العملاء" in cleaned.text
    assert "باقات الإنترنت المنزلي" in cleaned.text
    assert "باقة سوبر: 140 جيجابايت" in cleaned.text
    assert "سوبر 140" in cleaned.text

    # Arabic and English digits survive
    assert "١٢٠" in cleaned.text
    assert "200" in cleaned.text

    # Boilerplate removed
    assert "تسجيل الدخول" not in cleaned.text
    assert "ملفات تعريف الارتباط" not in cleaned.text
    assert "إعلان تجاري جانبي" not in cleaned.text
    assert "جميع الحقوق محفوظة" not in cleaned.text
    assert "analytics tracking loaded" not in cleaned.text

    # Metadata and language
    assert cleaned.language == "ar"
    assert "المصرية للاتصالات" in cleaned.title
    assert cleaned.canonical_url == "https://www.te.eg/customer-service"
    assert cleaned.cleaner_version == CLEANER_VERSION

    # Idempotent hash: same page gives same hash on repeated cleanings
    cleaned2 = clean_html(arabic_html, base_url="https://www.te.eg/customer-service")
    assert cleaned.content_hash == cleaned2.content_hash
    assert len(cleaned.content_hash) == 64


def test_cleaner_language_detection(arabic_html: str, english_html: str) -> None:
    """Language detection accurately flags Arabic vs English documents."""
    doc_ar = clean_html(arabic_html)
    doc_en = clean_html(english_html)
    assert doc_ar.language == "ar"
    assert doc_en.language == "en"


def test_timestamp_and_banner_changes_keep_same_content_hash(
    arabic_html: str, timestamp_change_html: str
) -> None:
    """A page whose only change is a timestamp, cookie notice, or banner preserves the same content hash."""
    doc_original = clean_html(arabic_html, base_url="https://www.te.eg/page")
    doc_timestamp = clean_html(timestamp_change_html, base_url="https://www.te.eg/page")

    assert doc_original.content_hash != ""
    assert doc_original.content_hash == doc_timestamp.content_hash


def test_real_text_change_gives_different_content_hash(
    arabic_html: str, real_change_html: str
) -> None:
    """A real content change gives a different content hash."""
    doc_original = clean_html(arabic_html, base_url="https://www.te.eg/page")
    doc_changed = clean_html(real_change_html, base_url="https://www.te.eg/page")

    assert doc_original.content_hash != doc_changed.content_hash


def test_javascript_only_shell_flagged_needs_js(js_shell_html: str) -> None:
    """A client-side JavaScript shell is detected and flagged needs_js=True with empty content hash."""
    doc = clean_html(js_shell_html, base_url="https://www.te.eg/app")
    assert doc.needs_js is True
    assert doc.content_hash == ""
    assert doc.text == ""


def test_cleaner_version_contains_actual_installed_version() -> None:
    """CLEANER_VERSION records the true installed library version and BS4 rule version."""
    assert "+bs4-v1.0" in CLEANER_VERSION
    assert CLEANER_VERSION.startswith(("trafilatura-", "bs4-only+"))


def test_detect_language_with_mixed_technical_arabic() -> None:
    """Arabic pages with English technical keywords (Wi-Fi, 5G, Router) are correctly classified as 'ar'."""
    mixed_text = "خدمة باقات Internet المنزلية فائقة السرعة مع راوتر VDSL و Wi-Fi 6 المطور"
    assert detect_language(mixed_text) == "ar"

    # With HTML lang hint
    assert detect_language("Internet Packages", html_lang="en") == "en"
    assert detect_language("Internet Packages باقة", html_lang="ar") == "ar"


def test_javascript_shell_detection_distinguishes_real_short_page_from_spa() -> None:
    """Multi-signal JS shell detection catches SPA shells with loader text but allows legitimate short pages."""
    # 1. SPA shell with mount point and loader text (> 20 chars) is correctly detected
    spa_html = """
    <html>
      <body>
        <div id="root">
          <p>Loading application, please wait...</p>
        </div>
      </body>
    </html>
    """
    doc_spa = clean_html(spa_html, base_url="https://www.te.eg/portal")
    assert doc_spa.needs_js is True
    assert doc_spa.content_hash == ""

    # 2. Legitimate short contact page without SPA mount or noscript is NOT flagged as a JS shell
    legit_short_html = """
    <html>
      <head><title>Contact</title></head>
      <body>
        <main>
          <h1>الدعم الفني</h1>
          <p>للتواصل: 111</p>
        </main>
      </body>
    </html>
    """
    doc_legit = clean_html(legit_short_html, base_url="https://www.te.eg/contact")
    assert doc_legit.needs_js is False
    assert "111" in doc_legit.text
    assert doc_legit.content_hash != ""


def test_canonical_fallback_rejects_sensitive_paths_when_config_is_none() -> None:
    """When config is None, fallback validation rejects sensitive/admin paths in canonical tag."""
    html_with_admin_canonical = """
    <html>
      <head>
        <link rel="canonical" href="https://www.te.eg/admin/dashboard" />
        <title>Customer Help</title>
      </head>
      <body>
        <main><h1>Help Center</h1><p>Customer help content.</p></main>
      </body>
    </html>
    """
    # config=None
    doc = clean_html(html_with_admin_canonical, base_url="https://www.te.eg/help", config=None)
    # Must reject /admin/dashboard and fall back to the safe page URL
    assert doc.canonical_url == "https://www.te.eg/help"
    assert "/admin" not in doc.canonical_url


# --- 2. Snapshot Store, Deduplication & Versioning ---


def test_two_pages_with_same_canonical_url_stored_once(arabic_html: str, tmp_path: Path) -> None:
    """Two pages that declare the same canonical URL are deduplicated and stored only once."""
    store = SnapshotStore(base_dir=tmp_path)
    items = [
        {"url": "https://www.te.eg/cs-alias", "text": arabic_html, "status_code": 200},
        {"url": "https://www.te.eg/customer-service", "text": arabic_html, "status_code": 200},
    ]
    snapshot = store.build_snapshot(items)
    # Both declare canonical https://www.te.eg/customer-service
    assert len(snapshot.pages) == 1
    assert "https://www.te.eg/customer-service" in snapshot.pages


def test_404_recorded_without_content(arabic_html: str, tmp_path: Path) -> None:
    """A 404 for an unknown URL is omitted from snapshot; a 404 for a known page marks it removed with no content."""
    store = SnapshotStore(base_dir=tmp_path)

    # 1. 404 on unknown URL: omitted from snapshot pages (only report records it, no raw HTML stored)
    items_unknown = [{"url": "https://www.te.eg/missing", "text": "Not Found", "status_code": 404}]
    snapshot_unknown = store.build_snapshot(items_unknown)
    assert len(snapshot_unknown.pages) == 0

    # 2. 404 on known page: marks it removed in the new snapshot with empty content
    snap_1 = store.build_snapshot(
        [{"url": "https://www.te.eg/customer-service", "text": arabic_html, "status_code": 200}],
        snapshot_id="2026-11-12-s1",
    )
    assert len(snap_1.pages) == 1
    assert snap_1.pages["https://www.te.eg/customer-service"].is_removed is False

    snap_2 = store.build_snapshot(
        [
            {
                "url": "https://www.te.eg/customer-service",
                "text": "<html>404 Not Found</html>",
                "status_code": 404,
            }
        ],
        previous_snapshot=snap_1,
        snapshot_id="2026-11-12-s2",
    )
    assert len(snap_2.pages) == 1
    page = snap_2.pages["https://www.te.eg/customer-service"]
    assert page.http_status == 404
    assert page.is_removed is True
    assert page.content_hash == ""
    assert page.cleaned_text == ""
    assert page.raw_html == ""


def test_snapshot_immutability_refuses_overwrite(arabic_html: str, tmp_path: Path) -> None:
    """Attempting to overwrite an existing snapshot raises FileExistsError."""
    store = SnapshotStore(base_dir=tmp_path)
    snapshot = store.build_snapshot(
        [{"url": "https://www.te.eg/customer-service", "text": arabic_html, "status_code": 200}],
        snapshot_id="2026-11-12-immutable",
    )
    store.save_snapshot(snapshot)

    # Overwrite attempt must raise FileExistsError
    with pytest.raises(FileExistsError, match="already exists"):
        store.save_snapshot(snapshot)


def test_save_snapshot_does_not_activate_automatically(arabic_html: str, tmp_path: Path) -> None:
    """save_snapshot persists files but does NOT activate; activation requires an explicit call."""
    store = SnapshotStore(base_dir=tmp_path)
    snapshot = store.build_snapshot(
        [{"url": "https://www.te.eg/customer-service", "text": arabic_html, "status_code": 200}],
        snapshot_id="2026-11-12-decoupled",
    )
    manifest_path, private_path = store.save_snapshot(snapshot)
    assert manifest_path.exists()
    assert private_path.exists()

    # Active pointer is NOT set automatically
    assert store.get_active_snapshot_id() is None
    assert store.get_active_snapshot() is None

    # Explicit activation sets the active snapshot
    store.activate_snapshot("2026-11-12-decoupled")
    assert store.get_active_snapshot_id() == "2026-11-12-decoupled"
    assert store.get_active_snapshot() is not None


def test_non_200_error_page_never_stored_as_content_and_keeps_previous_version(
    arabic_html: str, tmp_path: Path
) -> None:
    """Non-200 statuses (e.g. 500, 403, 429) never store error HTML; known pages retain previous version."""
    store = SnapshotStore(base_dir=tmp_path)
    snap_1 = store.build_snapshot(
        [{"url": "https://www.te.eg/customer-service", "text": arabic_html, "status_code": 200}],
        snapshot_id="2026-11-12-v1",
    )
    orig_page = snap_1.pages["https://www.te.eg/customer-service"]

    # 500 error on known page: carries over previous version unchanged
    snap_2 = store.build_snapshot(
        [
            {
                "url": "https://www.te.eg/customer-service",
                "text": "<html><body>500 Internal Server Error</body></html>",
                "status_code": 500,
            },
            {
                "url": "https://www.te.eg/unknown-broken",
                "text": "<html><body>500 Internal Server Error</body></html>",
                "status_code": 500,
            },
        ],
        previous_snapshot=snap_1,
        snapshot_id="2026-11-12-v2",
    )

    # Known page is retained with previous content and hash
    assert len(snap_2.pages) == 1
    retained_page = snap_2.pages["https://www.te.eg/customer-service"]
    assert retained_page.content_hash == orig_page.content_hash
    assert retained_page.cleaned_text == orig_page.cleaned_text
    assert "500 Internal Server Error" not in retained_page.raw_html
    # Unknown 500 is completely omitted from snapshot
    assert "https://www.te.eg/unknown-broken" not in snap_2.pages


def test_304_without_previous_version_is_skipped(tmp_path: Path) -> None:
    """A 304 response on an unknown URL is skipped without creating a junk empty page."""
    store = SnapshotStore(base_dir=tmp_path)
    items = [
        {"url": "https://www.te.eg/unknown", "text": "", "status_code": 304, "is_unchanged": True}
    ]
    snapshot = store.build_snapshot(items)
    assert len(snapshot.pages) == 0


def test_partial_crawl_carries_over_unvisited_pages(
    arabic_html: str, english_html: str, tmp_path: Path
) -> None:
    """When crawl_complete=False or pages are in failed_urls, unvisited pages are preserved, NOT marked removed."""
    store = SnapshotStore(base_dir=tmp_path)
    snap_1 = store.build_snapshot(
        [
            {"url": "https://www.te.eg/customer-service", "text": arabic_html, "status_code": 200},
            {"url": "https://www.te.eg/english", "text": english_html, "status_code": 200},
        ],
        snapshot_id="2026-11-12-snap1",
    )
    assert len(snap_1.pages) == 2

    # Partial crawl: crawl was interrupted or hit page cap; only customer-service was visited
    snap_partial = store.build_snapshot(
        [{"url": "https://www.te.eg/customer-service", "text": arabic_html, "status_code": 200}],
        previous_snapshot=snap_1,
        snapshot_id="2026-11-12-partial",
        crawl_complete=False,
    )
    # english page is NOT marked removed; carried over intact
    canonical_b = "https://www.te.eg/en/customer-care"
    assert len(snap_partial.pages) == 2
    assert snap_partial.pages[canonical_b].is_removed is False
    assert snap_partial.pages[canonical_b].cleaned_text != ""


def test_canonical_allowlist_validation_falls_back_to_page_url(tmp_path: Path) -> None:
    """A page whose canonical points to a disallowed external host or path falls back to the page's allowed URL."""
    store = SnapshotStore(base_dir=tmp_path)
    html_with_bad_canonical = """
    <html>
      <head>
        <link rel="canonical" href="https://phishing-site.com/steal-data" />
        <title>Account Info</title>
      </head>
      <body>
        <main><h1>Your Account</h1><p>Account details here.</p></main>
      </body>
    </html>
    """
    config = CrawlConfig(
        name="test",
        user_agent_prefix="Bot",
        start_urls=["https://www.te.eg/account"],
        allowed_hosts=["www.te.eg"],
        allowed_path_prefixes=["/"],
    )
    items = [
        {"url": "https://www.te.eg/account", "text": html_with_bad_canonical, "status_code": 200}
    ]
    snapshot = store.build_snapshot(items, config=config)

    assert "https://www.te.eg/account" in snapshot.pages
    assert "https://phishing-site.com/steal-data" not in snapshot.pages


def test_unchanged_page_reuses_existing_page_version(arabic_html: str, tmp_path: Path) -> None:
    """Unchanged pages reuse the existing PageVersion object across consecutive snapshots."""
    store = SnapshotStore(base_dir=tmp_path)
    items_v1 = [
        {"url": "https://www.te.eg/customer-service", "text": arabic_html, "status_code": 200}
    ]
    snap_1 = store.build_snapshot(items_v1, snapshot_id="2026-11-12-snap0001")

    # Run crawl 2 with identical content
    items_v2 = [
        {"url": "https://www.te.eg/customer-service", "text": arabic_html, "status_code": 200}
    ]
    snap_2 = store.build_snapshot(
        items_v2, previous_snapshot=snap_1, snapshot_id="2026-11-12-snap0002"
    )

    page1 = snap_1.pages["https://www.te.eg/customer-service"]
    page2 = snap_2.pages["https://www.te.eg/customer-service"]

    # PageVersion is reused: content hash, fetched_at, and text preserved, verification refreshed
    assert page1.content_hash == page2.content_hash
    assert page1.fetched_at == page2.fetched_at
    assert page1.cleaned_text == page2.cleaned_text
    assert page2.last_verified_at >= page1.last_verified_at


def test_removed_page_marked_removed_in_new_snapshot_only(
    arabic_html: str, english_html: str, tmp_path: Path
) -> None:
    """A page deleted from the crawl is marked is_removed=True in the new snapshot only."""
    store = SnapshotStore(base_dir=tmp_path)

    # Snapshot 1 has page A and page B
    snap_1 = store.build_snapshot(
        [
            {"url": "https://www.te.eg/arabic", "text": arabic_html, "status_code": 200},
            {"url": "https://www.te.eg/english", "text": english_html, "status_code": 200},
        ],
        snapshot_id="2026-11-12-snap0001",
    )
    assert len(snap_1.pages) == 2
    canonical_b = "https://www.te.eg/en/customer-care"
    assert snap_1.pages[canonical_b].is_removed is False

    # Snapshot 2 only returns page A (page B was deleted/removed from site)
    snap_2 = store.build_snapshot(
        [
            {"url": "https://www.te.eg/arabic", "text": arabic_html, "status_code": 200},
        ],
        previous_snapshot=snap_1,
        snapshot_id="2026-11-12-snap0002",
    )

    # Snap 2 still holds page B but flagged is_removed=True
    assert len(snap_2.pages) == 2
    assert snap_2.pages[canonical_b].is_removed is True

    # Active pages only returns non-removed pages
    assert len(snap_2.active_pages) == 1
    assert snap_2.active_pages[0].canonical_url == "https://www.te.eg/customer-service"

    # Snap 1 is untouched and immutable
    assert snap_1.pages[canonical_b].is_removed is False
    assert len(snap_1.active_pages) == 2


def test_manifest_has_no_text_field(arabic_html: str, tmp_path: Path) -> None:
    """The committed manifest.jsonl strictly omits all page text fields."""
    store = SnapshotStore(base_dir=tmp_path)
    snapshot = store.build_snapshot(
        [{"url": "https://www.te.eg/customer-service", "text": arabic_html, "status_code": 200}],
        snapshot_id="2026-11-12-test01",
    )
    manifest_file, private_file = store.save_snapshot(snapshot)

    assert manifest_file.exists()
    assert private_file.exists()

    # Read manifest line
    line = manifest_file.read_text(encoding="utf-8").strip()
    manifest_dict = json.loads(line)

    # Strictly verify required keys are present
    assert "url" in manifest_dict
    assert "canonical_url" in manifest_dict
    assert "content_hash" in manifest_dict
    assert "language" in manifest_dict
    assert "fetched_at" in manifest_dict
    assert "http_status" in manifest_dict

    # Strictly verify NO text field exists in manifest
    assert "text" not in manifest_dict
    assert "cleaned_text" not in manifest_dict
    assert "raw_html" not in manifest_dict

    # Verify private backup contains the text
    private_dict = json.loads(private_file.read_text(encoding="utf-8").strip())
    assert "cleaned_text" in private_dict
    assert "الدعم الفني" in private_dict["cleaned_text"]


def test_switching_and_rolling_back_active_snapshot(
    arabic_html: str, real_change_html: str, tmp_path: Path
) -> None:
    """Setting and rolling back active snapshot switches what retrieval reads."""
    store = SnapshotStore(base_dir=tmp_path)

    # Snapshot 1: original pricing
    snap_1 = store.build_snapshot(
        [{"url": "https://www.te.eg/customer-service", "text": arabic_html, "status_code": 200}],
        snapshot_id="2026-11-12-v1",
    )
    store.save_snapshot(snap_1)
    store.set_active_snapshot("2026-11-12-v1")

    active = store.get_active_snapshot()
    assert active.snapshot_id == "2026-11-12-v1"
    assert "١٢٠" in active.pages["https://www.te.eg/customer-service"].cleaned_text

    # Snapshot 2: price change
    snap_2 = store.build_snapshot(
        [
            {
                "url": "https://www.te.eg/customer-service",
                "text": real_change_html,
                "status_code": 200,
            }
        ],
        previous_snapshot=snap_1,
        snapshot_id="2026-11-12-v2",
    )
    store.save_snapshot(snap_2)
    store.set_active_snapshot("2026-11-12-v2")

    active_v2 = store.get_active_snapshot()
    assert active_v2.snapshot_id == "2026-11-12-v2"
    assert "١٨٠" in active_v2.pages["https://www.te.eg/customer-service"].cleaned_text

    # Rollback to v1
    store.rollback("2026-11-12-v1")
    rolled_back = store.get_active_snapshot()
    assert rolled_back.snapshot_id == "2026-11-12-v1"
    assert "١٢٠" in rolled_back.pages["https://www.te.eg/customer-service"].cleaned_text
