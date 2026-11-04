"""ChatModel Port definition (Protocol)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from copilot.llm.schemas import ChatRequest, ChatResponse


@runtime_checkable
class ChatModel(Protocol):
    """Protocol defining the interface for all chat model providers and wrappers."""

    @property
    def provider_name(self) -> str:
        """Identifier of the underlying provider (e.g. 'groq', 'gemini', 'fake')."""
        ...

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Generate a completion for the provided chat request."""
        ...
