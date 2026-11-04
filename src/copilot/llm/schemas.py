"""Frozen request and response schemas for LLM completions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Literal types strictly enforce allowed values at static analysis and runtime
Role = Literal["system", "user", "assistant", "tool"]
ModelRole = Literal["router", "answer", "judge"]
FinishReason = Literal["stop", "length", "tool_calls", "content_filter"]


class TokenUsage(BaseModel):
    """Token consumption statistics for a model completion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ChatMessage(BaseModel):
    """Immutable conversation message turn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    content: str = Field(min_length=1)
    name: str | None = None
    tool_call_id: str | None = None


class ChatRequest(BaseModel):
    """Immutable completion request sent to any LLM provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # tuple instead of list guarantees that the sequence of messages cannot be mutated in place
    messages: tuple[ChatMessage, ...] = Field(min_length=1)
    model: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    stop: tuple[str, ...] | None = None


class ChatResponse(BaseModel):
    """Immutable completion response returned by any LLM provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    model: str
    provider: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: FinishReason = "stop"
