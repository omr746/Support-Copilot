"""Generic component registry with validation and factory support."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from copilot.core.errors import CopilotError


class RegistryError(CopilotError):
    """Raised on invalid registration or lookup."""


class Registry[T]:
    """Generic component registry with factory support."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._entries: dict[str, Callable[..., T]] = {}

    def register(self, name: str, factory: Callable[..., T]) -> None:
        """Register a component factory under a unique name."""
        normalized = name.strip().lower()

        if not normalized:
            raise RegistryError(f"Cannot register {self.kind} with an empty name.")

        if not callable(factory):
            raise RegistryError(f"Factory for '{normalized}' must be callable.")

        if normalized in self._entries:
            raise RegistryError(f"{self.kind.capitalize()} '{normalized}' is already registered.")

        self._entries[normalized] = factory

    def get(self, name: str) -> Callable[..., T]:
        """Retrieve a factory by name."""
        normalized = name.strip().lower()

        if normalized not in self._entries:
            available = ", ".join(sorted(self._entries)) or "none"
            raise RegistryError(f"Unknown {self.kind} '{normalized}'. Available: [{available}].")

        return self._entries[normalized]

    def create(self, name: str, *args: Any, **kwargs: Any) -> T:
        """Instantiate a component using its registered factory."""
        factory = self.get(name)
        return factory(*args, **kwargs)

    def registered_names(self) -> list[str]:
        """Return sorted registered component names."""
        return sorted(self._entries)

    def __contains__(self, name: str) -> bool:
        """Check if a component name is registered (supports 'name in registry')."""
        return name.strip().lower() in self._entries

    def __repr__(self) -> str:
        return f"Registry(kind={self.kind!r}, entries={self.registered_names()!r})"
