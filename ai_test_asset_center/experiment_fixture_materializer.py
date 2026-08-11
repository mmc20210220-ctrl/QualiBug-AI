"""Public fixture materializer facade with source-truthful fixture authority.

The core module owns fixture DAG/data binding. The composed wrapper proves the
frozen FlowDataRequirement and establishes compiled state preconditions before
measured business steps. This public boundary prevents convenience choices from
becoming fixture facts:

* a generic response ``id`` can satisfy only the same binding-target identity;
* source CHECK enum conflicts are never silently rewritten;
* disposable UNIQUE-key rewriting is table-scoped;
* dependency fixture creation requires one exact source GET/HEAD resolver path;
* automatic fixture creation requires one unique POST candidate and one unique
  actor authority; and
* ownership identity materialization must consume the compile-sealed owner actor.
  Legacy/unsealed ownership rows and any mismatch with the core's historical
  first-account iteration are blocked before fixture transport.
"""
from __future__ import annotations

import re
from copy import deepcopy
import hashlib
from typing import Any

from . import experiment_fixture_materializer_core as _core
from . import experiment_fixture_materializer_with_preconditions as _composed
from .actor_scoped_query_binding import resolve_actor_runtime_identity
from .cleanup_identity_authority import strict_observed_resource_identity
from .real_id_resolver import collection_path, normalize_path_placeholders
from .schema_unique_materialization_authority import (
    TableScopedUniqueFields as _TableScopedUniqueFields,
    declared_unique_fields_scoped as _declared_unique_fields_scoped,
    materialize_unique_create_fields_scoped as _materialize_unique_create_fields_scoped,
    resolve_unique_table as _resolve_unique_table,
)

for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)

_original_strict_fixture_preconditions = (
    _composed._strict_validate_fixture_preconditions
)
_original_source_backed_dependency_fixture_setup = (
    _core._source_backed_dependency_fixture_setup
)
_original_auto_fixture_create_for_binding_target = (
    _core._auto_fixture_create_for_binding_target
)
_original_composed_materialize_experiment_fixtures = (
    _composed.materialize_experiment_fixtures
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _authoritative_resolver_paths(
    resolver_operations: list[Any],
    operations: dict[str, dict[str, Any]],
) -> list[str]:
    """Return exact source GET/HEAD paths referenced by resolver receipts/plans."""

    paths: list[str] = []
    for raw in resolver_operations:
        resolver = _dict(raw)
        operation_ref = _text(
            resolver.get("operation_ref") or resolver.get("operation_id")
        )
        source = _dict(operations.get(operation_ref))
        if not operation_ref or not source:
            continue
        declared_method = _text(source.get("method")).upper()
        supplied_method = _text(resolver.get("method")).upper()
        declared_path = normalize_path_placeholders(
            _text(source.get("path") or source.get("raw_path"))
        )
        supplied_path = normalize_path_placeholders(_text(resolver.get("path")))
        if (
            declared_method not in {"GET", "HEAD"}
            or supplied_method and supplied_method != declared_method
            or not declared_path.startswith("/")
            or supplied_path != declared_path
        ):
            continue
        if declared_path not in paths:
            paths.append(declared_path)
    return paths


def _source_backed_dependency_fixture_setup(
    *,
    dependency_leaf: str,
    resolver_operations: list[Any],
    ops: dict[str, dict[str, Any]],
    actors: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any] | None,
    binding_plan: dict[str, Any],
    accepted_residue_allowed: bool = False,
) -> dict[str, Any]:
    """Dependency fixture setup requires one exact resolver operation/path."""

    paths = _authoritative_resolver_paths(resolver_operations, ops)
    if len(paths) != 1:
        return {}
    return _original_source_backed_dependency_fixture_setup(
        dependency_leaf=dependency_leaf,
        resolver_operations=[
            dict(row)
            for row in resolver_operations
            if isinstance(row, dict)
            and normalize_path_placeholders(_text(row.get("path"))) == paths[0]
        ],
        ops=ops,
        actors=actors,
        behavior_ir=behavior_ir,
        binding_plan=binding_plan,
        accepted_residue_allowed=accepted_residue_allowed,
    )


