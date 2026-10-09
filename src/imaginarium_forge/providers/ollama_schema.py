"""Opt-in Ollama wire compatibility, with original Pydantic validation intact.

Some llama.cpp grammar compilers reject large finite string repetitions.
Only new prose-planning schemas explicitly inherit this marker. Their public
schema, provider-neutral contracts and post-generation bounds do not change.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel

OLLAMA_SCHEMA_POLICY = "prose-plan-postvalidate-large-string-bounds-v1"
_LARGE_STRING_BOUND = 2000
_SCHEMA_MAPS = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
_SCHEMA_CHILDREN = (
    "items",
    "additionalItems",
    "additionalProperties",
    "unevaluatedProperties",
    "unevaluatedItems",
    "contains",
    "propertyNames",
    "not",
    "if",
    "then",
    "else",
)
_SCHEMA_ARRAYS = ("allOf", "anyOf", "oneOf", "prefixItems")


class OllamaLargeStringBoundsSchema(BaseModel):
    """Explicit opt-in; no fields, config, validators or JSON-schema hooks."""


def ollama_wire_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Return a separate wire schema; never relax the response validator.

    Only large string maxLength bounds move to post-generation validation.
    Small/zero bounds, minLength, array limits, required fields and structural
    constraints remain on the wire. Examples/defaults/enums are data, not
    schema nodes, and must not be traversed.
    """
    original = schema.model_json_schema()
    if not issubclass(schema, OllamaLargeStringBoundsSchema):
        return original
    wire = deepcopy(original)
    _defer_large_string_bounds(wire)
    return wire


def _defer_large_string_bounds(node: Any) -> None:
    if not isinstance(node, dict):
        return
    limit = node.get("maxLength")
    if (
        node.get("type") == "string"
        and isinstance(limit, int)
        and not isinstance(limit, bool)
        and limit >= _LARGE_STRING_BOUND
    ):
        del node["maxLength"]
    for keyword in _SCHEMA_MAPS:
        children = node.get(keyword)
        if isinstance(children, dict):
            for child in children.values():
                _defer_large_string_bounds(child)
    for keyword in _SCHEMA_CHILDREN:
        child = node.get(keyword)
        if isinstance(child, list):
            for item in child:
                _defer_large_string_bounds(item)
        else:
            _defer_large_string_bounds(child)
    for keyword in _SCHEMA_ARRAYS:
        children = node.get(keyword)
        if isinstance(children, list):
            for child in children:
                _defer_large_string_bounds(child)
