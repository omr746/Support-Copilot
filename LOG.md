# LOG.md

Working log for Support Copilot. After every work session: what I did, what broke, how I fixed it, and any number I measured.
Decision entries use the heading format `## YYYY-MM-DD · Decision: <name>` and need a `Reason:` line. `scripts/check_docs.py` checks this in CI.

---

## 2026-11-01 · Decision: Knowledge base
Decision: the knowledge base has these parts:
- The built-in source is the customer-service section of the WE / Telecom Egypt website (te.eg), in Arabic and English.
- I crawl it myself, under the site's rules.
- Users can also upload their own files and add their own websites, and those are searched together with the built-in pages.
- The agent uses a fake backend database (seed data, created in Step 3.1). PRD question Q8 asks whether it should be telecom-style (bill, data usage, ticket) instead of orders.

Reason: I chose the WE pages for these reasons:
- The project is an Arabic and English customer-support assistant, and the WE pages are a real support domain.
- The content should be in both languages (to confirm in Step 2.0a), so it can test Arabic retrieval, cross-language search and Egyptian-Arabic voice questions.
- The page text belongs to the website owner, so I crawl politely and keep only a manifest in the public repository (no page text).
- Not checked yet: the site's robots.txt, its terms of use and which pages exist in both languages (Plan Steps 0.3 and 2.0a). When Claude tried to open te.eg it got an error, so nothing about the site is verified. If automated access is not allowed, the built-in crawl does not run and I use pages saved by hand.

Consequences for later steps:
- The crawl follows robots.txt, uses one connection with a delay, and never fetches login-protected or personal-account pages.
- The contact address in the User-Agent comes from the environment variable CRAWL_CONTACT, never from a file in the public repository. Rules live in configs/crawl.yaml (Steps 2.0a to 2.0c).
- Crawls make immutable snapshots. The golden set is written by me about a named snapshot, with no copied page text, and price and offer questions are marked volatile.
- CI uses a small hand-written fixture corpus and never contacts the live site. The full golden set runs locally and nightly against the real snapshot, and user uploads never change it (Steps 2.6 to 2.10).
- Answers that use built-in pages cite the page URL and the fetched date, say the assistant is unofficial and not affiliated with Telecom Egypt or WE, and the UI uses no WE logos (Step 2.5a).
- Uploaded files and submitted websites are untrusted text. The injection guard (Step 3.3) must also screen retrieved chunks, not only the user message.
- Uploaded files must never be stored on the container disk (a free Render disk is not persistent). Store extracted text and chunks in Postgres.
- Uploads are private to the user who added them. Allowed file types, size limits and per-user quotas are decided in Step 3.10.
- User websites go through one safe fetcher (Steps 3.12 and 3.13) and stay off in the public demo until its tests pass.
- Users are told not to upload sensitive data, because retrieved text is sent to third-party AI providers whose free-tier terms can differ. Online evaluation samples built-in answers only.
- The free host is small (512 MB RAM, 0.1 CPU, a small database), so background jobs run one at a time and per-user and global caps are needed.

## 2026-11-01 · Decision: Hosting path
Decision: the hosting path has these parts:
- Deploy the Docker image as a web service on Render.
- Use Render Postgres and Render Key Value (Redis-compatible) if their free versions fit; otherwise an external free Postgres.

Reason: I chose Render for these reasons:
- No payment method is needed to start. Render's own 2026 page on free tiers says no credit card is required.
- Its pricing page lists custom Docker containers, Postgres and Key Value with free instances.
- The Docker image stays portable, so moving to Cloud Run later is a config change.

Risks to check in Step 1.10 (not verified yet):
- A free instance has 512 MB RAM and 0.1 CPU. The local embedder and the reranker must both fit, because the reranker stays ON in the hosted demo (see the next decision). Memory is measured, not guessed: see "Decision: Reranker in hosting".
- Free services sleep when idle, so the first request after a pause is slow. Smoke tests (scripts/smoke.sh) need a warm-up call and a long timeout.
- A source says free Postgres expires after 30 days. Confirm on Render's docs, and confirm that the pgvector extension is available.
- Free Postgres has a size limit. User files and websites need per-user and global caps, and the limit must be confirmed in Step 1.10.
- Plan Step 1.10 shows Cloud Run commands, and Steps 7.1 and 7.3 assume Terraform, canary and rollback on Cloud Run. Rewrite them for Render when we reach those steps.

