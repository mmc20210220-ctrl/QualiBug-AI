"""Public fixture materializer facade with source-truthful fixture authority.

The core module owns fixture DAG/data binding. The composed wrapper proves the
frozen FlowDataRequirement and establishes compiled state preconditions before
measured business steps. This public boundary adds three truth constraints:

* a generic response ``id`` can satisfy only the same binding-target identity,
  never an arbitrary different identity-shaped field;
* a documented fixture value that conflicts with a schema CHECK enum is never
  silently replaced with the first legal value; and
* disposable UNIQUE-key rewriting is table-scoped. A UNIQUE declaration on one
  DDL table can never mutate a same-named field on another table. If the target
  table cannot be resolved uniquely from source structure, no business value is
  rewritten.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from . import disposable_identity_materializer as _disposable
from . import experiment_fixture_materializer_core as _core
from . import experiment_fixture_materializer_with_preconditions as _composed
from .cleanup_identity_authority import strict_observed_resource_identity

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

_original_strict_fixture_preconditions = (
    _composed._strict_validate_fixture_preconditions
)
_original_materialize_unique_create_fields = (
    _disposable.materialize_unique_create_fields
)

_UNIQUE_INDEX_TABLE_RE = re.compile(
    r"(?is)CREATE\s+UNIQUE\s+INDEX\s+[^;]*?\bON\b\s+"
    r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?"
    r"[`\"\[]?([A-Za-z_][A-Za-z0-9_]*)[`\"\]]?\s*"
    r"\(\s*([^)]+?)\s*\)",
)


class _TableScopedUniqueFields(set[str]):
    """Compatibility set carrying the DDL table that owns each UNIQUE field."""

    def __init__(self, by_table: dict[str, set[str]]) -> None:
        canonical = {
            _identity_key(table): {
                _identity_key(field)
                for field in fields
                if _identity_key(field)
            }
            for table, fields in by_table.items()
            if _identity_key(table)
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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _identity_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _identity_shaped(value: Any) -> bool:
    key = _identity_key(value)
    return bool(key) and key.endswith(("id", "ref", "uuid"))


def _exact_response_field_present(body: dict[str, Any], field: str) -> bool:
    if field in body and body.get(field) not in (None, "", [], {}):
        return True
    for envelope in ("data", "result", "entity", "record"):
        nested = body.get(envelope)
        if (
            isinstance(nested, dict)
            and field in nested
            and nested.get(field) not in (None, "", [], {})
        ):
            return True
    return False


def _preserve_source_enum_conflicts(
    body: Any,
    enums_by_table: dict[str, dict[str, list[str]]],
    *,
    table_hint: str = "",
) -> tuple[Any, list[str]]:
    """Diagnose CHECK-enum drift without mutating the source fixture body."""

    if not isinstance(body, dict):
        return body, []
    table_key = _identity_key(table_hint)
    table_enums = (
        enums_by_table.get(table_key)
        if isinstance(enums_by_table, dict)
        else None
    )
    if not isinstance(table_enums, dict):
        return body, []
    conflicts: list[str] = []
    for key, value in body.items():
        if not isinstance(value, str):
            continue
        allowed = table_enums.get(_identity_key(key))
        if not isinstance(allowed, list) or not allowed:
            continue
        if value.lower() not in {_text(item).lower() for item in allowed}:
            conflicts.append(str(key))
    return deepcopy(body), sorted(set(conflicts))


def _declared_unique_fields_scoped(schema_text: str) -> _TableScopedUniqueFields:
    """Parse single-column UNIQUE constraints with their owning DDL table."""

    by_table: dict[str, set[str]] = {}
    if not isinstance(schema_text, str) or not schema_text.strip():
        return _TableScopedUniqueFields({})

    for block in _disposable._CREATE_TABLE_BLOCK_RE.finditer(schema_text):
        table = _identity_key(block.group(1))
        if not table:
            continue
        body = str(block.group(2) or "")
        fields = by_table.setdefault(table, set())

        for match in _disposable._UNIQUE_COLUMN_RE.finditer(body):
            field = _identity_key(match.group(1))
            if field and not _disposable._is_identity_anchor_field(field):
                fields.add(field)

        # Table-level UNIQUE(a) belongs only to this CREATE TABLE block.
        for match in _disposable._TABLE_UNIQUE_RE.finditer(body):
            columns = [
                _identity_key(column.strip().strip('"').strip("`[]"))
                for column in str(match.group(1) or "").split(",")
                if column.strip()
            ]
            if (
                len(columns) == 1
                and columns[0]
                and not _disposable._is_identity_anchor_field(columns[0])
            ):
                fields.add(columns[0])

    # CREATE UNIQUE INDEX is outside CREATE TABLE, so retain the table captured
    # by the index statement instead of flattening the field globally.
    for match in _UNIQUE_INDEX_TABLE_RE.finditer(schema_text):
        table = _identity_key(match.group(1))
        columns = [
            _identity_key(column.strip().strip('"').strip("`[]"))
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

    return _TableScopedUniqueFields(by_table)


def _singular_key(value: str) -> str:
    key = _identity_key(value)
    if len(key) > 3 and key.endswith("ies"):
        return key[:-3] + "y"
    if len(key) > 3 and key.endswith("ses"):
        return key[:-2]
    if len(key) > 2 and key.endswith("s") and not key.endswith("ss"):
        return key[:-1]
    return key


def _resolve_unique_table(
    authority: _TableScopedUniqueFields,
    *,
    table_hint: str,
    body: Any,
) -> str:
    """Resolve exactly one DDL table without cross-table field guessing."""

    hint = _identity_key(table_hint)
    if hint in authority.by_table:
        return hint

    # Singular/plural spelling is structural, not business vocabulary.
    if hint:
        hint_singular = _singular_key(hint)
        matches = [
            table
            for table in authority.by_table
            if _singular_key(table) == hint_singular
        ]
        if len(matches) == 1:
            return matches[0]

    # Versioned paths in the historical core may hand us ``v1`` instead of the
    # collection table. We do not guess from path vocabulary. A DDL table can
    # still be resolved if exactly one table owns a UNIQUE field actually
    # present in this source-derived create body. Ambiguity means no rewrite.
    if isinstance(body, dict):
        body_fields = {_identity_key(key) for key in body if _identity_key(key)}
        matches = [
            table
            for table, fields in authority.by_table.items()
            if fields.intersection(body_fields)
        ]
        if len(matches) == 1:
            return matches[0]
    return ""


def _materialize_unique_create_fields_scoped(
    value: Any,
    nonce: str,
    unique_fields: set[str],
    *,
    fk_reference_columns: dict[str, set[str]] | set[str] | None = None,
    table_hint: str = "",
    schema_tables: set[str] | None = None,
) -> tuple[Any, list[str]]:
    """Rewrite UNIQUE keys only after one owning DDL table is proven."""

    if not isinstance(unique_fields, _TableScopedUniqueFields):
        return _original_materialize_unique_create_fields(
            value,
            nonce,
            unique_fields,
            fk_reference_columns=fk_reference_columns,
            table_hint=table_hint,
            schema_tables=schema_tables,
        )
    if not unique_fields or not isinstance(value, dict):
        return value, []

    table = _resolve_unique_table(
        unique_fields,
        table_hint=table_hint,
        body=value,
    )
    if not table:
        return deepcopy(value), []
    owned_fields = set(unique_fields.by_table.get(table) or set())
    if not owned_fields:
        return deepcopy(value), []

    return _original_materialize_unique_create_fields(
        deepcopy(value),
        nonce,
        owned_fields,
        fk_reference_columns=fk_reference_columns,
        table_hint=table,
        schema_tables=schema_tables,
    )


def _strict_validate_fixture_preconditions(
    exp: dict[str, Any],
    fixture_response_body: Any,
    target: str,
) -> list[dict[str, str]]:
    """Require identity-shaped preconditions to stay in the target domain."""

    failures = list(
        _original_strict_fixture_preconditions(
            exp,
            fixture_response_body,
            target,
        )
    )
    if not isinstance(fixture_response_body, dict):
        return failures

    required = _composed._declared_fixture_precondition_fields(exp, target)
    target_key = _identity_key(target)
    existing_failure_fields = {
        _text(row.get("field"))
        for row in failures
        if isinstance(row, dict) and _text(row.get("field"))
    }
    for field in required:
        if not _identity_shaped(field) or field in existing_failure_fields:
            continue
        if _exact_response_field_present(fixture_response_body, field):
            continue
        if (
            _identity_key(field) == target_key
            and strict_observed_resource_identity(
                fixture_response_body,
                identity_column=field,
            )
        ):
            continue
        failures.append(
            {
                "field": field,
                "reason": "fixture_precondition_identity_authority_mismatch",
                "target": _text(target),
            }
        )
    return failures


# Core fixture setup imported these helpers as private aliases. Replace the exact
# call sites so the historical implementation cannot retain either silent enum
# alignment or cross-table UNIQUE rewriting.
_core._align_body_enums_with_declared_schema = _preserve_source_enum_conflicts
_core._declared_unique_fields = _declared_unique_fields_scoped
_core._materialize_unique_create_fields = _materialize_unique_create_fields_scoped

# The composed materializer installs its own precondition validator into core
# immediately before execution. Replace that composition point here so public
# execution uses the target-scoped identity authority.
_composed._strict_validate_fixture_preconditions = (
    _strict_validate_fixture_preconditions
)
materialize_experiment_fixtures = _composed.materialize_experiment_fixtures

__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name not in {"_core", "_composed", "_disposable", "_name"}
)
