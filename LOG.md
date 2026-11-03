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
