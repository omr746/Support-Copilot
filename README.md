# Support-Copilot
Support Copilot is a production-minded AI support assistant. It answers product questions from a knowledge base with citations, performs actions such as order lookups and refunds through tools (sensitive actions wait for human approval), and speaks with customers in Egyptian Arabic and English.

Every model (LLM, ASR, TTS, embedder) is a swappable provider behind a stable interface, so adding one means adding one file and a config entry. Quality is measured, not assumed: evals gate every pull request, and every request is traced, logged and metered. It is built to run on free tiers and ships with Docker, CI/CD, dashboards and alerts.
