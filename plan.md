# Support Copilot: Step-by-Step Execution Plan

*Version 0.2 · November 11, 2026 · Companion to the PRD · 8 weeks (Week 2 is split into 2A and 2B)*

**Change in v0.2:** Week 2 is rewritten for professional, idempotent RAG from crawl to answer. New step 2.3b (incremental ingestion), a pipeline fingerprint, a content hash on cleaned text, a snapshot-to-page-version schema, ingestion metrics and spans, retrieval-stage tracing, deeper evals with a reproducibility record, and step 2.13 (judge calibration). This satisfies FR-RAG-4.

## How to use this plan

- Work in order. Every step ends with a “Done when” check. Do not start the next step until it is true.
- The plan assumes about 10 to 15 hours per week. If you have less, stretch the weeks. If you fall behind, cut P2 items first (see the last sections).
- Open one pull request per step, even though you work alone. Watching CI pass or fail on your own code is part of the training.
- Keep a file called LOG.md. After every work session write: what you did, what broke, how you fixed it, and any number you measured. This file becomes your README results and your interview stories.
- Build a thin end-to-end version first and improve it in place. Deploy early.
- Never commit secrets. Commit .env.example, never .env. Send only public or synthetic data to free-tier APIs.

**Testing rule:** a step is not done until its tests are written and green in CI. Write the contract test before the provider it checks. Use fakes and mocked HTTP by default, and mark anything that calls a real, rate-limited API with @pytest.mark.live so CI never spends your quota. Aim for at least 80% coverage on core and ports.

| Test type | What it proves | When it runs |
| --- | --- | --- |
| Unit | One function or class works, using fakes | Every commit |
| Contract | Every provider honors the same port | Every pull request, mocked HTTP |
| Integration | Modules work with real Postgres and Redis | Every pull request, docker compose |
| Eval | Answer, ASR and TTS quality | Subset on pull requests, full run nightly |
| Smoke | The deployed system answers | After every deploy |
| Load | Throughput and latency limits hold | Before a release |
| Live | A real provider call works (uses quota) | Manually and nightly only |

## MLOps and LLMOps at a glance

LLMOps manages the prompts, models, evaluations and cost of the LLM application. MLOps manages any model you train or host yourself: the embedder, the reranker, local ASR and TTS, and a small classifier. Both are built into the weeks below as new steps, about 3 to 4 extra days in total; step 6.8 is optional.

| Practice | What it means here | Free tools to start with | Step |
| --- | --- | --- | --- |
| Prompt versioning | Prompts live in versioned files, and every trace records the prompt id and hash | Git (Langfuse prompt management is optional; check plan limits) | 2.11 |
| Dataset versioning | Eval sets, ASR audio and the corpus have versions and checksums | Git, plus DVC or Git LFS for large files | 2.12 |
| Pipeline fingerprint | Every page version and chunk records the cleaner, normalizer, chunker and embedder versions, so a change reprocesses only what it affects | Postgres | 2.1 to 2.3b |
| Judge calibration | The LLM judge is checked against a small human-labeled sample before its numbers are trusted | Git, a small script | 2.13 |
| Red-team regression | Every attack and incident becomes a permanent test case | pytest | 3.8 |
| Experiment tracking | Each eval or training run is recorded as a Langfuse experiment run, with code, data, prompt and model versions and its scores | Langfuse datasets and experiment runs | 5.7 |
| Model registry | Each role has a pinned model id approved by a named eval run; local models are versioned with a model card | A lock file in Git, plus Hugging Face Hub or Git LFS for local model files | 5.8 |
| Index versioning | A new embedding model or chunk size builds a new index that is switched on after evaluation, with rollback | Postgres | 6.6 |
| Cost budgets | Daily token and cost budgets per role, with a degraded mode and alerts | Config, Prometheus | 6.7 |
| Training pipeline | A reproducible train, evaluate, register and deploy loop for a small router classifier | scikit-learn, Langfuse, DVC | 6.8 (optional) |
| Online evaluation | Sampled production traces are scored, and bad ones feed the golden set | Langfuse scores, a scheduled job | 7.8 |
| Drift and canary checks | A daily eval and distribution checks catch silent provider or data changes | Nightly workflow, Prometheus | 7.9 |
| Deprecation watch | A weekly job warns before a pinned model is retired | Scheduled script | 7.10 |
| Governance | Runbooks for quality drops, retired models and cost spikes, plus an append-only approval audit log | Markdown, Postgres | 8.7 |

## Knowledge sources at a glance

The knowledge base has three source types. The built-in WE customer-service pages are crawled by you and shared with every visitor. Files and websites that a user adds are private to that user. These steps add about one to two weeks in total. If time runs short, cut user websites (steps 3.12 and 3.13) first.

| Source | Who adds it | Who can see it | Steps |
| --- | --- | --- | --- |
| Built-in WE pages (te.eg) | You, with the crawl command | Every visitor, read-only | 2.0a to 2.0c, 2.3b, 2.5a, 7.11 |
| User files | Each visitor | Only that visitor | 3.9 to 3.11, 3.14 |
| User websites | Each visitor (feature flag, off by default in the public demo) | Only that visitor | 3.12, 3.13, 3.14 |

## Step 0: Prepare (1 to 2 days)

**0.1 Install tools.** Git, Python 3.12, uv (Python package manager), Docker Desktop (on Windows enable WSL2), VS Code, ffmpeg.

**Tests:** write scripts/check\_env.sh that runs the git, python, uv, docker and ffmpeg version checks and exits with an error if any tool is missing. Run it on every new machine.

**0.2 Create accounts.** GitHub, Groq, Google AI Studio (Gemini key), Langfuse Cloud (Hobby plan), Grafana Cloud (free plan). Cloud hosting comes in Week 1, step 1.10.

**Tests:** write a live-marked script that checks each key exists in .env (without printing it) and makes one tiny call per provider. A bad key must fail with a clear message.

**0.3 Make three decisions** and write them in LOG.md:

- Knowledge base: the built-in source is the WE / Telecom Egypt customer-service pages (te.eg), crawled politely by you, and users add their own files and websites. Before any code, read the site's robots.txt and terms of use and write the result in LOG.md. If automated access is not allowed, use pages you save by hand. Pair it with a fake backend database for the agent (see PRD question Q8).
- Hosting path: Cloud Run if you can provide a payment method for verification; otherwise a no-card container host, or a local demo plus a video.
- UI language: Arabic only, or bilingual.

**Tests:** each decision has a dated reason in LOG.md. Add a CI check that fails if LOG.md or docs/adr/ is missing.

**0.4 Create the repository.** Public GitHub repo named support-copilot with README stub, LICENSE, .gitignore. Protect the main branch so CI must pass before merging.

**Tests:** open a throwaway pull request with a deliberately failing test and confirm main cannot be merged until it is fixed.

**0.5 Write ADR-0001** in docs/adr/: why ports and adapters with a provider registry.

**Tests:** add a small script that fails CI when an ADR file lacks Status, Context or Decision headings.

**Done when:** docker run hello-world works, python --version and uv --version print versions, your keys are in a git-ignored .env, and the repo exists with a protected main branch.

