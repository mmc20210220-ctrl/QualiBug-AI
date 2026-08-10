"""Identity capture from money-family precondition writes.

A fixture-phase precondition step that creates the subject entity of a money
obligation (order before pay) declares ``identity_binding_target`` (orderId).
After the governed write is accepted, the created identity must be captured
into runtime_bindings — in both camelCase and snake_case placeholder forms —
so the control/treatment bodies materialize the just-created subject instead
of an unrelated existing row.
"""

from __future__ import annotations

from typing import Any

from ai_test_asset_center.experiment_precondition_executor import (
    execute_precondition_plan,
)


def _fake_governed_write(body: Any, *, status: int = 201) -> dict[str, Any]:
    return {
        "accepted": 200 <= status < 300,
        "write": {"status": status, "body": body, "headers": {}},
        "after": {"status": status, "body": body},
        "audit_record": {"record_type": "test"},
    }


class _FakeExecutor:
    """Stand-in for sandbox_write_executor.execute_governed_control_write."""

    def __init__(self, response_body: Any, status: int = 201) -> None:
        self.response_body = response_body
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return _fake_governed_write(self.response_body, status=self.status)


def _run(
    step: dict[str, Any],
    response_body: Any,
    *,
    status: int = 201,
    bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    exp = {"precondition_plan": [step]}
    ops = {
        "op_create_order": {
            "id": "op_create_order",
            "method": "POST",
            "path": "/api/orders",
            "request_example": {"items": [{"sku": "A", "qty": 1}]},
        },
        "op_get_order": {
            "id": "op_get_order",
            "method": "GET",
            "path": "/api/orders/{id}",
        },
    }
    actors = {
        "actor_buyer": {
            "id": "actor_buyer",
            "role": "buyer",
            "credential_secret_ref": "secret_ref:test_accounts:buyer01@example.com",
        }
    }
    fake = _FakeExecutor(response_body, status=status)
    from ai_test_asset_center import experiment_precondition_executor as module

    original = module.execute_governed_control_write
    module.execute_governed_control_write = fake  # type: ignore[assignment]
    try:
        return execute_precondition_plan(
            exp=exp,
            actors=actors,
            ops=ops,
            tokens={"secret_ref:test_accounts:buyer01@example.com": "tok"},
            runtime_bindings=dict(bindings or {}),
            root=__import__("pathlib").Path("."),
            project="p",
            base_url="http://localhost:8080",
            runtime_contract={},
            campaign_id="cmp",
        )
    finally:
        module.execute_governed_control_write = original


def _create_step() -> dict[str, Any]:
    return {
        "step_id": "money_precondition_create",
        "phase": "fixture",
        "actor_ref": "actor_buyer",
        "operation_ref": "op_create_order",
        "intent": "money_subject_establishment",
        "protocol_step": "precondition_write",
        "identity_binding_target": "orderId",
        "observe_response_body": True,
        "step_ordinal": 1,
        "method": "POST",
        "path": "/api/orders",
        "to_state": "PENDING_PAYMENT",
        "state_field": "status",
        "readback_contract": {
            "schema_version": "qualibug.readback-contract.v1",
            "required_fields": [{"field": "id"}],
            "state_field": "status",
            "resolver_operations": [
                {
                    "operation_ref": "op_get_order",
                    "method": "GET",
                    "path": "/api/orders/{id}",
                }
            ],
        },
    }


def test_created_identity_is_captured_into_runtime_bindings() -> None:
    created = {"id": "ord_123", "status": "PENDING_PAYMENT", "total_amount": 6899}
    result = _run(_create_step(), created)
    assert result["established"] is True
    captured = result.get("identity_bindings") or {}
    assert captured.get("orderId") == "ord_123"
    assert result.get("runtime_bindings", {}).get("orderId") == "ord_123"


def test_identity_captured_in_both_camel_and_snake_placeholder_forms() -> None:
    created = {"order_id": "ord_456", "status": "PENDING_PAYMENT"}
    result = _run(_create_step(), created)
    captured = result.get("identity_bindings") or {}
    # The response names the identity order_id (snake); the body placeholder
    # may use either spelling — both must bind.
    assert captured.get("orderId") == "ord_456"
    assert captured.get("order_id") == "ord_456"


def test_rejected_create_captures_no_identity() -> None:
    result = _run(_create_step(), {"error": "boom"}, status=400)
    assert result["established"] is False
    assert not (result.get("identity_bindings") or {})


def test_step_without_identity_binding_target_is_untouched() -> None:
    step = _create_step()
    step.pop("identity_binding_target")
    result = _run(step, {"id": "ord_789", "status": "PENDING_PAYMENT"})
    assert result["established"] is True
    assert not (result.get("identity_bindings") or {})


def test_identity_receipt_is_emitted() -> None:
    result = _run(_create_step(), {"id": "ord_abc", "status": "PENDING_PAYMENT"})
    receipts = [r for r in result.get("receipts") or []]
    identity_receipts = [
        r for r in receipts if r.get("phase") == "precondition_identity"
    ]
    assert len(identity_receipts) == 1
    assert identity_receipts[0]["identity_value"] == "ord_abc"
    assert identity_receipts[0]["target"] == "orderId"


def test_identity_aliases_bind_entity_identity_field() -> None:
    """Downstream state steps address the subject by its own identity field.

    The chain declares ``identity_binding_aliases`` (orderId + the entity's
    identity field id); the captured identity must be registered under every
    declared spelling so a cancel step's path /api/orders/{id}/cancel
    materializes from the same created subject.
    """
    step = _create_step()
    step["identity_binding_aliases"] = ["orderId", "id"]
    created = {"id": "ord_alias_1", "status": "PENDING_PAYMENT"}
    result = _run(step, created)
    assert result["established"] is True
    captured = result.get("identity_bindings") or {}
    assert captured.get("orderId") == "ord_alias_1"
    assert captured.get("id") == "ord_alias_1"
    bindings = result.get("runtime_bindings") or {}
    assert bindings.get("id") == "ord_alias_1"


def test_identity_alias_fallback_reads_entity_identity_field() -> None:
    """The create response may expose only the entity identity field (id).

    When the reference slot (orderId) is absent from the response, the
    executor must fall back to the declared alias spelling before giving up.
    """
    step = _create_step()
    step["identity_binding_aliases"] = ["orderId", "id"]
    # Response carries ONLY the entity identity field, not the reference slot.
    created = {"id": "ord_alias_2", "status": "PENDING_PAYMENT"}
    result = _run(step, created)
    assert result["established"] is True
    captured = result.get("identity_bindings") or {}
    assert captured.get("orderId") == "ord_alias_2"
    assert captured.get("id") == "ord_alias_2"


def test_identity_aliases_are_recorded_in_receipt() -> None:
    step = _create_step()
    step["identity_binding_aliases"] = ["orderId", "id"]
    result = _run(step, {"id": "ord_alias_3", "status": "PENDING_PAYMENT"})
    receipts = [r for r in result.get("receipts") or []]
    identity_receipts = [
        r for r in receipts if r.get("phase") == "precondition_identity"
    ]
    assert len(identity_receipts) == 1
    assert identity_receipts[0]["identity_binding_aliases"] == ["orderId", "id"]
