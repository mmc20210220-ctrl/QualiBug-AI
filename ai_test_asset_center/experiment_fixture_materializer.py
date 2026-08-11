"""Public fixture materializer facade with target-scoped identity authority.

The core module owns fixture DAG/data binding. The composed wrapper proves the
frozen FlowDataRequirement and establishes compiled state preconditions before
measured business steps. This public boundary additionally prevents a generic
response ``id`` from satisfying an arbitrary different identity-shaped field:
``order_id`` may use the created order's generic ``id`` when it is the binding
target, but ``addressId``/``userRef`` cannot borrow that same value.
"""
from __future__ import annotations

import re
from typing import Any

from . import experiment_fixture_materializer_core as _core
from . import experiment_fixture_materializer_with_preconditions as _composed
from .cleanup_identity_authority import strict_observed_resource_identity

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

        # Only the binding target itself may bridge a generic API primary-key
        # spelling (id/uuid/guid/key). A differently named identity field is a
        # different business identity until the source/binding graph proves a
        # mapping; name shape alone is never that proof.
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


# The composed materializer installs its own validator into core immediately
# before execution. Replace that composition point here so public execution uses
# the target-scoped authority rather than the historical any-id alias.
_composed._strict_validate_fixture_preconditions = (
    _strict_validate_fixture_preconditions
)
materialize_experiment_fixtures = _composed.materialize_experiment_fixtures

__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_composed", "_name"}
)
