# Product Requirements Document: Polite Web Crawler & Knowledge Snapshot Subsystem

## 1. Overview

The Polite Web Crawler subsystem is the automated knowledge acquisition engine for Support Copilot. It periodically crawls the official Telecom Egypt (WE) customer service web portal (`https://www.te.eg`), cleans raw HTML into high-fidelity Arabic text, and generates immutable, version-controlled snapshots. These snapshots serve as the authoritative grounding corpus for the hybrid RAG (Retrieval-Augmented Generation) pipeline, citations engine, and speech assistants.

The subsystem operates under strict ethical and technical constraints:
- **Zero Aggression**: Enforces robots.txt compliance, mandatory operator contact identification in the `User-Agent`, inter-request politeness delays, and automatic circuit breakers.
- **Arabic-First Precision**: Strips banners, navigation menus, and scripts while preserving Arabic diacritics, eastern/western Arabic numerals, headings, tables, and product lists.
- **Enterprise Deduplication**: Employs two-tier deduplication (HTTP conditional requests and SHA-256 content hashes) to handle dynamic Liferay CMS pages without redundant network transfer or duplicate index generation.
- **Privacy & Copyright Boundary**: Public Git manifests contain only cryptographic hashes and metadata; raw page text is strictly quarantined in private, git-ignored storage.

---

## 2. User Stories

- **US-CRAWL-1 (Support Visitor)**: As a telecom customer asking questions about internet tariffs or fiber upgrades, I want answers grounded in up-to-date, verified Telecom Egypt documentation with exact source links and fetch dates, so that I receive reliable assistance.
- **US-CRAWL-2 (Website Administrator / Upstream Ops)**: As a webmaster for `te.eg`, I want automated bots to clearly identify their operator contact info, respect `robots.txt` rules, throttle requests with polite delays, and back off upon server errors, so that our production service availability is never degraded.
- **US-CRAWL-3 (AI Engineer / Operator)**: As an AI engineer, I want to trigger incremental crawls that only fetch updated pages, resume interrupted jobs without duplicate downloads, and inspect visual trace waterfalls in Jaeger, so that I can maintain the knowledge base efficiently.
- **US-CRAWL-4 (Compliance & Legal Officer)**: As a compliance officer, I want scraped proprietary webpage text to remain quarantined on private storage rather than committed to public source control, so that we comply with copyright policies.
- **US-CRAWL-5 (RAG Pipeline Service)**: As the chunking and embedding service, I want to consume immutable snapshots indexed by content hashes with atomic rollback capabilities, so that search indices remain stable and reproducible.

---

## 3. Acceptance Criteria

### 3.1 Politeness & Safety
- **AC-SAF-1**: The crawler MUST refuse to execute network requests unless an operator contact email or URL is configured via `CRAWL_CONTACT`.
- **AC-SAF-2**: The crawler MUST parse `robots.txt` prior to crawling and immediately skip any disallowed URL paths.
- **AC-SAF-3**: The crawler MUST enforce a configurable inter-request delay (default: `1.0s`) and honor upstream `Crawl-delay` directives when present.
- **AC-SAF-4**: The crawler MUST halt immediately via circuit breaker if 3 consecutive requests encounter rate limits (429) or server errors (5xx).

### 3.2 Cleaning & Content Extraction
- **AC-CLN-1**: Extracted text MUST omit navigation bars, headers, footers, cookie banners, and `<script>`/`<style>` elements.
- **AC-CLN-2**: Extracted text MUST preserve Arabic sentence boundaries, headings, bullet points, and tables.
- **AC-CLN-3**: Pages with dynamic JavaScript shells (SPAs) containing minimal static text MUST be detected and tagged with `needs_js=True`.
- **AC-CLN-4**: Volatile content (such as changing timestamp banners or session IDs) MUST NOT alter the page's SHA-256 `content_hash`.

### 3.3 Deduplication & Snapshots
- **AC-SNP-1**: Unchanged pages MUST reuse the previous snapshot's `PageVersion`, retaining original timestamps and content hashes while refreshing `last_verified_at`.
- **AC-SNP-2**: Once created, a snapshot folder MUST be immutable and write-protected against modification.
- **AC-SNP-3**: Public manifests (`manifest.jsonl`) MUST strictly omit page text (`raw_html` and `cleaned_text`).
- **AC-SNP-4**: The active snapshot pointer (`active.txt`) MUST be updated atomically via temporary files and `os.replace`.

### 3.4 Observability
- **AC-OBS-1**: Structured logs MUST emit only domain names and irreversible SHA-256 path hashes; raw query strings or URLs MUST never be logged.
- **AC-OBS-2**: OpenTelemetry root spans (`copilot.rag.crawl`) and nested per-page child spans (`crawler.fetch_page`) MUST be emitted to Jaeger over OTLP.

---

## 4. Behavioral Scenarios

## PRD-CRAWL-001 - Politeness Delay and Contact User-Agent Header

Version: 1 - 2026-11-13

