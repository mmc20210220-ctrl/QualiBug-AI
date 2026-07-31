"""Fixture materialization followed by compiled state preconditions.

The original fixture materializer remains the data/setup authority. This
wrapper executes the already-frozen precondition plan only after fixtures and
runtime bindings are ready, before any measured control/treatment step.
"""
from __future__ import annotations

from typing import Any

from .experiment_fixture_materializer import (
    materialize_experiment_fixtures as _materialize_experiment_fixtures,
)
from .experiment_precondition_executor import execute_precondition_plan


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def materialize_experiment_fixtures(**kwargs: Any) -> dict[str, Any]:
    state = _dict(_materialize_experiment_fixtures(**kwargs))
    if _text(state.get("status")) != "ready":
        return state

    exp = _dict(kwargs.get("exp"))
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

    # Do not enter the measured window. Accepted governed writes stay in
    # steps_out so the existing cleanup authority can see and compensate them.
    state["status"] = "precondition_blocked"
    state["precondition_reason_code"] = _text(precondition.get("reason_code"))
    state["precondition_detail"] = _text(precondition.get("detail"))
    return state