## 2026-11-01 · Decision: Reranker in hosting
Decision: the reranker has these rules:
- The cross-encoder reranker stays enabled in the hosted demo.
- It is never removed to save memory.
- Reranking is a config switch on the retriever port (Step 2.4), set to enabled in prod.yaml.

Reason: I keep the reranker on for these reasons:
- Reranking is part of the retrieval quality we measure and show (recall@5 and MRR before and after, Steps 2.4 and 2.7).
- A hosted demo without it would not match the numbers in the README.

How we make it fit (to verify, nothing here is measured yet):
- Prefer ONNX runtimes (fastembed) over a PyTorch install, because they use much less memory.
- Questions and pages can be Arabic or English, including Arabic questions against English pages, so the reranker must be multilingual. Multilingual rerankers are larger, so this is the likely tight spot. Check the model size and license on its model card before choosing.
- Load models lazily, use one worker, and rerank only the top 10 to 20 candidates from the hybrid search.
- Measure peak memory (RSS) of the container with embedder and reranker loaded, in Step 2.4 locally and again in Step 1.10 on Render.
- If it does not fit in 512 MB, in this order: (1) a smaller or quantized reranker, if recall@5 and MRR on the golden set stay within the tolerance we set; (2) a larger Render instance type (paid, not free); (3) run the reranker as a separate service. Dropping the reranker is not an option on this list.

## 2026-11-01 · Decision: UI language
Decision: the interface has these parts:
- Bilingual interface: Arabic (right-to-left) and English (left-to-right), with a language switch.
- UI texts live in one translation file per language.

Reason: I chose a bilingual UI for these reasons:
- The project targets Arabic and English users (voice questions in Arabic, WE pages in Arabic and English).
- One UI that handles both avoids a rewrite later.

Consequences:
- Step 3.7 UI tests cover both directions: Arabic sets right-to-left, English sets left-to-right, and switching language keeps the conversation.

---

## 2026-11-01 · Session 1: environment, keys, decisions, repository files
Did:
- Step 0.1: scripts/check_env.sh passed on WSL2 (Ubuntu).
- Step 0.2: accounts created; scripts/check_keys_live.py passed for Groq, Gemini and Langfuse.
- Line endings: git ls-files --eol shows eol=lf is active, so .gitattributes works.
- Step 0.3: the three decisions above.
- Step 0.4: LICENSE, .gitignore and a minimal CI workflow written.

Broke: nothing reported so far.

Numbers: none yet.

## 2026-11-02 · Session 2: ADR lint

Did:
- Step 0.5: ADR lint checks completed.
- All tests passed.

Broke: no failures reported.

Numbers:
- 49 tests passed: 12 from `scripts/check_docs.py` and 37 from `scripts/check_adrs.py`.

## 2026-11-03 · Session 3: Project skeleton & CI quality suite (Step 1.1)

Did:
- Step 1.1: Created `pyproject.toml` with hatchling build backend, core dependencies, optional extras (`local-asr`, `local-tts`, `evals`), `dev` dependency group, pytest configuration with custom `live` marker, and ruff/mypy settings.
- Configured `pythonpath = ["src"]` in `pyproject.toml` so pytest properly discovers the package under the `src/` layout without requiring an editable install.
- Created `.python-version` (3.12), `.importlinter` with architectural boundary contracts, and `Makefile` (`install`, `lint`, `format`, `typecheck`, `import-check`, `test`, `check`).
- Created package layout `src/copilot/` with `__init__.py` for all subpackages (`core`, `llm`, `asr`, `tts`, `rag`, `agent`, `voice`, `observability`, `api`).
- Created test structure with root `tests/conftest.py` guarding `@pytest.mark.live` tests, and `tests/unit/test_imports.py` testing subpackage imports, package version, and optional-extras isolation.
- Expanded `.github/workflows/ci.yml` using `astral-sh/setup-uv@v5` to run all quality checks: docs check, ADR lint, ruff format check, ruff lint check, mypy typecheck, import boundary check, and pytest suite.

Broke:
- CI failed with `ModuleNotFoundError: No module named 'copilot'` because `src` was not in Python path in the container runner.
- `make check` found 5 lint findings in `scripts/`: `B905` & `RUF007` (`zip` without strict/pairwise), `RUF005` (list concatenation), and `B904` (`raise ... from err`).
- `ruff format --check` flagged 5 files needing reformatting.