Intent:
Ensure all outbound HTTP requests carry an honest User-Agent containing the operator contact identifier and respect the configured politeness delay.

Given:
- A valid `CrawlConfig` specifying `user_agent_prefix: SupportCopilot/0.1.0` and `delay_seconds: 1.0`.
- The environment variable `CRAWL_CONTACT=crawler-ops@support-copilot.org`.

When:
- The crawler initializes `PoliteClient` and issues requests to start URLs.

Then:
- Outbound request headers include `User-Agent: SupportCopilot/0.1.0 (+https://github.com/omr746/Support-Copilot; contact: crawler-ops@support-copilot.org)`.
- Consecutive network requests to the same host are spaced by at least `1.0` seconds.

Verification:
Automated by: test_polite_delay_and_contact_user_agent

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-002 - Robots.txt Disallow Path Enforcement

Version: 1 - 2026-11-13

Intent:
Prevent crawling of URLs explicitly prohibited by the target host's robots.txt policy.

Given:
- Target server serves a `robots.txt` containing `User-agent: *` and `Disallow: /customer-service/internal-notes`.
- Frontier queue contains `https://www.te.eg/customer-service/internal-notes`.

When:
- `CrawlOrchestrator` pops the URL and checks `robots_policy.can_fetch(user_agent, url)`.

Then:
- The URL is skipped without issuing an HTTP request.
- Metric `summary.blocked_robots` increments by 1.
- Structured audit event `blocked_robots` is logged with status code `403`.

Verification:
Automated by: test_robots_disallowed_blocks_subpath

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-003 - Circuit Breaker Consecutive Error Halting

Version: 1 - 2026-11-13

Intent:
Halt the crawl run immediately when upstream servers return repeated error responses to prevent Denial-of-Service effects.

Given:
- `CrawlConfig` configured with `consecutive_error_limit: 3`.
- A target server returning HTTP `429 Too Many Requests` or HTTP `500 Internal Server Error`.

When:
- The crawler receives 3 consecutive failure responses.

Then:
- `CrawlBlockedError` is caught and the circuit breaker trips.
- The crawler halts the active crawl loop immediately.
- Crawl state is flushed to disk with remaining URLs intact.

Verification:
Automated by: test_circuit_breaker_halts_on_consecutive_errors

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-004 - Boilerplate Stripping and Arabic Content Retention

Version: 1 - 2026-11-13

Intent:
Extract clean Arabic text and tables while discarding navigational headers, footers, cookie banners, and scripts.

Given:
- Raw HTML document containing Arabic body copy, nested navigation bars, and marketing scripts.

When:
- `clean_html(raw_html, url)` processes the page.

Then:
- Output `cleaned_text` contains all Arabic body paragraphs and headings.
- Scripts, stylesheets, navigation tags, and footer banners are completely removed.
- Language is detected as `ar`.

Verification:
Automated by: test_cleaner_keeps_main_text_and_drops_boilerplate

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-005 - JavaScript-Only SPA Shell Detection

Version: 1 - 2026-11-13

Intent:
Flag empty Single Page Application (SPA) shells so they are excluded from indexing and earmarked for headless rendering.

Given:
- Raw HTML containing `<div id="app"></div>` and heavy JavaScript bundles with fewer than 30 meaningful words of text.

When:
- `clean_html` cleans the HTML payload and evaluates DOM heuristics.

Then:
- Resulting `CleanedPage` has `needs_js=True`.
- `CrawlSummary.needs_js` counter increments by 1.
- Page is omitted from `snapshot.active_pages`.

Verification:
Automated by: test_javascript_only_shell_flagged_needs_js

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-006 - Timestamp Change Resistance via Content Hash

Version: 1 - 2026-11-13

Intent:
Prevent false-positive document versioning caused by volatile timestamp banners and rotating session tokens.

Given:
- A webpage version crawled at Time T1.
- The same webpage crawled at Time T2 containing an updated footer timestamp "Updated on 14:02".

When:
- `compute_content_hash` normalizes whitespace and calculates SHA-256 over cleaned text.

Then:
- Content hash at T1 matches content hash at T2 exactly.
- Page is identified as unchanged and no new version is created.

Verification:
Automated by: test_timestamp_and_banner_changes_keep_same_content_hash

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-007 - Incremental Crawl Idempotence & HTTP 304 Reuse

Version: 1 - 2026-11-13

Intent:
Reuse existing page versions when the server responds with HTTP 304 Not Modified to eliminate bandwidth and processing overhead.

Given:
- An active snapshot containing a page version with `etag: "v1-xyz"` and `last_modified: "Wed, 21 Oct 2026 07:28:00 GMT"`.

When:
- A new crawl requests the page sending conditional `If-None-Match` and `If-Modified-Since` headers.
- The web server responds with HTTP `304 Not Modified`.

Then:
- `CrawlSummary.unchanged` increments by 1.
- New snapshot reuses the existing `PageVersion` object.
- `last_verified_at` timestamp is updated to the current crawl time.

