"""Shared fundamental types and aliases for Support Copilot."""

from __future__ import annotations

from collections.abc import Mapping

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]
type JSONMapping = Mapping[str, JSONValue]