def _auto_create_candidates(
    binding: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> list[str]:
    """Collect every source POST candidate instead of accepting source order."""

    candidate_paths: set[str] = set()
    for resolver in _list(binding.get("resolver_operations")):
        row = _dict(resolver)
        operation_ref = _text(row.get("operation_ref") or row.get("operation_id"))
        source = _dict(operations.get(operation_ref))
        if not source or _text(source.get("method")).upper() not in {"GET", "HEAD"}:
            continue
        declared = normalize_path_placeholders(
            _text(source.get("path") or source.get("raw_path"))
        )
        supplied = normalize_path_placeholders(_text(row.get("path")))
        if supplied != declared or not declared.startswith("/"):
            continue
        candidate_paths.add(declared)
        parent = normalize_path_placeholders(collection_path(declared))
        if parent.startswith("/"):
            candidate_paths.add(parent)

    target_path = normalize_path_placeholders(_text(binding.get("target_path")))
    if target_path.startswith("/"):
        if any(
            normalize_path_placeholders(
                _text(op.get("path") or op.get("raw_path"))
            ) == target_path
            for op in operations.values()
            if isinstance(op, dict) and _text(op.get("id"))
        ):
            parent = normalize_path_placeholders(collection_path(target_path))
            if parent.startswith("/"):
                candidate_paths.add(parent)

    matches: list[str] = []
    for op_id, raw in operations.items():
        op = _dict(raw)
        if not op or _text(op.get("method")).upper() != "POST" or not _text(op_id):
            continue
        op_path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        )
        op_collection = normalize_path_placeholders(collection_path(op_path))
        matched = op_path in candidate_paths or op_collection in candidate_paths
        if not matched:
            for candidate in candidate_paths:
                base = normalize_path_placeholders(candidate).rstrip("/")
                if (
                    base
                    and op_collection.startswith(base + "/")
                    and "/" not in op_collection[len(base) + 1 :]
                ):
                    matched = True
                    break
        if matched and op_id not in matches:
            matches.append(_text(op_id))
    return matches


def _fixture_actor_authority(
    binding: dict[str, Any],
    actors: dict[str, dict[str, Any]] | None,
) -> str:
    explicit = {
        _text(binding.get(field))
        for field in (
            "fixture_owner_actor_ref",
            "owner_actor_ref",
            "resolver_actor_ref",
            "source_actor_ref",
        )
        if _text(binding.get(field))
    }
    if len(explicit) == 1:
        actor_ref = next(iter(explicit))
        if actor_ref in (actors or {}):
            return actor_ref
        return ""
    if explicit:
        return ""
    executable = [
        actor_ref
        for actor_ref, actor in (actors or {}).items()
        if isinstance(actor, dict)
        and _text(actor.get("role")).lower() not in {"anonymous", "public"}
    ]
    return executable[0] if len(executable) == 1 else ""


def _auto_fixture_create_for_binding_target(
    target: str,
    binding: dict[str, Any],
    operations: dict[str, dict[str, Any]],
    binding_plan: dict[str, Any],
    actors: dict[str, dict[str, Any]] | None = None,
    behavior_ir: dict[str, Any] | None = None,
    accepted_residue_allowed: bool = False,
) -> dict[str, Any] | None:
    """Auto-create only when create operation and fixture actor are unique."""

    candidates = _auto_create_candidates(binding, operations)
    if len(candidates) != 1:
        return None
    actor_ref = _fixture_actor_authority(binding, actors)
    if not actor_ref:
        return None
    governed_binding = {
        **_dict(binding),
        "fixture_owner_actor_ref": actor_ref,
    }
    result = _original_auto_fixture_create_for_binding_target(
        target,
        governed_binding,
        operations,
        binding_plan,
        actors=actors,
        behavior_ir=behavior_ir,
        accepted_residue_allowed=accepted_residue_allowed,
    )
    if not isinstance(result, dict):
        return None
    if _text(result.get("create_operation_ref")) != candidates[0]:
        return None
    setup = dict(_dict(result.get("fixture_setup")))
    setup["actor_refs"] = [actor_ref]
    result = dict(result)
    result["fixture_setup"] = setup
    result["fixture_actor_authority"] = "unique_actor"
    result["create_operation_authority"] = "unique_source_candidate"
    return result


