"""Public fixture materializer facade with source-truthful fixture authority.

The core module owns fixture DAG/data binding. The composed wrapper proves the
frozen FlowDataRequirement and establishes compiled state preconditions before
measured business steps. This public boundary adds three truth constraints:

* a generic response ``id`` can satisfy only the same binding-target identity,
  never an arbitrary different identity-shaped field;
* a documented fixture value that conflicts with a schema CHECK enum is never
  silently replaced with the first legal value; and
* disposable UNIQUE-key rewriting uses the shared table-scoped DDL authority,
  so a UNIQUE declaration on one table can never mutate another table's field.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from . import experiment_fixture_materializer_core as _core
from . import experiment_fixture_materializer_with_preconditions as _composed
from .cleanup_identity_authority import strict_observed_resource_identity
from .schema_unique_materialization_authority import (
    TableScopedUniqueFields as _TableScopedUniqueFields,
    declared_unique_fields_scoped as _declared_unique_fields_scoped,
    materialize_unique_create_fields_scoped as _materialize_unique_create_fields_scoped,
    resolve_unique_table as _resolve_unique_table,
)

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

_original_strict_fixture_preconditions = (
    _composed._strict_validate_fixture_preconditions
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
# alignment or globally flattened UNIQUE rewriting.
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
    if not name.startswith("__") and name not in {"_core", "_composed", "_name"}
)
