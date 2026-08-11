"""Public cleanup executor facade.

The core module retains the existing compensation implementation. The lifecycle
adapter adds precondition and process-graph visibility without changing public
call sites. This facade also preserves the compiled cleanup subject identity on
the runtime path: an adapter cleanup may be activated only by the exact write it
compensates and by a concrete identity observed from that write. The projection
is call-local and is removed before execution evidence leaves this facade.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import experiment_cleanup_executor_core as _core
from . import experiment_cleanup_lifecycle_adapter as _adapter
from .experiment_cleanup import _single_entity_for_restoration


_ADAPTER_BINDING_SCHEMA = "qualibug.declared-adapter-cleanup-runtime-binding.v1"
_ADAPTER_BINDING_MARKER = "_declared_adapter_cleanup_requirement"
_RUNTIME_IDENTITY_CONFLICTS = "_runtime_step_identity_conflicts"
_ORIGINAL_ATTEMPTS_ATTR = "_qualibug_original_governed_write_attempts"
_ORIGINAL_CHANGED_STATE_ATTR = "_qualibug_original_governed_write_changed_state"
_ORIGINAL_ADAPTER_IDENTITY_ATTR = "_qualibug_original_adapter_cleanup_identity"
_CLEANUP_PRE_HOOKS: dict[str, Any] = {}

if not hasattr(_core, _ORIGINAL_ATTEMPTS_ATTR):
    setattr(_core, _ORIGINAL_ATTEMPTS_ATTR, _core._governed_write_attempts)
if not hasattr(_core, _ORIGINAL_CHANGED_STATE_ATTR):
    setattr(
        _core,
        _ORIGINAL_CHANGED_STATE_ATTR,
        _core._governed_write_changed_state,
    )
if not hasattr(_core, _ORIGINAL_ADAPTER_IDENTITY_ATTR):
    setattr(
        _core,
        _ORIGINAL_ADAPTER_IDENTITY_ATTR,
        _core._adapter_cleanup_identity,
    )

_ORIGINAL_GOVERNED_WRITE_ATTEMPTS = getattr(_core, _ORIGINAL_ATTEMPTS_ATTR)
_ORIGINAL_GOVERNED_WRITE_CHANGED_STATE = getattr(
    _core,
    _ORIGINAL_CHANGED_STATE_ATTR,
)
_ORIGINAL_ADAPTER_CLEANUP_IDENTITY = getattr(
    _core,
    _ORIGINAL_ADAPTER_IDENTITY_ATTR,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def register_cleanup_pre_hook(name: str, hook: Any) -> None:
    """Register one named hook before governed cleanup executes.

    The registry preserves the public cleanup function identity while allowing
    source-backed observers to compose at the existing cleanup seam. Invalid
    registrations fail fast; hooks may mutate the call-local observations or
    return explicit context updates.
    """

    key = _text(name)
    if not key:
        raise ValueError("cleanup_pre_hook_name_missing")
    if hook is None:
        _CLEANUP_PRE_HOOKS.pop(key, None)
        return
    if not callable(hook):
        raise TypeError(f"cleanup_pre_hook_not_callable:{key}")
    _CLEANUP_PRE_HOOKS[key] = hook


def cleanup_pre_hook_names() -> list[str]:
    """Return installed cleanup hook names in deterministic order."""

    return sorted(_CLEANUP_PRE_HOOKS)


def _run_cleanup_pre_hooks(context: dict[str, Any]) -> dict[str, Any]:
    """Run registered hooks against one cleanup invocation context."""

    call_context = dict(context)
    for name, hook in tuple(_CLEANUP_PRE_HOOKS.items()):
        update = hook(call_context)
        if update is None:
            continue
        if not isinstance(update, dict):
            raise TypeError(f"cleanup_pre_hook_result_invalid:{name}")
        call_context.update(update)
    return call_context


def _stable_fingerprint(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _status_code(value: Any) -> int:
    row = _dict(value)
    try:
        return int(row.get("status") or row.get("status_code") or 0)
    except (TypeError, ValueError):
        return 0


def _identity_from_governed_write(
    cleanup: dict[str, Any],
    governed: dict[str, Any],
    *,
    step_body: Any = None,
) -> str:
    """Resolve only the cleanup contract's declared identity from one write."""
    from .cleanup_adapter_ladder import (
        identity_value_from_body,
        observed_resource_identity,
    )

    identity_column = _text(cleanup.get("identity_column")) or "id"
    tracked = _text(_dict(governed).get("observed_created_identity"))
    if tracked:
        return tracked
    for raw in (
        _dict(governed.get("response_bound_after")).get("body"),
        _dict(governed.get("write")).get("body"),
        _dict(governed.get("after")).get("body"),
        _dict(governed.get("before")).get("body"),
        step_body,
    ):
        if not isinstance(raw, dict):
            continue
        identity = identity_value_from_body(raw, identity_column)
        if identity:
            return identity
        # Generic observed-identity fallback for the same accepted write: the
        # created row resolves from any conventional resource identity key the
        # response carried, even when it differs from the declared column.
        identity = observed_resource_identity(raw, identity_column=identity_column)
        if identity:
            return identity
    return ""