def _binding_rows(exp: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _dict(exp).get("binding_plan")
    if isinstance(raw, dict):
        return [
            {**_dict(value), "target": _text(_dict(value).get("target") or key)}
            for key, value in raw.items()
            if isinstance(value, dict)
        ]
    return [dict(row) for row in _list(raw) if isinstance(row, dict)]


def _ownership_runtime_preflight(
    *,
    exp: dict[str, Any],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Prove the legacy core will consume the compile-sealed owner, not actor order."""

    governed_actors = deepcopy(actors)
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    ownership_bindings = [
        row
        for row in _binding_rows(exp)
        if _text(row.get("source_priority")) == "ownership_identity_param"
    ]
    for binding in ownership_bindings:
        target = _text(binding.get("target"))
        scope = _text(binding.get("ownership_binding_scope"))
        owner_actor_ref = _text(binding.get("owner_actor_ref"))
        base = {
            "target": target,
            "scope": scope,
            "owner_actor_ref": owner_actor_ref,
            "source_order_selection_allowed": False,
            "identity_value_persisted": False,
        }
        if scope != "shared_control_resource_owner" or not owner_actor_ref:
            issue = {
                **base,
                "status": "BLOCKED",
                "reason_code": "OWNERSHIP_RUNTIME_SCOPE_UNSEALED",
            }
            issues.append(issue)
            rows.append(issue)
            continue

        identity = resolve_actor_runtime_identity(
            owner_actor_ref,
            actors=governed_actors,
            tokens=tokens,
        )
        if _text(identity.get("status")) != "RESOLVED":
            issue = {
                **base,
                "status": "BLOCKED",
                "reason_code": _text(identity.get("reason_code"))
                or "OWNERSHIP_RUNTIME_OWNER_IDENTITY_UNRESOLVED",
            }
            issues.append(issue)
            rows.append(issue)
            continue

        # The historical mechanics consumes actor.account_id only. Enrich the
        # exact sealed owner on a call-local catalog so JWT/user_id authority can
        # be consumed without mutating Behavior IR or persisting the raw value.
        owner_copy = dict(_dict(governed_actors.get(owner_actor_ref)))
        owner_copy["account_id"] = _text(identity.get("identity_value"))
        governed_actors[owner_actor_ref] = owner_copy

        first_core_actor = ""
        for planned in [
            *_list(exp.get("control_plan")),
            *_list(exp.get("treatment_plan")),
        ]:
            if not isinstance(planned, dict):
                continue
            actor_ref = _text(planned.get("actor_ref"))
            actor = _dict(governed_actors.get(actor_ref))
            if _text(actor.get("account_id")):
                first_core_actor = actor_ref
                break
        if first_core_actor != owner_actor_ref:
            issue = {
                **base,
                "status": "BLOCKED",
                "reason_code": "OWNERSHIP_CORE_SELECTION_NOT_SEALED_OWNER",
                "core_first_actor_ref": first_core_actor,
            }
            issues.append(issue)
            rows.append(issue)
            continue

        rows.append(
            {
                **base,
                "status": "READY",
                "reason_code": "",
                "identity_authority": _text(identity.get("authority")),
                "identity_fingerprint": _text(
                    identity.get("identity_fingerprint")
                )
                or hashlib.sha256(
                    _text(identity.get("identity_value")).encode("utf-8")
                ).hexdigest()[:16],
                "core_consumption_aligned": True,
            }
        )

    return governed_actors, {
        "schema_version": "qualibug.ownership-runtime-materialization.v1",
        "status": "BLOCKED" if issues else "READY",
        "binding_count": len(ownership_bindings),
        "rows": rows,
        "issues": issues,
        "source_order_selection_allowed": False,
        "identity_value_persisted": False,
    }


def _ownership_terminal(kwargs: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    issues = _list(receipt.get("issues"))
    detail = ";".join(
        f"{_text(_dict(row).get('target'))}:{_text(_dict(row).get('reason_code'))}"
        for row in issues[:8]
    )
    reason = "BLOCKED_MISSING_BINDING"
    return {
        "status": "terminal",
        "result": {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": _text(kwargs.get("eid")),
            "obligation_id": _text(kwargs.get("oid")),
            "status": "BLOCKED",
            "reason_code": reason,
            "detail": "ownership_runtime_materialization:" + detail,
            "steps": [],
            "fixture_receipts": [],
            "binding_materialization_receipts": [],
            "finding": None,
            "cleanup_failures": 0,
            "ownership_runtime_materialization_receipt": receipt,
            "execution_receipt": {
                "status": "BLOCKED",
                "reason_code": reason,
                "detail": "ownership_runtime_materialization_not_authoritative",
                "cleanup_failures": 0,
            },
        },
    }


# Replace exact core authorities. The core materializer resolves these globals at
# call time, so neither synthetic dependency paths nor first-candidate fixture
# choices can bypass the public facade.
_core._align_body_enums_with_declared_schema = _preserve_source_enum_conflicts
_core._declared_unique_fields = _declared_unique_fields_scoped
_core._materialize_unique_create_fields = _materialize_unique_create_fields_scoped
_core._source_backed_dependency_fixture_setup = _source_backed_dependency_fixture_setup
_core._auto_fixture_create_for_binding_target = _auto_fixture_create_for_binding_target

_composed._strict_validate_fixture_preconditions = (
    _strict_validate_fixture_preconditions
)


def materialize_experiment_fixtures(**kwargs: Any) -> dict[str, Any]:
    governed_actors, ownership_receipt = _ownership_runtime_preflight(
        exp=_dict(kwargs.get("exp")),
        actors=_dict(kwargs.get("actors")),
        tokens=_dict(kwargs.get("tokens")),
    )
    if _text(ownership_receipt.get("status")) == "BLOCKED":
        return _ownership_terminal(kwargs, ownership_receipt)
    state = _dict(
        _original_composed_materialize_experiment_fixtures(
            **{**kwargs, "actors": governed_actors}
        )
    )
    state["ownership_runtime_materialization_receipt"] = ownership_receipt
    terminal_result = _dict(state.get("result"))
    if terminal_result:
        terminal_result["ownership_runtime_materialization_receipt"] = (
            ownership_receipt
        )
        state["result"] = terminal_result
    return state


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_composed", "_name"}
)
