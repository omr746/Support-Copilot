# PROJECT_STATE.md: Support Copilot

> Source of truth for what exists in the repo. Put this file at the repo root.
> When you start a new chat with Claude, send this file first. Claude treats it as the current state and continues from "Next up".

Last updated: 2026-11-01 · Delivery count: 5 (rows 1, 2 and 3 confirmed) · Plan reference: Step-by-Step Execution Plan v0.1, October 30, 2026 (Steps 0 to 8.7)

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
| LLM | Groq primary (OpenAI-compatible client), Gemini fallback. Roles `router`, `answer`, `judge` are set in YAML. Code never names a model |
| ASR | Groq Whisper primary, local faster-whisper as fallback only |
| TTS | Piper (local) as the reliable base, Groq Orpheus for quality (Saudi dialect, small free quota), edge-tts for demos only. Cache in Redis |
| Embeddings and reranker | Local on CPU (fastembed or sentence-transformers CPU build) |
| Data | Postgres with pgvector, Redis, Alembic migrations |
| Agent | LangGraph. Refunds need human approval |
| Observability | structlog JSON logs, OpenTelemetry, Langfuse Cloud (tracing, datasets, experiment runs), Prometheus, Grafana, Loki, Tempo |
| MLflow | Not used. Langfuse handles experiment tracking. The model registry is `configs/models.lock.yaml` in Git |
| Evals | Live in `evals/`, outside `src`. Thresholds in `configs/thresholds.yaml` |
| Hosting | Decided 2026-11-01: Docker web service on Render (no payment method needed), with Render Postgres and Key Value if the free versions fit. Risks to check at Step 1.10: 512 MB RAM and 0.1 CPU on a free instance (the reranker stays on, see the next row), sleep when idle, free Postgres expiry, pgvector availability. Plan Steps 1.10, 7.1 and 7.3 are written for Cloud Run and get rewritten for Render |
| Reranker in hosting | Decided 2026-11-01: the reranker stays ON in the hosted demo (`reranker enabled` in `prod.yaml`). Fit it with ONNX runtimes, a multilingual model (Arabic questions, English docs), lazy loading, one worker and top 10 to 20 candidates only. Measure peak memory at Step 2.4 and again at Step 1.10. If 512 MB is not enough: smaller or quantized model, then a larger Render instance, then a separate service. Never drop it |
| Knowledge base | Decided 2026-11-01: English FastAPI docs (MIT licensed) as the built-in set, plus files uploaded by users (private to the uploader, stored as text and chunks in Postgres, never on the container disk, treated as untrusted text). The golden set and CI evals use only the FastAPI docs. Upload file types, size limits and quotas are decided at Step 2.1. The agent uses a fake orders database (Step 3.1) |
| UI language | Decided 2026-11-01: bilingual, Arabic (right-to-left) and English (left-to-right) with a language switch |
| Documents | PRD and Execution Plan exist as Claude Docs. Documentation language is English |
| Dev environment | Windows with WSL2 (Ubuntu). Repo lives at `/mnt/c/Users/user/Support-Copilot` (chosen by the owner). Rules: run scripts with `bash scripts/<name>.sh`; `.gitattributes` forces LF; `git config core.fileMode false`; store the executable bit with `git update-index --chmod=+x`; the uv virtualenv is the default `.venv` inside the repo (created with `uv venv --python 3.12`, git-ignored; owner's choice, no `UV_PROJECT_ENVIRONMENT`; it is slower on `/mnt/c`, and if uv warns about hardlinks use `--link-mode copy`); deactivate conda (`conda deactivate`) before working |
| Environment variable names | `GROQ_API_KEY`, `GEMINI_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (default `https://cloud.langfuse.com`, EU). Names are listed in `.env.example`; values live only in `.env` |
| Key check | `scripts/check_keys_live.py` uses only the Python standard library (runs before `uv sync`). It makes one read-only call per provider that spends no tokens: Groq `GET /openai/v1/models`, Gemini `GET /v1beta/models`, Langfuse `GET /api/public/projects`. It never prints key values. The Groq call does not prove the Orpheus terms were accepted; that is checked when the TTS provider is delivered |
| Plan sequencing | Plan step 0.3 and 0.4 mention CI and a protected `main`, but the CI workflow is built in step 1.9. So Step 0.4 ships a minimal `.github/workflows/ci.yml` (job `checks`) so branch protection has a check to require, and the check of Step 0.3 (`scripts/check_docs.py`) is already wired into it. Step 1.9 replaces the steps with the full pipeline |
| Grafana Cloud | Account is created in step 0.2, but no key is needed until step 7.4, so `check_keys_live.py` does not cover it yet. Add it when 7.4 is delivered |
| Python tooling | Never use `pip install` or `--break-system-packages` on the WSL system Python. Use `uv` only. Before `pyproject.toml` exists (before Step 1.1) activate `.venv`, run `uv pip install pytest`, then `pytest ...` (or use `uv run --no-project --with pytest pytest ...`). After Step 1.1 pytest lives in the `dev` dependency group and the command is `uv run pytest`. The CI file `ci.yml` still uses `pip` on the GitHub runner until Step 1.9 replaces it with uv |
| Live tests | Marked `live` and also need `COPILOT_RUN_LIVE=1`, so a plain `pytest` never calls a provider |
| Architecture | ADR-0001 (`docs/adr/0001-ports-and-adapters-with-provider-registry.md`, accepted 2026-11-01): every model kind is a port (Protocol plus schemas) with one adapter per provider, found through a registry and wrapped by a factory. Contract tests run against every provider. Import rules are enforced with import-linter from Steps 1.1 and 1.9 |
| ADR lint | `scripts/check_adrs.py` (standard library only, Python 3.8+). Rules: ADR files are named `NNNN-short-title.md` (lowercase, hyphens); numbers start at 0001 with no gap and no duplicate; the first line is `# ADR-NNNN: Title` and the number matches the file name; the Status, Context and Decision sections exist and are not empty; the Status starts with Proposed, Accepted, Rejected, Deprecated or Superseded. A Consequences section is recommended, not enforced. `README.md` and `TEMPLATE.md` in `docs/adr/` are ignored. Runs as its own step in the `checks` job of `ci.yml` |

---

## 4. Structure and status

Items marked `[x]` or `[~]` have been delivered so far. All other paths are the target layout.

```
support-copilot/
├── [ ] pyproject.toml                    (must register the `live` marker, Step 1.1)
├── [ ] .python-version
├── [x] .gitattributes
├── [x] .env.example                      (names only, Plan Step 0.2)
├── [x] .gitignore                        (full file, Plan Step 0.4; replaces your temporary one)
├── [x] LICENSE                           (MIT, Plan Step 0.4; check the copyright name)
├── [x] README.md                         (you created it; Claude has not seen it)
├── [ ] Makefile
├── [ ] .importlinter
├── [x] LOG.md                            (three decisions plus session 1, Plan Step 0.3)
├── [ ] PROJECT_STATE.md                  (this file)
├── scripts/
│   ├── [x] check_env.sh                  (Plan Step 0.1)
│   ├── [x] check_keys_live.py            (Plan Step 0.2, live key check)
│   ├── [x] check_docs.py                 (Plan Step 0.3, LOG.md and docs/adr check)
│   ├── [x] check_adrs.py                 (Plan Step 0.5, ADR lint)
│   └── [ ] smoke.sh                      (Plan Step 1.10)
├── configs/
│   ├── [ ] base.yaml
│   ├── [ ] dev.yaml
│   ├── [ ] prod.yaml
│   ├── [ ] thresholds.yaml
│   └── [ ] models.lock.yaml
├── prompts/                              (versioned prompt files, Step 2.11)
├── src/copilot/
│   ├── core/
│   │   ├── [ ] types.py
│   │   ├── [ ] errors.py
│   │   ├── [ ] registry.py
│   │   ├── [ ] config.py
│   │   ├── [ ] pricing.py
│   │   └── resilience/
│   │       ├── [ ] retry.py
│   │       ├── [ ] circuit_breaker.py
│   │       ├── [ ] rate_limiter.py
│   │       └── [ ] fallback.py
│   ├── llm/
│   │   ├── [ ] base.py
│   │   ├── [ ] schemas.py
│   │   ├── [ ] registry.py
│   │   ├── [ ] factory.py
│   │   ├── wrappers/                     retrying, fallback, cached, traced, metered
│   │   └── providers/
│   │       ├── [ ] openai_compatible.py
│   │       └── [ ] gemini_native.py
│   ├── asr/
│   │   ├── [ ] base.py  [ ] schemas.py  [ ] registry.py  [ ] factory.py
│   │   ├── wrappers/
│   │   └── providers/
│   │       ├── [ ] groq_whisper.py
│   │       └── [ ] faster_whisper_local.py
│   ├── tts/
│   │   ├── [ ] base.py  [ ] schemas.py  [ ] registry.py  [ ] factory.py
│   │   ├── wrappers/                     cached, fallback, traced, metered
│   │   └── providers/
│   │       ├── [ ] groq_orpheus.py
│   │       ├── [ ] edge_tts.py
│   │       └── [ ] piper_local.py
│   ├── rag/                              ingestion, chunking, embedder, retriever, reranker
│   ├── agent/                            graph, tools, guardrails, approvals
│   ├── voice/
│   │   ├── [ ] pipeline.py
│   │   ├── [ ] audio_utils.py
│   │   └── [ ] speech_formatter.py
│   ├── observability/
│   │   ├── [ ] context.py  [ ] logging.py  [ ] redaction.py
│   │   ├── [ ] tracing.py  [ ] metrics.py  [ ] middleware.py  [ ] health.py
│   └── api/
│       ├── [ ] main.py
│       ├── [ ] container.py
│       └── routes/
├── evals/
│   ├── core/                             evaluator, metric, dataset, runner, report, gates
│   ├── eval_asr/  eval_tts/  eval_llm/  eval_voice_e2e/
├── data/eval/{asr,tts,llm}/v1/           manifest.jsonl + DATASET_CARD.md
├── tests/
│   ├── fakes/                            FakeLLM, FakeASR, FakeTTS, FakeEmbedder
│   ├── contract/  redteam/
│   ├── unit/
│   │   ├── [x] test_check_docs.py        (tests for check_docs.py)
│   │   └── [x] test_check_adrs.py        (tests for check_adrs.py)
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
    ├── [x] ci.yml                        (minimal, Plan Steps 0.4 and 0.5: docs check, ADR check and unit tests; Step 1.9 makes it the full pipeline; job name stays `checks`)
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
| 4 | 2026-11-01 | 0.3 Make three decisions, 0.4 Create the repository | `LOG.md`, `scripts/check_docs.py`, `tests/unit/test_check_docs.py`, `docs/adr/.gitkeep`, `.gitignore`, `LICENSE`, `.github/workflows/ci.yml` | `check_docs.py` is tested with 12 pytest cases: passes on the real LOG.md; fails on missing or empty LOG.md, missing docs/adr/, each missing decision, missing or empty Reason, impossible date, and a Reason that belongs to a different decision; accepts Windows line endings. Also checked in a clean git repo: `.env` and `.env.local` are ignored, `.env.example` is not, the workflow parses and has one job named `checks`, and a deliberately failing test makes pytest exit non-zero. NOT yet done by you: the GitHub repo, branch protection and the throwaway pull request (manual Tests line of Step 0.4) | `python3 scripts/check_docs.py` and `uv run --no-project --with pytest pytest tests/unit -q -m "not live"` | no |
| 5 | 2026-11-01 | 0.5 Write ADR-0001 (Tests line: ADR lint) | `docs/adr/0001-ports-and-adapters-with-provider-registry.md`, `scripts/check_adrs.py`, `tests/unit/test_check_adrs.py`, `.github/workflows/ci.yml` (one step added, based on the file you sent) | `check_adrs.py` is tested with 37 pytest cases: passes on the real ADR-0001, on valid ADRs with different heading levels, a trailing colon and Windows line endings, and with ignored files (`.gitkeep`, `README.md`, `TEMPLATE.md`); fails on each missing Status, Context or Decision heading, a heading that only appears inside a code fence, empty and comment-only sections, an invalid Status value, a missing, misplaced, badly formatted or wrong-number title, bad file names, a numbering gap, a duplicate number, numbering that does not start at 0001, a missing folder and a folder without ADRs; command-line exit codes 0, 1 and 2. Also checked: the Python 3.8 grammar, and that the tests fail when a rule is removed from the script (two rules tried). `ci.yml` was checked: it parses as YAML, keeps the single job `checks`, and the steps run in this order: docs check, ADR check, install test tools, unit tests. NOT yet done by you: pushing it and confirming that CI turns red on a broken ADR (optional, on a throwaway branch) | `python3 scripts/check_adrs.py` and `uv run --no-project --with pytest pytest tests/unit -q -m "not live"` (expect 49 passed: 12 from `check_docs.py` plus 37 new) | no |

---

## 6. Next up

1. Finish Step 0.4 by hand (commands are in the Step 0.4 delivery message): create the public GitHub repo `support-copilot`, push `main`, turn on branch protection requiring the `checks` status, then open the throwaway pull request with a deliberately failing test and confirm the merge is blocked. Send Claude the result and a screenshot for LOG.md.
2. Replace `.github/workflows/ci.yml` with the new one, then run `python3 scripts/check_adrs.py` and `uv run --no-project --with pytest pytest tests/unit -q -m "not live"` (expect 49 passed). Tell Claude the results so rows 4 and 5 can be marked confirmed.
3. Check the `LICENSE` copyright name (`Omar`) and fix it if you want your full name.
4. Step 0 is complete when items 1 and 2 are done. Then Plan Steps 1.1 to 1.4 (project skeleton, `core`, LLM port, first provider) with their tests. Step 1.1 registers the `live` marker and excludes it from default runs. After it, run the pytest wrapper once and mark `tests/integration/test_provider_keys_live.py` as `[x]`.

## 7. Open questions

- Your operating system and RAM (affects the Docker and local-model setup).
- Optional router classifier (PRD FR-MLOPS-4): yes or no?
- Uploads: which file types (for example .md, .txt, .pdf), the size limit and the per-user quota? (decided at Step 2.1)
- Does your README mention a hosting or license choice that conflicts with the decisions in section 3? (Claude has not seen it)

## 8. Delivery rules Claude follows

- Every delivery includes: each file as a separate file with its exact repo path, a table saying where each file goes, the test command, and an updated copy of this file. No zip.
- Every new step ships with its **Tests** from the Execution Plan.
- Nothing outside the requested step is changed. If a file you edited by hand is needed, Claude asks you to send it first.
- No secrets in any file. Keys go in `.env` (git-ignored); `.env.example` lists the names only.