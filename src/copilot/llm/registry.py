"""Component registry for ChatModel providers."""

from __future__ import annotations

from copilot.core.registry import Registry
from copilot.llm.base import ChatModel

# Global registry instance for all ChatModel providers
llm_registry: Registry[ChatModel] = Registry("llm_providers")
