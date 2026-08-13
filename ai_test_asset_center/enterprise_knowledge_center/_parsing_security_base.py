"""Knowledge parsing facade with source-declared relationship authority.

The established source parsers live in ``_parsing_mechanics``. Markdown API
field names such as ``user_id``, ``couponCode`` or ``code`` may help humans
recognize a likely relationship, but a name is not a foreign-key declaration.
This facade performs two coupled corrections:

* Markdown field tables retain an explicit ``foreign_key/fk/外键/外键约束``
  declaration as structured evidence; and
* Markdown API operations remove every name-derived ``x-foreign-key`` marker
  and every non-required property admitted only by that heuristic, keeping
  relationship structure only for explicitly declared FK fields.

This preserves real source authority while removing relationship fabrication.
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


def _infer_field_rows_from_markdown(
    text: str,
    source_id: str = "",
) -> list[dict[str, Any]]:
    """Extract Markdown field rows without losing explicit FK declarations."""

    from ._format_normalizer import extract_tables_from_markdown

    rows: list[dict[str, Any]] = []
    for table in extract_tables_from_markdown(text):
        section_table, section_alias = _core._canonical_entity_name(
            _core._section_table_label(str(table.get("source_locator") or ""))
        )
        for raw in table.get("rows") or []:
            if not isinstance(raw, dict):
                continue
            explicit_table = _core._pick_first(
                raw,
                ("table", "table_name", "table name", "表", "数据表"),
            )
            explicit_name, explicit_alias = _core._canonical_entity_name(
                explicit_table
            )
            table_name = explicit_name or section_table
            table_alias = explicit_alias if explicit_name else section_alias
            field_name = _core._pick_first(
                raw,
                (
                    "field",
                    "field_name",
                    "field name",
                    "column",
                    "column_name",
                    "字段",
                    "列名",
                    "属性",
                ),
            )
            if not field_name:
                continue
            field_type = _core._pick_first(
                raw,
                ("type", "data_type", "datatype", "字段类型", "类型"),
            )
            description = _core._pick_first(
                raw,
                ("description", "desc", "comment", "说明", "描述", "备注"),
            )
            required = _core._pick_first(
                raw,
                ("required", "nullable", "必填", "是否必填"),
            )
            constraint = _core._pick_first(
                raw,
                tuple(sorted(_core._IDENTITY_CONSTRAINT_HEADERS)),
            )
            foreign_key_raw = _core._pick_first(
                raw,
                ("foreign_key", "foreignKey", "fk", "外键", "外键约束"),
            )
            foreign_key_declared = bool(_text(foreign_key_raw))
            foreign_key_value = (
                _core._doc_bool(foreign_key_raw)
                if foreign_key_declared
                else False
            )
            evidence_bits = [f"field={field_name}"]
            if table_name:
                evidence_bits.insert(0, f"table={table_name}")
            if _text(required):
                evidence_bits.append(
                    f"required={'true' if _core._doc_bool(required) else 'false'}"
                )
            if foreign_key_declared:
                evidence_bits.append(
                    f"foreign_key={'true' if foreign_key_value else 'false'}"
                )
            if field_type:
                evidence_bits.append(f"type={field_type}")
            rows.append(
                {
                    "field_id": (
                        f"field:{source_id}:"
                        f"{_core._short_hash({'table': table_name or 'default', 'field': field_name})}"
                    ),
                    "source_id": source_id,
                    "table": table_name or "default",
                    "table_id": f"table:{table_name or 'default'}",
                    "field": field_name,
                    "field_path": field_name,
                    "type": field_type,
                    "required": _core._doc_bool(required),
                    "foreign_key": foreign_key_value,
                    "foreign_key_declared": foreign_key_declared,
                    "constraint": _core._redact_text(constraint, 160),
                    "identity": _core._declares_identity(constraint),
                    "description": _core._redact_text(description, 320),
                    "table_alias": table_alias,
                    "normalized_evidence": _core._redact_text(
                        "; ".join(evidence_bits),
                        320,
                    ),
                    "evidence_kind": "NORMALIZED_STRUCTURED_DECLARATION",
                    "evidence_derivation": "markdown_field_dictionary_projection",
                    "tokens": sorted(
                        _core._tokens(
                            f"{table_name} {table_alias} {field_name} "
                            f"{field_type} {description}"
                        )
                    ),
                }
            )
    return rows


def _explicit_markdown_fk_contracts(
    text: str,
    source_id: str,
) -> list[tuple[str, str, set[str]]]:
    """Return endpoint rows with only source-declared FK field identities."""

    matches = list(_core.MARKDOWN_API_ENDPOINT_RE.finditer(text or ""))
    rows: list[tuple[str, str, set[str]]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(text or "")
        )
        section = str(text or "")[start:end]
        explicit_fields = {
            _text(row.get("field"))
            for row in _core._field_dictionary_entries(section, None, source_id)
            if isinstance(row, dict)
            and _text(row.get("field"))
            and row.get("foreign_key") is True
            and row.get("foreign_key_declared") is not False
        }
        methods = [
            part.strip().upper()
            for part in _core.re.split(r"\s*/\s*", match.group("methods"))
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
    # source order. If that invariant drifts, relationship authority fails
    # closed: no heuristic property survives rather than being attached to the
    # wrong operation.
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
        required_fields = {
            _text(value)
            for value in request_schema.get("required") or []
            if _text(value)
        }
        governed_properties: dict[str, Any] = {}
        for field, raw in properties.items():
            field_name = _text(field)
            # The historical parser placed non-required fields here only when
            # their names matched the FK heuristic. Once relationship inference
            # is removed, such a property has no request-schema authority.
            if (
                not aligned
                or (
                    field_name not in required_fields
                    and field_name not in explicit_fields
                )
            ):
                continue
            prop = dict(raw) if isinstance(raw, dict) else raw
            if isinstance(prop, dict):
                if field_name not in explicit_fields:
                    prop.pop("x-foreign-key", None)
                else:
                    prop["x-foreign-key"] = True
            governed_properties[str(field)] = prop
        request_schema = dict(request_schema)
        request_schema["properties"] = governed_properties
        operation["request_schema"] = request_schema

    if not aligned:
        # Keep JSON examples/content and explicit required names, but no property
        # admitted by the old relationship heuristic can be trusted when source
        # alignment itself is ambiguous.
        for operation in operations:
            schema = operation.get("request_schema")
            if not isinstance(schema, dict):
                continue
            properties = schema.get("properties")
            if not isinstance(properties, dict):
                continue
            required_fields = {
                _text(value)
                for value in schema.get("required") or []
                if _text(value)
            }
            schema["properties"] = {
                field: {
                    key: value
                    for key, value in raw.items()
                    if key != "x-foreign-key"
                }
                if isinstance(raw, dict)
                else raw
                for field, raw in properties.items()
                if _text(field) in required_fields
            }
    return operations


# Mechanics functions resolve these helpers from their defining-module globals.
# Point both internal call sites at the governed implementations so direct source
# parsing cannot retain either the lost explicit-FK bug or the field-name
# relationship heuristic.
_core._infer_field_rows_from_markdown = _infer_field_rows_from_markdown
_core._markdown_api_operations = _markdown_api_operations

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "_infer_field_rows_from_markdown",
        "_markdown_api_operations",
    }
)
