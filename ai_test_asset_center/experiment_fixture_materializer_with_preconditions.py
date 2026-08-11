"""Fixture materialization followed by frozen data proof and preconditions.

The core fixture materializer remains the sole data/setup executor. This wrapper
projects the frozen FlowDataRequirement into the core's supported node schema,
proves the result, then executes compiled preconditions before measured business
steps. Any block preserves cleanup context and clears measurement plans.

Two truth boundaries are enforced before any measured step:
* activation reconciliation is diagnostic only; a fixture the DAG never
  executed cannot be rewritten into ``resolved`` evidence;
* fixture preconditions are real gates. Missing structured response evidence or
  an explicit precondition validation failure cannot coexist with a synthetic
  ``resolved`` fixture receipt.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import experiment_fixture_materializer_core as _materializer_core
from .experiment_fixture_materializer_core import (
    materialize_experiment_fixtures as _materialize_experiment_fixtures,
)
from .experiment_precondition_executor import execute_precondition_plan
from .flow_data_materialization import (
    STATUS_VALID as FLOW_DATA_VALID,
    validate_flow_data_materialization,
)
from .flow_data_materializer_projection import (
    project_flow_data_materializer_dag,
)

_original_validate_fixture_preconditions = (
    _materializer_core._validate_fixture_preconditions
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _declared_fixture_precondition_fields(exp: dict[str, Any], target: str) -> list[str]:
    fields: set[str] = set()
    for raw in _list(_dict(exp).get("assertions")):
        assertion = _dict(raw)
        for term in _list(assertion.get("terms")):
            field = _text(_dict(term).get("field"))
            if field:
                fields.add(field)
        for key in ("from_field", "to_field", "state_field", "field"):
            field = _text(assertion.get(key))
            if field:
                fields.add(field)
    token = "{" + _text(target) + "}"
    for raw in _list(_dict(exp).get("treatment_plan")):
        body = _dict(_dict(raw).get("body"))
        for key, value in body.items():
            if isinstance(value, str) and token in value:
                fields.add(_text(key))
    return sorted(field for field in fields if field)


def _strict_validate_fixture_preconditions(
    exp: dict[str, Any],
    fixture_response_body: Any,
    target: str,
) -> list[dict[str, str]]:
    """Missing structured response evidence is unknown, never a passed fixture."""

    required = _declared_fixture_precondition_fields(exp, target)
    if required and not isinstance(fixture_response_body, dict):
        return [
            {
                "field": field,
                "reason": "fixture_precondition_response_unstructured",
                "target": _text(target),
            }
            for field in required
        ]
    return _original_validate_fixture_preconditions(
        exp,
        fixture_response_body,
        target,
    )


def _block_measurement(
    *,
    exp: dict[str, Any],
    state: dict[str, Any],
    reason_code: str,
    detail: str,
    phase: str,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Preserve cleanup state while preventing all business measurement."""
    exp["blocked_measured_plans"] = {
        "precondition_plan": deepcopy(_list(exp.get("precondition_plan"))),
        "control_plan": deepcopy(_list(exp.get("control_plan"))),
        "treatment_plan": deepcopy(_list(exp.get("treatment_plan"))),
        "reason_code": reason_code,
        "phase": phase,
    }
    exp["precondition_plan"] = []
    exp["control_plan"] = []
    exp["treatment_plan"] = []
    exp[f"execution_{phase}_blocked"] = deepcopy(receipt)
    state[f"{phase}_blocked"] = True
    state[f"{phase}_reason_code"] = reason_code
    state[f"{phase}_detail"] = detail
    # Remain on the normal executor path only so existing cleanup and Finalizer
    # receive the fixture/precondition write receipts already produced.
    state["status"] = "ready"
    return state


def _remove_fake_fixture_contract_evidence(
    state: dict[str, Any], affected_ids: set[str]
) -> None:
    state["contract_evidence_receipts"] = [
        row
        for row in _list(state.get("contract_evidence_receipts"))
        if not (
            isinstance(row, dict)
            and _text(row.get("kind")) == "fixture"
            and _text(row.get("subject_id")) in affected_ids
        )
    ]


