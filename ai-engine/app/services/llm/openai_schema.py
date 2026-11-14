from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from pydantic import BaseModel

_INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]+")
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")
_UNSUPPORTED_KEYWORDS = {
    "$schema",
    "default",
    "examples",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "multipleOf",
    "pattern",
    "title",
    "uniqueItems",
}


class UnsupportedJSONSchemaError(ValueError):
    """Raised when a Pydantic model cannot be represented as a strict OpenAI schema."""


def response_schema_name(model: type[BaseModel]) -> str:
    name = _CAMEL_BOUNDARY.sub("_", model.__name__).lower()
    name = _INVALID_NAME_CHARS.sub("_", name).strip("_-")
    return name[:64] or "structured_response"


def build_response_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    if _contains_free_form_object(schema):
        raise UnsupportedJSONSchemaError(
            f"{model.__name__} contains a free-form object that strict structured "
            "outputs cannot represent."
        )
    normalized = _normalize_schema(schema)
    normalized["type"] = "object"
    normalized["additionalProperties"] = False
    normalized["required"] = list(normalized.get("properties", {}))
    return normalized


def _contains_free_form_object(node: Any) -> bool:
    if isinstance(node, dict):
        additional = node.get("additionalProperties")
        if node.get("type") == "object" and additional not in (None, False):
            return True
        return any(_contains_free_form_object(value) for value in node.values())
    if isinstance(node, list):
        return any(_contains_free_form_object(value) for value in node)
    return False


def _normalize_schema(node: Any) -> Any:
    if isinstance(node, list):
        return [_normalize_schema(value) for value in node]
    if not isinstance(node, dict):
        return node

    normalized = deepcopy(node)
    const = normalized.pop("const", None)
    if const is not None and "enum" not in normalized:
        normalized["enum"] = [const]

    for keyword in _UNSUPPORTED_KEYWORDS:
        normalized.pop(keyword, None)

    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["properties"] = {
            name: _normalize_schema(value) for name, value in properties.items()
        }
        normalized["required"] = list(normalized["properties"])
        normalized["additionalProperties"] = False
    elif normalized.get("type") == "object":
        normalized["additionalProperties"] = False

    for keyword in ("$defs", "definitions"):
        definitions = normalized.get(keyword)
        if isinstance(definitions, dict):
            normalized[keyword] = {
                name: _normalize_schema(value) for name, value in definitions.items()
            }

    items = normalized.get("items")
    if items is not None:
        normalized["items"] = _normalize_schema(items)

    for keyword in ("anyOf", "oneOf", "allOf"):
        variants = normalized.get(keyword)
        if isinstance(variants, list):
            normalized[keyword] = [_normalize_schema(value) for value in variants]

    return normalized