## Week 1: Foundations and walking skeleton

Goal: a small but real system that runs in Docker, passes CI, makes one LLM call through a port, and has a health endpoint on a public URL.

**1.1 Project skeleton.** Use uv init and a src layout (src/copilot). Create folders core, llm, observability, api. Define optional dependency groups in pyproject.toml: local-asr, local-tts, evals, dev.

**Tests:** pytest runs and finds at least one test. A smoke test imports copilot and every subpackage. An import test proves the default install does not pull in the optional extras.

**1.2 Core package.** Write types, the shared errors (ProviderError, RateLimitError, ProviderTimeout, AuthError, InvalidInputError), the generic Registry class, and the config loader (pydantic-settings plus YAML). Done when a unit test registers and creates a fake component.

**Tests:** Registry: a duplicate name raises, an unknown name lists the available ones. Config: YAML plus environment override works, a missing key fails with a clear message. Errors: each carries the provider name, and RateLimitError carries retry\_after.

**1.3 LLM port.** Write the ChatModel Protocol, the request and response schemas, and a FakeLLM in tests/fakes.

**Tests:** FakeLLM satisfies the ChatModel Protocol (runtime check) and returns a valid response. Schemas are frozen, and an invalid role is rejected.

**1.4 First provider.** Write one OpenAI-compatible provider and point it at Groq through config. Translate SDK errors into your core errors. Define the roles (router, answer, judge) in YAML. Done when a contract test passes against a mocked HTTP layer and one manual live call works.

**Tests:** contract tests with mocked HTTP: a success maps to a valid response, 429 becomes RateLimitError with retry\_after, a timeout becomes ProviderTimeout, 401 becomes AuthError, a malformed body becomes ProviderError. Parametrize the suite over every registered provider. Add one live-marked test for the manual call.

**1.5 Wrappers.** Add Retrying and Fallback wrappers; use Gemini as the second provider in the chain.

**Tests:** Retrying succeeds after two transient errors, gives up after the maximum attempts, and never retries AuthError or InvalidInputError (use a fake clock for backoff). Fallback moves to the next provider on RateLimitError, raises the last error when all fail, and records which provider answered.

**1.6 API.** Build a FastAPI app with /healthz, /readyz and a stub /ask that calls the answer role. Wire everything in api/container.py (the composition root).

**Tests:** with FastAPI TestClient: /healthz returns 200; /readyz returns 503 when a fake dependency is down and 200 when up; /ask returns the FakeLLM answer through a dependency override (this proves the composition root works); a bad payload returns 422.

**1.7 Logging.** Add structlog JSON output, a request\_id middleware, and a redaction processor.

**Tests:** log lines are valid JSON with request\_id, the id is the same across lines of one request and different across requests. Redaction masks an email, a phone number, a card number and a token. No message body appears at INFO.

**1.8 Docker.** Write a multi-stage Dockerfile (non-root user, ffmpeg installed) and a docker-compose.yml core profile with the app, Postgres (pgvector image) and Redis. Done when docker compose up works and curl on /healthz returns ok.

**Tests:** a CI job runs docker compose up with the CI file, waits for health, then calls /healthz and /readyz. It also checks that the container runs as non-root, that ffmpeg is present, and that the image size stays under a limit you set.

**1.9 CI.** GitHub Actions workflow running ruff, mypy, import-linter and pytest, then building the image. Done when a pull request shows a green check.

**Tests:** test the pipeline itself. Push a throwaway branch with a lint error, a type error, a failing test and an import-linter violation, and confirm each one turns the check red.

**1.10 Deploy the skeleton.** Follow the hosting path from step 0.3. For Cloud Run the core commands are:

```
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
gcloud run deploy support-copilot --source . --region YOUR_REGION --allow-unauthenticated --max-instances 2
```

Set a budget alert in Billing right away. Done when a public URL returns ok on /healthz.

**Tests:** write scripts/smoke.sh that takes the URL and checks /healthz, /readyz and the stub /ask. Run it by hand now; Week 7 runs it automatically after every deploy.

**Record in LOG.md:** the public URL, a screenshot of green CI, and every problem you hit during the first deploy.

## Week 2: RAG (professional, idempotent, observable)

Goal: grounded answers with citations over the built-in WE snapshot, where re-running ingestion does nothing when nothing changed, every stage is observable, and every number you measure can be reproduced. This week has about 25 steps, so treat it as two weeks.

- **Week 2A (ingest and answer):** 2.0a, 2.0b, 2.0c, 2.1, 2.2, 2.3, 2.3b, 2.4, 2.5, 2.5a. Exit: a cited answer from the real snapshot and a passing "run it twice" test.
- **Week 2B (measure and operate):** 2.6 to 2.13. Exit: baseline numbers with a before/after, a dashboard, a trace and a CI gate.
- If time is short, move 2.11 and 2.12 to the start of Week 3. Never cut 2.3b, 2.7, 2.8 or 2.9.

**Design rules for this week**

1. **Identity by content.** A page version is identified by the hash of its cleaned main text. A chunk is identified by the hash of its normalized text plus its section path.
2. **Pipeline fingerprint.** Every page version and chunk records a fingerprint: a hash of the cleaner version, normalizer version, chunker settings, embedder id and embedder dimension. If the fingerprint changes, affected items are reprocessed. If not, they are skipped.
3. **Snapshots are views.** A snapshot is a list of page-version ids. Chunks belong to a page version. Unchanged pages are shared between snapshots, so nothing is duplicated.
4. **Everything is an upsert.** Every write uses a unique key, so running a job twice or resuming a killed job cannot create duplicates.
5. **Every stage emits a metric and a span** with bounded labels (source type, outcome). Never user, session or request ids as labels.

