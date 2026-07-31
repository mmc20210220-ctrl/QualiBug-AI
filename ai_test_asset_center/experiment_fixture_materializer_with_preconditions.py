"""Fixture materialization followed by frozen data proof and preconditions.

The core fixture materializer remains the sole data/setup executor. This wrapper
first proves its result against the compile-frozen FlowDataRequirement, then
executes the already-frozen precondition plan, all before measured business
steps. Any block preserves cleanup context and clears measurement plans.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .experiment_fixture_materializer_core import (
    materialize_experiment_fixtures as _materialize_experiment_fixtures,
)
from .experiment_precondition_executor import execute_precondition_plan
from .flow_data_materialization import (
    STATUS_VALID as FLOW_DATA_VALID,
    validate_flow_data_materialization,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def materialize_experiment_fixtures(**kwargs: Any) -> dict[str, Any]:
    state = _dict(_materialize_experiment_fixtures(**kwargs))
    if _text(state.get("status")) != "ready":
        return state

    exp = _dict(kwargs.get("exp"))
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
