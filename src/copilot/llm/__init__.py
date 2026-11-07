"""llm — ChatModel port, schemas, registry, factory, wrappers, and providers."""

from copilot.llm.base import ChatModel
from copilot.llm.providers.openai_compatible import OpenAICompatibleChatModel
from copilot.llm.registry import llm_registry
from copilot.llm.schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    FinishReason,
    ModelRole,
    Role,
    TokenUsage,
)
from copilot.llm.wrappers import FallbackChatModel, RetryingChatModel

__all__ = [
    "ChatMessage",
    "ChatModel",
    "ChatRequest",
    "ChatResponse",
    "FallbackChatModel",
    "FinishReason",
    "ModelRole",
    "OpenAICompatibleChatModel",
    "RetryingChatModel",
    "Role",
    "TokenUsage",
    "llm_registry",
]
