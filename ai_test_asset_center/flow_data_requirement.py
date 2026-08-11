"""Flow-data requirement facade with execution-contract authority.

The historical dependency freeze mechanics live in
``_flow_data_requirement_mechanics``. A target name is not a produced value and
is also not proof that a binding can be materialized. ``blocked`` bindings,
empty fingerprint-only rows, and name-only outputs must never become initial
flow facts.

A requirement is FROZEN only when:
* every HTTP step references one source operation with a non-drifting method;
* the existing flow execution contract proves producer/output/consumer identity;
* every target the execution contract treats as an initial binding has an
  executable materialization channel in the current binding/fixture runtime.
"""
from __future__ import annotations

from typing import Any

from . import _flow_data_requirement_mechanics as _core
from ._flow_data_requirement_mechanics import *  # noqa: F401,F403
from .binding_target_materialization_authority import (
    resolve_binding_target_materialization,
)
from .flow_data_execution_contract import (
    STATUS_FROZEN as EXECUTION_STATUS_FROZEN,
    freeze_flow_data_execution_contract,
)

_original_build_flow_data_requirement = _core.build_flow_data_requirement


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _flow_operation_issues(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, str]]:
    """Validate step operation identity before data availability is frozen."""

    operations = {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }
    issues: list[dict[str, str]] = []
    for phase in ("precondition", "control", "treatment"):
        for raw in _list(_dict(experiment).get(f"{phase}_plan")):
            step = _dict(raw)
            if not step:
                continue
            if _text(step.get("protocol_step")) == "ui_open":
                continue
            step_id = _text(step.get("step_id") or step.get("id"))
            operation_ref = _text(step.get("operation_ref"))
            operation = _dict(operations.get(operation_ref))
            if not operation_ref or not operation:
                issues.append(
                    {
                        "kind": "FLOW_STEP_OPERATION_UNRESOLVED",
                        "phase": phase,
                        "step_id": step_id,
                        "operation_ref": operation_ref,
                    }
                )
                continue
            declared_method = _text(operation.get("method")).upper()
            step_method = _text(step.get("method")).upper()
            if not declared_method:
                issues.append(
                    {
                        "kind": "FLOW_STEP_METHOD_UNDECLARED",
                        "phase": phase,
                        "step_id": step_id,
                        "operation_ref": operation_ref,
                    }
                )
            elif step_method and step_method != declared_method:
                issues.append(
                    {
                        "kind": "FLOW_STEP_METHOD_DRIFT",
                        "phase": phase,
                        "step_id": step_id,
                        "operation_ref": operation_ref,
                        "step_method": step_method,
                        "source_method": declared_method,
                    }
                )
    return issues


def _initial_binding_targets(
    execution_contract: dict[str, Any],
) -> list[str]:
    targets: list[str] = []
    for raw in _list(_dict(execution_contract).get("step_contracts")):
        for value in _list(_dict(raw).get("initial_binding_targets")):
            target = _text(value)
            if target and target not in targets:
                targets.append(target)
    return targets


def _binding_materialization_issues(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    execution_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for target in _initial_binding_targets(execution_contract):
        receipt = resolve_binding_target_materialization(
            target,
            experiment=experiment,
            behavior_ir=behavior_ir,
            flow_execution_contract=execution_contract,
        )
        if _text(receipt.get("status")) == "RESOLVED":
            continue
        issues.append(receipt)
    return issues


def build_flow_data_requirement(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Freeze data only after current executors can prove every dependency."""

    issues = _flow_operation_issues(experiment, behavior_ir)
    if issues:
        return {
            "schema_version": _core.SCHEMA_VERSION,
            "status": _core.STATUS_BLOCKED,
            "reason_code": _core.BLOCKED_FLOW_DATA_BINDING_INCOMPLETE,
            "detail": ";".join(
                f"{row['phase']}:{row['step_id']}:{row['kind']}"
                for row in issues[:12]
            ),
            "operation_contract_issues": issues,
        }

    requirement = _original_build_flow_data_requirement(
        experiment,
        behavior_ir=behavior_ir,
    )
    if _text(requirement.get("status")) != _core.STATUS_FROZEN:
        return requirement

    execution_contract = freeze_flow_data_execution_contract(
        experiment,
        requirement,
    )
    if _text(execution_contract.get("status")) != EXECUTION_STATUS_FROZEN:
        return {
            "schema_version": _core.SCHEMA_VERSION,
            "status": _core.STATUS_BLOCKED,
            "reason_code": _core.BLOCKED_FLOW_DATA_BINDING_INCOMPLETE,
            "detail": (
                "execution_contract:"
                + _text(execution_contract.get("reason_code") or "incomplete")
                + ":"
                + _text(execution_contract.get("detail"))
            )[:1000],
            "candidate_requirement_id": _text(requirement.get("requirement_id")),
            "candidate_requirement_fingerprint": _text(
                requirement.get("requirement_fingerprint")
            ),
            "flow_data_execution_contract": execution_contract,
        }

    materialization_issues = _binding_materialization_issues(
        experiment,
        behavior_ir,
        execution_contract,
    )
    if materialization_issues:
        return {
            "schema_version": _core.SCHEMA_VERSION,
            "status": _core.STATUS_BLOCKED,
            "reason_code": _core.BLOCKED_FLOW_DATA_BINDING_INCOMPLETE,
            "detail": "binding_materialization:"
            + ";".join(
                f"{_text(row.get('target'))}:{_text(row.get('reason_code'))}"
                for row in materialization_issues[:12]
            ),
            "candidate_requirement_id": _text(requirement.get("requirement_id")),
            "candidate_requirement_fingerprint": _text(
                requirement.get("requirement_fingerprint")
            ),
            "binding_materialization_issues": materialization_issues,
            "flow_data_execution_contract": execution_contract,
        }

    # Preserve the existing content-addressed requirement exactly. The
    # execution/materialization contracts gate it rather than mutating its
    # fingerprinted payload after the fact.
    return requirement


_core.build_flow_data_requirement = build_flow_data_requirement

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "build_flow_data_requirement",
        "_flow_operation_issues",
        "_binding_materialization_issues",
    }
)
