"""Unit tests for cleaner hardening, quality gate, DOM traversal, and rule versioning."""

from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from copilot.rag.crawler.cleaner import (
    BS4_RULE_VERSION,
    CLEANER_VERSION,
    TRAFILATURA_RULE_VERSION,
    _extract_list,
    _extract_table,
    clean_html,
    passes_quality_gate,
)


class TestCleanerHardening(unittest.TestCase):
    def test_cleaner_rule_versions(self) -> None:
        """Rule versions must be separated from library versions."""
        self.assertEqual(TRAFILATURA_RULE_VERSION, "traf-rules-v1.1")
        self.assertEqual(BS4_RULE_VERSION, "bs4-rules-v1.1")
        self.assertIn(f"+{BS4_RULE_VERSION}", CLEANER_VERSION)

    def test_navigation_and_skip_links_portal_shell_detected_as_js(self) -> None:
        """Portal shell with only quick links, skip links, and menu items must be flagged needs_js=True."""
        html = """
        <html>
            <head><title>باقات WE كنترول - Telecom Egypt</title></head>
            <body>
                <nav id="sbst_quickAccessNav" class="quick-access-nav">
                    <a href="#main-content" class="skip-link">تخطي إلى المحتوى الرئيسي</a>
                    <a href="/links">روابط سريعة</a>
                </nav>
                <div class="lfr-layout-structure-item-main-header">
                    <div>الصفحة الرئيسية</div>
                    <div>شخصي</div>
                    <div>موبايل أنظمة</div>
                </div>
                <div id="main-content" role="main">
                    <div class="main_page_template_container">
                        <p class="navC_title">أنظمة الكارت</p>
                        <p class="navC_title">أنظمة كنترول</p>
                        <p class="we-home-card-title">WE MIX</p>
                        <p class="we-home-card-title">سوبر كيكس</p>
                        <p class="we-home-card-title">تظبيط</p>
                    </div>
                </div>
                <script src="/static/app.js"></script>
            </body>
        </html>
        """
        cleaned = clean_html(html, base_url="https://www.te.eg/personal/mobile/control")
        self.assertFalse(cleaned.needs_js)
        self.assertIn(cleaned.extractor, ("trafilatura", "beautifulsoup"))
        self.assertIn("أنظمة الكارت", cleaned.text)
        self.assertIn("WE MIX", cleaned.text)
        self.assertNotIn("تخطي إلى المحتوى", cleaned.text)
        self.assertNotIn("روابط سريعة", cleaned.text)
        self.assertNotIn("الصفحة الرئيسية", cleaned.text)

    def test_no_duplicate_content_from_nested_p_or_overlays(self) -> None:
        """Headings containing nested <p> and hover overlays must not duplicate text."""
        html = """
        <html>
            <body>
                <main>
                    <h2>WE Sports</h2>
                    <div class="hover-box-text">
                        <h3 class="special_ser_desc">
                            <p class="f-18-m14">خدمة WE Sports خدمة جديدة تقدم محتوى كرة القدم وأحدث الأخبار للبطولات لحظة بلحظة.</p>
                        </h3>
                    </div>
                    <div class="card">
                        <div class="overlay">
                            <p class="we-home-card-overlay-title">باقة OneHome</p>
                        </div>
                        <p class="we-home-card-title">باقة OneHome</p>
                        <p>باقة واحدة للخط الأرضي وإنترنت البيت بإشتراك وميعاد تجديد واحد لإدارة خدماتك بسهولة.</p>
                    </div>
                </main>
            </body>
        </html>
        """
        cleaned = clean_html(html, base_url="https://www.te.eg/services")
        # Ensure description is only present once, not twice
        self.assertEqual(
            cleaned.text.count("خدمة WE Sports خدمة جديدة تقدم محتوى كرة القدم"),
            1,
        )
        # Ensure card title is only present once, not duplicated from overlay
        self.assertEqual(cleaned.text.count("باقة OneHome"), 1)
        self.assertNotIn("we-home-card-overlay-title", cleaned.text)

    def test_extractor_field_recorded_on_cleaned_page(self) -> None:
        """CleanedPage must record which extractor generated the text."""
        html = """
        <html>
            <head><title>WE Support</title></head>
            <body>
                <main>
                    <h1>خدمة عملاء الإنترنت</h1>
                    <p>تقدم المصرية للاتصالات باقات الإنترنت المنزلي فائق السرعة لدعم جميع العملاء في كافة المحافظات.</p>
                </main>
            </body>
        </html>
        """
        cleaned = clean_html(html, base_url="https://www.te.eg/support")
        self.assertIn(cleaned.extractor, ("trafilatura", "beautifulsoup"))
        self.assertTrue(len(cleaned.text) > 0)

        # For a JS shell, extractor should be "none"
        js_shell = "<html><body><div id='root'></div><noscript>You need to enable JavaScript to run this app.</noscript></body></html>"
        cleaned_js = clean_html(js_shell, base_url="https://www.te.eg/app")
        self.assertEqual(cleaned_js.extractor, "none")
        self.assertTrue(cleaned_js.needs_js)

    def test_quality_gate_evaluation(self) -> None:
        """Quality gate must reject short, noisy, or empty text."""
        self.assertFalse(passes_quality_gate(""))
        self.assertFalse(passes_quality_gate("Short text"))
        self.assertFalse(passes_quality_gate("   \n\n   "))

        # Pure boilerplate comparison modal noise
        boilerplate_text = (
            "Products in comparison will be cleared\n"
            "سيتم محو المنتجات التي يتم مقارنتها\n"
            "Compare\n"
            "المقارنة\n"
            "Sorry. There are no products to compare\n"
            "عفوا لا يوجد منتجات للمقارنة"
        )
        self.assertFalse(passes_quality_gate(boilerplate_text))

        # Real content with substantive sentences passes
        real_content = (
            "باقات الإنترنت المنزلي فائقة السرعة من وي.\n"
            "توفر الشركة سرعات تبدأ من 30 ميجابت وحتى 200 ميجابت لتناسب جميع الاستخدامات المنزلية.\n"
            "يمكنك الاشتراك من خلال فروع الشركة أو تطبيق ماي وي بسهولة."
        )
        self.assertTrue(passes_quality_gate(real_content))

    def test_site_specific_modal_and_comparison_removal(self) -> None:
        """Comparison modals like #resetComparableWarning and .modal must be pruned."""
        html = """
        <html>
            <body>
                <div class="modal fade" id="resetComparableWarning" tabindex="-1" role="dialog">
                    <p>Products in comparison will be cleared سيتم محو المنتجات التي يتم مقارنتها</p>
                    <button>OK</button>
                </div>
                <div class="compare-widget">
                    <p>Compare basket is empty</p>
                </div>
                <main>
                    <h1>باقات الموبايل الشهرية</h1>
                    <p>استمتع بأفضل باقات المكالمات والإنترنت مع خطط كنترول تيز وخيارات متعددة للتجديد الشهري.</p>
                </main>
            </body>
        </html>
        """
        cleaned = clean_html(html, base_url="https://www.te.eg/mobile")
        self.assertNotIn("resetComparableWarning", cleaned.text)
        self.assertNotIn("محو المنتجات", cleaned.text)
        self.assertNotIn("Compare basket", cleaned.text)
        self.assertIn("باقات الموبايل الشهرية", cleaned.text)

    def test_article_header_preserved_and_site_header_removed(self) -> None:
        """Article headers must be kept while site-wide banner headers are removed."""
        html = """
        <html>
            <body>
                <header class="site-header" role="banner">
                    <nav>
                        <ul><li><a href="/">Home</a></li><li><a href="/login">Login</a></li></ul>
                    </nav>
                </header>
                <main>
                    <article>
                        <header>
                            <h1>دليل إعداد الراوتر المنزلي</h1>
                            <p>خطوات ضبط إعدادات الراوتر الجديد وتغيير كلمة السر بسهولة وبأمان.</p>
                        </header>
                        <p>قم بتوصيل كابل الهاتف بالراوتر ثم افتح صفحة الإعدادات 192.168.1.1 في المتصفح.</p>
                    </article>
                </main>
            </body>
        </html>
        """
        cleaned = clean_html(html, base_url="https://www.te.eg/router-guide")
        self.assertNotIn("Login", cleaned.text)
        self.assertIn("دليل إعداد الراوتر المنزلي", cleaned.text)
        self.assertIn("خطوات ضبط إعدادات الراوتر الجديد", cleaned.text)

    def test_informative_form_preserved_and_search_form_removed(self) -> None:
        """Search/login forms are removed, but informative forms (calculators, queries) retain text."""
        html = """
        <html>
            <body>
                <form role="search" id="site-search">
                    <input type="search" name="q" placeholder="Search..." />
                    <button type="submit">Search</button>
                </form>
                <main>
                    <h1>حاسبة استهلاك الباقة</h1>
                    <form id="usage-calculator">
                        <fieldset>
                            <legend>اختر نوع الاستخدام الشهري:</legend>
                            <label>مشاهدة الفيديو عالي الدقة: يستهلك 3 جيجابايت لكل ساعة.</label>
                            <label>التصفح والموسيقى: يستهلك 500 ميجابايت يومياً.</label>
                        </fieldset>
                        <button type="submit">احسب الباقة المناسبة</button>
                    </form>
                </main>
            </body>
        </html>
        """
        cleaned = clean_html(html, base_url="https://www.te.eg/calculator")
        self.assertNotIn("Search...", cleaned.text)
        self.assertIn("حاسبة استهلاك الباقة", cleaned.text)
        self.assertIn("اختر نوع الاستخدام الشهري", cleaned.text)
        self.assertIn("مشاهدة الفيديو عالي الدقة", cleaned.text)

    def test_exact_class_matching_does_not_remove_content_widgets(self) -> None:
        """Elements with classes like content-widget or hero-banner-title must NOT be deleted."""
        html = """
        <html>
            <body>
                <main>
                    <div class="content-widget">
                        <h2>مزايا خدمة الألياف الضوئية FTTH</h2>
                        <p>تمنحك شبكة الفايبر استقراراً فائقاً وسرعات تحميل ورفع متطابقة للعمل والألعاب عبر الإنترنت.</p>
                    </div>
                    <div class="hero-banner-title">
                        <p>أسرع شبكة إنترنت منزلي في مصر لعام 2026.</p>
                    </div>
                </main>
            </body>
        </html>
        """
        cleaned = clean_html(html, base_url="https://www.te.eg/ftth")
        self.assertIn("مزايا خدمة الألياف الضوئية", cleaned.text)
        self.assertIn("أسرع شبكة إنترنت منزلي", cleaned.text)

    def test_nested_lists_no_duplication_with_indent(self) -> None:
        """Nested lists must preserve indentation and avoid duplicating children inside parents."""
        html = """
        <ul>
            <li>الخدمات الأساسية
                <ul>
                    <li>الإنترنت الأرضي VDSL</li>
                    <li>الهاتف الأرضي المنزلي</li>
                </ul>
            </li>
            <li>الخدمات الإضافية
                <ul>
                    <li>خدمة الأمان والرقابة الأبوية</li>
                </ul>
            </li>
        </ul>
        """
        soup = BeautifulSoup(html, "html.parser")
        lines = _extract_list(soup.find("ul"))
        text = "\n".join(lines)

        # Child items should appear exactly once
        self.assertEqual(text.count("الإنترنت الأرضي VDSL"), 1)
        self.assertEqual(text.count("الهاتف الأرضي المنزلي"), 1)
        self.assertEqual(text.count("خدمة الأمان والرقابة الأبوية"), 1)

        # Indentation should be present for child items
        self.assertIn("- الخدمات الأساسية", text)
        self.assertIn("  - الإنترنت الأرضي VDSL", text)
        self.assertIn("  - الهاتف الأرضي المنزلي", text)

    def test_table_formatting_with_clear_separators(self) -> None:
        """Tables must extract cells with clear '|' separators and markdown headers."""
        html = """
        <table>
            <thead>
                <tr>
                    <th>الباقة</th>
                    <th>السرعة</th>
                    <th>السعر الشهري</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>سوبر 140</td>
                    <td>30 ميجابت</td>
                    <td>120 جنيه</td>
                </tr>
                <tr>
                    <td>ميجا 250</td>
                    <td>70 ميجابت</td>
                    <td>210 جنيه</td>
                </tr>
            </tbody>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        table_lines = _extract_table(soup.find("table"))
        table_text = "\n".join(table_lines)

        self.assertIn("| الباقة | السرعة | السعر الشهري |", table_text)
        self.assertIn("| --- | --- | --- |", table_text)
        self.assertIn("| سوبر 140 | 30 ميجابت | 120 جنيه |", table_text)
        self.assertIn("| ميجا 250 | 70 ميجابت | 210 جنيه |", table_text)

    def test_http_200_reuse_requires_same_cleaner_version(self) -> None:
        """On HTTP 200, previous PageVersion is reused ONLY if cleaner_version also matches."""
        import tempfile

        from copilot.rag.crawler.models import PageVersion
        from copilot.rag.crawler.snapshot import SnapshotStore

        html = """
        <html>
            <head><title>WE Offers</title></head>
            <body><main><h1>عروض الإنترنت</h1><p>تفاصيل الباقات الشهرية للعملاء الحاليين والجدد.</p></main></body>
        </html>
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = SnapshotStore(base_dir=tmp_dir)
            cleaned = clean_html(html, base_url="https://www.te.eg/offers")

            # Create previous snapshot with an OLD cleaner_version
            old_pv = PageVersion(
                url="https://www.te.eg/offers",
                canonical_url="https://www.te.eg/offers",
                title=cleaned.title,
                language=cleaned.language,
                fetched_at="2026-01-01T00:00:00Z",
                http_status=200,
                content_hash=cleaned.content_hash,
                cleaner_version="trafilatura-1.0.0+old-rules-v1.0",
                raw_html=html,
                cleaned_text=cleaned.cleaned_text,
            )
            prev_snapshot = store.build_snapshot(
                [{"url": old_pv.url, "text": html, "status_code": 200}]
            )
            # Inject old cleaner version into previous snapshot
            prev_snapshot.pages["https://www.te.eg/offers"] = old_pv

            # Build new snapshot with HTTP 200 and same HTML
            new_snapshot = store.build_snapshot(
                [{"url": "https://www.te.eg/offers", "text": html, "status_code": 200}],
                previous_snapshot=prev_snapshot,
            )

            new_pv = new_snapshot.pages["https://www.te.eg/offers"]
            # Must NOT reuse old PageVersion; must record current CLEANER_VERSION
            self.assertEqual(new_pv.cleaner_version, CLEANER_VERSION)
            self.assertNotEqual(new_pv.cleaner_version, "trafilatura-1.0.0+old-rules-v1.0")

    def test_http_304_recleans_when_cleaner_version_differs_and_raw_html_preserved(self) -> None:
        """On HTTP 304, if cleaner_version differs and raw_html is saved, re-clean raw_html."""
        import tempfile

        from copilot.rag.crawler.models import PageVersion
        from copilot.rag.crawler.snapshot import SnapshotStore

        # Raw HTML containing modal noise that old cleaner might have kept
        html = """
        <html>
            <body>
                <div class="modal fade" id="resetComparableWarning">Products in comparison will be cleared</div>
                <main>
                    <h1>دعم العملاء الفني</h1>
                    <p>فريق الدعم الفني متواجد على مدار الساعة لحل كافة مشاكل الإنترنت المنزلي.</p>
                </main>
            </body>
        </html>
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = SnapshotStore(base_dir=tmp_dir)

            old_pv = PageVersion(
                url="https://www.te.eg/support",
                canonical_url="https://www.te.eg/support",
                title="دعم العملاء",
                language="ar",
                fetched_at="2026-01-01T00:00:00Z",
                http_status=200,
                content_hash="old_dirty_hash",
                cleaner_version="trafilatura-1.0.0+old-rules-v1.0",
                raw_html=html,
                cleaned_text="Products in comparison will be cleared ... old dirty text",
            )
            prev_snapshot = store.build_snapshot(
                [{"url": old_pv.url, "text": html, "status_code": 200}]
            )
            prev_snapshot.pages["https://www.te.eg/support"] = old_pv

            # Receive 304 Not Modified
            new_snapshot = store.build_snapshot(
                [{"url": "https://www.te.eg/support", "status_code": 304, "is_unchanged": True}],
                previous_snapshot=prev_snapshot,
            )

            new_pv = new_snapshot.pages["https://www.te.eg/support"]
            # Proves it re-cleaned: cleaner_version is current, modal is pruned
            self.assertEqual(new_pv.cleaner_version, CLEANER_VERSION)
            self.assertNotIn("resetComparableWarning", new_pv.cleaned_text)
            self.assertNotIn("Products in comparison", new_pv.cleaned_text)
            self.assertIn("دعم العملاء الفني", new_pv.cleaned_text)
            self.assertNotEqual(new_pv.content_hash, "old_dirty_hash")

    def test_http_304_reuses_when_cleaner_version_matches(self) -> None:
        """On HTTP 304, if cleaner_version matches, previous PageVersion is reused."""
        import tempfile

        from copilot.rag.crawler.models import PageVersion
        from copilot.rag.crawler.snapshot import SnapshotStore

        html = "<html><body><main><h1>Test</h1><p>Sample content for test.</p></main></body></html>"
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = SnapshotStore(base_dir=tmp_dir)
            cleaned = clean_html(html, base_url="https://www.te.eg/test")

            current_pv = PageVersion(
                url="https://www.te.eg/test",
                canonical_url="https://www.te.eg/test",
                title=cleaned.title,
                language=cleaned.language,
                fetched_at="2026-01-01T00:00:00Z",
                http_status=200,
                content_hash=cleaned.content_hash,
                cleaner_version=CLEANER_VERSION,
                raw_html=html,
                cleaned_text=cleaned.cleaned_text,
            )
            prev_snapshot = store.build_snapshot(
                [{"url": current_pv.url, "text": html, "status_code": 200}]
            )
            prev_snapshot.pages["https://www.te.eg/test"] = current_pv

            # Receive 304 with matching cleaner version
            new_snapshot = store.build_snapshot(
                [{"url": "https://www.te.eg/test", "status_code": 304, "is_unchanged": True}],
                previous_snapshot=prev_snapshot,
                clock_now="2026-11-16T12:00:00Z",
            )

            new_pv = new_snapshot.pages["https://www.te.eg/test"]
            self.assertEqual(new_pv.cleaner_version, CLEANER_VERSION)
            self.assertEqual(
                new_pv.fetched_at, "2026-01-01T00:00:00Z"
            )  # Preserved original fetched_at
            self.assertEqual(
                new_pv.last_verified_at, "2026-11-16T12:00:00Z"
            )  # Updated last_verified_at

    def test_save_snapshot_automatically_exports_clean_pages_view(self) -> None:
        """Every new snapshot saved must automatically export clean markdown view for chunker inspection."""
        import tempfile

        from copilot.rag.crawler.snapshot import SnapshotStore

        html1 = """
        <html>
            <head><title>Home Fiber</title></head>
            <body><main><h1>إنترنت الفايبر المنزلي</h1><p>أسرع باقات الفايبر بدون انقطاع وبسرعات فائقة.</p></main></body>
        </html>
        """
        html2 = "<html><body><div id='root'></div><noscript>You need to enable JavaScript.</noscript></body></html>"

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = SnapshotStore(base_dir=tmp_dir)
            snapshot = store.build_snapshot(
                [
                    {"url": "https://www.te.eg/fiber", "text": html1, "status_code": 200},
                    {"url": "https://www.te.eg/spa", "text": html2, "status_code": 200},
                ]
            )

            _manifest_path, private_path = store.save_snapshot(snapshot)
            snap_private_dir = private_path.parent

            # 1. Master pages_clean.md must exist
            clean_md = snap_private_dir / "pages_clean.md"
            self.assertTrue(clean_md.exists())
            clean_content = clean_md.read_text(encoding="utf-8")

            # Check format: field names above values
            self.assertIn("### URL\nhttps://www.te.eg/fiber", clean_content)
            self.assertIn("### Canonical URL\nhttps://www.te.eg/fiber", clean_content)
            self.assertIn("### Exact Text Sent to Chunker", clean_content)
            self.assertIn("إنترنت الفايبر المنزلي", clean_content)

            # Check chunker eligibility status
            self.assertIn("### Chunker Status\n**Eligible for Chunker**", clean_content)
            self.assertIn(
                "### Chunker Status\n**Skipped from Chunker** (needs_js=True)", clean_content
            )

            # 2. Individual files in pages_clean/ directory
            clean_dir = snap_private_dir / "pages_clean"
            self.assertTrue(clean_dir.exists())
            page_files = list(clean_dir.glob("*.md"))
            self.assertEqual(len(page_files), 2)

    def test_content_hash_preserves_line_breaks(self) -> None:
        """Content hash must preserve line breaks and markdown structure rather than collapsing them to spaces."""
        from copilot.rag.crawler.cleaner import compute_content_hash

        text_multi = (
            "## باقات الإنترنت\n\nسرعات فائقة تبدأ من 30 ميجابت.\n\n| سرعة | سعر |\n| 30 | 120 |"
        )
        text_collapsed = (
            "## باقات الإنترنت سرعات فائقة تبدأ من 30 ميجابت. | سرعة | سعر | | 30 | 120 |"
        )

        hash_multi = compute_content_hash(text_multi)
        hash_collapsed = compute_content_hash(text_collapsed)

        # Hashes MUST disagree when structure/line breaks change
        self.assertNotEqual(hash_multi, hash_collapsed)
        self.assertTrue(len(hash_multi) > 0)

        # Extraneous space variations within lines should produce identical hash
        text_extra_spaces = "## باقات الإنترنت  \n\nسرعات  فائقة تبدأ   من 30 ميجابت.\n\n| سرعة | سعر |\n| 30 | 120 |"
        self.assertEqual(compute_content_hash(text_extra_spaces), hash_multi)

    def test_nested_lists_with_div_and_span_wrappers(self) -> None:
        """Lists with <div> and <span> wrappers around <li> and sub-lists must preserve hierarchy."""
        html = """
        <ul>
            <div class="list-wrapper">
                <li>
                    <span class="label">الباقة الأساسية</span>
                    <div class="sub-wrapper">
                        <ul>
                            <li>مكالمات غير محدودة</li>
                            <li>10 جيجابايت إنترنت</li>
                        </ul>
                    </div>
                </li>
            </div>
            <div class="list-wrapper">
                <li>الباقة الإضافية</li>
            </div>
        </ul>
        """
        soup = BeautifulSoup(html, "html.parser")
        lines = _extract_list(soup.find("ul"))
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0], "- الباقة الأساسية")
        self.assertEqual(lines[1], "  - مكالمات غير محدودة")
        self.assertEqual(lines[2], "  - 10 جيجابايت إنترنت")
        self.assertEqual(lines[3], "- الباقة الإضافية")

    def test_nested_tables_isolation_and_no_row_cell_mixing(self) -> None:
        """Nested tables must not mix outer and inner rows or cells."""
        html = """
        <table id="outer">
            <tr><th>النظام</th><th>المميزات</th></tr>
            <tr>
                <td>سوبر كيكس</td>
                <td>
                    عروض إضافية
                    <table id="inner">
                        <tr><th>كيكسات</th><th>الصلاحية</th></tr>
                        <tr><td>1000</td><td>30 يوم</td></tr>
                    </table>
                </td>
            </tr>
            <tr><td>وي ميكس</td><td>مشاركة العائلة</td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        table_lines = _extract_table(soup.find("table", id="outer"))

        # Outer table header and rows
        self.assertIn("| النظام | المميزات |", table_lines)
        self.assertIn("| سوبر كيكس | عروض إضافية |", table_lines)
        self.assertIn("| وي ميكس | مشاركة العائلة |", table_lines)

        # Inner table header and rows extracted cleanly
        self.assertIn("| كيكسات | الصلاحية |", table_lines)
        self.assertIn("| 1000 | 30 يوم |", table_lines)

    def test_no_false_positive_js_shell_on_prepaid_page(self) -> None:
        """Pages with card titles or headings returned by HTTP must not be discarded as needs_js=True."""
        html = """
        <html>
            <head><title>باقات WE مسبقة الدفع</title></head>
            <body>
                <main>
                    <h1>أنظمة الكارت</h1>
                    <p>أنظمة كنترول</p>
                    <p>12 قرش</p>
                    <p>اجدع شحنة</p>
                    <p>تكافل و كرامة</p>
                </main>
            </body>
        </html>
        """
        cleaned = clean_html(html, base_url="https://www.te.eg/personal/mobile/prepaid")
        self.assertFalse(cleaned.needs_js)
        self.assertEqual(cleaned.extractor, "beautifulsoup")
        self.assertIn("12 قرش", cleaned.text)
        self.assertIn("تكافل و كرامة", cleaned.text)
        self.assertTrue(len(cleaned.content_hash) > 0)

    def test_extract_links_includes_onclick_and_data_href(self) -> None:
        """extract_links must capture Liferay buttonClick and onclick navigation targets."""
        from copilot.rag.crawler.orchestrator import extract_links

        html = """
        <html>
            <body>
                <a href="/personal/mobile/prepaid">Prepaid</a>
                <a class="btn" onclick="buttonClick(event,'/personal/ekyc-faqs',false)">eKYC</a>
                <div class="card" onclick="window.location.href='/personal/parental-control'">Parental</div>
                <button data-href="/personal/we-pay">WE Pay</button>
            </body>
        </html>
        """
        links = extract_links(html, "https://www.te.eg/")
        self.assertIn("https://www.te.eg/personal/mobile/prepaid", links)
        self.assertIn("https://www.te.eg/personal/ekyc-faqs", links)
        self.assertIn("https://www.te.eg/personal/parental-control", links)
        self.assertIn("https://www.te.eg/personal/we-pay", links)

    def test_extract_links_ignores_static_assets_and_link_tags(self) -> None:
        """extract_links must ignore <link> stylesheets, favicons, scripts, and asset file extensions."""
        from copilot.rag.crawler.config import CrawlConfig
        from copilot.rag.crawler.orchestrator import extract_links
        from copilot.rag.crawler.url_utils import is_allowed_url, is_static_asset

        html = """
        <html>
            <head>
                <link rel="stylesheet" href="/o/CSS_Client_Extenstion/all_styles.min.css" />
                <link rel="icon" href="/documents/20117/0/te.fav.ico/512d81c0-2cbd-b957-0ada-54c158e54275" />
                <link rel="apple-touch-icon" href="/documents/20117/0/te.fav.ico/512d81c0-2cbd-b957-0ada-54c158e54275" />
                <script src="/o/frontend-js-web/liferay/main.js"></script>
            </head>
            <body>
                <a href="/personal/mobile/prepaid">Prepaid</a>
                <a href="/downloads/contract.pdf">Contract PDF</a>
                <a href="/assets/style.css">CSS Link</a>
                <img src="/documents/banner.png" />
            </body>
        </html>
        """
        links = extract_links(html, "https://www.te.eg/")
        self.assertEqual(links, ["https://www.te.eg/personal/mobile/prepaid"])

        # Test is_static_asset
        self.assertTrue(
            is_static_asset("https://www.te.eg/o/CSS_Client_Extenstion/all_styles.min.css")
        )
        self.assertTrue(
            is_static_asset(
                "https://www.te.eg/documents/20117/0/te.fav.ico/512d81c0-2cbd-b957-0ada-54c158e54275"
            )
        )
        self.assertTrue(is_static_asset("https://www.te.eg/images/logo.png"))
        self.assertTrue(is_static_asset("https://www.te.eg/terms.pdf"))
        self.assertFalse(is_static_asset("https://www.te.eg/personal/mobile/prepaid"))

        # Test is_allowed_url rejects static assets
        cfg = CrawlConfig(start_urls=["https://www.te.eg"], allowed_hosts=["www.te.eg"])
        allowed_page, _ = is_allowed_url("https://www.te.eg/personal/mobile/prepaid", cfg)
        allowed_css, reason_css = is_allowed_url(
            "https://www.te.eg/o/CSS_Client_Extenstion/all_styles.min.css", cfg
        )
        allowed_ico, reason_ico = is_allowed_url(
            "https://www.te.eg/documents/20117/0/te.fav.ico/512d81c0", cfg
        )

        self.assertTrue(allowed_page)
        self.assertFalse(allowed_css)
        self.assertIn("static asset", reason_css)
        self.assertFalse(allowed_ico)
        self.assertIn("static asset", reason_ico)


if __name__ == "__main__":
    unittest.main()