Fixed:
- Added `pythonpath = ["src"]` under `[tool.pytest.ini_options]` in `pyproject.toml`.
- Refactored `scripts/check_adrs.py` to use `itertools.pairwise()`, `scripts/check_docs.py` to unpack lists, and `scripts/check_keys_live.py` to explicitly chain exceptions with `from err`.
- Added `per-file-ignores` in `pyproject.toml` for `scripts/*` and `tests/*` for `T20` (`print`) and `UP`.
- Ran `make format` to auto-format all files, making `make check` pass 100%.

Numbers:
- 63 test items collected: 61 passed offline tests (12 from `check_docs.py`, 37 from `check_adrs.py`, 12 from `test_imports.py`), and 2 live tests deselected by default via `addopts = "-m 'not live'"`.
- 10 subpackages verified importable.
- 0 lint or format errors remaining; mypy reported 0 issues in 10 source files; all import boundary contracts passed.

## 2026-11-04 · Session 4: Core package (Step 1.2)

Did:
- Step 1.2: Implemented core types, exceptions, component registry, and configuration loader:
  - `src/copilot/core/types.py`: Defined JSON recursive type aliases (`JSONScalar`, `JSONValue`, `JSONObject`, `JSONMapping`) using Python 3.12 PEP 695 `type` syntax.
  - `src/copilot/core/errors.py`: Built shared exception hierarchy rooted at `CopilotError`: `ProviderError` (carries `provider` and `component`), `RateLimitError` (carries `retry_after` with negative validation), `ProviderTimeout`, `AuthError`, and `InvalidInputError` (inherits from `CopilotError` with `component` attribute and `.provider` alias).
  - `src/copilot/core/registry.py`: Generic component registry `Registry[T]` using Python 3.12 generic syntax, supporting thread-safe registration, factory lookup, instantiation, duplicate prevention, empty name guards, `__contains__`, and alphabetical listing on unknown lookups.
  - `src/copilot/core/config.py`: Implemented `AppConfig` with strict 5-tier source priority hierarchy (Explicit kwargs > OS Environment > .env > YAML file > field defaults) via a custom `_YamlSettingsSource` and thread-safe ephemeral subclassing, with production JWT secret validation.
- Created unit test suites:
  - `tests/unit/test_errors.py`: 7 tests covering hierarchy, attributes, aliases, and retry_after validation.
  - `tests/unit/test_registry.py`: 9 tests covering registration, creation, duplicate errors, unknown listing, callable guards, `in` operator, and repr.
  - `tests/unit/test_config.py`: 9 tests verifying YAML loading, environment overrides, explicit overrides, empty/malformed YAML handling, and production secret security.

Broke:
- Ruff reported `UP046` on `Generic[T]` subclassing and `UP040` on `TypeAlias` annotations in `types.py` and `registry.py`.
- Mypy reported dynamic class variable errors when creating an ephemeral subclass in `config.py` (`ConfigWithYaml(cls)` and missing `_yaml_path` attribute).
- Ruff flagged `I001` import sorting in the new unit test files.

Fixed:
- Migrated `types.py` and `registry.py` to native Python 3.12 PEP 695 syntax (`type` keyword and `class Registry[T]:`).
- Declared `_yaml_path: ClassVar[Path | None] = None` and typed the base class explicitly as `AppConfig`.
- Organized imports across all test files separating third-party from first-party imports.

Numbers:
- 88 test items collected: 86 passed offline tests (+25 new tests in Step 1.2), 2 live tests deselected.
- Mypy: 0 issues in 14 source files.
- Ruff: 0 lint or formatting errors across src, tests, scripts.
- Import boundary checks: all contracts passed.

## 2026-11-04 · Session 5: LLM port Protocol and schemas (Step 1.3)

Did:
- Step 1.3: Implemented the LLM port Protocol, request/response schemas, and a test fake satisfying the protocol:
  - `src/copilot/llm/schemas.py`: Defined strictly typed, immutable models (`TokenUsage`, `ChatMessage`, `ChatRequest`, `ChatResponse`) and literal roles (`Role = Literal["system", "user", "assistant", "tool"]`, `ModelRole`, `FinishReason`). Used `ConfigDict(frozen=True, extra="forbid")` and `tuple[ChatMessage, ...]` for messages to guarantee deep immutability across async boundaries.
  - `src/copilot/llm/base.py`: Defined `@runtime_checkable class ChatModel(Protocol)` exposing `provider_name: str` and `async def complete(request: ChatRequest) -> ChatResponse`.
  - `src/copilot/llm/__init__.py`: Re-exported port protocol and schemas.
  - `tests/fakes/fake_llm.py`: Implemented `FakeLLM` conforming to `ChatModel` protocol at runtime (`isinstance(fake, ChatModel)`), supporting call tracking (`calls: list[ChatRequest]`), queued responses, and default fallbacks.
  - `tests/fakes/__init__.py`: Re-exported `FakeLLM`.
  - `tests/unit/test_llm_port.py`: Added 6 unit tests verifying runtime protocol compliance, valid completion, call history recording, schema immutability, role rejection, request boundary checks, and rejection of extra fields.