def _reject_synthetic_activation_reconciliation(
    *,
    exp: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Turn non-executed fixture reconciliation into visible DAG drift."""

    fixture_receipts = [
        dict(row) if isinstance(row, dict) else row
        for row in _list(state.get("fixture_receipts"))
    ]
    affected_ids: list[str] = []
    diagnostic_rows: list[dict[str, Any]] = []
    for row in fixture_receipts:
        if not isinstance(row, dict):
            continue
        if _text(row.get("source")) != "activation_requirement_reconciliation":
            continue
        fixture_id = _text(row.get("node_id"))
        if fixture_id:
            affected_ids.append(fixture_id)
        stale = _text(row.get("kind")) == "stale_requirement"
        row.update({
            "status": "BLOCKED",
            "reason_code": "BLOCKED_FIXTURE_DAG_DRIFT",
            "detail": (
                "activation_required_fixture_missing_from_dag"
                if stale
                else "activation_required_fixture_not_executed"
            ),
            "reconciliation_is_evidence": False,
        })
        diagnostic_rows.append(dict(row))

    if not diagnostic_rows:
        return None

    state["fixture_receipts"] = fixture_receipts
    affected = set(affected_ids)
    _remove_fake_fixture_contract_evidence(state, affected)
    receipt = {
        "schema_version": "qualibug.fixture-activation-reconciliation-gate.v1",
        "status": "BLOCKED",
        "reason_code": "BLOCKED_FIXTURE_DAG_DRIFT",
        "affected_fixture_ids": sorted(affected),
        "diagnostics": diagnostic_rows,
        "synthetic_resolution_allowed": False,
    }
    return _block_measurement(
        exp=exp,
        state=state,
        reason_code="BLOCKED_FIXTURE_DAG_DRIFT",
        detail=(
            "activation_fixture_reconciliation_not_evidence:"
            + ",".join(sorted(affected))
        ),
        phase="fixture_activation_reconciliation",
        receipt=receipt,
    )


def _reject_failed_fixture_preconditions(
    *,
    exp: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Make the core's PRECONDITION_FAILED diagnostic an actual fixture gate."""

    fixture_receipts = [
        dict(row) if isinstance(row, dict) else row
        for row in _list(state.get("fixture_receipts"))
    ]
    failed_nodes = {
        _text(row.get("node_id"))
        for row in fixture_receipts
        if isinstance(row, dict)
        and _text(row.get("kind")) == "fixture_precondition_validation"
        and _text(row.get("status")).upper() == "FAILED"
        and _text(row.get("node_id"))
    }
    failed_bindings = [
        dict(row)
        for row in _list(state.get("binding_materialization_receipts"))
        if isinstance(row, dict)
        and _text(row.get("status")).upper() == "PRECONDITION_FAILED"
    ]
    if not failed_nodes and not failed_bindings:
        return None

    # The core may have appended a later generic ``resolved`` row for the same
    # node after recording the failed validation. Withdraw that synthetic
    # resolution and retain the explicit failure as the only authoritative row.
    for row in fixture_receipts:
        if not isinstance(row, dict):
            continue
        node_id = _text(row.get("node_id"))
        if node_id not in failed_nodes:
            continue
        if _text(row.get("kind")) == "fixture_precondition_validation":
            row["reason_code"] = "BLOCKED_FIXTURE_CONTRACT_FAILED"
            continue
        if _text(row.get("status")).lower() in {"resolved", "ready", "completed", "bound"}:
            row.update({
                "status": "BLOCKED",
                "reason_code": "BLOCKED_FIXTURE_CONTRACT_FAILED",
                "detail": "fixture_precondition_not_proven",
            })
    state["fixture_receipts"] = fixture_receipts
    _remove_fake_fixture_contract_evidence(state, failed_nodes)
    receipt = {
        "schema_version": "qualibug.fixture-precondition-gate.v1",
        "status": "BLOCKED",
        "reason_code": "BLOCKED_FIXTURE_CONTRACT_FAILED",
        "failed_fixture_ids": sorted(failed_nodes),
        "failed_bindings": failed_bindings,
        "precondition_failure_is_diagnostic_only": False,
    }
    return _block_measurement(
        exp=exp,
        state=state,
        reason_code="BLOCKED_FIXTURE_CONTRACT_FAILED",
        detail=(
            "fixture_precondition_not_proven:"
            + ",".join(sorted(failed_nodes))
        ),
        phase="fixture_precondition",
        receipt=receipt,
    )


def materialize_experiment_fixtures(**kwargs: Any) -> dict[str, Any]:
    exp = _dict(kwargs.get("exp"))
    projected_exp, projection_receipt = project_flow_data_materializer_dag(exp)

    # The core materializer resolves this helper from its own module globals.
    # Install the strict tri-state validator before the core executes.
    _materializer_core._validate_fixture_preconditions = (
        _strict_validate_fixture_preconditions
    )
    state = _dict(
        _materialize_experiment_fixtures(
            **{
                **kwargs,
                "exp": projected_exp,
            }
        )
    )
    state["flow_data_materializer_projection_receipt"] = projection_receipt
    if _text(state.get("status")) != "ready":
        terminal_result = _dict(state.get("result"))
        if terminal_result:
            terminal_result["flow_data_materializer_projection_receipt"] = (
                projection_receipt
            )
            state["result"] = terminal_result
        return state

    reconciliation_block = _reject_synthetic_activation_reconciliation(
        exp=exp,
        state=state,
    )
    if reconciliation_block is not None:
        return reconciliation_block

    precondition_block = _reject_failed_fixture_preconditions(
        exp=exp,
        state=state,
    )
    if precondition_block is not None:
        return precondition_block

    flow_data_receipt = validate_flow_data_materialization(exp, state)
    state["flow_data_materialization_receipt"] = flow_data_receipt
    exp["flow_data_materialization_receipt"] = deepcopy(flow_data_receipt)
    fixture_receipts = list(_list(state.get("fixture_receipts")))
    fixture_receipts.append(
        {
            "node_id": _text(flow_data_receipt.get("requirement_id"))
            or "flow_data_requirement",
            "kind": "flow_data_materialization",
            "phase": "fixture_binding",
            "status": _text(flow_data_receipt.get("status")),
            "reason_code": _text(flow_data_receipt.get("reason_code")),
            "detail": _text(flow_data_receipt.get("detail")),
            "receipt_id": _text(flow_data_receipt.get("requirement_id")),
            "requirement_fingerprint": _text(
                flow_data_receipt.get("requirement_fingerprint")
            ),
            "projection_fingerprint": _text(
                projection_receipt.get("projection_fingerprint")
            ),
            "required_target_count": int(
                flow_data_receipt.get("required_target_count") or 0
            ),
            "materialized_target_count": int(
                flow_data_receipt.get("materialized_target_count") or 0
            ),
            "missing_targets": list(
                flow_data_receipt.get("missing_targets") or []
            ),
            "unreceipted_targets": list(
                flow_data_receipt.get("unreceipted_targets") or []
            ),
        }
    )
    state["fixture_receipts"] = fixture_receipts
    if _text(flow_data_receipt.get("status")) != FLOW_DATA_VALID:
        return _block_measurement(
            exp=exp,
            state=state,
            reason_code=_text(flow_data_receipt.get("reason_code"))
            or "BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE",
            detail=_text(flow_data_receipt.get("detail"))
            or "flow_data_materialization_not_valid",
            phase="flow_data_materialization",
            receipt=flow_data_receipt,
        )

    precondition = execute_precondition_plan(
        exp=exp,
        actors=_dict(kwargs.get("actors")),
        ops=_dict(kwargs.get("ops")),
        tokens=_dict(kwargs.get("tokens")),
        runtime_bindings=_dict(state.get("runtime_bindings")),
        root=kwargs["root"],
        project=_text(kwargs.get("project")),
        base_url=_text(kwargs.get("base_url")),
        runtime_contract=_dict(kwargs.get("runtime_contract")),
        campaign_id=_text(kwargs.get("campaign_id")),
    )
    state["precondition_execution_receipt"] = precondition
    state["state_precondition_established"] = precondition.get("established") is True

    _precondition_bindings = dict(
        precondition.get("runtime_bindings") or {}
    )
    if _precondition_bindings:
        merged = dict(state.get("runtime_bindings") or {})
        merged.update(_precondition_bindings)
        state["runtime_bindings"] = merged
        exp["runtime_bindings"] = merged

    steps_out = list(_list(state.get("steps_out")))
    steps_out.extend(_list(precondition.get("governed_write_steps")))
    state["steps_out"] = steps_out

    fixture_receipts = list(_list(state.get("fixture_receipts")))
    for receipt in _list(precondition.get("receipts")):
        row = _dict(receipt)
        fixture_receipts.append(
            {
                "node_id": _text(row.get("step_id")),
                "step_id": _text(row.get("step_id")),
                "kind": "state_precondition_establishment",
                "phase": "precondition",
                "status": _text(row.get("status")),
                "reason_code": _text(row.get("reason_code")),
                "detail": _text(row.get("detail")),
                "receipt_id": _text(row.get("receipt_id")),
                "target_reached": row.get("target_reached"),
            }
        )
    state["fixture_receipts"] = fixture_receipts

    contract_receipts = list(_list(state.get("contract_evidence_receipts")))
    contract_receipts.extend(_list(precondition.get("receipts")))
    state["contract_evidence_receipts"] = contract_receipts

    if precondition.get("established") is True:
        return state

    return _block_measurement(
        exp=exp,
        state=state,
        reason_code=_text(precondition.get("reason_code"))
        or "BLOCKED_PRECONDITION_UNREACHABLE",
        detail=_text(precondition.get("detail"))
        or "state_precondition_not_established",
        phase="precondition",
        receipt=precondition,
    )
