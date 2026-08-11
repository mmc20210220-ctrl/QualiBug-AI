"""Knowledge parsing facade with source-declared relationship authority.

The established source parsers live in ``_parsing_mechanics``.  Markdown API
field names such as ``user_id``, ``couponCode`` or ``code`` may help humans
recognize a likely relationship, but a name is not a foreign-key declaration.
This facade removes name-derived ``x-foreign-key`` markers and retains them only
when the source field dictionary explicitly declares ``foreign_key=true`` /
``外键=是``.
"""
from __future__ import annotations

from typing import Any

from . import _parsing_mechanics as _core
from ._parsing_mechanics import *  # noqa: F401,F403

_original_markdown_api_operations = _core._markdown_api_operations


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _explicit_markdown_fk_contracts(
    text: str,
    source_id: str,
) -> list[tuple[str, str, set[str]]]:
    """Return endpoint rows with only source-declared FK field identities."""

    matches = list(_core.MARKDOWN_API_ENDPOINT_RE.finditer(text or ""))
    rows: list[tuple[str, str, set[str]]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text or "")
        section = str(text or "")[start:end]
        explicit_fields = {
            _text(row.get("field"))
            for row in _core._field_dictionary_entries(section, None, source_id)
            if isinstance(row, dict)
            and _text(row.get("field"))
            and row.get("foreign_key") is True
        }
        methods = [
            part.strip().upper()
            for part in _core.re.split(
                r"\s*/\s*",
                match.group("methods"),
            )
            if part.strip()
        ]
        path = _text(match.group("path")) or "/"
        rows.extend((method, path, set(explicit_fields)) for method in methods)
    return rows


def _markdown_api_operations(text: str, source_id: str = "") -> list[dict[str, Any]]:
    """Parse Markdown APIs, then remove every inferred relationship marker."""

    operations = [
        dict(row)
        for row in _original_markdown_api_operations(text, source_id)
    ]
    contracts = _explicit_markdown_fk_contracts(text, source_id)

    # The historical parser emits exactly one operation per endpoint-method in
    # source order.  If that invariant drifts, relationship authority fails
    # closed: no x-foreign-key marker survives rather than being attached to
    # the wrong operation.
    aligned = len(operations) == len(contracts)
    for index, operation in enumerate(operations):
        explicit_fields: set[str] = set()
        if aligned:
            method, path, declared = contracts[index]
            if (
                _text(operation.get("method")).upper() == method
                and _text(operation.get("path")) == path
            ):
                explicit_fields = declared
            else:
                aligned = False
        request_schema = operation.get("request_schema")
        if not isinstance(request_schema, dict):
            continue
        properties = request_schema.get("properties")
        if not isinstance(properties, dict):
            continue
        governed_properties: dict[str, Any] = {}
        for field, raw in properties.items():
            prop = dict(raw) if isinstance(raw, dict) else raw
            if isinstance(prop, dict):
                if not aligned or _text(field) not in explicit_fields:
                    prop.pop("x-foreign-key", None)
                elif explicit_fields and _text(field) in explicit_fields:
                    prop["x-foreign-key"] = True
            governed_properties[str(field)] = prop
        request_schema = dict(request_schema)
        request_schema["properties"] = governed_properties
        operation["request_schema"] = request_schema

    # If a late alignment failure was discovered after earlier rows were
    # processed, scrub the complete result in one second pass.
    if not aligned:
        for operation in operations:
            schema = operation.get("request_schema")
            if not isinstance(schema, dict):
                continue
            properties = schema.get("properties")
            if not isinstance(properties, dict):
                continue
            for raw in properties.values():
                if isinstance(raw, dict):
                    raw.pop("x-foreign-key", None)
    return operations


# ``_parse_source`` was defined in the mechanics module and resolves its globals
# there.  Point the exact internal call site at the governed relationship parser
# so direct parsing cannot retain the field-name heuristic.
_core._markdown_api_operations = _markdown_api_operations

# Re-export the mechanics public surface plus the governed override.
__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "_markdown_api_operations",
    }
)