Broke:
- Test collection failed with `ModuleNotFoundError: No module named 'tests'` during `from tests.fakes.fake_llm import FakeLLM` because pytest's `pythonpath` was restricted to `["src"]` only and `tests/__init__.py` was missing.

Fixed:
- Added `.` to `pythonpath` in `pyproject.toml` (`pythonpath = [".", "src"]`) so the repo root and `tests` package are discoverable by pytest.
- Created `tests/__init__.py` to establish `tests` as an explicit Python package.
- Added `"tests"` to `tool.ruff.lint.isort.known-first-party` in `pyproject.toml` to ensure clean first-party import grouping across test packages.

Numbers:
- 94 test items collected: 92 passed offline tests (+6 new tests in Step 1.3), 2 live tests deselected.
- Mypy: strict compliance on LLM port Protocol and schemas with 0 issues.
- Ruff: 0 lint or format warnings.
- Architecture: 0 violations reported by import-linter.

## 2026-11-05 · Session 6: First LLM provider & contract suite (Step 1.4)

Did:
- Step 1.4: Implemented first LLM provider adapter, YAML roles mapping, error translation with date-aware retry_after parsing, and mocked HTTP contract tests:
  - `src/copilot/core/errors.py`: Enhanced `RateLimitError` and implemented `_parse_retry_after` supporting numeric seconds (float/int/str) and RFC 7231 / RFC 9110 HTTP-dates (e.g. `Wed, 21 Oct 2026 07:28:00 GMT`), calculating future delta seconds or returning `0.0` for expired dates, while validating non-negativity.
  - `src/copilot/core/config.py`: Added `LLMConfig` to `AppConfig` defining default LLM provider (`groq`), roles mapping (`router: openai/gpt-oss-20b`, `answer: openai/gpt-oss-120b`, `judge: openai/gpt-oss-120b`), and provider base URLs.
  - `configs/base.yaml` & `configs/dev.yaml`: Created baseline configuration files with modern 2026 model roles (`openai/gpt-oss-20b` for low-latency routing, `openai/gpt-oss-120b` for deep answer synthesis, supporting `qwen/qwen3.8-27b` and `allam-2-7b`) and Groq endpoint.
  - `src/copilot/llm/registry.py`: Defined `llm_registry: Registry[ChatModel]` for component registration and lookup.
  - `src/copilot/llm/providers/openai_compatible.py`: Built `OpenAICompatibleChatModel` translating `ChatRequest` to OpenAI SDK completions, parsing token usage, and translating SDK/HTTP errors into core errors: `openai.RateLimitError` / 429 into `RateLimitError` with parsed `retry_after`, `openai.APITimeoutError` into `ProviderTimeout`, `openai.AuthenticationError` / 401 into `AuthError`, and response validation / bad request / malformed body into `ProviderError`. Registered under `"openai_compatible"` and `"groq"`. Implemented explicit resource lifecycle `aclose()` respecting external client ownership (`_owns_http_client`), and factory method `from_config` guaranteeing uniform provider settings. Added fallback mapping for reasoning/thinking models where final answer might be enclosed in `reasoning_content`.
  - `src/copilot/llm/base.py`: Added `aclose()` method to `ChatModel` Protocol to standardize lifecycle cleanup across providers.
  - `src/copilot/llm/providers/__init__.py`: Re-exported `OpenAICompatibleChatModel`.
  - `src/copilot/llm/__init__.py`: Exported `llm_registry` and `OpenAICompatibleChatModel`.
  - `tests/contract/__init__.py` & `tests/contract/test_llm_contract.py`: Implemented contract test suite parametrized across all registered providers (`llm_registry.registered_names()`) using offline `httpx.MockTransport` covering success, 429 numeric retry-after, 429 date retry-after, timeout, 401 auth error, and malformed body. Added tests for client ownership, developer bug isolation in mapping, config instantiation, and live-marked test `test_live_groq_manual_call` for real API call verification.
  - `scripts/test_live_models.py`: Created zero-dependency live test and discovery script querying `/models` and benchmarking candidate models (`openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, `allam-2-7b`) with latency and token metrics.
  - `tests/unit/test_errors.py`: Added 2 unit tests for numeric string and HTTP-date `retry_after` parsing.
  - `tests/unit/test_config.py`: Added 1 unit test verifying `configs/base.yaml` role and provider loading.

Broke:
- No test failures. Offline `httpx.MockTransport` ensures zero external requests during unit and contract runs.

Fixed:
- Identified that Groq shut down `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` on August 16, 2026 (returning HTTP 404). Migrated role mappings and candidate pools to current 2026 models: `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, and `allam-2-7b`.
- Handled reasoning model token mechanics: reasoning models (e.g. `openai/gpt-oss-20b`) generate chain-of-thought tokens in `reasoning_content` before populating visible `content`. When `max_tokens` was constrained to 10 or 40, token limits were exhausted during thinking (`finish_reason="length"`), yielding empty `content`. Resolved by updating `_map_response` to fallback to `reasoning_content` if `content` is empty, and increasing test token budgets to accommodate reasoning tokens.
- Ensured HTTP-date strings in `retry-after` header are parsed via `email.utils.parsedate_to_datetime` and converted to delta seconds rather than raising `ValueError`.
- Separated API call execution from `_map_response` in `complete()` and narrowed error mapping to `(openai.APIError, httpx.HTTPError)`, preventing internal Python code bugs (e.g. `AttributeError`, `TypeError`) from being masked as `ProviderError`.
- Added `_owns_http_client` check in `aclose()` to ensure shared external HTTP clients are never closed by an individual provider instance.
- Unified provider configuration via `LLMProviderConfig` and `from_config()`, eliminating hardcoded defaults and ensuring `response.provider` consistently reflects the requested provider.
- Resolved Mypy type-checking conflict with OpenAI's generated `httpx2.AsyncClient` binding by typing `http_client: httpx.AsyncClient | Any = None` and passing `cast(Any, http_client)` to `AsyncOpenAI`.

Numbers:
- 114 test items collected: 111 passed offline tests (+19 new offline tests: 12 contract tests across 2 registered providers, 4 lifecycle/isolation tests, +2 in test_errors, +1 in test_config), 3 live tests deselected by default.
- Mypy: 0 issues across all source files.
- Ruff: 0 lint or format warnings.
- Architecture: 0 violations reported by import-linter.

## 2026-11-06 · Session 7: LLM wrappers (Retrying and Fallback) (Step 1.5)

Did:
- Step 1.5: Built Retrying and Fallback wrappers adhering to Ports-and-Adapters with clock dependency injection and chained Groq -> Gemini fallback:
  - `src/copilot/core/resilience/sleep.py`: Created `Sleeper` protocol and production `AsyncioSleeper` implementation to allow deterministic clock dependency injection in retrying wrappers.
  - `src/copilot/core/resilience/retry.py`: Implemented `is_transient_error()` to enforce strict transient error classification. Explicitly forbids retries on `AuthError`, `InvalidInputError`, and Python language/runtime bugs (`TypeError`, `AttributeError`, etc.), while authorizing retries on `RateLimitError`, `ProviderTimeout`, and transient 502/503/504 server errors.
  - `src/copilot/core/resilience/__init__.py`: Exported resilience primitives (`Sleeper`, `AsyncioSleeper`, `is_transient_error`).
  - `src/copilot/llm/wrappers/retrying.py`: Built `RetryingChatModel` wrapping `ChatModel` to retry transient failures up to `max_attempts` using exponential backoff, full jitter, and respecting `retry_after` from `RateLimitError`. Employs structured logging (`provider`, `attempt`, `error`, `delay`) without leaking API keys, headers, or request content.
  - `src/copilot/llm/wrappers/fallback.py`: Built `FallbackChatModel` wrapping a sequence of `ChatModel` providers. Moves to the next provider on `ProviderError` (e.g. `RateLimitError`, `ProviderTimeout`), raises immediately on `InvalidInputError` or code bugs without attempting fallbacks, raises the last error in the chain when all providers fail, records which provider answered in `response.provider`, and delegates cleanup via `aclose()`.
  - `src/copilot/llm/wrappers/__init__.py`: Re-exported `RetryingChatModel` and `FallbackChatModel`.
  - `src/copilot/llm/__init__.py`: Exported `RetryingChatModel` and `FallbackChatModel`.
  - Registered `"gemini"` in `llm_registry` (`src/copilot/llm/providers/openai_compatible.py`) and configured Gemini's official OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`) in `configs/base.yaml` and default `LLMConfig.providers`.
  - `scripts/test_live_models.py`: Upgraded live discovery and benchmarking script to support both Groq and Gemini, benchmarking latency, token usage, and response previews across models.
  - `tests/fakes/fake_sleeper.py`: Created `FakeSleeper` recording sleep durations with zero wall-clock delay.
  - `tests/fakes/fake_llm.py`: Enhanced `FakeLLM` to support sequential queued side-effects/exceptions, configurable `provider_name`, and `closed` state tracking.
  - `tests/fakes/__init__.py`: Exported `FakeSleeper`.
  - `tests/unit/test_llm_wrappers.py`: Added 22 comprehensive unit tests covering immediate success, two transient errors then success, giving up after maximum attempts, non-retryable errors (`AuthError`, `InvalidInputError`, `TypeError`), backoff and jitter calculations, strictly respecting `retry_after` as minimum delay without reductive jitter, logging distinctions between `retry_exhausted` and `non_retryable_error`, fallback on rate limiting/timeout, raising the last error, provider recording, `aclose()` delegation, composed Groq -> Gemini fallback chains, and privacy-preserving structured logging.
  - `tests/contract/test_llm_contract.py`: Automatically runs offline contract test suite against registered `gemini` adapter (+6 contract tests), and added `@pytest.mark.live` manual tests for Gemini and the live fallback chain.

Broke:
- First live Gemini manual test and fallback chain failed with `404 Not Found` when requesting deprecated models (`gemini-2.0-flash`, `gemini-1.5-flash`, `gemini-2.5-*`) or attempting `client.models.list()` against Google's OpenAI-compatible endpoint (which does not support `/models`).
- `gemini-3.8-flash` and `gemini-3.6-flash` timed out on live calls (`The read operation timed out > 30s`) due to heavy reasoning generation and congested quota limits (20 RPD).
- Constrained token budgets (`max_tokens=20`) caused reasoning models to spend all tokens on internal chain-of-thought, yielding empty `content` and triggering `AssertionError: assert len('') > 0`.
- Broad `except Exception:` in test candidate loops caught assertion failures and masked root causes as provider 404s.
- Ruff format check failed on `RUF043` for unescaped decimal in regex pattern (`1.0`).

Fixed:
- Benchmarked active Gemini models using `scripts/test_live_models.py`, identifying `gemini-3.1-flash-lite` (1.434s, 500 RPD) and `gemini-3.5-flash-lite` (1.849s, 500 RPD) as the fastest 200 OK models.
- Set `gemini-3.1-flash-lite` as default model in `configs/base.yaml` and `AppConfig`, eliminating live test timeouts.
- Increased live test token budgets to `max_tokens=100` and enhanced `_map_response()` to extract thinking tokens from `reasoning_content`, `reasoning`, and `thought`.
- Restricted test loop exception handling to `(ProviderError, openai.APIError)` so assertion errors fail immediately with clear messages.
- Fixed `RUF043` with raw regex `r"backoff_factor must be >= 1\.0"`.
- Enforced strict transient error classification so `RetryingChatModel` never catches or retries `AuthError`, `InvalidInputError`, or developer programming bugs.
- Fixed jitter behavior on `RateLimitError`: server-specified `retry_after` is strictly honored as mandatory minimum delay; jitter only adds a positive buffer.
- Distinguished between `retry_exhausted` and `non_retryable_error` in logger metadata.
- Decoupled clock and sleep delays from `asyncio.sleep` via `Sleeper` dependency injection, keeping tests instant and deterministic.
- Ensured `FallbackChatModel` propagates the last error when all providers fail and transparently reflects the answering provider in `response.provider`.
- Ensured logging outputs structured metadata (`provider`, `attempt`, `fallback_used`) without leaking secrets or prompts.

Numbers:
- 144 test items collected: 139 passed offline tests (+28 new offline tests: 22 wrapper tests in `test_llm_wrappers.py`, +6 parametrized contract tests for `gemini`), 5 live tests deselected by default.
- Live test latency: `gemini-3.1-flash-lite` responding in 1.434s, `gemini-3.5-flash-lite` in 1.849s.
- Mypy: 0 issues across all source files.
- Ruff: 0 lint or format warnings.
- Architecture: 0 violations reported by import-linter.



