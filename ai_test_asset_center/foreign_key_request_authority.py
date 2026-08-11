"""Source-declared foreign-key request guard without value-shape guessing.

An explicit ``x-foreign-key`` declaration tells QualiBug which request fields are
references. It does *not* tell us that ``1``, ``test`` or ``unknown`` cannot be a
real key. Historical runtime logic rejected those values by vocabulary/number
heuristics, creating false pre-transport gaps on perfectly valid systems.

This authority rejects only values that prove the harness itself did not finish
materialization: a surviving ``{placeholder}`` / ``<placeholder>`` token or a
QualiBug unresolved sentinel. Existence of a concrete FK belongs to the real
resolver/target evidence path, never to lexical guessing.
"""
from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER_RE = re.compile(r"[<{][A-Za-z_][A-Za-z0-9_]*[>}]")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _request_schema(operation: dict[str, Any]) -> dict[str, Any]:
    schema = _dict(operation.get("request_schema") or operation.get("requestBody"))
    content = _dict(schema.get("content"))
    if content:
        media = _dict(content.get("application/json"))
        schema = _dict(media.get("schema")) or schema
    return schema


def declared_foreign_key_fields(operation: dict[str, Any]) -> list[str]:
    properties = _dict(_request_schema(operation).get("properties"))
    return [
        str(name)
        for name, raw in properties.items()
        if _dict(raw).get("x-foreign-key") is True
    ]


def foreign_key_materialization_violations(
    request_body: Any,
    operation: dict[str, Any],
) -> list[str]:
    body = request_body if isinstance(request_body, dict) else {}
    violations: list[str] = []
    for field in declared_foreign_key_fields(operation):
        if field not in body:
            continue
        value = body.get(field)
        if not isinstance(value, str):
            continue
        text = _text(value)
        if _PLACEHOLDER_RE.search(text) or (
            text.startswith("QUALIBUG_") and text.endswith("_UNRESOLVED")
        ):
            violations.append(field)
    return violations


__all__ = [
    "declared_foreign_key_fields",
    "foreign_key_materialization_violations",
]
