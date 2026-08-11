"""Table-scoped UNIQUE authority for disposable non-production fixture values.

UNIQUE constraints are DDL facts owned by one table. A field name that is unique
on table A never authorizes rewriting the same-named field on table B. This
module keeps that ownership through parsing and materialization while preserving
the historical ``set`` surface expected by existing callers.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Callable

from . import disposable_identity_materializer as _disposable

_UNIQUE_INDEX_TABLE_RE = re.compile(
    r"(?is)CREATE\s+UNIQUE\s+INDEX\s+[^;]*?\bON\b\s+"
    r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?"
    r"[`\"\[]?([A-Za-z_][A-Za-z0-9_]*)[`\"\]]?\s*"
    r"\(\s*([^)]+?)\s*\)",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


class TableScopedUniqueFields(set[str]):
    """Flattened compatibility set plus exact DDL ownership by table."""

    def __init__(self, by_table: dict[str, set[str]]) -> None:
        canonical = {
            _key(table): {
                _key(field)
                for field in fields
                if _key(field)
            }
            for table, fields in by_table.items()
            if _key(table)
        }
        canonical = {
            table: fields for table, fields in canonical.items() if fields
        }
        self.by_table = canonical
        super().__init__(
            field
            for fields in canonical.values()
            for field in fields
        )


def declared_unique_fields_scoped(schema_text: str) -> TableScopedUniqueFields:
    """Parse single-column UNIQUE declarations without losing table identity."""

    by_table: dict[str, set[str]] = {}
    if not isinstance(schema_text, str) or not schema_text.strip():
        return TableScopedUniqueFields({})

    for block in _disposable._CREATE_TABLE_BLOCK_RE.finditer(schema_text):
        table = _key(block.group(1))
        if not table:
            continue
        body = str(block.group(2) or "")
        fields = by_table.setdefault(table, set())
        for match in _disposable._UNIQUE_COLUMN_RE.finditer(body):
            field = _key(match.group(1))
            if field and not _disposable._is_identity_anchor_field(field):
                fields.add(field)
        for match in _disposable._TABLE_UNIQUE_RE.finditer(body):
            columns = [
                _key(column.strip().strip('"').strip("`[]"))
                for column in str(match.group(1) or "").split(",")
                if column.strip()
            ]
            if (
                len(columns) == 1
                and columns[0]
                and not _disposable._is_identity_anchor_field(columns[0])
            ):
                fields.add(columns[0])

    for match in _UNIQUE_INDEX_TABLE_RE.finditer(schema_text):
        table = _key(match.group(1))
        columns = [
            _key(column.strip().strip('"').strip("`[]"))
            for column in str(match.group(2) or "").split(",")
            if column.strip()
        ]
        if (
            table
            and len(columns) == 1
            and columns[0]
            and not _disposable._is_identity_anchor_field(columns[0])
        ):
            by_table.setdefault(table, set()).add(columns[0])

    return TableScopedUniqueFields(by_table)


def _singular_key(value: str) -> str:
    key = _key(value)
    if len(key) > 3 and key.endswith("ies"):
        return key[:-3] + "y"
    if len(key) > 3 and key.endswith("ses"):
        return key[:-2]
    if len(key) > 2 and key.endswith("s") and not key.endswith("ss"):
        return key[:-1]
    return key


def resolve_unique_table(
    authority: TableScopedUniqueFields,
    *,
    table_hint: str,
    body: Any,
) -> str:
    """Resolve exactly one DDL table from structural evidence or return empty."""

    hint = _key(table_hint)
    if hint in authority.by_table:
        return hint
    if hint:
        hint_singular = _singular_key(hint)
        matches = [
            table
            for table in authority.by_table
            if _singular_key(table) == hint_singular
        ]
        if len(matches) == 1:
            return matches[0]

    # Historical callers sometimes pass the API version segment (e.g. v1) as
    # the table hint. Do not infer from domain names. A table is still provable
    # when exactly one DDL table owns a UNIQUE field present in the source body.
    if isinstance(body, dict):
        body_fields = {_key(field) for field in body if _key(field)}
        matches = [
            table
            for table, fields in authority.by_table.items()
            if fields.intersection(body_fields)
        ]
        if len(matches) == 1:
            return matches[0]
    return ""


def materialize_unique_create_fields_scoped(
    value: Any,
    nonce: str,
    unique_fields: set[str],
    *,
    fk_reference_columns: dict[str, set[str]] | set[str] | None = None,
    table_hint: str = "",
    schema_tables: set[str] | None = None,
    legacy_materializer: Callable[..., tuple[Any, list[str]]] | None = None,
) -> tuple[Any, list[str]]:
    """Nonce-suffix only UNIQUE fields owned by one proven DDL table."""

    materializer = legacy_materializer or _disposable.materialize_unique_create_fields
    if not isinstance(unique_fields, TableScopedUniqueFields):
        return materializer(
            value,
            nonce,
            unique_fields,
            fk_reference_columns=fk_reference_columns,
            table_hint=table_hint,
            schema_tables=schema_tables,
        )
    if not unique_fields or not isinstance(value, dict):
        return value, []

    table = resolve_unique_table(
        unique_fields,
        table_hint=table_hint,
        body=value,
    )
    if not table:
        return deepcopy(value), []
    owned_fields = set(unique_fields.by_table.get(table) or set())
    if not owned_fields:
        return deepcopy(value), []

    return materializer(
        deepcopy(value),
        nonce,
        owned_fields,
        fk_reference_columns=fk_reference_columns,
        table_hint=table,
        schema_tables=schema_tables,
    )


__all__ = [
    "TableScopedUniqueFields",
    "declared_unique_fields_scoped",
    "resolve_unique_table",
    "materialize_unique_create_fields_scoped",
]