Verification:
Automated by: test_incremental_crawl_idempotence_and_304_reuse

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-008 - Dry-Run Execution Without Disk Writes or Network

Version: 1 - 2026-11-13

Intent:
Permit operators to simulate crawl scope, URL filtering, and configuration without issuing network requests or writing to disk.

Given:
- A valid crawler configuration.
- The CLI flag `--dry-run` is passed.

When:
- `CrawlOrchestrator.run(dry_run=True)` executes.

Then:
- Exactly 0 outbound HTTP network requests are made.
- Exactly 0 bytes are written to `data/snapshots/` or `data/private/`.
- Summary report returns discovered start URLs and exits with code 0.

Verification:
Automated by: test_dry_run_makes_no_network_calls_and_writes_nothing

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-009 - Atomic Interruption and Resume Without Duplicate Fetches

Version: 1 - 2026-11-13

Intent:
Allow an interrupted crawl to resume exactly where it stopped without repeating visits to previously fetched URLs.

Given:
- An interrupted crawl session with checkpoint saved in `.crawl_state.json` containing 10 visited URLs and 40 queued URLs.

When:
- The operator executes `make crawl-resume` (or `orchestrator.run(resume=True)`).

Then:
- Previously visited URLs are restored to `_visited` and never requested again.
- Frontier queue resumes processing from URL 11.
- Final snapshot aggregates results from both run segments.

Verification:
Automated by: test_crawl_interruption_and_atomic_resume

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-010 - Snapshot Immutability and Write Protection

Version: 1 - 2026-11-13

Intent:
Protect persisted snapshot directories from subsequent accidental overwriting.

Given:
- A snapshot already committed at `data/snapshots/2026-11-13-test01/`.

When:
- A process attempts to save a new snapshot using the identical snapshot ID.

Then:
- `SnapshotStore.save_snapshot` raises `FileExistsError`.
- Existing files and manifests remain unmodified.

Verification:
Automated by: test_snapshot_immutability_refuses_overwrite

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-011 - Public Manifest Privacy Separation (No Text Fields)

Version: 1 - 2026-11-13

Intent:
Enforce copyright and privacy compliance by ensuring public committed manifests never contain raw or cleaned webpage text.

Given:
- A completed snapshot build.

When:
- `SnapshotStore.save_snapshot` writes `data/snapshots/<id>/manifest.jsonl`.

Then:
- Manifest JSON records contain `url`, `canonical_url`, `language`, `http_status`, and `content_hash`.
- Manifest JSON records strictly omit `text`, `cleaned_text`, and `raw_html`.
- Full text fields exist solely in `data/private/snapshots/<id>/pages.jsonl`.

Verification:
Automated by: test_manifest_has_no_text_field

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-012 - Active Snapshot Activation and Atomic Rollback

Version: 1 - 2026-11-13

Intent:
Enable instant, atomic switching between active knowledge base versions and zero-downtime rollback.

Given:
- Two completed snapshots: `2026-11-13-v1` and `2026-11-13-v2`.

When:
- Operator activates `2026-11-13-v2` via `activate_snapshot("2026-11-13-v2")`, and subsequently rolls back to `2026-11-13-v1`.

Then:
- `data/snapshots/active.txt` atomically updates via temporary file replacement.
- `store.get_active_snapshot()` immediately reflects the chosen version.

Verification:
Automated by: test_switching_and_rolling_back_active_snapshot

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-013 - Structured Logging Privacy (No Raw URLs or Queries)

Version: 1 - 2026-11-13

Intent:
Ensure structured crawler access logs never leak query parameters or sensitive tracking tokens to log storage.

Given:
- A visited URL containing sensitive query parameters: `https://www.te.eg/support?token=secret123&session=999`.

When:
- `log_page_visit` emits a structured log event.

Then:
- Log record `host` is `www.te.eg`.
- Log record `path_hash` contains SHA-256 of `/support`.
- No log field contains `token`, `secret123`, or query parameters.

Verification:
Automated by: test_structured_log_never_contains_query_string

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13

---

## PRD-CRAWL-014 - OpenTelemetry Trace Emission to Jaeger

Version: 1 - 2026-11-13

Intent:
Export end-to-end distributed tracing telemetry for crawl workflows to Jaeger via standard OTLP protocols.

Given:
- OpenTelemetry SDK configured with `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`.

When:
- A crawl executes under `trace_crawl_run("copilot.rag.crawl")`.

Then:
- Root span `copilot.rag.crawl` is created with attributes `crawl.total_visited`, `crawl.fetched`, `crawl.duration_seconds`.
- Nested child spans `crawler.fetch_page` are recorded for each visited page.
- Buffered spans are force-flushed to Jaeger upon completion.

Verification:
Automated by: test_crawl_span_and_metrics_emitted

Review:
Status: Approved
Reviewed version: 1
Reviewed by: tech-lead
Reviewed at: 2026-11-13
