# Support Copilot: Product Requirements Document

*Version 0.1 (draft) · October 30, 2026 · Owner: \[your name\] · Status: ready for review*

## 1. Summary

Support Copilot is a production-grade AI customer-support assistant. It answers product questions from a knowledge base (RAG), performs real actions through tools (an agent), and talks with customers by voice (ASR + TTS). Arabic, including Egyptian dialect, is a first-class language alongside English.

The system is modular: every model (LLM, ASR, TTS, embedder, reranker) is a swappable provider behind a stable interface. It ships with the full production toolchain: containers, CI/CD with evaluation gates, structured logging, tracing, metrics, dashboards and alerts.

The project has two purposes: (1) a realistic support product, and (2) an engineering showcase that proves the builder can design, evaluate, deploy and operate an AI system on a near-zero budget.

The knowledge base is dynamic. It combines a built-in public source (the customer-service pages of WE / Telecom Egypt, crawled by the owner under the site's rules) with private sources that each user adds: uploaded files and websites. Built-in content is shared and read-only; a user's own content is visible only to that user.

## 2. Problem Statement

- Support teams spend most of their time on repetitive questions, so answers are slow and inconsistent.
- Naive chatbots hallucinate, cannot take actions, fail silently and are never measured.
- Voice support in Arabic dialects is underserved, and ASR/TTS quality varies widely between providers.
- AI teams cannot tell whether a change improved or broke the system without built-in evaluation and observability.
- The builder has limited hardware (local model inference is too slow) and almost no budget, so the system must use hosted free or low-cost APIs and be designed around their rate limits.

## 3. Goals and Non-Goals

**Goals**

- G1: Answer from a knowledge base with citations and an honest “I don’t know”.
- G2: Execute support actions safely through tools, with human approval for sensitive ones.
- G3: Support voice conversations in Egyptian Arabic and English.
- G4: Make any model replaceable by adding one file and a config entry, with no change to pipeline code.
- G5: Measure quality continuously (RAG, agent, ASR, TTS, end to end) and block regressions in CI.
- G6: Be fully observable: structured logs, traces, metrics, dashboards, alerts.
- G7: Be reproducible and deployed: Docker, CI/CD, staging and production, rollback.
- G8: Run on a $0 baseline budget using free tiers, and report cost per 1,000 requests.

* G9: Operate the LLM and ML components over time: version prompts, models, datasets and indexes, detect drift, and control cost.

- G10: Let users build a private knowledge base by uploading files and submitting websites, safely and within free-tier limits, while the built-in WE knowledge base stays measured and reproducible.

**Non-goals:** see section 18.

## 4. Users and Personas

| Persona | Needs |
| --- | --- |
| Customer | Fast, correct answers by text or voice in Arabic or English; order status; a human when the bot cannot help |
| Support agent | Review and approve sensitive actions; receive escalations with a conversation summary |
| Knowledge-base owner (any user) | Add files and websites to a private knowledge base, see the status of each source, refresh or delete it |
| AI engineer (builder) | Swap models, run evals, watch dashboards, deploy safely |
| Reviewer (recruiter or interviewer) | Open a live demo, read the README and ADRs, inspect dashboards and eval numbers |

## 5. Release Plan

| Release | Scope |
| --- | --- |
| R1 | Text RAG with citations, golden set and first eval, structured logging, Docker, CI, skeleton deployed |
| R2 | Agent with tools, human approval, guardrails, tracing, metrics and dashboards |
| R3 | Voice (push-to-talk): ASR, TTS, caching, fallback chains, ASR/TTS evals |
| R4 | Streaming voice, load tests, canary releases, alerts and runbooks, README and demo video |

Priorities: P0 = required for its release, P1 = should have, P2 = nice to have.

LLMOps and MLOps arrive in stages. R1 adds the prompt registry and dataset versioning. R2 adds the red-team regression set. R3 adds experiment tracking, the model registry, index versioning and cost budgets. R4 adds online evaluation, drift and canary monitoring, the deprecation watch, runbooks and the approval audit log. The trained router classifier is optional (P2).

Knowledge sources arrive in stages. R1 adds the polite crawler and snapshots for the built-in WE pages. R2 adds visitor identity, file uploads, the safe fetcher, user websites (behind a feature flag) and the Sources page. R4 adds the scheduled re-crawl with snapshot promotion. User websites are the first item to cut if time runs short.

## 6. User Stories

- US-1: As a customer, I ask how to request a refund and get a short answer with a link to the source document. If the docs do not cover it, the bot says so and offers a human.
- US-2: As a customer, I ask “where is order 123?” and get the real status.
- US-3: As a customer, I press a button, speak in Egyptian Arabic and hear the answer.
- US-4: As a customer, when my speech is unclear, the bot asks me to repeat instead of guessing.
- US-5: As a support agent, I see pending refund requests and approve or reject them.
- US-6: As a user, I upload a file or give a website address, and it becomes searchable in my own private knowledge base.
- US-7: As an AI engineer, I add a new ASR provider with one file, run its eval and compare it with the others.
- US-8: As an AI engineer, I see which provider is failing, how much quota is used and what each request cost. A pull request that lowers quality below the threshold fails the build.

* US-9: As a user, I see the status of each of my sources (queued, running, ready, failed with a reason), refresh a website, and delete any source, which removes its data.
* US-10: As a visitor, I ask a WE customer-service question and get an answer from the built-in WE pages, with the page link and the date it was fetched, and a notice that the assistant is unofficial.

## 7. Functional Requirements

### 7.1 Knowledge and RAG

- FR-RAG-1 (P0): Ingest documents from three source types (the built-in website crawl, user uploads and user-submitted websites); chunk, embed and store them with metadata and a version.
- FR-RAG-2 (P0): Hybrid retrieval (vector plus BM25) followed by reranking, with configurable k.
- FR-RAG-3 (P0): Every answer cites its sources. If evidence is insufficient, reply that the answer was not found and offer escalation.
- FR-RAG-4 (P1): Idempotent re-ingestion that reprocesses only changed documents.
- FR-RAG-5 (P1): Arabic and English queries both work; the reply language follows the user.
- FR-RAG-6 (P2): Semantic cache for repeated questions.

### 7.2 Agent and Tools

- FR-AGT-1 (P0): A router classifies each message: knowledge question, action request, chit-chat or escalation.
- FR-AGT-2 (P0): Tools get\_order\_status and create\_ticket. P1: request\_refund (approval required) and escalate\_to\_human.
- FR-AGT-3 (P0): Tool inputs and outputs are schema-validated, with timeouts, retries and structured errors.
- FR-AGT-4 (P0): Sensitive actions pause the agent until a human approves; the run then resumes.
- FR-AGT-5 (P1): Per-session state with a token budget for conversation history.
- FR-AGT-6 (P1): Tool permissions are scoped by user role.

### 7.3 Voice (ASR and TTS)

- FR-VOX-1 (P0): Accept recorded audio (webm/opus, mp4/aac, wav); validate size and duration; convert to 16 kHz mono wav.
- FR-VOX-2 (P0): ASR returns a transcript and a normalized confidence from 0 to 1; low confidence triggers a request to repeat.
- FR-VOX-3 (P0): A speech formatter strips markdown and links and normalizes numbers before TTS.
- FR-VOX-4 (P0): TTS results are cached by a hash of text, voice and settings.
- FR-VOX-5 (P0): ASR and TTS each have a primary provider and an ordered fallback chain.
- FR-VOX-6 (P1): Read-back confirmation for order numbers and sensitive actions.
- FR-VOX-7 (P1): Arabic and English code-switching handled in ASR output and TTS input.
- FR-VOX-8 (P2): Streaming: LLM stream, sentence splitter, TTS, WebSocket transport, voice activity detection and barge-in.

### 7.4 Providers and Extensibility

- FR-PRV-1 (P0): LLM, ASR, TTS, embedder and reranker are each defined by a Protocol; adding a provider means adding one file and a config block.
- FR-PRV-2 (P0): LLMs are requested by role (router, answer, judge) from configuration; code never names a model.
- FR-PRV-3 (P0): Providers raise a shared set of errors; wrappers add retry with backoff, circuit breaker and fallback.
- FR-PRV-4 (P0): A shared, distributed rate limiter enforces provider quotas across all instances.
- FR-PRV-5 (P1): A contract test suite runs automatically against every registered provider.
- FR-PRV-6 (P1): Usage and estimated cost are recorded for every provider call.

### 7.5 Safety, Security and Human Review

- FR-SAF-1 (P0): Prompt-injection screening on text input and on ASR transcripts.
- FR-SAF-2 (P0): PII redaction in logs and traces; raw audio is not stored by default.
- FR-SAF-3 (P1): Groundedness check on the output before it is sent.
- FR-SAF-4 (P0): Per-user and per-IP rate limits; JWT authentication.
- FR-HIL-1 (P0): An approval queue (API and simple page) for staff.
- FR-HIL-2 (P1): Thumbs up/down feedback stored and linked to the request trace.
- FR-HIL-3 (P1): Escalation hands a conversation summary to a human.

### 7.6 Interfaces

- FR-API-1 (P0): REST endpoints /ask, /voice, /approvals, /feedback, /healthz and /readyz, documented with OpenAPI.
- FR-API-2 (P2): WebSocket endpoint /voice/stream.
- FR-UI-1 (P0): Minimal web UI with chat, push-to-talk, visible citations and a staff approvals page; right-to-left layout for Arabic.

### 7.7 LLMOps and MLOps

- FR-LLMOPS-1 (P0): Prompts are stored as versioned files in a registry. Every trace records the prompt id and hash, and every prompt change ships through a pull request that includes eval results before and after.
- FR-LLMOPS-2 (P0): Each LLM role has a pinned model id recorded in a lock file with the date it was verified and the id of the eval run that approved it. A model becomes primary only with an approving run.
- FR-LLMOPS-3 (P0): Eval datasets are versioned with checksums, and every eval report records the dataset version, prompt ids, model ids and git commit, so any result can be reproduced.
- FR-LLMOPS-4 (P1): A red-team regression set of attacks in Arabic and English runs in CI and nightly, and every real incident adds a case.
- FR-LLMOPS-5 (P1): Online evaluation: a configurable share of production traces is scored by the judge role and the scores are attached to the traces. Thumbs-down feedback and escalations become candidate cases for the golden set.
- FR-LLMOPS-6 (P1): Drift and canary monitoring: a daily canary eval of the production configuration alerts when quality falls below the release baseline. Retrieval score, no-answer rate, escalation rate, ASR confidence and language mix are tracked against a rolling baseline.
- FR-LLMOPS-7 (P1): Per-role daily token and cost budgets with an alert at 80% and a degraded mode at 100%; cost per 1,000 requests is compared with the previous release.
- FR-LLMOPS-8 (P1): A weekly deprecation watch compares pinned models with the models each provider offers and opens an issue when one is missing or being retired. Every role has at least one fallback.
- FR-LLMOPS-9 (P1): Runbooks cover quality drops, retired models and cost spikes, and an append-only audit log records every approval or rejection of a sensitive action.
- FR-MLOPS-1 (P1): Experiment tracking: every eval or training run is recorded in Langfuse as an experiment run over a versioned dataset, with the code version, dataset version, prompt and model ids and parameters as metadata and its metrics as scores, and the run name appears in the report.
- FR-MLOPS-2 (P1): Locally hosted models (ASR, TTS voice, embedder, reranker, classifier) are stored as versioned, checksummed artifacts with a model card covering purpose, version, license, eval results and known limits.
- FR-MLOPS-3 (P1): Vector indexes are versioned by embedding model, dimension and chunking settings. A new index is built next to the old one, evaluated on the golden set, switched on by configuration, and kept for rollback.
- FR-MLOPS-4 (P2): Optional small router classifier trained in a reproducible pipeline (fixed seeds, versioned data, an evaluation gate against the LLM router, registration only when the gate passes), with fallback to the LLM router on low confidence.

### 7.8 Knowledge sources

The knowledge base has three source types: the built-in WE customer-service pages (shared and read-only), files a user uploads, and websites a user submits. Built-in content is crawled by the owner. A user's own content is private to that user.

**Built-in WE pages**

- FR-KB-1 (P0): The built-in source is the customer-service section of the WE / Telecom Egypt website (te.eg), in Arabic and English. Start URLs and path prefixes live in configs/crawl.yaml. The owner reads the site's robots.txt and terms of use before the first crawl. If automated access is not allowed, the built-in crawl does not run and pages saved by hand are used instead.
- FR-KB-2 (P0): Every crawl follows robots.txt, sends an honest User-Agent with a contact address (kept in an environment variable, not in the public repository), uses one connection with a delay between requests and conditional requests, stops after repeated 403 or 429 answers, and never fetches login-protected or personal-account pages.
- FR-KB-3 (P0): Pages are cleaned to their main text and stored with URL, canonical URL, title, language, fetched-at time, content hash and snapshot id.
- FR-KB-4 (P0): Crawls produce immutable snapshots. Only changed pages are re-chunked and re-embedded. The active snapshot is chosen by configuration and rollback is a configuration change. A new snapshot becomes active only after the golden-set eval on it passes the gates.
- FR-KB-5 (P1): The built-in crawl re-runs on a schedule and on demand. Answers show the fetched-at date, and pages older than a configured age carry an out-of-date note and a link to the official site.
- FR-KB-6 (P0): Answers that use built-in content cite the page URL and fetched date and state that the assistant is unofficial and not affiliated with Telecom Egypt or WE. The UI uses no WE logos or trademarks.
- FR-KB-7 (P0): The public repository holds the crawler, the allowlist and a snapshot manifest (URL, time, language, hash) but not the page text. CI uses a small hand-written fixture corpus and never contacts a live site.
- FR-KB-8 (P1): Arabic handling for all sources: text normalization, language detection, heading-based chunking, a multilingual embedder and reranker, and cross-language retrieval. The Arabic keyword-search method is chosen and measured at Step 2.4.

**User files**

- FR-UPL-1 (P0): Every visitor gets a signed anonymous user id (a JWT in a cookie) that owns their sources. Real accounts are optional (P2).
- FR-UPL-2 (P0): Users upload .txt, .md and .pdf files (more types only if needed) within a per-file size limit, a page limit and a per-user quota. The file type is checked by content, not only by name.
- FR-UPL-3 (P0): Extracted text is normalized, chunked and stored in Postgres with the owner id, source id, filename and checksum. Files are never written to the container disk.
- FR-UPL-4 (P1): Users can list, replace, refresh and delete their sources. Deleting a source removes its chunks and embeddings, and a repeated upload with the same checksum does nothing.
- FR-UPL-5 (P0): Ingestion runs in the background with a visible status: queued, running, ready or failed (with a reason).

**User websites**

- FR-WEB-1 (P1): A user can submit a website address. The default is a single page. An optional small crawl of the same host is limited by a page cap and a depth limit (starting values: 20 pages, depth 1). The feature sits behind a feature flag and is off in the public demo until the safeguards below pass their tests.
- FR-WEB-2 (P0 for FR-WEB-1): SSRF protection. Only http and https on ports 80 and 443, no credentials in the URL. The host is resolved and every address must be public: loopback, private, link-local (including the cloud metadata address), unique-local, multicast and unspecified ranges are blocked in IPv4 and IPv6, including unusual number formats and IPv4-mapped IPv6 addresses. The connection goes to the address that was checked, so DNS rebinding cannot change it, and every redirect is re-validated with a small redirect limit.
- FR-WEB-3 (P0 for FR-WEB-1): Fetch limits. robots.txt is obeyed, the User-Agent is honest, no cookies and no stored credentials are sent, JavaScript is not run, and timeouts, a maximum size per page and a content-type allowlist (text/html, text/plain, application/pdf) apply. There is a delay per host, a per-user and per-IP limit, a global daily cap and a kill switch.
- FR-WEB-4 (P1): Pages that need login or JavaScript are reported as such and are not added.
- FR-WEB-5 (P0 for FR-WEB-1): Submitted URLs are untrusted. Query strings are kept out of logs and traces (log the host and a hash of the path), and users are told they must have the right to index the content.
- FR-WEB-6 (P1): A user can refresh a website. Unchanged pages are skipped by content hash.

**Retrieval, isolation and safety (all sources)**

- FR-KB-9 (P0): Every query searches the active built-in snapshot plus the caller's own sources and nothing else. A user's chunks, citations and filenames are never returned to another user, and automated tests prove it.
- FR-KB-10 (P0): All retrieved text, from every source type, is untrusted. The injection guard screens retrieved chunks, and instructions found inside them are never followed.
- FR-KB-11 (P1): Each citation is labeled by origin: built-in page, your file, or your website.
- FR-KB-12 (P1): Users are told not to upload sensitive data, because retrieved text is sent to third-party AI providers whose free-tier terms can differ. Online evaluation samples built-in answers only; answers from private sources are not sampled without consent.
- FR-KB-13 (P1): Quotas protect the free tiers: caps on sources, chunks and stored bytes per user, and a global cap with an alert before the database is full.

## 8. Non-Functional Requirements

Targets are initial values, to be revised after the first measured baseline.

| Area | Requirement |
| --- | --- |
| Latency (text) | p95 end to end ≤ 5 s; time to first token ≤ 1.5 s |
| Latency (voice) | p95 time to first audio ≤ 3 s for short answers (push-to-talk) |
| Availability | ≥ 99% over 30 days for the demo (best effort on free tiers) |
| Load | 5 concurrent sessions within quota; graceful degradation beyond |
| Security | No secrets in images or repo; image and dependency scanning; least-privilege credentials; every server-side request to a user-supplied URL is validated against SSRF |
| Privacy | No PII in logs; no raw audio stored; retention policy documented; CI and evals use only public or synthetic data; user sources are private to their owner and can be deleted, and users are warned that retrieved text is sent to third-party AI providers whose free-tier data terms can differ from paid tiers |
| Cost | $0 baseline; budget alert configured; cost per 1,000 requests reported |
| Portability | Runs locally with docker compose (core profile) on an 8 GB machine; deployable to any container platform |
| Maintainability | Strict typing on ports and schemas; import boundaries enforced in CI; ≥ 80% coverage on core and ports |
| Localization | Arabic and English, including right-to-left UI |

## 9. Architecture Requirements

- Style: modular monolith using ports and adapters. Ports are Protocols, adapters are providers, and dependencies always point inward.
- SOLID: one responsibility per folder; extend by adding files; every provider honors the same contract (types, errors, confidence scale); small interfaces (streaming is a separate Protocol); pipelines receive interfaces through their constructors.
- Composition: wrappers (Retrying, Fallback, Cached, Traced, Metered) are assembled by each module factory; a single composition root (api/container.py) builds the object graph from configuration.
- Enforcement: import-linter rules, mypy strict on ports, contract tests, and ADRs for key decisions.

| Folder | Responsibility |
| --- | --- |
| core | Shared types, errors, registry, config, pricing, resilience (retry, circuit breaker, rate limiter, fallback) |
| llm, asr, tts | Port, schemas, registry, factory, wrappers and providers for each model kind |
| rag | Crawler, safe fetcher, ingestion, chunking, embedder, retriever, reranker, source management |
| agent | Orchestration, tools, guardrails, human approval |
| voice | Voice pipeline, audio utilities, speech formatter |
| api | HTTP layer and composition root |
| observability | Logging, tracing, metrics, health, redaction |
| evals | Datasets, runner, metrics and evaluators for ASR, TTS, LLM and voice end to end (outside src) |
| ops, docker, infra | Monitoring config as code, containers, Terraform |

Additional locations for LLMOps and MLOps: prompts/ (versioned prompts), configs/models.lock.yaml (pinned models with their approving eval runs), data/ (versioned datasets and dataset cards), tests/redteam/ (attack regression set) and docs/model-cards/ (one card per locally hosted model).

Additional locations for knowledge sources: configs/crawl.yaml (built-in crawl rules), data/snapshots/\<id>/manifest.jsonl (page list without text), data/private/ (git-ignored page text backup), tests/fixtures/corpus/ (hand-written fixture corpus), and one safe fetcher module that both the built-in crawl and user websites must use.

## 10. Providers, Constraints and Budget

Figures were gathered on October 30, 2026 from third-party summaries. They change often, so verify them on each provider’s official page before relying on them.

| Service | Free-tier facts | Design implication |
| --- | --- | --- |
| Groq LLM | llama-3.1-8b-instant: 30 requests/min, 14,400/day, 6K tokens/min, 500K/day. gpt-oss-120b: 30/min, 1K/day, 8K tokens/min, 200K/day. Limits apply per organization | Small contexts (3 to 4 chunks), small model for routing, shared rate limiter |
| Groq Whisper (ASR) | About 20 requests/min, 2,000/day, 7,200 audio seconds/hour, 25 MB per file (sources vary) | Subset evals in CI; confidence-based re-ask |
| Groq Orpheus (Arabic TTS) | Saudi dialect only; about 10 requests/min and 100/day free; paid around $40 per million characters | Cache is mandatory; local Piper fallback |
| Gemini API | Flash-Lite about 500 requests/day, Flash about 20/day; embeddings available; free-tier data terms differ | Fallback and embeddings; public data only |
| Langfuse Hobby | 50K units/month, 30-day retention, 2 users | Sample traces; always keep errors and eval runs |
| Grafana Cloud Free | 10K metric series, 50 GB logs, 50 GB traces, about 14-day retention, 3 users | Avoid high-cardinality labels |
| Google Cloud Run | 2M requests, 180K vCPU-seconds, 360K GiB-seconds per month; billing account with payment method required; no hard spending cap | Budget alert, low max instances, stateless design |
| No-card hosts (Koyeb, Render) | About 512 MB RAM and 0.1 vCPU; Render sleeps after 15 minutes idle | Keep the image light; accept cold starts |

Constraints for knowledge sources: a free instance has about 512 MB RAM and 0.1 vCPU, so fetching, PDF parsing and embedding run as background jobs, one at a time. Free Postgres storage is limited (check the limit at Step 1.10), so per-user and global caps are required. Groq and Gemini quotas are shared by all visitors, so every new source multiplies embedding and answer traffic. The free host sleeps when idle, so jobs can start late.

## 11. Evaluation and Quality Requirements

**Datasets** (versioned, each with a dataset card and checksum)

- RAG golden set: at least 100 questions written by the owner about a named snapshot of the built-in WE pages, about half in Arabic and half in English, each with the expected source URL, the snapshot id and the owner's own notes on the expected answer (no copied page text), plus at least 20 adversarial cases (injection, out of scope, ambiguous, and questions the site does not answer). Questions about prices or offers are marked volatile.
- ASR set: 50 to 100 Egyptian-dialect utterances covering clean and noisy audio, mixed English words and numbers.
- TTS lexicon: product names, numbers and amounts that must be pronounced correctly.
- Voice end to end: golden questions synthesized to audio, noise added, then sent through ASR and the agent.

**Knowledge-source tests.** CI uses a small hand-written fixture corpus, never a live site and never the private snapshot. Automated tests also cover isolation between users, the SSRF guard (a table of blocked addresses, redirects and URL forms), upload validation, quotas, and injection text inside uploaded files and fetched pages. The full golden set runs locally and nightly against the real snapshot, which is stored outside the public repository. User sources are not part of any golden set.

| Component | Metrics | Initial gate |
| --- | --- | --- |
| Retrieval | recall@5, MRR | recall@5 ≥ 0.80 |
| Answers | faithfulness, relevance, correct-refusal rate | faithfulness ≥ 0.85 |
| Agent | tool-selection accuracy, task completion, steps per task | set after baseline |
| ASR | WER and CER after Arabic normalization, p95 latency, cost per minute | WER ≤ 0.30, p95 ≤ 3 s |
| TTS | round-trip WER, real-time factor, human clarity/naturalness/dialect score (1 to 5) on at least 20 samples | round-trip WER ≤ 0.15 |
| Voice end to end | quality drop versus the text pipeline | ≤ 10% relative |

**Process**

- The judge model is a separate role from the model under test and is calibrated against a small human-labeled sample.
- Pull requests run a subset (20 to 30 items) on same-repository branches; the full suite runs nightly and on demand.
- Reports are written as JSON and Markdown, with a comparison table across all registered providers.
- Thresholds live in configs/thresholds.yaml and change only through reviewed pull requests.

## 12. Observability Requirements

- **Logging:** structured JSON on stdout with timestamp, level, event, service, environment, version, request\_id, session\_id, trace\_id and span\_id. No raw audio, transcripts or message text at INFO (log lengths and hashes). A redaction step removes emails, phone numbers, card numbers and tokens.
- **Tracing:** instrument once with OpenTelemetry and export to a system-tracing backend and to Langfuse. Each request shows spans for audio validation, ASR, guardrails, router, retrieval, tool calls, LLM calls, speech formatting and TTS, with provider, model, tokens, cost, confidence, cache hit and fallback attributes. Sampling is configurable; errors and eval runs are always kept.
- **Metrics:** request duration, provider calls and latency by outcome, tokens, estimated cost, cache hits, fallbacks, quota used ratio, ASR confidence, time to first audio, tool calls, escalations. Labels never include user, session or request identifiers.
- **Dashboards (as code):** Overview, Voice, Providers, Cost and Quota, Agent and RAG.
- **Alerts and runbooks:** provider error rate, p95 latency, quota above 80%, open circuit breaker, 5xx spike, daily cost. Every alert links to a runbook.
- **Health:** /healthz for liveness; /readyz for readiness (database, cache, configuration) without calling paid external models.

* **Knowledge sources:** metrics for crawl and ingestion jobs by source type and outcome, pages fetched, pages blocked by robots.txt, requests blocked by the SSRF guard, snapshot age, and total stored bytes (never labeled per user). Alerts: the active snapshot is older than the allowed age, database size above 80% of the free limit, and a spike in SSRF blocks. Logs record the host of a fetched URL and a hash of its path, never the query string.

## 13. Deployment and CI/CD Requirements

- **Environments:** local (docker compose), staging and production, each with separate secrets and databases.
- **Containers:** multi-stage image, non-root user, ffmpeg included, pinned versions, no secrets. Heavy local-model dependencies live in optional extras so the production image stays light. One image serves the API and worker commands.
- **Compose profiles:** core (app, Postgres with pgvector, Redis), observability (collector, Prometheus, Grafana, Loki, Tempo) and CI.
- **Hosting (decision pending):** Option A, Cloud Run with managed Postgres and Redis (needs a payment method; set a budget alert and max instances). Option B, a no-card container host with tighter limits. The image is portable, so the choice does not change the code.
- **Infrastructure as code:** Terraform for cloud resources; secrets in a secrets manager.
- **Pull request pipeline:** lint, type check, import rules, unit and contract tests, integration tests, image build, security scans, eval subset gate.
- **Nightly pipeline:** full ASR, TTS and LLM evals, with reports and an issue on failure.
- **Merge to main:** build and push an image tagged with the commit, run database migrations, deploy to staging, run smoke tests (health, one text query, one voice fixture).
- **Production:** manual approval, deploy without traffic, canary then full rollout, automatic rollback on failed health or error-rate checks.
- **Migrations and runtime:** Alembic with expand/contract; graceful shutdown; timeouts on every external call; backpressure with friendly errors; shared Redis state for rate limits and caches.

**Experiment tracking:** no extra compose profile is needed, because eval runs are recorded in Langfuse.

**Background jobs:** crawling, file extraction and embedding run outside the web request, as a separate command or worker, one job at a time on the free instance, with a status stored in the database. The fetcher has no access to internal services.

## 14. Success Metrics

| Category | Metric |
| --- | --- |
| Quality | Eval gates passing; faithfulness, recall@5, WER and round-trip WER reported with before/after for each improvement |
| Performance | p95 text latency and time to first audio within targets on staging |
| Reliability | Fallback demonstrated in a chaos drill; ≥ 99% demo availability |
| Cost | $0 baseline; cost per 1,000 requests published |
| Portfolio | Live demo link, README with architecture diagram and numbers, dashboard and trace screenshots, 60-second demo video, at least 5 ADRs, one write-up of lessons learned |

**LLMOps and MLOps measures:** every prompt or model change ships with an eval run id; Langfuse shows the full experiment run history; one drift or canary alert is demonstrated end to end; the deprecation watch catches a retired or simulated-retired model; cost per 1,000 requests is compared across releases.

**Knowledge-source measures:** zero cross-user leaks and zero SSRF bypasses in the automated suites; every built-in crawl follows robots.txt; the golden set is tied to a named snapshot; a new snapshot is promoted only after its eval passes; a user can add a file and ask a cited question about it on the demo within a few minutes (initial target, to be measured).

## 15. Milestones

| Week | Deliverables | Exit criteria |
| --- | --- | --- |
| 1 | Repo skeleton, core package, Dockerfile, compose core, CI basics, structured logging, health endpoints, LLM port with first provider; skeleton deployed | Health check reachable on a public URL; CI green |
| 2 | Polite crawler and snapshots for the built-in WE pages, ingestion, hybrid retrieval, reranking, citations, golden set, first RAG eval, metrics and first dashboards | Baseline numbers recorded in the README |
| 3 | Agent, tools, human approval, guardrails, tracing in Langfuse, visitor identity, file uploads, safe fetcher, user websites, Sources page | Refund flow works end to end with approval; a user can add a file and a website and get cited answers that another user cannot see |
| 4 | ASR and TTS ports and providers, cache, fallback chains, voice endpoint, push-to-talk UI | A spoken question is answered with citations |
| 5 | ASR and TTS evals, Egyptian-dialect test set, round-trip test, nightly workflow | Provider comparison table published |
| 6 | Streaming voice, shared rate limiter, semantic cache, load tests | Load test report with throughput and latency |
| 7 | Staging and production pipelines, canary and rollback, alerts, runbooks, chaos drill | Rollback and alert demonstrated |
| 8 | Polish: README, ADRs, diagrams, demo video, write-up | Review checklist complete |

Build a thin end-to-end version first and improve it in place; a deployed rough version beats a polished local one.

## 16. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Free tiers change or disappear | Provider abstraction, configurable fallback chains, periodic re-verification of limits |
| Egyptian-dialect ASR/TTS quality is weak | Benchmark several providers, human listening tests, fallbacks, honest limitations section |
| Quota exhausted during a demo | Caching, shared rate limiter, fallbacks, friendly degraded-mode message |
| No international payment method for cloud signup | No-card hosts, or local demo plus video; portable Docker image |
| Weak development machine | Hosted inference, compose profiles, small local models only as fallback |
| Scope creep | Release phases, priorities, thin end to end first |
| Prompt injection or unsafe actions | Input guardrails, tool scoping, human approval for sensitive actions |
| Unexpected cloud cost | Budget alert, low max instances, cost metrics |
| Privacy exposure through free-tier data terms | Public or synthetic data only; no raw audio retention |
| Flaky LLM judge | Calibration against human labels, low temperature, repeated runs |

**LLMOps and MLOps risks.** A provider retires or silently changes a model: mitigate with the lock file, deprecation watch, canary eval and fallbacks. Quality drifts after launch: mitigate with online evaluation, drift alerts and a weekly case review. A prompt change causes a regression: mitigate with the prompt registry and eval gate. Local model files become inconsistent: mitigate with checksums and model cards.

**Knowledge-source risks.** The website's robots.txt or terms forbid automated access: check before the first crawl, and fall back to pages saved by hand. A user-submitted URL reaches an internal address (SSRF): block at the address level with tests, run the fetcher with no access to internal services, and keep the feature behind a flag. Abuse of the fetcher or storage: per-user and per-IP quotas, a global daily cap and a kill switch. One user's data leaks to another: an owner filter on every query and isolation tests in CI. Prompt injection hidden in pages and files: screen retrieved chunks and never follow instructions found in them. The site changes or disappears: snapshots, fetched-at dates, and a scheduled re-crawl with a promotion gate. Arabic search quality is weak: measure keyword and vector search separately on the golden set. Stale prices or offers: show dates and the official link. Confusion with the real company: an unofficial notice and no logos. Free host limits (512 MB RAM, 0.1 vCPU, small database): background jobs one at a time and strict caps.

## 17. Assumptions, Dependencies and Open Questions

**Assumptions:** solo builder; Python 3.11 or 3.12; the built-in knowledge base is the WE customer-service pages crawled under the site's rules, and users add their own files and websites; account and order data come from a sample database.

**Dependencies:** provider APIs (LLM, ASR, TTS), GitHub, a container host, Grafana Cloud and Langfuse.

**Open questions**

- Q1: Which hosting option, and is a payment method available?
- Q2: Which exact te.eg pages are the start URLs, and do the site's robots.txt and terms allow automated access?
- Q3: Which TTS voice and dialect becomes the default after benchmarking?
- Q4: Should the UI be Arabic-only or bilingual?
- Q5: Langfuse Cloud or self-hosted?
- Q6: What are the developer machine RAM and operating system?

* Q7: Do you want the optional trained router classifier (FR-MLOPS-4), and where should local model files be stored (Git LFS or Hugging Face Hub)?

- Q8: Should the fake backend data and the agent tools be telecom-style (bill, data usage, ticket) instead of orders?
- Q9: Should website submission be open to every visitor, require sign-in, or stay off in the public demo?
- Q10: Where is the private snapshot stored so the nightly eval can load it?

## 18. Out of Scope

Real payment or refund processing, multi-tenant SaaS, telephony and phone calls, model fine-tuning, native mobile apps, GPU inference and formal compliance certification.

The optional router classifier (FR-MLOPS-4) is in scope; fine-tuning language models is not.

Per-visitor private knowledge bases are in scope. Organizations, teams, sharing between users, billing and enterprise access control are not. Crawling arbitrary sites at scale, JavaScript rendering, OCR for scanned files and login-protected pages are out of scope for the first version.
