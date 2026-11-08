# PROJECT_STATE.md: Support Copilot

> Source of truth for what exists in the repo. Put this file at the repo root.
> When you start a new chat with Claude, send this file first. Claude treats it as the current state and continues from "Next up".

Last updated: 2026-11-07 · Delivery count: 12 (rows 1 to 12 confirmed) · Plan reference: Step-by-Step Execution Plan v0.1, October 30, 2026 (Steps 0 to 8.7)

Plan progress: Step 0, Step 1.1, Step 1.2, Step 1.3, Step 1.4, Step 1.5, and Step 1.6 are complete. Next: Step 1.7 (Observability: structured logging, tracing, metrics).

---

## 1. How this file is used

1. You tell Claude: "implement <step or module>".
2. Claude delivers the real files (code plus tests) as separate files, each with its exact repo path, and says where each file goes. You create or copy each file into the repo yourself. There is no zip.
3. Claude delivers an updated copy of this file with the new entries in sections 4 and 5.
4. You copy the files into the repo, run the test commands, and replace this file with the new one.
5. In a later chat, send this file (and any file you changed by hand) so Claude knows the exact state.

Status markers in the tree: `[ ]` planned, `[x]` done and tests passing, `[~]` delivered but not yet confirmed by you.

---

## 2. Project snapshot

| Item | Value |
|---|---|
| Name | Support Copilot (RAG + agent + voice, Arabic and English) |
| Repo root | `support-copilot/` |
| Python package | `src/copilot/` (import name `copilot`) |
| Python version | 3.12 (`requires-python = ">=3.12,<3.13"`, `.python-version` = 3.12) |
| Package manager | uv (commit `uv.lock`) |
| Docker base image | `python:3.12-slim` (Debian, not Alpine) |
| Style | ports and adapters, SOLID, registry plus factory plus wrappers |
| Composition root | `src/copilot/api/container.py` |
| Test runner | pytest (markers: `live` for real API calls, never run in CI by default) |
| Lint and types | ruff, mypy (strict on ports and schemas), import-linter |

---

## 3. Decisions made so far

