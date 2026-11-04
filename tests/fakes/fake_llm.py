"""Fake LLM implementation for deterministic unit and contract testing."""

from __future__ import annotations

from copilot.llm.schemas import ChatRequest, ChatResponse, TokenUsage


class FakeLLM:
    """Deterministic in-memory ChatModel implementation satisfying the ChatModel Protocol."""

    def __init__(
        self,
        responses: list[str] | None = None,
        default_response: str = "Fake response from Support Copilot",
        model_name: str = "fake-model",
    ) -> None:
        self._responses: list[str] = list(responses or [])
        self.default_response = default_response
        self.model_name = model_name
        self.calls: list[ChatRequest] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Record the call and return the next queued response (or default)."""
        self.calls.append(request)

        content = self._responses.pop(0) if self._responses else self.default_response

        return ChatResponse(
            content=content,
            model=self.model_name,
            provider=self.provider_name,
            usage=TokenUsage(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
            finish_reason="stop",
        )
