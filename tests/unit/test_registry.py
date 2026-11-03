"""Unit tests for generic component Registry (Plan Step 1.2)."""

from __future__ import annotations

import pytest

from copilot.core.errors import CopilotError
from copilot.core.registry import Registry, RegistryError


class FakeComponent:
    def __init__(self, greeting: str = "hello") -> None:
        self.greeting = greeting


def test_registry_error_inherits_from_copilot_error() -> None:
    err = RegistryError("Registration failure")
    assert isinstance(err, CopilotError)


def test_register_and_create_component() -> None:
    reg = Registry[FakeComponent]("component")
    reg.register("fake", FakeComponent)

    instance = reg.create("fake", greeting="custom")
    assert isinstance(instance, FakeComponent)
    assert instance.greeting == "custom"


def test_get_returns_factory() -> None:
    reg = Registry[FakeComponent]("component")
    reg.register("fake", FakeComponent)

    factory = reg.get("fake")
    instance = factory(greeting="direct")
    assert instance.greeting == "direct"


def test_duplicate_registration_raises() -> None:
    reg = Registry[FakeComponent]("component")
    reg.register("fake", FakeComponent)

    with pytest.raises(RegistryError, match="already registered"):
        reg.register("fake", FakeComponent)


def test_unknown_name_lists_available_in_alphabetical_order() -> None:
    reg = Registry[FakeComponent]("model")
    reg.register("zeta", FakeComponent)
    reg.register("alpha", FakeComponent)

    with pytest.raises(RegistryError) as exc_info:
        reg.get("unknown_model")

    err_msg = str(exc_info.value)
    assert "Unknown model 'unknown_model'" in err_msg
    assert "Available: [alpha, zeta]" in err_msg


def test_empty_registration_name_raises() -> None:
    reg = Registry[FakeComponent]("component")
    with pytest.raises(RegistryError, match="empty name"):
        reg.register("   ", FakeComponent)


def test_non_callable_factory_raises() -> None:
    reg = Registry[FakeComponent]("component")
    with pytest.raises(RegistryError, match="must be callable"):
        reg.register("bad", "not_callable")  # type: ignore[arg-type]


def test_contains_operator_and_registered_names() -> None:
    reg = Registry[FakeComponent]("service")
    assert "groq" not in reg

    reg.register("Groq", FakeComponent)
    assert "groq" in reg
    assert "GROQ" in reg
    assert reg.registered_names() == ["groq"]


def test_repr_output() -> None:
    reg = Registry[FakeComponent]("provider")
    reg.register("groq", FakeComponent)
    assert repr(reg) == "Registry(kind='provider', entries=['groq'])"