**2.0a Crawl rules and polite client.** Create configs/crawl.yaml with the start URLs (https://www.te.eg/we is your starting point; confirm the exact customer-service URLs by hand), the allowed host and path prefixes, denied patterns (login, account, payment, forms, query strings), the languages (ar and en), a page cap, a delay between requests, and a User-Agent that names the project. The contact address in the User-Agent comes from an environment variable (CRAWL\_CONTACT), never from a file in the public repository. First read the site's robots.txt and terms of use in a browser and note the result in LOG.md. If automated access is not allowed, stop and use pages you save by hand. Then write the robots.txt check (Python's urllib.robotparser works) and a polite HTTP client: one connection, a delay, timeouts, retries with backoff, conditional requests (ETag and Last-Modified), and a stop after repeated 403 or 429 answers. A missing robots.txt (404) allows crawling. A robots.txt that cannot be fetched (5xx or a network error) means do not crawl.

**Tests:** everything runs against a local fake HTTP server, never the real site. Robots rules: allowed, disallowed, crawl-delay, 404 allows, 5xx blocks. The allowlist rejects other hosts and denied patterns. URLs are normalized (fragments and tracking parameters removed). A fake clock proves the delay between requests. The client stops after repeated 429 or 403 answers, treats a 304 as unchanged, and always sends the User-Agent. The command refuses to run without CRAWL\_CONTACT.

**2.0b Fetch, clean, store and snapshot.** Fetch only allowed pages and extract the main text (drop navigation, footers and scripts; keep headings, lists and tables as text) with an HTML-to-text library that installs on Python 3.12 and handles Arabic. Detect the page language. Pages that look empty without JavaScript are recorded as needs\_js and not indexed.

*Content hash rule:* hash the cleaned main text after whitespace normalization, never the raw HTML. Keep the response ETag and Last-Modified separately, only for conditional requests. Record the cleaner version with every page version.

Store each page version with URL, canonical URL, title, language, fetched-at time, HTTP status, content hash and cleaner version. Every crawl creates a snapshot id (the date plus a short hash). Snapshots never change: unchanged pages (same content hash) reuse the existing page version, changed pages get a new version, and removed pages are marked removed in the new snapshot only. The active snapshot is a config value, and rollback means switching it back. Commit only a manifest per snapshot (data/snapshots/\<id>/manifest.jsonl with URL, time, language and hash, no page text). Page text lives in the database and in a private backup under data/private/ (git-ignored), because the content belongs to the website owner. Decide where the private backup is kept so the nightly job in step 5.6 can load it.

**Tests:** with fixture HTML files (Arabic, English, mixed, with navigation, footer and script tags): the main text is kept and the boilerplate removed, Arabic text and digits survive, and the same page gives the same hash on every run. A page whose only change is a timestamp, banner, tracking token or footer keeps the same hash. A real text change gives a different hash. Two pages with the same canonical URL are stored once. A JavaScript-only shell is flagged needs\_js. A 404 is recorded without content. A removed page is marked removed in the new snapshot only. Switching and rolling back the active snapshot changes what retrieval reads. The manifest has no text field (the test checks its keys).

**2.0c Crawl command and report.** Add python -m copilot.rag.crawl --config configs/crawl.yaml as a separate command (not part of the web server), with a dry-run mode that only lists the URLs it would fetch, a resume option, and a summary report: fetched, unchanged, changed, removed, failed, blocked by robots.txt, needs\_js. Add crawl metrics (pages by outcome, crawl duration, snapshot age), one span per crawl run, and one structured log line per page (host and path hash, never the query string). Scheduling comes in step 7.11.

**Tests:** a dry run makes no network calls and writes nothing. A resume continues after an interruption without duplicates. The summary counts match a fake server with known pages. The exit code is non-zero when the failure rate is above a threshold. Running the crawl twice against an unchanged fake server reports everything as unchanged and creates no new page versions.

**2.1 Normalization, chunking and fingerprint.** Chunk by headings (start with 500 to 800 tokens and some overlap). Normalize Arabic text before chunking (diacritics, alef, yaa and taa marbuta variants, Arabic-Indic digits) and keep both the original and the normalized text, so users always see the original. Keep metadata: source type (builtin, upload or website), owner id (empty for built-in), source id, page version id, URL or filename, title, section path, language, fetched-at time, a chunk content hash (normalized text plus section path) and the fingerprint parts (normalizer version, chunker settings). Chunk ids are deterministic: derived from the page version id, section path and chunk index.

**Tests:** no chunk exceeds the token limit, overlap is respected, metadata is kept (source type, owner, language, page version), Arabic and English text both work, normalization changes the search text but never the text shown to the user, an empty document is handled, and a short section is not split across a heading. The same input always gives the same chunk ids and chunk hashes. Changing one sentence changes the hash of the chunk that contains it (and at most its overlap neighbor), and no others. Changing the chunker settings or the normalizer version changes the fingerprint.

**2.2 Embeddings.** Put an Embedder port in front of a small multilingual CPU model that handles Arabic, English and cross-language search (fastembed or sentence-transformers CPU build; check the size and license on the model card). Gemini embeddings can be a second provider. The port exposes model\_id, dimension and a fingerprint string, so the pipeline fingerprint never depends on provider details.

**Tests:** an Embedder contract suite: the output dimension matches the config, the same text gives the same vector, a batch equals single calls. A sanity test checks that a paraphrase scores higher than an unrelated sentence, in Arabic, in English and across languages (an Arabic question against English text). FakeEmbedder counts its calls (step 2.3b needs this) and is used everywhere else.

**2.3 Database.** Write an Alembic migration. Snapshots point to page versions, and chunks belong to page versions, so unchanged pages are never duplicated.

- snapshots: id, created\_at, status, gate\_report\_id.
- page\_versions: id, canonical\_url, content\_hash, cleaner\_version, language, title, fetched\_at, text, status.
- snapshot\_pages: snapshot\_id, page\_version\_id, state (present or removed).
- sources: id, type, owner\_id, name, checksum, status, quota counters.
- chunks: id, page\_version\_id or source\_id, owner\_id, source\_type, section\_path, language, text, normalized\_text, chunk\_hash, pipeline\_fingerprint, a pgvector column, a full-text search column and tombstoned\_at.
- embedding\_cache: chunk\_hash, embedder fingerprint, vector, so identical chunks are never embedded twice.
- jobs: id, type, source\_id, status, reason, started\_at, finished\_at, counters as JSON.

Unique keys: (canonical\_url, content\_hash) on page versions, (page\_version\_id, chunk\_index, pipeline\_fingerprint) on chunks, and (chunk\_hash, embedder fingerprint) on the cache. Add a Postgres advisory-lock helper for single-job execution. Check which text-search configuration your Postgres offers for Arabic. If none fits, use the 'simple' configuration on normalized text, and measure the choice in step 2.7.

**Tests:** an integration test against the CI Postgres: the migration runs up and down, constraints hold, the vector column has the right dimension, a full-text query returns the expected row, and an Arabic query finds an Arabic row after normalization. Inserting the same page version or chunk twice hits the unique key, and the upsert helper handles it cleanly. Two snapshots that share an unchanged page share the same chunk rows (count them).

**2.3b Incremental ingestion (new).** Ingestion reads the pages of the target snapshot and brings the chunk store up to date. For each page version:

1. If its chunks already exist with the current pipeline fingerprint, skip it.
2. Otherwise re-chunk it and compute the chunk hashes.
3. For each chunk, look up embedding\_cache by (chunk hash, embedder fingerprint). Embed only the misses.
4. Upsert the chunks and cache rows in one transaction per page version.
5. Tombstone the chunks of pages removed in the target snapshot, and chunks left over from older versions of a changed page that no active snapshot references.

Run it as a background job (not in the web process), one at a time under the advisory lock, with a stored status and counters: pages skipped, pages processed, chunks created, chunks reused, chunks embedded and chunks tombstoned. Later, uploads and user websites use the same ingestion path.

**Tests:** *run it twice:* the second run writes zero rows, makes zero embedder calls (assert on FakeEmbedder) and produces identical chunk ids. Change one page: only that page is reprocessed, and if one sentence changed, only the affected chunks are embedded. Change only boilerplate (header, footer, timestamp): nothing is reprocessed. Change the chunker settings, normalizer version or embedder id: everything is reprocessed, and old-fingerprint chunks are never returned by retrieval. Kill the job mid-run (raise an exception after N pages) and rerun: no duplicates, and the final state equals a clean uninterrupted run. Two jobs started at once: the second waits or exits cleanly because of the lock. A removed page's chunks never appear in retrieval (not just a flag on the page). A failed embed call for one page rolls back that page only, marks it failed with a reason, and the job continues. The counters in the job record match the rows actually written.

**2.4 Retrieval.** Combine vector search and keyword search (reciprocal rank fusion), then rerank with a small cross-encoder if your machine allows it. Every query searches the active built-in snapshot (chunks reachable through its page versions, current fingerprint, not tombstoned) plus the caller's own sources and nothing else. Expose each stage separately (vector, keyword, fuse, rerank) so each can be traced and evaluated on its own.

**Tests:** use a tiny fixture corpus of about 10 documents with designed queries. Vector-only, keyword-only and hybrid search each return the expected top result. Unit-test the fusion with known rankings, check that the reranker reorders a known pair, that k is respected, and that an empty result is handled. One user's chunks are never returned to another user. Chunks from a non-active snapshot, tombstoned chunks and old-fingerprint chunks are never returned. Each stage returns chunk ids with scores in a structure the tracing code can record.

**2.5 Answering.** Send only the top 3 to 4 chunks to the model, require citations, and add a clear "not found" path. Small contexts protect your rate limits. Wrap retrieved text as untrusted data in the prompt. Load the answer prompt from the registry once step 2.11 exists; until then keep it in one file with a version constant.

**Tests:** with FakeLLM: the prompt holds at most the allowed number of chunks, each with its id. With no evidence the answer is the not-found message. Every citation maps to a chunk that was actually retrieved, so no source is invented. Retrieved text appears inside the untrusted-data wrapper.

**2.5a Source labels, freshness and disclaimer.** Each citation shows where it came from: a built-in page (URL and fetched-at date), your file (filename) or your website (URL and fetched-at date). Pages older than a configured age get a note that the information may be out of date, with a link to the official site. Built-in answers state that this is an unofficial assistant, not affiliated with Telecom Egypt or WE, and the UI uses no WE logos. The not-found message points to the official site (the link comes from config).

**Tests:** citations contain the source label and the date; a page older than the limit (with a fake clock) adds the note; the disclaimer is present in the API response and in the UI for built-in answers; the not-found message contains the configured link.

**2.6 Golden set.** Write the questions yourself from the crawled pages, starting with 30 and growing to 100: about half in Arabic and half in English, including cross-language pairs. For each, store the expected source URL, the snapshot id and your own short notes on the expected answer (do not copy page text into the repository). Tag each question with a type (factual, procedural, cross-language, volatile, adversarial) so results can be broken down. Mark questions about prices and offers as volatile, because they change when the site changes. Add 20 adversarial cases (injection, out of scope, ambiguous, unanswerable). Store them in data/eval/llm/v1/ with a dataset card.

**Tests:** write a dataset validator script and run it in CI: every question has a unique id and tags, each expected source URL exists in the manifest of the named snapshot, and the checksum matches the dataset card.

**2.7 Evaluation.** Use the judge role, a different model from the one being tested. Measure:

- Retrieval: recall@5, MRR and hit rate at k = 1, 3 and 5.
- Answers: faithfulness, answer relevance, citation accuracy (the cited chunk really supports the claim) and correct-refusal rate on adversarial and unanswerable questions.
- An ablation table: vector-only, keyword-only, hybrid and hybrid plus rerank, each reported separately and split by language. This table justifies your Arabic keyword-search choice from step 2.3.
- A breakdown by question type and by language.

*Reproducibility record:* write every eval report as JSON and Markdown, including the snapshot id, dataset version and checksum, pipeline fingerprint and its parts, k values, prompt ids and hashes, model ids per role, judge model id, git SHA and run time. Plain JSON now; Langfuse experiments (5.7) and the lock file (5.8) build on it later.

Done when baseline numbers are in LOG.md. Then change one thing (chunk size or hybrid search), re-measure, and keep the before and after.

**Tests:** check recall@k and MRR against hand-computed values on three toy examples. The judge output must parse as JSON, and a malformed judge reply (from FakeLLM) is handled without crashing. Two runs with the same settings give scores within a tolerance. The report schema requires every reproducibility field, and the eval refuses to run if the dataset checksum does not match its card. Changing the pipeline fingerprint changes the fingerprint field in the report.

**2.8 Metrics and first dashboard.** Expose Prometheus metrics, start the observability compose profile, build an Overview dashboard plus an Ingestion and RAG panel group, and export the JSON into ops/grafana. Labels are limited to source type and outcome.

- Ingestion and crawl: pages by outcome (fetched, unchanged, changed, removed, failed, blocked, needs\_js), chunks created, reused, embedded and tombstoned, embedder calls and latency, embedding-cache hit ratio, ingestion job duration and outcome, snapshot age, total stored bytes.
- RAG: retrieval latency by stage, top retrieval score distribution, no-answer rate, chunks sent to the LLM, tokens and estimated cost per request.

**Tests:** after one request, /metrics contains the expected metric names. A test fails if any label is user\_id, session\_id or request\_id. After running step 2.3b twice, the counters show chunks embedded on the first run and zero on the second. The dashboard JSON parses and contains the required panels.

**2.9 Tracing v1.** Add OpenTelemetry with Langfuse export. One /ask trace shows these spans in order: retrieval with child spans vector\_search, keyword\_search, fusion and rerank, then prompt\_build, llm\_answer and citation\_check. Record these attributes: for retrieval, k, the chunk ids with scores per stage, the snapshot id and the pipeline fingerprint; for the LLM, provider, model, prompt id and hash, token counts and estimated cost. Add a span per ingestion job and per page processed, with counters (skipped, embedded, reused) as attributes. Never record message text or page text on spans at INFO; use ids, lengths and hashes. Done when one full request trace is visible in Langfuse.

**Tests:** with an in-memory span exporter, one /ask produces the expected span names, parent-child structure and attributes (provider, model, tokens). Retrieval spans carry chunk ids and scores but no text. Redaction is applied to span attributes, and errors are always kept by sampling. An ingestion job produces job and per-page spans with correct counters.

**2.10 CI eval gate.** Run a 20-question subset in CI against a small hand-written fixture corpus (tests/fixtures/corpus/), never the live site and never the private snapshot, with thresholds stored in a YAML file (start at recall@5 of 0.80 and faithfulness of 0.85, then revise after your baseline). The idempotency test from step 2.3b also runs in the CI integration job. The full golden set runs locally and nightly against the real snapshot.

**Tests:** test the gate itself: a report below the threshold must exit non-zero and one at or above must exit zero. Validate the thresholds file against a schema.

**2.11 Prompt registry.** Move every prompt into prompts/\<name>/vN.md with a short header (purpose, model role, inputs). A loader returns the text, the version and a sha256 hash. Every trace and every eval report records the prompt id and hash, and a prompt change goes through a pull request that shows the eval before and after.

**Tests:** the loader returns the requested version and fails clearly on a missing one; the hash changes when the text changes; an in-memory exporter shows prompt\_id on the LLM span; a lint check fails if an LLM call uses a prompt string that is not from the registry; the CI eval job runs whenever files under prompts/ change.

**2.12 Dataset versioning.** Keep text datasets in Git. Put audio and other large files under DVC or Git LFS (check the free storage limits of the remote you choose). Every dataset has a version in its card, and every eval report records the dataset version and checksum.

**Tests:** an eval refuses to run if the dataset checksum does not match its card; CI fails when a dataset file changes without a version bump; the report schema requires dataset\_version.

**2.13 Judge calibration (new).** After the first baseline, hand-label 30 to 40 answers (faithful or not, relevant or not, citation correct or not) and compare them with the judge's scores. Record the agreement in LOG.md. Use a low temperature and, for borderline items, repeat the judge call and take the majority. If agreement is weak, change the judge prompt (through the registry) or the judge model before you trust any number.

**Tests:** the agreement function is correct on hand-built examples; the labeled sample is stored as a versioned dataset with a card; the calibration script reports agreement and fails below the minimum you set.

**Done when:** ingesting the same snapshot twice writes nothing and makes zero embedder calls (proven by an automated test); changing one page re-embeds only its changed chunks, and changing the chunker or embedder reprocesses everything; killing and resuming ingestion leaves no duplicates; baseline recall@5, MRR, faithfulness, citation accuracy and correct-refusal rate are in LOG.md with the ablation table split by language; judge agreement with your human labels is recorded; a dashboard shows ingestion and RAG metrics; one /ask trace in Langfuse shows every retrieval stage with chunk ids and scores; and CI runs the fixture eval gate and the idempotency test.

**Record in LOG.md:** the baseline and after numbers, the ablation table, tokens and cost per request, chunks embedded on the first versus second ingestion run, which Arabic keyword method you chose and why, a Langfuse trace screenshot and a dashboard screenshot.

## Week 3: Agent

Goal: an agent that takes real actions safely.

**3.1 Tools.** Create a fake orders database and two tools, get\_order\_status and create\_ticket, with Pydantic input and output schemas.

**Tests:** valid input returns a schema-valid output, an unknown order returns a structured not-found error, invalid input returns a validation error, a simulated timeout is handled, and an SQL-injection string in the order id is handled safely.

**3.2 Agent graph.** Build it in LangGraph: a router node (small model), the RAG tool, the action tools, shared state, and a maximum step count.

**Tests:** script FakeLLM outputs so each route is covered: an order question reaches the order tool, a knowledge question reaches RAG, chit-chat gets a direct reply. The maximum step count stops loops, and state persists across turns.

**3.3 Guardrails.** Add input screening for prompt injection, an output groundedness check, and PII redaction.

**Tests:** a table-driven set of at least 20 injection strings (English and Arabic) that must be blocked, and 20 normal messages that must pass, so you also catch false positives. PII cases are redacted, and an answer that cites a source that was not retrieved is rejected.

**3.4 Human approval.** Make request\_refund pause the run, store the request in an approvals table, expose /approvals endpoints, and resume the run after approval.

**Tests:** an integration test of the approval flow: a refund request pauses the run as pending; approve resumes it and executes exactly once (approving twice does nothing); reject performs no action; a non-staff user gets 403.

**3.5 Observability.** Add spans for the router, tools and LLM calls, and metrics for tool calls and escalations.

**Tests:** an in-memory exporter shows the span tree for router, tool and LLM calls. The tool\_calls\_total counter increases by outcome, and an escalation increases escalations\_total.

**3.6 Agent evaluation.** Write 30 scenarios covering tool selection, correct refusals and injection attempts, and measure tool-selection accuracy and task completion.

**Tests:** unit-test the scenario file format and the scoring function with hand-built examples. Run the scenarios against FakeLLM in CI; the real-model run is nightly.

**3.7 Web UI.** Build a simple page with chat, visible citations and a staff approvals view.

**Tests:** a Playwright smoke test sends a message and sees an answer with a citation link; the approvals page lists a pending item and the approve button works; Arabic text sets right-to-left direction.

**3.8 Red-team regression set.** Create tests/redteam/ with attacks grouped by type (instruction override, data exfiltration, tool abuse, jailbreak, in Arabic and English). Run it in CI against the guardrails and nightly against the real model. Every real incident or odd trace adds a case.

**Tests:** the suite loads and every case has a type and an expected outcome; the block rate stays above your threshold; a case without an expected outcome fails the validator; the nightly job reports results per attack type.

**3.9 Visitor identity.** On the first visit, issue a signed JWT in an HttpOnly cookie that holds a random anonymous user id. This id owns the visitor's sources. Add per-user and per-IP rate limits. Real accounts are optional later. The signing secret is a new environment variable (APP\_JWT\_SECRET), listed in .env.example and never committed.

**Tests:** a new visitor gets a token and an id different from other visitors; the same cookie returns the same id; a tampered, expired or unsigned token never grants access to another visitor's sources (the visitor gets a new identity); the secret comes from the environment and the app refuses to start in production without it; the cookie is HttpOnly, and Secure outside local development; the rate limit returns 429 after the limit.

**3.10 File uploads.** Add POST /files, GET /files and DELETE /files/{id}. Accept .txt, .md and .pdf (add more types only if you need them) within starting limits of 2 MB per file, 50 PDF pages, 5 files and a fixed total number of chunks per user; tune them against your free database size. Check the type by content (magic bytes), not only the extension. Extract text only (no OCR, no scripts), normalize it, chunk it and embed it as a background job through the same incremental ingestion path as step 2.3b (same fingerprint, same embedding cache, same job lock), with a status: queued, running, ready, or failed with a reason. Store text and chunks in Postgres with owner id, source id and checksum. Never write the file to the container disk.

**Tests:** a wrong extension, wrong content, empty file, oversize file, too many pages and an over-quota upload are rejected with a clear 4xx message; a duplicate checksum does nothing; deleting a source removes its chunks and embeddings; text is extracted from sample .txt, .md, English PDF and Arabic PDF fixtures; a corrupt PDF returns 422 and the job is marked failed with a reason; no file remains on disk after a request (list the temp folder); extracted text is not written to logs at INFO.

**3.11 Isolation and untrusted content.** Filter retrieval by owner on every query (the step 2.4 rule, now tested with real uploads). Extend the injection guard from step 3.3 to screen retrieved chunks from every source, and never follow instructions found inside them. Label answers that used a user source (for example, from your file). Show a notice in the UI: do not upload sensitive data, because retrieved text is sent to AI providers whose free-tier terms can differ.

**Tests:** a property-style test with several users and many files: a query by one user never returns another user's chunks, citations or filenames; a deleted source never appears; an uploaded file that says to ignore previous instructions or to reveal the system prompt does not change the answer behavior (add these cases to tests/redteam/); the notice is present in the UI.

**3.12 Safe fetcher (SSRF guard).** Write one function that every outgoing page request must use. Allow only http and https on ports 80 and 443, with no credentials in the URL. Resolve the host and require every returned address to be public: block loopback, private, link-local (including the cloud metadata address 169.254.169.254), unique-local, multicast and unspecified ranges, in IPv4 and IPv6, and IPv4-mapped IPv6 addresses. Connect to the address you checked, so DNS rebinding cannot change it. Follow at most 3 redirects and re-validate each one. Add a timeout, a maximum body size (start with 2 MB), a content-type allowlist (text/html, text/plain, application/pdf), no cookies and no stored credentials, and obey robots.txt with the polite client from step 2.0a. The built-in crawl uses the same client with its allowlist.

**Tests:** a table of blocked targets: localhost, 127.0.0.1, 0.0.0.0, 10.x, 172.16.x, 192.168.x, 169.254.169.254, ::1, fc00::, fe80::, ::ffff:127.0.0.1, the decimal, hex and octal forms of 127.0.0.1, a name that resolves to a private address (fake resolver), a name whose address changes between two lookups (rebinding), a redirect to a private address, credentials in the URL, ports other than 80 and 443, and the file://, ftp:// and gopher:// schemes. Public targets pass. The 4th redirect fails. An oversize body is cut off and rejected, a disallowed content type is rejected, and no Cookie or Authorization header is ever sent. These tests use a fake resolver and a local server, never real internet targets.

**3.13 User websites.** Add POST /sources/websites with a URL, plus list, refresh and delete endpoints. The default is one page. An optional small crawl stays on the same host and is limited to starting values of 20 pages and depth 1. Pages that need login or JavaScript are reported as such and not added. Keep query strings out of logs and traces (log the host and a hash of the path). Show the user a notice that they must have the right to index the content. The whole feature sits behind a feature flag, off in the public demo until the tests of steps 3.12 and 3.13 pass and you decide (PRD question Q9). Add per-user and per-IP limits, a global daily cap on fetches, and a kill switch. Ingestion runs as a background job with a status, like uploads, through the same incremental ingestion path.

**Tests:** with a local fake website: one page is ingested and cited as your website; the crawl respects the page cap, the depth and the same-host rule; a robots.txt disallow is obeyed; a login page and a JavaScript-only page are reported and not added; refresh skips unchanged pages by hash; query strings never appear in captured logs; with the flag off the endpoint returns 404 or 403; over-quota and over-cap requests are rejected with 429; the kill switch stops running jobs.

**3.14 Sources page in the UI.** Add a Sources page: add a file, add a website, see each source with its status (queued, running, ready, or failed with a reason), refresh a website, delete a source, and see how much of your quota is used. Show the language switch (Arabic right-to-left, English left-to-right), and mark built-in sources as shared and read-only.

**Tests:** a Playwright test uploads a fixture file, sees its status change to ready, asks a question and sees a citation labeled as your file; deleting the source removes it from the list and the answer no longer cites it; built-in sources have no delete button; the page renders in both directions and keeps the conversation when the language changes.

**Done when:** the refund flow works end to end with approval, the injection tests meet your threshold, one trace shows the whole span tree, and a user can add a file and a website and get cited answers that another user cannot see.

## Week 4: Voice

Goal: speak a question in Arabic and hear a grounded answer.

**4.1 ASR.** Write the ASRProvider port, its schemas and a FakeASR. Add a Groq Whisper provider. Convert its quality signals into one confidence value between 0 and 1.

**Tests:** a contract suite over every ASR provider using short recorded fixtures and mocked HTTP: the transcript is not empty, confidence is between 0 and 1 (or None), 429, timeout and oversize errors are mapped. Unit-test the confidence mapping with known avg\_logprob and no\_speech\_prob values.

**4.2 Audio utilities.** Enforce size and duration limits and convert any input to 16 kHz mono wav with ffmpeg.

**Tests:** webm/opus, mp4/aac and wav fixtures all convert to 16 kHz mono wav. An oversize file and a too-long file are rejected, a corrupt file raises InvalidInputError, a silence-only file is detected, and temporary files are deleted afterwards.

**4.3 TTS.** Write the TTSProvider port and schemas. Add Piper (local, no limits) as the reliable base, Groq Orpheus (limited free quota, Saudi dialect) as the quality option, and optionally edge-tts for demos only. Add a Redis cache keyed by a hash of text, voice and settings.

**Tests:** a contract suite for TTS: the result has a valid audio header and a non-zero duration, and Piper works offline in CI. Cache tests: the second identical call is a hit with no provider call, and a different text or voice is a miss.

**4.4 Speech formatter.** Strip markdown and links and make numbers speakable.

**Tests:** table-driven cases: markdown is removed, links keep only their text, '#1234' is read digit by digit, mixed Arabic and English is preserved, emojis are removed, and formatting twice gives the same result as once.

**4.5 Fallbacks and contract tests.** Build ordered fallback chains for ASR and TTS and run the shared contract tests on every provider.

**Tests:** when the primary returns 429 the secondary is used and fallback\_total increases; when all fail the caller gets a clear error. Contract tests discover providers from the registry, so prove it by registering a dummy provider in a test and seeing the suite pick it up with no new code.

**4.6 Voice pipeline.** Build the /voice endpoint: ASR, guardrails, agent, formatter, TTS. Add the “please repeat” path for low confidence and read-back for order numbers.

**Tests:** with FakeASR, FakeLLM and FakeTTS: the normal path returns text and audio. Low confidence returns the repeat prompt and the agent is not called. An order number triggers read-back, an injection in the transcript is blocked, a failure in any stage gives a friendly spoken error, and no audio is stored.

**4.7 Push-to-talk UI.** Use the browser MediaRecorder API and test Chrome and a phone browser, because they record different formats.

**Tests:** a Playwright test with a fake microphone (Chromium's fake media stream flags plus a wav file) records, calls /voice and plays the reply. Safari and phone browsers cannot be automated, so keep a manual checklist and write the results in LOG.md.

**4.8 Observability.** Add asr and tts spans and the voice metrics.

**Tests:** asr and tts spans exist with provider and cache attributes, and the time-to-first-audio histogram records one observation per request.

**Done when:** a spoken question is answered with citations on screen, and the answer still works after you disable the primary provider key (fallback proof).

## Week 5: ASR and TTS evaluation

Goal: choose default providers using data, not guesses.

**5.1 Record the ASR set.** Record 50 to 100 Egyptian-dialect utterances with correct transcripts. Include noisy audio, English words mixed in, and numbers. Write the dataset card.

**Tests:** a validator for the ASR set: every audio file has a transcript, durations and sample rates are within limits, ids are unique, tags are present, and the checksum matches. Assert minimum counts of noisy and mixed-language items.

**5.2 Metrics.** Write the Arabic normalizer (remove diacritics, unify letter forms and digits), then WER and CER.

**Tests:** normalizer before/after pairs (diacritics, alef variants, taa marbuta, Arabic-Indic digits). WER and CER on hand-computed cases: identical text gives 0, one substitution, one deletion, and an empty reference is handled.

**5.3 ASR evaluation.** Compare Whisper turbo, Whisper v3 and your local model. The runner must respect rate limits.

**Tests:** run the runner against FakeASR with known outputs and expect a known WER. A fake clock proves it never exceeds the allowed requests per minute, and the report file matches its schema.

**5.4 TTS evaluation.** Compute round-trip WER with a fixed reference ASR, real-time factor, and the pronunciation lexicon test. Run a listening test with two or three people on 20 samples (clarity, naturalness, dialect, scored 1 to 5).

**Tests:** with fake TTS and ASR: a perfect pipeline gives round-trip WER 0, the real-time factor is computed from known durations, the lexicon test fails when a term is mispronounced, and the listening-sheet generator writes the expected CSV columns.

**5.5 Voice end to end.** Turn golden questions into audio, add noise, run ASR and the agent, and measure the quality drop against the text pipeline.

**Tests:** on a 5-question fixture with fakes: noise is added with a fixed seed so results repeat, the quality drop versus the text baseline is computed correctly, and a failure in any stage is counted instead of crashing the run.

**5.6 Automation.** Add the nightly workflow, thresholds and report artifacts.

**Tests:** reuse the gate tests from step 2.10, lint the workflow files with actionlint in CI, and dry-run the nightly job with fakes to prove it produces the report artifacts.

**5.7 Experiment tracking.** Use Langfuse for experiment tracking, so there is no extra service to run. Upload the golden set as a Langfuse dataset and run each eval as an experiment run over it. Every run records the git SHA, dataset version, prompt ids, provider, model and parameters as metadata (the reproducibility record from step 2.7), attaches its metrics as scores, and puts the run name in the report. Watch your monthly unit budget: a full run creates many traces, so keep run-level scores for every run and full traces only for failures, or run the full suite weekly.

**Tests:** a run with FakeASR sends the expected metadata and scores to a mocked Langfuse client; the report contains the run name; if Langfuse is unreachable the eval still finishes and prints a warning; two runs with different prompt versions show different prompt ids; a test checks that a full run stays within the trace budget you set.

**5.8 Model and provider registry.** Create configs/models.lock.yaml with the exact model id for each role, the date it was verified, and the eval run id that approved it. A model becomes primary only through a pull request that includes its approving run. For local models (Whisper, Piper voice, embedder, reranker) store versioned artifacts with a checksum and write a model card: purpose, version, license, eval results and known limits.

**Tests:** the lock file validates against a schema; CI fails if a primary model in the config is missing from the lock file or has no approving run id; the loader rejects a local model whose checksum does not match; a script fails if a model in the lock file has no model card.

**Done when:** a provider comparison table is in your README and your default providers are chosen from it.

## Week 6: Performance and resilience

Goal: faster, cheaper, and safe under quota limits.

**6.1 Shared limits.** Implement a Redis token-bucket rate limiter that matches provider quotas across all instances, plus a circuit breaker.

**Tests:** with a fake clock the limiter allows a burst up to capacity, refuses beyond it and refills correctly. An integration test with Redis sends 50 concurrent calls through two limiter instances and never exceeds the limit. The circuit breaker goes closed, open, half-open, closed.

**6.2 Streaming.** Stream the LLM output, split it into sentences, and start TTS on the first sentence. Measure time to first audio before and after.

**Tests:** table-driven sentence splitting (Arabic marks, English, decimals like 3.5, abbreviations, no final punctuation). A streaming test with a slow fake LLM proves the first audio chunk arrives before the stream ends, chunks keep sentence order, and a client disconnect stops further TTS calls.

**6.3 Caching.** Add exact and semantic caches and track the hit rate.

**Tests:** the same query hits and a different language or voice misses; entries expire with a fake clock. For the semantic cache, test near-duplicates (should hit) and different intents (must miss), because false hits are the real risk. Content flagged as PII is never cached.

**6.4 Load test.** Use k6 or Locust with 5 concurrent sessions. Record p50 and p95 latency, error rate and cost per 1,000 requests.

**Tests:** the load test is the test. Put the pass criteria in the script (for example k6 thresholds: p95 under your target and error rate under 1%), run it against staging only, and store the report.

**6.5 Optional (P2).** WebSocket transport, voice activity detection and barge-in.

**Tests:** a WebSocket integration test connects, sends audio frames and receives partial and final results. Test voice activity detection on speech and silence fixtures, and check that new speech cancels playback (barge-in).

**6.6 Index versioning.** Store each index with a version that records the embedding model, dimension and chunking settings (this builds on the pipeline fingerprint from steps 2.1 to 2.3b). Build a new index next to the old one, evaluate it on the golden set, then switch with one config value. Keep the previous index for a week so rollback is instant.

**Tests:** retrieval reads only the active version; switching and rolling back change the results as expected on a fixture corpus; vectors of different dimensions can never be mixed in one index; a migration test builds v2 from a fixture and passes the retrieval checks.

**6.7 Cost and token budgets.** Set daily token and cost budgets per role in config. At 80% raise an alert; at 100% switch to a cheaper role or a degraded mode (shorter answers, cache-only for repeated questions). In the nightly run, compare the cost per 1,000 requests with the last release.

**Tests:** with a fake clock and fake usage, crossing 100% triggers the degraded mode and a new day resets it; the 80% alert rule passes a promtool test; the comparison script flags a 20% cost increase on synthetic data and ignores a 2% change.

**6.8 Train a small router classifier (optional).** Train a scikit-learn classifier on your embeddings to route messages (knowledge, action, chit-chat, escalate), so the cheap path does not spend LLM quota. Make the pipeline reproducible: fixed seeds, versioned data, Langfuse experiment logging, an evaluation gate against the LLM router on the agent eval set, and registration only if the gate passes. Fall back to the LLM router when the classifier's confidence is low. Fine-tuning a language model with LoRA needs a GPU, so do that, if at all, in a free cloud notebook (check its current limits).

**Tests:** training is deterministic with a fixed seed; the train and test splits do not overlap; the saved model loads and predicts in a unit test; the gate blocks promotion when accuracy falls below the LLM router's score minus your tolerance; low confidence falls back to the LLM router; inference stays under a latency budget you set.

**Done when:** you have a load-test report and a before/after number for time to first audio.

## Week 7: Production pipeline

Goal: safe releases with rollback and alerts.

**7.1 Infrastructure.** Write Terraform for staging and production, or document the manual setup if you are blocked.

**Tests:** run terraform fmt -check and terraform validate on every pull request, and a plan (never an apply). A check confirms staging and production use different secrets and database names.

**7.2 Staging pipeline.** On merge: build and push the image, run migrations, deploy to staging, run smoke tests (health, one text query, one voice fixture).

**Tests:** the smoke suite runs automatically after the staging deploy: /healthz, /readyz, one text query that returns a citation, and one voice fixture that returns audio. Any failure stops the pipeline. Test each migration on a copy of staging data first.

**7.3 Production pipeline.** Manual approval, deploy with no traffic, canary, then full rollout, with rollback on failed checks.

**Tests:** rehearse the failure path. Deploy a deliberately broken revision that fails readiness and confirm the rollback happens by itself. Verify the canary traffic split in the metrics, and confirm the approval gate blocks a deploy without a reviewer.

**7.4 Monitoring.** Send metrics, logs and traces to Grafana Cloud, load dashboards from the repo, and add alerts with a runbook for each.

**Tests:** run promtool check rules and promtool test rules with synthetic series for each alert, and lint the dashboard JSON. Then send one request and confirm its metric, its log line and its trace share the same trace\_id.

**7.5 Cost safety.** Confirm the budget alert and the maximum instance count.

**Tests:** a CI script reads the deployed service config and fails if max instances is unset or the budget alert is missing.

**7.6 Chaos drill.** Disable the Groq key and watch the fallback work, the alert fire and the dashboard show it. Take screenshots.

**Tests:** the drill is the test. Write the expected results first (fallback within a set number of seconds, alert within a set number of minutes, no 5xx to the user), record pass or fail in LOG.md, then restore the key.

**7.7 Security checks.** Add image scanning, secret scanning and dependency auditing to CI.

**Tests:** on a throwaway branch add a fake secret and confirm gitleaks fails, add a known-vulnerable pinned dependency and confirm pip-audit fails, and use a known-bad base image to confirm Trivy fails. Revert each one afterwards.

**7.8 Online evaluation and feedback loop.** Sample a small share of production traces (for example 5%), score them with the judge role for faithfulness and relevance, and attach the scores to the traces in Langfuse. Thumbs-down feedback and escalations become candidate cases. Once a week, review about 10 candidates and add the good ones to the golden set as a new dataset version.

**Tests:** the sampler picks about the configured share with a seeded random generator; the scoring job is idempotent and never scores the same trace twice; exported candidate cases pass the dataset validator; PII is redacted in exported cases; a failed judge call is retried and then skipped without crashing the job.

**7.9 Drift and canary monitoring.** Run a daily canary eval of the production configuration on a small fixed set, and alert if quality drops more than your threshold below the release baseline. Track the distributions that signal drift: top retrieval score, no-answer rate, escalation rate, ASR confidence and the language mix of questions, each against a rolling baseline.

**Tests:** drift detector unit tests with synthetic data (no change gives no alert, a shifted mean gives an alert); the alert rules pass promtool tests; a dry run of the canary job with fakes produces a report and the right exit code.

**7.10 Deprecation watch.** Add a weekly job that lists the models each provider offers (where the provider has a list endpoint) and compares them with models.lock.yaml. If a pinned model is missing or marked for retirement, the job opens an issue naming the affected role and its fallback.

**Tests:** with a fake provider listing, a missing pinned model produces an issue payload and a present one produces none; an API failure is logged and does not crash the job; every role in the lock file has at least one fallback.

**7.11 Scheduled re-crawl and snapshot promotion.** Run the built-in crawl on a schedule (weekly to start) and on demand, using whatever the free host allows (a hosting cron job or a scheduled GitHub Actions workflow). A new snapshot becomes active only after incremental ingestion (step 2.3b) and the golden-set eval on it pass the gates, with the diff (new, changed and removed pages) attached to the report. Re-validate the golden set against the new manifest, and list the questions whose expected page disappeared for review. Alert when the active snapshot is older than the allowed age.

**Tests:** the promotion script refuses to activate a snapshot when the gate fails and activates it when the gate passes; the diff report lists new, changed and removed pages on fixtures; the golden-set validator flags a question whose expected URL is missing from the new manifest; the alert rule for snapshot age passes a promtool test; a dry run of the scheduled job with fakes produces the report.

**Done when:** you have demonstrated a rollback and a fired alert, with screenshots saved.

## Week 8: Polish and interview readiness

**8.1 README.** Include: what and why, architecture diagram, live link and demo video, results tables with before and after, cost per 1,000 requests, known limitations, and a three-step guide to adding a provider.

**Tests:** a script checks that every relative link in the README resolves. A docs test follows the three-step add-a-provider guide with a dummy provider and confirms it passes the contract tests. A script compares the numbers in the README with the latest eval report.

**8.2 ADRs.** Write at least five (for example: provider registry, hybrid retrieval, model tiering, shared rate limiter, evals outside src). Consider a sixth on idempotent ingestion with a pipeline fingerprint.

**Tests:** the ADR lint from step 0.5 passes on all files, and numbering is sequential.

**8.3 Demo video.** Record 60 seconds of a spoken Arabic question and the answer.

**Tests:** a manual checklist: record from the live URL, not localhost; the audio is audible; a citation is visible; the link opens in a private window.

**8.4 Write-up.** Publish one article on what you learned.

**Tests:** run every snippet you publish, check all links, and ask one person to follow the article cold and note where they get stuck.

**8.5 Clean-up.** Remove dead code, check the git history for secrets, pin dependencies, tag v1.0.

**Tests:** the clean-clone test. Clone fresh, follow the README from scratch on a clean machine or container, and run make test. Run gitleaks over the full history and pip-audit, check the lock file matches pyproject, and tag only when CI is green on main.

**8.6 Interview preparation.** Turn LOG.md into six stories (see below) and practice explaining the architecture in five minutes.

**Tests:** a mock interview: explain the architecture in five minutes to someone else, then answer their questions using your own measured numbers without notes.

**8.7 Runbooks and audit log.** Write three runbooks: a quality drop, a provider retiring a model, and a cost spike. Each one says how to detect it, diagnose it and roll back. Store every approval or rejection of a sensitive action in an append-only audit table with the actor, time, decision and trace id.

**Tests:** a script fails CI if an alert has no runbook link; the audit table rejects updates and deletes (a test attempts both); every approval-flow test also asserts that an audit row was written.

**Done when:** a stranger can open the README, understand the project in two minutes, and click a working demo.

## Daily and weekly routine

- Daily: work on one step, commit, and update LOG.md with what you did, the problem and the fix.
- Weekly review (30 minutes): check each “Done when”, copy the new numbers into LOG.md, plan next week, and cut scope if you are behind.
- Debugging recipe: reproduce with a fake provider, open the trace, find the logs by request\_id, then check the metrics.

## If you are stuck or behind

- **Behind schedule:** cut in this order: user-submitted websites (steps 3.12 and 3.13), P2 items (WebSocket, voice activity detection, semantic cache), then streaming, then Terraform (document the manual setup instead). In Week 2, move steps 2.11 and 2.12 into Week 3 before cutting anything else. Never cut evals, observability or incremental ingestion (step 2.3b); they are what make the project stand out.
- **No payment method for cloud:** use a no-card container host, or run locally and show a video. The Docker image stays portable either way.
- **Machine too slow:** use hosted inference only, run only the core compose profile, and start the observability profile only when you need screenshots.
- **Quota exhausted:** rely on fallbacks and caches, run full evals at night, use smaller subsets during the day.
- **A provider changes limits or models:** update the YAML config, then rerun the contract tests and evals.

## Interview stories to prepare

- A time a provider hit its rate limit and how your design handled it.
- A retrieval improvement you proved with before/after numbers.
- A failure you found through tracing.
- How you made adding a new model a one-file change.
- A voice or dialect problem you measured and solved.
- How you decided what to cut when time ran short.
- How you made ingestion idempotent and proved that a second run does nothing (the "run it twice" test).

## Final deliverables checklist

- Live URL and CI badge
- README with architecture diagram and result tables
- Dashboard and trace screenshots
- Chaos drill screenshots
- 60-second demo video
- At least five ADRs
- Write-up article
- v1.0 tag

**MLOps and LLMOps additions:** a screenshot of Langfuse experiment runs, the prompt registry, models.lock.yaml with approving run ids, model cards, the index version history, a drift and canary dashboard, and the ingestion dashboard with the first-versus-second-run embedding counts.