| Area | Decision |
|---|---|
| LLM | Groq primary (OpenAI-compatible client), Gemini fallback. Roles `router`, `answer`, `judge` are set in YAML. Code never names a model. As of August 2026, Groq deprecated `llama-3.1-8b-instant` and `llama-3.3-70b-versatile`; modern active models are `openai/gpt-oss-20b` (fast router/MoE), `openai/gpt-oss-120b` (deep reasoning answer/judge), `qwen/qwen3.8-27b` (direct bilingual chat), and `allam-2-7b` (Arabic specialist). Gemini uses `gemini-3.1-flash-lite` (low-latency 1.4s, 500 RPD free quota). Provider transparently maps `reasoning_content` for reasoning models |
| ASR | Groq Whisper primary, local faster-whisper as fallback only |
| TTS | Piper (local) as the reliable base, Groq Orpheus for quality (Saudi dialect, small free quota), edge-tts for demos only. Cache in Redis |
| Embeddings and reranker | Local on CPU (fastembed or sentence-transformers CPU build). Both must be multilingual (Arabic, English and cross-language search); chosen and measured at Steps 2.2 and 2.4 |
| Data | Postgres with pgvector, Redis, Alembic migrations |
| Agent | LangGraph. Refunds need human approval |
| Observability | structlog JSON logs, OpenTelemetry, Langfuse Cloud (tracing, datasets, experiment runs), Prometheus, Grafana, Loki, Tempo |
| MLflow | Not used. Langfuse handles experiment tracking. The model registry is `configs/models.lock.yaml` in Git |
| Evals | Live in `evals/`, outside `src`. Thresholds in `configs/thresholds.yaml` |
| Hosting | Decided 2026-11-01: Docker web service on Render (no payment method needed), with Render Postgres and Key Value if the free versions fit. Risks to check at Step 1.10: 512 MB RAM and 0.1 CPU on a free instance (the reranker stays on, see the next row), sleep when idle, free Postgres expiry, pgvector availability. Plan Steps 1.10, 7.1 and 7.3 are written for Cloud Run and get rewritten for Render |
| Reranker in hosting | Decided 2026-11-01: the reranker stays ON in the hosted demo (`reranker enabled` in `prod.yaml`). Fit it with ONNX runtimes, a multilingual model (Arabic and English questions and pages), lazy loading, one worker and top 10 to 20 candidates only. Measure peak memory at Step 2.4 and again at Step 1.10. If 512 MB is not enough: smaller or quantized model, then a larger Render instance type (paid, not free); (3) run the reranker as a separate service. Dropping the reranker is not an option on this list. |
| Knowledge base | Decided 2026-11-01 (replaces the earlier FastAPI-docs choice): dynamic, three source types. Built-in: the WE / Telecom Egypt customer-service pages (starting point `https://www.te.eg/we`, exact pages to confirm), crawled by the owner, shared and read-only. User files and user-submitted websites: private to the user. The golden set and CI evals use only the built-in snapshot (CI uses a hand-written fixture corpus); user sources are never evaluated with a golden set. Not verified: Claude could not open te.eg, so `robots.txt` and the terms of use must be read by the owner at Step 2.0a first. If automated access is not allowed, use pages saved by hand. The agent uses a fake backend database (Step 3.1; PRD question Q8) |
| UI language | Decided 2026-11-01: bilingual, Arabic (right-to-left) and English (left-to-right) with a language switch |
| Documents | PRD and Execution Plan exist as Claude Docs. Documentation language is English |
| Dev environment | Windows with WSL2 (Ubuntu). Repo lives at `/mnt/c/Users/user/Support-Copilot` (chosen by the owner). Rules: run scripts with `bash scripts/<name>.sh`; `.gitattributes` forces LF; `git config core.fileMode false`; store the executable bit with `git update-index --chmod=+x`; the uv virtualenv is the default `.venv` inside the repo (created with `uv venv --python 3.12`, git-ignored; owner's choice, no `UV_PROJECT_ENVIRONMENT`; it is slower on `/mnt/c`, and if uv warns about hardlinks use `--link-mode copy`); deactivate conda (`conda deactivate`) before working |
| Environment variable names | `GROQ_API_KEY`, `GEMINI_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (default `https://cloud.langfuse.com`, EU), and from later steps `CRAWL_CONTACT` (Step 2.0a) and `APP_JWT_SECRET` (Step 3.9). Names are listed in `.env.example`; values live only in `.env` |
| Key check | `scripts/check_keys_live.py` uses only the Python standard library (runs before `uv sync`). It makes one read-only call per provider that spends no tokens: Groq `GET /openai/v1/models`, Gemini `GET /v1beta/models`, Langfuse `GET /api/public/projects`. It never prints key values. The Groq call does not prove the Orpheus terms were accepted; that is checked when the TTS provider is delivered |
| Plan sequencing | Plan step 0.3 and 0.4 mention CI and a protected `main`, but the CI workflow is built in step 1.9. So Step 0.4 ships a minimal `.github/workflows/ci.yml` (job `checks`) so branch protection has a check to require, and the check of Step 0.3 (`scripts/check_docs.py`) is already wired into it. Step 1.9 replaces the steps with the full pipeline |
| Grafana Cloud | Account is created in step 0.2, but no key is needed until step 7.4, so `check_keys_live.py` does not cover it yet. Add it when 7.4 is delivered |
| Python tooling | Never use `pip install` or `--break-system-packages` on the WSL system Python. Use `uv` only. Before `pyproject.toml` exists (before Step 1.1) activate `.venv`, run `uv pip install pytest`, then `pytest ...` (or use `uv run --no-project --with pytest pytest ...`). After Step 1.1 pytest lives in the `dev` dependency group and the command is `uv run pytest`. The CI file `ci.yml` still uses `pip` on the GitHub runner until Step 1.9 replaces it with uv |
| Live tests | Marked `live` and also need `COPILOT_RUN_LIVE=1`, so a plain `pytest` never calls a provider |
| Architecture | ADR-0001 (`docs/adr/0001-ports-and-adapters-with-provider-registry.md`, accepted 2026-11-01): every model kind is a port (Protocol plus schemas) with one adapter per provider, found through a registry and wrapped by a factory. Contract tests run against every provider. Import rules are enforced with import-linter from Steps 1.1 and 1.9 |
| ADR lint | `scripts/check_adrs.py` (standard library only, Python 3.8+). Rules: ADR files are named `NNNN-short-title.md` (lowercase, hyphens); numbers start at 0001 with no gap and no duplicate; the first line is `# ADR-NNNN: Title` and the number matches the file name; the Status, Context and Decision sections exist and are not empty; the Status starts with Proposed, Accepted, Rejected, Deprecated or Superseded. A Consequences section is recommended, not enforced. `README.md` and `TEMPLATE.md` in `docs/adr/` are ignored. Runs as its own step in the `checks` job of `ci.yml` |
| Crawl policy | Built-in crawl only (Steps 2.0a to 2.0c): obey `robots.txt`; honest User-Agent with the contact address from `CRAWL_CONTACT` (never in the public repo); one connection with a delay and conditional requests; stop after repeated 403 or 429; never fetch login or personal-account pages. Snapshots are immutable; only a manifest (URL, time, language, hash) is committed; page text lives in the database and in `data/private/` (git-ignored). Answers cite the page and fetched date, warn when a page is old, and say the assistant is unofficial and not affiliated with Telecom Egypt or WE (no logos). A new snapshot is promoted only after its eval passes (Step 7.11) |
| Knowledge sources for users | Visitor identity is an anonymous signed cookie (Step 3.9). Uploads (Step 3.10): `.txt`, `.md`, `.pdf`, checked by content, stored in Postgres, never on the container disk, with quotas. Retrieval always filters by owner (Steps 2.4 and 3.11). Retrieved text from every source is untrusted. User websites (Steps 3.12 and 3.13) need the SSRF guard first, sit behind a feature flag, and stay off in the public demo until their tests pass and the owner decides (PRD question Q9); they are the first thing to cut. Users are warned not to upload sensitive data, because retrieved text goes to third-party AI providers |

---

## 4. Structure and status

Items marked `[x]` or `[~]` have been delivered so far. All other paths are the target layout.

```
support-copilot/
├── [x] pyproject.toml                    (registered `live` marker, dev group, ruff/mypy configs, Step 1.1)
├── [x] .python-version                   (Step 1.1)
├── [x] .gitattributes
├── [x] .env.example                      (names only, Plan Step 0.2; the Step 0.5 delivery added CRAWL_CONTACT and APP_JWT_SECRET)
├── [x] .gitignore                        (full file, Plan Step 0.4; replaces your temporary one)
├── [x] LICENSE                           (MIT, Plan Step 0.4; check the copyright name)
├── [x] README.md                         (you created it; Claude has not seen it)
├── [x] Makefile                          (Step 1.1)
├── [x] .importlinter                     (Step 1.1)
├── [x] LOG.md                            (decisions plus sessions 1 to 4, Plan Step 0.3, 1.1, 1.2)
├── [x] PROJECT_STATE.md                  (this file)
├── scripts/
│   ├── [x] check_env.sh                  (Plan Step 0.1)
│   ├── [x] check_keys_live.py            (Plan Step 0.2, live key check)
│   ├── [x] check_docs.py                 (Plan Step 0.3, LOG.md and docs/adr check)
│   ├── [x] check_adrs.py                 (Plan Step 0.5, ADR lint)
│   ├── [x] test_live_models.py           (Live Groq model discovery and benchmark tool, zero-dep)
│   └── [ ] smoke.sh                      (Plan Step 1.10)
├── configs/
│   ├── [x] base.yaml                     (model roles: router, answer, judge, and Groq endpoint, Step 1.4)
│   ├── [ ] crawl.yaml                    (built-in crawl rules, Plan Step 2.0a)
│   ├── [x] dev.yaml                      (development environment overrides, Step 1.4)
│   ├── [ ] prod.yaml
│   ├── [ ] thresholds.yaml
│   └── [ ] models.lock.yaml
├── prompts/                              (versioned prompt files, Step 2.11)
├── src/copilot/
│   ├── [x] __init__.py                   (__version__ = "0.1.0", Step 1.1)
│   ├── core/
│   │   ├── [x] __init__.py               (Step 1.1 package skeleton)
│   │   ├── [x] types.py                  (JSON recursive type aliases, Step 1.2)
│   │   ├── [x] errors.py                 (CopilotError hierarchy, ProviderError, RateLimitError, Step 1.2)
│   │   ├── [x] registry.py               (Generic component Registry[T], Step 1.2)
│   │   ├── [x] config.py                 (AppConfig loader with 5-tier priority hierarchy, Step 1.2)
│   │   ├── [ ] pricing.py
│   │   └── resilience/
│   │       ├── [x] __init__.py           (Step 1.5)
│   │       ├── [x] sleep.py              (Sleeper protocol & AsyncioSleeper, Step 1.5)
│   │       ├── [x] retry.py              (Transient error classification, Step 1.5)
│   │       ├── [ ] circuit_breaker.py
│   │       ├── [ ] rate_limiter.py
│   │       └── [ ] fallback.py
│   ├── llm/
│   │   ├── [x] __init__.py               (Step 1.1 package skeleton)
│   │   ├── [x] base.py                   (ChatModel Protocol, Step 1.3)
│   │   ├── [x] schemas.py                (Frozen request/response schemas, Step 1.3)
│   │   ├── [x] registry.py               (ChatModel registry, Step 1.4)
│   │   ├── [ ] factory.py
│   │   ├── wrappers/
│   │   │   ├── [x] __init__.py           (Step 1.5)
│   │   │   ├── [x] retrying.py           (RetryingChatModel with backoff, jitter, retry_after, Step 1.5)
│   │   │   ├── [x] fallback.py           (FallbackChatModel sequential chain with provider recording, Step 1.5)
│   │   │   └── cached, traced, metered
│   │   └── providers/
│   │       ├── [x] __init__.py           (Step 1.4)
│   │       ├── [x] openai_compatible.py  (OpenAI-compatible adapter for Groq, Step 1.4)
│   │       └── [ ] gemini_native.py
│   ├── asr/
│   │   ├── [x] __init__.py               (Step 1.1 package skeleton)
│   │   ├── [ ] base.py  [ ] schemas.py  [ ] registry.py  [ ] factory.py
│   │   ├── wrappers/
│   │   └── providers/
│   │       ├── [ ] groq_whisper.py
│   │       └── [ ] faster_whisper_local.py
│   ├── tts/
│   │   ├── [x] __init__.py               (Step 1.1 package skeleton)
│   │   ├── [ ] base.py  [ ] schemas.py  [ ] registry.py  [ ] factory.py
│   │   ├── wrappers/                     cached, fallback, traced, metered
│   │   └── providers/
│   │       ├── [ ] groq_orpheus.py
│   │       ├── [ ] edge_tts.py
│   │       └── [ ] piper_local.py
│   ├── rag/                              (Step 1.1 package skeleton: __init__.py)
│   ├── agent/                            (Step 1.1 package skeleton: __init__.py)
│   ├── voice/
│   │   ├── [x] __init__.py               (Step 1.1 package skeleton)
│   │   ├── [ ] pipeline.py
│   │   ├── [ ] audio_utils.py
│   │   └── [ ] speech_formatter.py
│   ├── observability/
│   │   ├── [x] __init__.py               (Step 1.1 package skeleton)
│   │   ├── [ ] context.py  [ ] logging.py  [ ] redaction.py
│   │   ├── [ ] tracing.py  [ ] metrics.py  [ ] middleware.py  [ ] health.py
│   └── api/
│       ├── [x] __init__.py               (Step 1.1 package skeleton, Step 1.6 API exports)
│       ├── [x] schemas.py                (Step 1.6: AskRequest, AskResponse, HealthResponse, ReadyResponse)
│       ├── [x] main.py                   (Step 1.6: FastAPI create_app, lifespan, exception handlers)
│       ├── [x] container.py              (Step 1.6: Container composition root, readiness checks, DI)
│       ├── [ ] auth.py                   (visitor identity, Plan Step 3.9)
│       └── routes/
│           ├── [x] __init__.py           (Step 1.6)
│           ├── [x] health.py             (Step 1.6: /healthz and /readyz probes)
│           └── [x] ask.py                (Step 1.6: /ask stub calling answer role)
├── evals/
│   ├── core/                             evaluator, metric, dataset, runner, report, gates
│   ├── eval_asr/  eval_tts/  eval_llm/  eval_voice_e2e/
├── data/eval/{asr,tts,llm}/v1/           manifest.jsonl + DATASET_CARD.md
├── data/snapshots/<id>/manifest.jsonl    (page list without text, committed, Plan Step 2.0b)
├── data/private/                         (page text backup, git-ignored, never committed)
├── tests/
│   ├── [x] __init__.py                   (Step 1.3)
│   ├── [x] conftest.py                   (Step 1.1, live marker guard)
│   ├── fakes/
│   │   ├── [x] __init__.py               (Step 1.3)
│   │   ├── [x] fake_llm.py               (FakeLLM satisfying ChatModel Protocol, Step 1.3, enhanced Step 1.5)
│   │   └── [x] fake_sleeper.py           (FakeSleeper for zero-delay clock testing, Step 1.5)
│   ├── fixtures/corpus/                  hand-written fixture corpus for CI (never live site data)
│   ├── contract/
│   │   ├── [x] __init__.py               (Step 1.4)
│   │   └── [x] test_llm_contract.py      (Step 1.4: mocked HTTP contract tests & manual live test)
│   ├── redteam/
│   ├── unit/
│   │   ├── [x] test_check_docs.py        (tests for check_docs.py)
│   │   ├── [x] test_check_adrs.py        (tests for check_adrs.py)
│   │   ├── [x] test_imports.py           (Step 1.1: subpackage imports, extras isolation, live marker guard)
│   │   ├── [x] test_errors.py            (Step 1.2: exception hierarchy, provider/component attributes, retry_after)
│   │   ├── [x] test_registry.py          (Step 1.2: generic Registry[T] validation, creation, duplicate guard)
│   │   ├── [x] test_config.py            (Step 1.2: YAML loading, env overrides, priority hierarchy, prod secret guard)
│   │   ├── [x] test_llm_port.py          (Step 1.3: Protocol runtime check, frozen schemas, role rejection)
│   │   ├── [x] test_llm_wrappers.py      (Step 1.5: Retrying transient backoff, Fallback chain, Groq->Gemini)
│   │   └── [x] test_api.py               (Step 1.6: healthz, readyz 503/200, ask DI override, 422 validation)
│   └── integration/
│       └── [~] test_provider_keys_live.py   (live-marked wrapper around the key check)
├── docker/
│   ├── [ ] Dockerfile
│   └── compose/                          docker-compose.yml, docker-compose.obs.yml, docker-compose.ci.yml
├── ops/                                  otel, prometheus, grafana, loki, tempo, runbooks
├── infra/terraform/                      modules, envs/staging, envs/prod
├── docs/
│   ├── adr/
│   │   ├── [x] .gitkeep                  (keeps the folder in git; `check_docs.py` needs docs/adr/ to exist)
│   │   └── [x] 0001-ports-and-adapters-with-provider-registry.md   (Plan Step 0.5)
│   └── model-cards/
└── .github/workflows/
    ├── [x] ci.yml                        (Plan Step 1.1 & 1.2: format, lint, typecheck, import-linter, unit tests)
    └── eval-nightly.yml, deploy-staging.yml, deploy-prod.yml, security.yml
```

### Folder responsibilities

| Folder | One job |
|---|---|
| `core` | Shared types, errors, registry, config, resilience helpers. Depends on nothing else |
| `llm`, `asr`, `tts` | Port, schemas, registry, factory, wrappers and providers for one model kind |
| `rag` | Ingestion, chunking, embedding, retrieval, reranking |
| `agent` | Orchestration, tools, guardrails, human approval |
| `voice` | Voice pipeline, audio utilities, speech formatter |
| `observability` | Logging, tracing, metrics, health, redaction |
| `api` | HTTP layer and composition root |
| `evals` | Datasets, runners, metrics and gates (never imported by `src`) |

---

## 5. Implementation log

One row per delivery. Claude adds a row every time it ships files.

| # | Date | Plan step | Files created or changed | Tests added | Test command | Confirmed by you |
|---|---|---|---|---|---|---|
| 1 | 2026-11-01 | 0.1 Install tools (Tests line) | `scripts/check_env.sh` | The script is the test. Checked against stub tools: all present, a missing tool, wrong Python with and without uv, Docker daemon down, empty PATH, and the REQUIRED_PYTHON override | `bash scripts/check_env.sh` | yes (all checks passed on your machine) |
| 2 | 2026-11-01 | Environment setup (not a numbered plan step) | `.gitattributes` | Not applicable. Check with `git ls-files --eol` that tracked files show `eol=lf` | `git ls-files --eol` | yes (README.md shows `i/lf attr/text=auto eol=lf`, so the rule is active; its working copy is still CRLF, which is harmless) |
| 3 | 2026-11-01 | 0.2 Create accounts (Tests line) | `scripts/check_keys_live.py`, `tests/integration/test_provider_keys_live.py`, `.env.example` | The script is the live test. Checked against a local stub server: all keys good (with CRLF line endings and quoted values), bad keys (clear REJECTED message per provider), missing key, placeholder value, rate-limited answer (key accepted), provider unreachable, `.env` missing, `.env` not git-ignored, unknown `--only` name. Confirmed that no key value appears in any output. The pytest wrapper skips by default and fails with the same clear messages when `COPILOT_RUN_LIVE=1` | `python3 scripts/check_keys_live.py` and `COPILOT_RUN_LIVE=1 uv run pytest tests/integration/test_provider_keys_live.py -m live -s` (the pytest form needs Step 1.1) | yes (script run on your machine, all keys OK; the pytest wrapper is not run yet) |
| 4 | 2026-11-01 | 0.3 Make three decisions, 0.4 Create the repository | `LOG.md`, `scripts/check_docs.py`, `tests/unit/test_check_docs.py`, `docs/adr/.gitkeep`, `.gitignore`, `LICENSE`, `.github/workflows/ci.yml` | `check_docs.py` is tested with 12 pytest cases: passes on the real LOG.md; fails on missing or empty LOG.md, missing docs/adr/, each missing decision, missing or empty Reason, impossible date, and a Reason that belongs to a different decision; accepts Windows line endings. Also checked in a clean git repo: `.env` and `.env.local` are ignored, `.env.example` is not, the workflow parses and has one job named `checks`, and a deliberately failing test makes pytest exit non-zero. Manual part of Step 0.4 (GitHub repo, branch protection, throwaway pull request): you reported it finished; no screenshot is recorded in LOG.md yet | `python3 scripts/check_docs.py` and `uv run --no-project --with pytest pytest tests/unit -q -m "not live"` | yes (you reported Step 0.4 finished, including the GitHub part; Session 2 in LOG.md records the tests passing) |
| 5 | 2026-11-01 | 0.5 Write ADR-0001 (Tests line: ADR lint) | `docs/adr/0001-ports-and-adapters-with-provider-registry.md`, `scripts/check_adrs.py`, `tests/unit/test_check_adrs.py`, `.github/workflows/ci.yml` (one step added, based on the file you sent) | `check_adrs.py` is tested with 37 pytest cases: passes on the real ADR-0001, on valid ADRs with different heading levels, a trailing colon and Windows line endings, and with ignored files (`.gitkeep`, `README.md`, `TEMPLATE.md`); fails on each missing Status, Context or Decision heading, a heading that only appears inside a code fence, empty and comment-only sections, an invalid Status value, a missing, misplaced, badly formatted or wrong-number title, bad file names, a numbering gap, a duplicate number, numbering that does not start at 0001, a missing folder and a folder without ADRs; command-line exit codes 0, 1 and 2. Also checked: the Python 3.8 grammar, and that the tests fail when a rule is removed from the script (two rules tried). `ci.yml` was checked: it parses as YAML, keeps the single job `checks`, and the steps run in this order: docs check, ADR check, install test tools, unit tests. Optional check that CI turns red on a broken ADR (on a throwaway branch): not reported | `python3 scripts/check_adrs.py` and `uv run --no-project --with pytest pytest tests/unit -q -m "not live"` (expect 49 passed: 12 from `check_docs.py` plus 37 new) | yes (you reported Steps 0.4 and 0.5 finished; Session 2 in LOG.md records 49 tests passed) |
| 6 | 2026-11-01 | Change request: dynamic knowledge base (not a numbered plan step) | `LOG.md` (Knowledge base, Reranker, Hosting and UI language entries), `.env.example` (adds `CRAWL_CONTACT` and `APP_JWT_SECRET`), `PROJECT_STATE.md`. PRD and Execution Plan were updated in place as Claude Docs: PRD goal G10, section 7.8 (FR-KB, FR-UPL, FR-WEB), and edits to NFRs, risks, milestones, open questions Q8 to Q10; plan steps 2.0a to 2.0c, 2.5a, 3.9 to 3.14 and 7.11 added, and steps 0.3, 2.1 to 2.4, 2.6 and 2.10 changed | Not applicable (documents). Each new plan step carries its own Tests line. LOG.md was checked for the heading format and a Decision and Reason line in every entry, but Claude has not seen `check_docs.py`, so please run it | `python3 scripts/check_docs.py` | yes (LOG.md passes check_docs.py, .env.example contains new keys) |
| 7 | 2026-11-03 | 1.1 Project skeleton | `pyproject.toml`, `.python-version`, `.importlinter`, `Makefile`, `uv.lock`, `src/copilot/__init__.py` and all subpackage `__init__.py` files, `tests/conftest.py`, `tests/unit/test_imports.py` | `tests/unit/test_imports.py`: 12 unit tests passing (10 subpackage imports, package version, extras isolation), 1 live marker test skipped | `uv run pytest tests/unit/test_imports.py -v` | yes (tests verified and passed) |
| 8 | 2026-11-04 | 1.2 Core package | `src/copilot/core/types.py`, `src/copilot/core/errors.py`, `src/copilot/core/registry.py`, `src/copilot/core/config.py`, `tests/unit/test_errors.py`, `tests/unit/test_registry.py`, `tests/unit/test_config.py`, `pyproject.toml` | 25 unit tests added: 7 in `test_errors.py`, 9 in `test_registry.py`, 9 in `test_config.py`. Proves registry duplicate guard, unknown listing, component instantiation, 5-tier config priority hierarchy, YAML + env overrides, production secret validation, shared errors, and non-negative retry_after | `make check` (or `uv run pytest tests/unit -v`) | yes (all 86 unit tests passed, mypy and ruff 0 issues) |
| 9 | 2026-11-04 | 1.3 LLM port | `src/copilot/llm/schemas.py`, `src/copilot/llm/base.py`, `src/copilot/llm/__init__.py`, `tests/fakes/fake_llm.py`, `tests/fakes/__init__.py`, `tests/unit/test_llm_port.py`, `pyproject.toml` | 6 unit tests in `test_llm_port.py`: ChatModel Protocol runtime check (`isinstance`), FakeLLM completion and call history recording, frozen schemas immutability, invalid role rejection, request boundary checks, extra fields forbidden | `make check` (or `uv run pytest tests/unit/test_llm_port.py -v`) | yes (all 92 unit tests passed, mypy and ruff 0 issues) |
| 10 | 2026-11-05 | 1.4 First provider | `src/copilot/llm/providers/openai_compatible.py`, `src/copilot/llm/providers/__init__.py`, `src/copilot/llm/registry.py`, `src/copilot/core/config.py`, `src/copilot/core/errors.py`, `configs/base.yaml`, `configs/dev.yaml`, `tests/contract/__init__.py`, `tests/contract/test_llm_contract.py`, `tests/unit/test_errors.py`, `tests/unit/test_config.py` | 19 tests added: 12 mocked HTTP contract tests parametrized across registered providers (`openai_compatible`, `groq`), 4 lifecycle/isolation tests, 1 live manual Groq test (`test_live_groq_manual_call`), 2 unit tests in `test_errors.py` (numeric & HTTP-date `retry_after`), 1 unit test in `test_config.py` (YAML roles loading) | `make check` (or `uv run pytest tests/contract/test_llm_contract.py -v`) | yes (all 111 offline tests passed, 3 live tests deselected) |
| 11 | 2026-11-06 | 1.5 LLM wrappers (Retrying and Fallback) | `src/copilot/core/resilience/sleep.py`, `src/copilot/core/resilience/retry.py`, `src/copilot/core/resilience/__init__.py`, `src/copilot/llm/wrappers/retrying.py`, `src/copilot/llm/wrappers/fallback.py`, `src/copilot/llm/wrappers/__init__.py`, `src/copilot/llm/__init__.py`, `src/copilot/llm/providers/openai_compatible.py`, `src/copilot/core/config.py`, `configs/base.yaml`, `tests/fakes/fake_sleeper.py`, `tests/fakes/fake_llm.py`, `tests/fakes/__init__.py`, `tests/unit/test_llm_wrappers.py`, `tests/unit/test_config.py`, `tests/contract/test_llm_contract.py`, `LOG.md`, `PROJECT_STATE.md` | 30 tests added (+22 unit tests in `test_llm_wrappers.py`, +6 contract tests for registered `gemini` adapter, +2 live tests): proves retrying on transient errors, strict non-reductive jitter for retry_after, distinct logging for retry_exhausted vs non_retryable_error, fake clock backoff, fallback on rate limiting, raising last error, recording answering provider, composed Groq->Gemini fallback chain, and non-leakage logging | `make check` (or `uv run pytest tests/unit/test_llm_wrappers.py tests/contract/test_llm_contract.py -v`) | yes (all 139 offline tests passed, 5 live tests deselected) |
| 12 | 2026-11-07 | 1.6 API (/healthz, /readyz, /ask, composition root) | `src/copilot/api/schemas.py`, `src/copilot/api/container.py`, `src/copilot/api/main.py`, `src/copilot/api/routes/__init__.py`, `src/copilot/api/routes/health.py`, `src/copilot/api/routes/ask.py`, `src/copilot/api/__init__.py`, `tests/unit/test_api.py`, `LOG.md`, `PROJECT_STATE.md` | 18 unit tests in `tests/unit/test_api.py`: /healthz returns 200, /readyz returns 200 when up and 503 when dependency down, async checks, /ask returns FakeLLM answer through dependency override (`get_answer_model` and `get_container`), 422 validation on empty/invalid/extra payload, domain error translation (429, 502, 504), container lifecycle cleanup, and production fallback chain construction | `make check` (or `uv run pytest tests/unit/test_api.py -v`) | yes (all 157 offline tests passed, 5 live tests deselected) |

---

## 6. Next up

1. Carry-over items that do not block Week 1:
   - Add `data/private/` to `.gitignore`: `printf '\n# Crawled page text belongs to the website owner: never commit it\ndata/private/\n' >> .gitignore`.
   - Check that `.env.example` lists `CRAWL_CONTACT` and `APP_JWT_SECRET`.
   - Add the GitHub part of Step 0.4 to `LOG.md`: a line plus a screenshot of the blocked pull request.
   - Check the `LICENSE` copyright name (`Omar`).
   - Before Step 2.0a (not before Week 1): read the te.eg `robots.txt` and terms of use and write the result in `LOG.md`. The Knowledge base entry still says "Not checked yet".
2. **Next step: Step 1.7 (Observability: structured logging, tracing, metrics)**
   - Retrying wrapper: exponential backoff with jitter on `RateLimitError` and `ProviderTimeout`, respecting `retry_after`.
   - Fallback wrapper: secondary provider execution on non-transient or exhausted errors (Groq -> Gemini).
   - Contract test suite: verifying wrappers preserve the `ChatModel` Protocol and transparently retry or fall back.
3. Steps 1.6 to 1.9: Additional wrappers (`Cached`, `Traced`, `Metered`), API (`FastAPI` `/healthz`, `/readyz`, stub `/ask`), logging (`structlog`), Docker, and full CI.
4. Step 1.10: Deploy the skeleton to Render.
5. Step 1.10: Deploy the skeleton to Render.

---

## 7. Open questions

- Your operating system and RAM (affects the Docker and local-model setup).
- Optional router classifier (PRD FR-MLOPS-4): yes or no?
- Uploads: the plan starts with `.txt`, `.md` and `.pdf`; what size limit and per-user quota? (decided at Step 3.10)
- Which exact te.eg pages are the start URLs, and do the site's `robots.txt` and terms allow automated access?
- PRD Q8: should the fake backend and the agent tools be telecom-style (bill, data usage, ticket) instead of orders?
- PRD Q9: should website submission be open to every visitor, require sign-in, or stay off in the public demo?
- PRD Q10: where is the private snapshot stored so the nightly eval can load it?
- Does your README mention a hosting or license choice that conflicts with the decisions in section 3? (Claude has not seen it)

---

## 8. Delivery rules Claude follows

- Every delivery includes: each file as a separate file with its exact repo path, a table saying where each file goes, the test command, and an updated copy of this file. No zip.
- Every new step ships with its **Tests** from the Execution Plan.
- Nothing outside the requested step is changed. If a file you edited by hand is needed, Claude asks you to send it first.
- No secrets in any file. Keys go in `.env` (git-ignored); `.env.example` lists the names only.