def _adapter_cleanup_contracts(
    experiment: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    by_operation: dict[str, list[dict[str, Any]]] = {}
    for raw in _list(_dict(experiment).get("cleanup_plan")):
        cleanup = _dict(raw)
        if _text(cleanup.get("adapter")) != "db_sql":
            continue
        operation_ref = _text(cleanup.get("compensates_operation_ref"))
        if not operation_ref:
            continue
        by_operation.setdefault(operation_ref, []).append(dict(cleanup))
    return by_operation


def _contracts_for_runtime_step(
    contracts_by_operation: dict[str, list[dict[str, Any]]],
    *,
    operation_ref: str,
    step_id: str,
) -> list[dict[str, Any]]:
    """Select cleanup contracts that compensate this exact runtime write step.

    Multi-write expansion stamps ``source_step_id`` on each cleanup template
    copy. Matching must prefer that stamp; otherwise control+treatment writes
    that share one operation_ref look like an ambiguous contract set and every
    adapter cleanup stays unbound with an empty identity.
    """
    matching = [
        dict(row)
        for row in _list(contracts_by_operation.get(operation_ref))
        if isinstance(row, dict)
    ]
    if not matching:
        return []
    if step_id:
        step_scoped = [
            row
            for row in matching
            if _text(row.get("source_step_id")) == step_id
        ]
        if step_scoped:
            return step_scoped
    # Templates without source_step_id remain operation-scoped (single-write).
    unscoped = [row for row in matching if not _text(row.get("source_step_id"))]
    return unscoped


def _project_adapter_cleanup_requirements(
    *,
    experiment: dict[str, Any],
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bind one compiled db cleanup contract to one observed write step.

    The marker is an internal execution projection, not a receipt mutation. It is
    attached to a shallow step copy and stripped before the result leaves the
    facade. Missing or ambiguous contracts and missing runtime identities remain
    visible in the audit and never activate cleanup.
    """
    contracts = _adapter_cleanup_contracts(experiment)
    projected: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    observed_operation_refs: set[str] = set()
    measured_phases = {"control", "treatment", "precondition"}

    for raw in steps:
        if not isinstance(raw, dict):
            continue
        step = dict(raw)
        phase = _text(step.get("phase"))
        operation_ref = _text(step.get("operation_ref"))
        step_id = _text(step.get("step_id"))
        if phase in measured_phases and operation_ref:
            observed_operation_refs.add(operation_ref)
        matching = _contracts_for_runtime_step(
            contracts,
            operation_ref=operation_ref,
            step_id=step_id,
        )
        audit_row: dict[str, Any] = {
            "step_id": step_id,
            "phase": phase,
            "operation_ref": operation_ref,
            "cleanup_contract_count": len(matching),
            "status": "NOT_APPLICABLE",
            "reason_code": "",
        }
        if phase not in measured_phases or not matching:
            projected.append(step)
            continue
        if len(matching) != 1:
            audit_row.update(
                {
                    "status": "UNBOUND",
                    "reason_code": "ADAPTER_CLEANUP_CONTRACT_NOT_UNIQUE",
                }
            )
            rows.append(audit_row)
            projected.append(step)
            continue

        cleanup = matching[0]
        governed = _dict(step.get("governance_receipt"))
        cleanup_mode = _text(cleanup.get("mode"))
        method = _text(step.get("method") or governed.get("method")).upper()
        contract_valid = bool(
            _text(cleanup.get("adapter")) == "db_sql"
            and cleanup.get("requires_ownership_proof") is True
            and _text(cleanup.get("scope")) == "run_created_only"
            and cleanup_mode in {"row_delete", "adapter_row_delete"}
            and _text(cleanup.get("table"))
            and _text(cleanup.get("identity_column"))
        )
        if not contract_valid:
            audit_row.update(
                {
                    "status": "UNBOUND",
                    "reason_code": "ADAPTER_CLEANUP_CONTRACT_INVALID",
                }
            )
            rows.append(audit_row)
            projected.append(step)
            continue
        if governed.get("accepted") is not True:
            audit_row.update(
                {
                    "status": "UNBOUND",
                    "reason_code": "ADAPTER_CLEANUP_WRITE_NOT_ACCEPTED",
                }
            )
            rows.append(audit_row)
            projected.append(step)
            continue
        if method != "POST" or not 200 <= _status_code(governed.get("write")) < 300:
            audit_row.update(
                {
                    "status": "UNBOUND",
                    "reason_code": "ADAPTER_CLEANUP_CREATE_NOT_PROVEN",
                }
            )
            rows.append(audit_row)
            projected.append(step)
            continue

        identity = _identity_from_governed_write(
            cleanup,
            governed,
            step_body=step.get("body"),
        )
        if not identity:
            audit_row.update(
                {
                    "status": "UNBOUND",
                    "reason_code": "ADAPTER_CLEANUP_IDENTITY_NOT_OBSERVED",
                }
            )
            rows.append(audit_row)
            projected.append(step)
            continue

        marker = {
            "schema_version": _ADAPTER_BINDING_SCHEMA,
            "cleanup_required": True,
            "step_id": _text(step.get("step_id")),
            "operation_ref": operation_ref,
            "adapter": "db_sql",
            "cleanup_mode": cleanup_mode,
            "table": _text(cleanup.get("table")),
            "identity_column": _text(cleanup.get("identity_column")),
            "identity_fingerprint": _stable_fingerprint(identity),
            "scope": "run_created_only",
            "ownership_proof_required": True,
            "binding_basis": "compiled_cleanup_contract_and_write_response_identity",
        }
        step[_ADAPTER_BINDING_MARKER] = marker
        audit_row.update(
            {
                "status": "BOUND",
                "reason_code": "",
                "identity_column": marker["identity_column"],
                "identity_fingerprint": marker["identity_fingerprint"],
            }
        )
        rows.append(audit_row)
        projected.append(step)

    missing_runtime_operation_refs = sorted(
        set(contracts) - observed_operation_refs
    )
    for operation_ref in missing_runtime_operation_refs:
        rows.append(
            {
                "step_id": "",
                "phase": "",
                "operation_ref": operation_ref,
                "cleanup_contract_count": len(contracts[operation_ref]),
                "status": "UNBOUND",
                "reason_code": "ADAPTER_CLEANUP_RUNTIME_STEP_MISSING",
            }
        )

    bound = [row for row in rows if row.get("status") == "BOUND"]
    unbound = [row for row in rows if row.get("status") == "UNBOUND"]
    required = bool(contracts)
    return projected, {
        "schema_version": _ADAPTER_BINDING_SCHEMA,
        "required": required,
        "declared_operation_refs": sorted(contracts),
        "declared_operation_count": len(contracts),
        "observed_operation_refs": sorted(observed_operation_refs),
        "missing_runtime_operation_refs": missing_runtime_operation_refs,
        "bound": bound,
        "unbound": unbound,
        "bound_step_ids": [
            _text(row.get("step_id")) for row in bound if _text(row.get("step_id"))
        ],
        "bound_count": len(bound),
        "unbound_count": len(unbound),
        "complete": (not required) or (not unbound and bool(bound)),
        "runtime_marker_persisted": False,
        "cross_operation_identity_fallback_forbidden": True,
    }


def _governed_write_attempts_with_step_identity(
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep immutable runtime step identity beside the governance payload."""
    attempts: list[dict[str, Any]] = []
    for raw in steps:
        step = _dict(raw)
        if _text(step.get("phase")) not in {"control", "treatment"}:
            continue
        governed = step.get("governance_receipt")
        if not isinstance(governed, dict):
            continue
        attempt = dict(governed)
        conflicts: list[str] = []
        for field in (
            "step_id",
            "phase",
            "operation_ref",
            "actor_ref",
            "method",
            "path",
            "observation_path",
        ):
            value = step.get(field)
            if value in (None, ""):
                continue
            existing = attempt.get(field)
            if existing not in (None, "") and _text(existing) != _text(value):
                conflicts.append(field)
            # The runtime step is the execution identity authority. Work on a
            # copy so the content-addressed governance receipt is never mutated.
            attempt[field] = value
        if conflicts:
            attempt[_RUNTIME_IDENTITY_CONFLICTS] = sorted(set(conflicts))
        marker = step.get(_ADAPTER_BINDING_MARKER)
        if isinstance(marker, dict):
            attempt[_ADAPTER_BINDING_MARKER] = dict(marker)
        attempts.append(attempt)
    return attempts


def _governed_write_changed_state_with_adapter_requirement(
    attempt: dict[str, Any],
) -> bool:
    """Treat accepted creates with observed identity as cleanup-required.

    Collection snapshots can stay unchanged for the writing actor when a broken
    cross-owner write creates the row in another principal's scope. A successful
    POST response carrying one concrete resource identity is direct write-effect
    evidence and must not be downgraded to state-unchanged before cleanup.
    """
    if _ORIGINAL_GOVERNED_WRITE_CHANGED_STATE(attempt):
        return True
    row = _dict(attempt)
    write = _dict(row.get("write"))
    response_identities = _core._primary_resource_identity_candidates(
        write.get("body")
    )
    if (
        row.get("accepted") is True
        and _text(row.get("method")).upper() == "POST"
        and 200 <= _status_code(write) < 300
        and len(response_identities) == 1
        and not _list(row.get(_RUNTIME_IDENTITY_CONFLICTS))
    ):
        # Only a CREATE-shaped POST may prove write effect through the
        # response identity: the carried id must be absent from the pre-write
        # observation. Action-style POSTs (POST /{id}/status, /{id}/amount)
        # echo the pre-existing resource's own id, which was already visible
        # before the write — that identity is not evidence of a state change,
        # so the write falls back to business-state comparison and may be
        # declared state-unchanged (no cleanup required).
        if not _single_entity_for_restoration(
            _dict(row.get("before")).get("body"),
            response_identities,
        ):
            return True
    marker = _dict(row.get(_ADAPTER_BINDING_MARKER))
    return bool(
        marker.get("schema_version") == _ADAPTER_BINDING_SCHEMA
        and marker.get("cleanup_required") is True
        and row.get("accepted") is True
        and not _list(row.get(_RUNTIME_IDENTITY_CONFLICTS))
        and _text(marker.get("operation_ref"))
        and _text(marker.get("operation_ref")) == _text(row.get("operation_ref"))
        and _text(marker.get("step_id")) == _text(row.get("step_id"))
        and _text(marker.get("adapter")) == "db_sql"
        and _text(marker.get("cleanup_mode"))
        in {"row_delete", "adapter_row_delete"}
        and _text(marker.get("identity_fingerprint"))
        and _text(marker.get("scope")) == "run_created_only"
        and marker.get("ownership_proof_required") is True
    )


def _adapter_cleanup_identity_exact(
    cleanup: dict[str, Any],
    *,
    runtime_bindings: dict[str, Any],
    steps_out: list[dict[str, Any]],
) -> str:
    """Resolve adapter identity only from the write step being compensated.

    When compile expands one cleanup template across control+treatment, each
    copy carries ``source_step_id``. That stamp is the authority for which
    accepted write supplies the row identity. Without it, two distinct created
    identities for the same operation_ref correctly remain unbound.
    """
    cleanup_row = _dict(cleanup)
    operation_ref = _text(cleanup_row.get("compensates_operation_ref"))
    source_step_id = _text(cleanup_row.get("source_step_id"))
    if not operation_ref and not source_step_id:
        return _ORIGINAL_ADAPTER_CLEANUP_IDENTITY(
            cleanup,
            runtime_bindings=runtime_bindings,
            steps_out=steps_out,
        )

    identities: list[str] = []
    for raw in _list(steps_out):
        step = _dict(raw)
        if _text(step.get("phase")) not in {"control", "treatment"}:
            continue
        if source_step_id and _text(step.get("step_id")) != source_step_id:
            continue
        if operation_ref and _text(step.get("operation_ref")) != operation_ref:
            continue
        governed = _dict(step.get("governance_receipt"))
        if governed.get("accepted") is not True:
            continue
        identity = _identity_from_governed_write(
            cleanup_row,
            governed,
            step_body=step.get("body"),
        )
        if identity and identity not in identities:
            identities.append(identity)
    if len(identities) == 1:
        return identities[0]
    return ""


for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)


def _sync_core_hooks() -> None:
    """Keep established injection points and exact cleanup identity authoritative."""
    for name in (
        "execute_governed_control_write",
        "sandbox_write_allowed",
        "_http_request",
    ):
        if name in globals() and hasattr(_core, name):
            setattr(_core, name, globals()[name])
    _core._governed_write_attempts = _governed_write_attempts_with_step_identity
    _core._governed_write_changed_state = (
        _governed_write_changed_state_with_adapter_requirement
    )
    _core._adapter_cleanup_identity = _adapter_cleanup_identity_exact


def _strip_runtime_markers(steps: Any) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for raw in _list(steps):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row.pop(_ADAPTER_BINDING_MARKER, None)
        cleaned.append(row)
    return cleaned


def execute_experiment_cleanup_compensation(**kwargs: Any) -> dict[str, Any]:
    call_context = _run_cleanup_pre_hooks(kwargs)
    _sync_core_hooks()
    projected_steps, binding_audit = _project_adapter_cleanup_requirements(
        experiment=_dict(call_context.get("exp")),
        steps=[
            row
            for row in _list(call_context.get("steps_out"))
            if isinstance(row, dict)
        ],
    )
    result = _dict(
        _adapter.execute_experiment_cleanup_compensation(
            **{**call_context, "steps_out": projected_steps}
        )
    )
    returned_steps = result.get("steps_out") or projected_steps
    result["steps_out"] = _strip_runtime_markers(returned_steps)
    observations = _dict(
        result.get("observations") or call_context.get("observations")
    )
    observations["declared_adapter_cleanup_runtime_binding"] = binding_audit
    result["observations"] = observations
    return result


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name
    not in {
        "_core",
        "_adapter",
        "_name",
        "_ORIGINAL_GOVERNED_WRITE_ATTEMPTS",
        "_ORIGINAL_GOVERNED_WRITE_CHANGED_STATE",
        "_ORIGINAL_ADAPTER_CLEANUP_IDENTITY",
    }
)
