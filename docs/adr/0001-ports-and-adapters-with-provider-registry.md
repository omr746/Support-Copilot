# ADR-0001: Ports and adapters with a provider registry

## Status

Accepted on 2026-11-01.

## Context

Support Copilot depends on several external model services: an LLM, speech to text (ASR), text to speech (TTS), embeddings and a reranker. Four facts about them shape the design:

- We run on free tiers. Free-tier limits, model names and availability change often, and a provider can retire a model.
- We need fallbacks. When one provider is rate limited or down, the next one in a configured chain must answer.
- We need to compare models fairly. The evals must run the same test against every provider, and a new model must be a small change.
- Tests must not spend quota. Almost all tests have to run offline.

If the pipeline and the agent call vendor SDKs directly, vendor types and vendor errors spread through the code. Changing or adding a model then means editing many files, and every file needs its own retry and fallback logic.

## Decision

Every model kind (`llm`, `asr`, `tts`, embedder, reranker) is built with ports and adapters, and providers are found through a registry.

1. **Port.** A `typing.Protocol` in the module's `base.py`, plus frozen dataclasses in `schemas.py`. Code that uses a model depends only on the port.
2. **Adapter.** One provider class per file in `providers/`. It translates our request and response types to and from the vendor's, and translates vendor errors into the shared errors in `core/errors.py` (`ProviderError`, `RateLimitError`, `ProviderTimeout`, `AuthError`, `InvalidInputError`). Quality signals such as ASR confidence are normalized to a 0 to 1 scale.
3. **Registry.** A generic `Registry` in `core/registry.py`. A provider registers itself under a name with a decorator, and the names come from the YAML config.
4. **Factory and wrappers.** Each module has a factory that reads the config and builds a chain of wrappers around the provider: retrying, fallback, cached (TTS), traced and metered. LLMs are requested by role (`router`, `answer`, `judge`). Application code never names a model.
5. **Composition root.** `api/container.py` is the only place that builds the object graph. Pipelines and agents receive ports through their constructors.
6. **Contract tests.** The same test suite runs against every registered provider, so any provider can replace any other. Fakes in `tests/fakes/` cover everything else. Tests that call a real API carry the `live` marker and are skipped by default.
7. **Small ports.** Optional abilities are separate Protocols (for example streaming). A provider declares what it supports in `capabilities`, and callers read that instead of checking the provider's name.
8. **Boundaries are enforced.** Import rules will forbid `voice`, `agent` and `api` from importing any `providers` module, forbid `asr`, `tts` and `llm` from importing each other, and forbid `src` from importing `evals`. They are added with import-linter in Steps 1.1 and 1.9.

LangGraph is used only to orchestrate the agent. Its nodes call our ports.

## Consequences

Good:

- Adding a model is one new file plus a config block. No pipeline, agent or API code changes.
- Retry, fallback, caching, tracing and cost metering work the same for every provider, because they live in wrappers.
- Almost all tests run offline against fakes and mocked HTTP.
- The evals can compare all registered providers with one command.
- Moving between cloud and local models is a config change.

Costs:

- More files and some indirection for a project that starts with few providers per kind.
- A feature that only one vendor offers needs a capability or an extra port, or it stays out of the shared interface.
- It needs discipline: vendor types must not leak out of an adapter. The contract tests and the import rules exist to catch this.

To keep the cost down, ports stay minimal, and a new Protocol is added only when two providers or a test need it.

## Alternatives considered

- **Call vendor SDKs directly from the pipeline.** Fastest to start, but vendor types and errors leak and every model change touches many files. Rejected.
- **A general LLM framework's provider abstraction for everything.** Quick to start, but we need control over error mapping, quota handling and the trace attributes we record, and the project would follow that framework's release pace. Rejected for providers. LangGraph stays for orchestration only.
- **Plugin discovery through entry points.** More machinery than a single repository needs. Explicit registration is enough.
- **One configurable OpenAI-compatible client for all LLMs.** We do this for the LLM providers that speak that protocol: one adapter, `openai_compatible.py`, serves several models through `base_url` and `model` in the config. It is one adapter inside this design, not an alternative to it.

## References

- Execution plan: Steps 1.2 to 1.5 (core, ports, first provider, wrappers).
- PRD: FR-PRV-1 to FR-PRV-6.