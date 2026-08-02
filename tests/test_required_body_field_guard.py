from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.experiment_plan_step_executor_core import (
    execute_non_barrier_plans,
)
from ai_test_asset_center.experiment_runtime_support import (
    _missing_required_body_fields,
)


# ---------------------------------------------------------------------------
# Unit tests for the shared helper (the actual new logic)
# ---------------------------------------------------------------------------


def test_missing_required_detects_absent_field() -> None:
    op = {"request_schema": {"required": ["sku"], "properties": {"sku": {}}}}
    assert _missing_required_body_fields({}, op) == ["sku"]


def test_missing_required_detects_empty_field() -> None:
    op = {"request_schema": {"required": ["sku", "name"]}}
    assert _missing_required_body_fields({"sku": "", "name": "x"}, op) == ["sku"]


def test_missing_required_accepts_present_field() -> None:
    op = {"request_schema": {"required": ["sku"]}}
    assert _missing_required_body_fields({"sku": "SKU-1"}, op) == []


def test_missing_required_safe_when_no_contract_known() -> None:
    # Unknown target contract: never block on a guessed contract.
    assert _missing_required_body_fields({}, {}) == []
    assert _missing_required_body_fields({}, {"request_schema": {}}) == []


def test_missing_required_handles_openapi_content_shape() -> None:
    op = {
        "request_schema": {
            "content": {
                "application/json": {
                    "schema": {"required": ["coupon_code"]},
                }
            }
        }
    }
    assert _missing_required_body_fields({}, op) == ["coupon_code"]


def test_missing_required_treats_null_like_empty() -> None:
    op = {"request_schema": {"required": ["sku"]}}
    # None body is treated as an empty object: all required fields missing.
    assert _missing_required_body_fields(None, op) == ["sku"]


# ---------------------------------------------------------------------------
# End-to-end: the executor must block a write whose body omits a required field
# ---------------------------------------------------------------------------


def _run_treatment_with(op: dict[str, Any], *, body_in_step: bool = False) -> dict[str, Any]:
    step: dict[str, Any] = {
        "step_id": "treatment_1",
        "method": "POST",
        "operation_ref": "op-1",
        "actor_ref": "actor-1",
        "path": "/api/products/admin",
    }
    if body_in_step:
        step["body"] = {"name": "no-sku"}
    return execute_non_barrier_plans(
        control_plan=[],
        treatment_plan=[step],
        consumed_barrier_steps=set(),
        actors={"actor-1": {"actor_id": "actor-1"}},
        ops={"op-1": op},
        tokens={},
        runtime_bindings={},
        activation_requirements={"control": [], "treatment": ["treatment_1"]},
        observations={},
        eid="exp-1",
        oid="obl-1",
        resolved_campaign_id="CMP-1",
        resolved_execution_id="exec-1",
        campaign_id="CMP-1",
        root=Path("/tmp"),
        project="proj-1",
        base_url="http://target.invalid",
        runtime_contract={},
    )


def test_executor_blocks_write_missing_required_field() -> None:
    op = {
        "method": "POST",
        "path": "/api/products/admin",
        "request_schema": {
            "type": "object",
            "required": ["sku"],
            "properties": {"sku": {"type": "string"}},
        },
    }
    result = _run_treatment_with(op)
    blocked = [
        s
        for s in result["steps"]
        if str(s.get("skipped_reason", "")).startswith(
            "BLOCKED_MISSING_REQUIRED_BODY_FIELDS"
        )
    ]
    assert blocked, result
    assert "sku" in blocked[0]["skipped_reason"]
    assert any(
        r.startswith("missing_required_body_fields:")
        for r in result["pre_transport_block_reasons"]
    )


def test_executor_does_not_block_when_required_field_present() -> None:
    op = {
        "method": "POST",
        "path": "/api/products/admin",
        "request_schema": {
            "type": "object",
            "required": ["sku"],
            "properties": {"sku": {"type": "string"}},
        },
    }
    step_body = {"sku": "SKU-1"}
    result = execute_non_barrier_plans(
        control_plan=[],
        treatment_plan=[
            {
                "step_id": "treatment_1",
                "method": "POST",
                "operation_ref": "op-1",
                "actor_ref": "actor-1",
                "path": "/api/products/admin",
                "body": step_body,
            }
        ],
        consumed_barrier_steps=set(),
        actors={"actor-1": {"actor_id": "actor-1"}},
        ops={"op-1": op},
        tokens={},
        runtime_bindings={},
        activation_requirements={"control": [], "treatment": ["treatment_1"]},
        observations={},
        eid="exp-1",
        oid="obl-1",
        resolved_campaign_id="CMP-1",
        resolved_execution_id="exec-1",
        campaign_id="CMP-1",
        root=Path("/tmp"),
        project="proj-1",
        base_url="http://target.invalid",
        runtime_contract={},
    )
    blocked = [
        s
        for s in result["steps"]
        if str(s.get("skipped_reason", "")).startswith(
            "BLOCKED_MISSING_REQUIRED_BODY_FIELDS"
        )
    ]
    assert not blocked, result


def test_executor_does_not_block_when_contract_unknown() -> None:
    # No request_schema -> safe default, request is allowed through.
    op = {"method": "POST", "path": "/api/products/admin"}
    result = _run_treatment_with(op)
    blocked = [
        s
        for s in result["steps"]
        if str(s.get("skipped_reason", "")).startswith(
            "BLOCKED_MISSING_REQUIRED_BODY_FIELDS"
        )
    ]
    assert not blocked, result
