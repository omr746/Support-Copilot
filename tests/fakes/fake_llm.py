"""Fake LLM implementation for deterministic unit and contract testing."""

from __future__ import annotations

from copilot.llm.schemas import ChatRequest, ChatResponse, TokenUsage


class FakeLLM:
    """Deterministic in-memory ChatModel implementation satisfying the ChatModel Protocol."""

    def __init__(
        self,
        responses: list[str | Exception] | None = None,
        default_response: str = "Fake response from Support Copilot",
        model_name: str = "fake-model",
        provider_name: str = "fake",
    ) -> None:
        self._responses: list[str | Exception] = list(responses or [])
        self.default_response = default_response
        self.model_name = model_name
        self._provider_name = provider_name
        self.calls: list[ChatRequest] = []
        self.closed: bool = False

    @property
    def provider_name(self) -> str:
        return self._provider_name

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Record the call and return the next queued response (or default, or raise queued exception)."""
        self.calls.append(request)

        item = self._responses.pop(0) if self._responses else self.default_response

        if isinstance(item, Exception):
            raise item

        return ChatResponse(
            content=item,
            model=self.model_name,
            provider=self.provider_name,
            usage=TokenUsage(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
            finish_reason="stop",
        )

    async def aclose(self) -> None:
        """Record cleanup call for deterministic FakeLLM."""
        self.closed = True
