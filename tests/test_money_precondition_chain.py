"""Money-family subject-establishment precondition chain planning.

Conservation/idempotency write obligations consume a subject entity (order
before pay) whose documented example reference fields (``orderId``) point at
entities that may not exist in the environment. Without establishment the
control arm fails with BLOCKED_CONTROL_ARM_NOT_PROVEN on a fresh target.

These tests pin the planner's fail-closed behavior: subject resolution is
structural (reference field -> entity name), the create operation must be a
source-declared collection POST with an example, actor and cleanup, state
advancement uses the source transition graph, and nothing is ever invented.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_test_asset_center.money_precondition_chain import (
    BLOCKED,
    MONEY_PRECONDITION_FAMILIES,
    NOT_APPLICABLE,
    PLANNED,
    REASON_NO_ACTOR,
    REASON_NO_CLEANUP,
    REASON_NO_CREATE_OPERATION,
    REASON_NO_SUBJECT_ENTITY,
    plan_money_family_precondition,
)


def _base_ir() -> dict[str, Any]:
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "entities": [
            {
                "id": "ent_order",
                "name": "order",
                "source_entity_names": ["orders"],
            },
            {
                "id": "ent_user",
                "name": "user",
                "source_entity_names": ["users"],
            },
        ],
        "operations": [
            {
                "id": "op_create_order",
                "method": "POST",
                "path": "/api/orders",
                "read_write": "write",
                "request_example": {
                    "items": [{"sku": "SKU-PHONE-001", "qty": 1}],
                    "addressId": "<address_id>",
                },
                "source_refs": [{"kind": "api_operation", "locator": "POST /api/orders"}],
            },
            {
                "id": "op_cancel_order",
                "method": "POST",
                "path": "/api/orders/{id}/cancel",
                "read_write": "write",
                "source_refs": [
                    {"kind": "api_operation", "locator": "POST /api/orders/{id}/cancel"}
                ],
            },
            {
                "id": "op_pay",
                "method": "POST",
                "path": "/api/payments/pay",
                "read_write": "write",
                "request_example": {
                    "orderId": "<order_id>",
                    "amount": 6899,
                    "channel": "BALANCE",
                },
                "source_refs": [
                    {"kind": "api_operation", "locator": "POST /api/payments/pay"}
                ],
            },
            {
                "id": "op_list_orders",
                "method": "GET",
                "path": "/api/orders",
                "read_write": "read",
                "source_refs": [
                    {"kind": "api_operation", "locator": "GET /api/orders"}
                ],
            },
        ],
        "actors": [
            {
                "id": "actor_buyer",
                "name": "buyer01",
                "role": "buyer",
                "runtime_bound": True,
                "credential_secret_ref": "secret_ref:actor:buyer01",
            },
        ],
        "states": [
            {"id": "st_created", "value": "CREATED"},
            {"id": "st_pending", "value": "PENDING_PAYMENT"},
            {"id": "st_paid", "value": "PAID"},
            {"id": "st_cancelled", "value": "CANCELLED"},
        ],
        "relations": [
            {
                "relation_type": "transitions",
                "from_ref": "st_created",
                "to_ref": "st_pending",
                "operation_ref": "op_create_order",
                "source_refs": [{"kind": "document", "locator": "prd"}],
            },
            {
                "relation_type": "transitions",
                "from_ref": "st_pending",
                "to_ref": "st_cancelled",
                "operation_ref": "op_cancel_order",
                "source_refs": [{"kind": "document", "locator": "prd"}],
            },
            {
                "relation_type": "permits",
                "actor_ref": "actor_buyer",
                "operation_ref": "op_pay",
                "status": "accepted",
                "source_refs": [{"kind": "document", "locator": "prd"}],
            },
            {
                "relation_type": "permits",
                "actor_ref": "actor_buyer",
                "operation_ref": "op_create_order",
                "status": "accepted",
                "source_refs": [{"kind": "document", "locator": "prd"}],
            },
            {
                "relation_type": "compensates",
                "from_ref": "op_cancel_order",
                "to_ref": "op_create_order",
                "status": "accepted",
                "source_refs": [{"kind": "document", "locator": "prd"}],
            },
        ],
        "invariants": [],
    }


def _pay_operation(ir: dict[str, Any]) -> dict[str, Any]:
    return next(
        op for op in ir["operations"] if op["id"] == "op_pay"
    )


# ── subject resolution ──────────────────────────────────────────────────────


def test_families_are_money_families() -> None:
    assert "conservation" in MONEY_PRECONDITION_FAMILIES
    assert "idempotency" in MONEY_PRECONDITION_FAMILIES


def test_pay_example_resolves_order_subject_and_plans_create() -> None:
    ir = _base_ir()
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=_pay_operation(ir),
        actor_refs=["actor_buyer"],
        property_spec={"template": "idempotent_effect_cardinality"},
        family="idempotency",
    )
    assert result["status"] == PLANNED
    assert result["identity_binding_target"] == "orderId"
    assert result["create_operation_ref"] == "op_create_order"
    steps = result["steps"]
    assert steps[0]["step_id"] == "money_precondition_create"
    assert steps[0]["phase"] == "fixture"
    assert steps[0]["operation_ref"] == "op_create_order"
    assert steps[0]["identity_binding_target"] == "orderId"
    assert steps[0]["actor_ref"] == "actor_buyer"


def test_example_without_reference_field_is_not_applicable() -> None:
    ir = _base_ir()
    operation = {
        "id": "op_pay",
        "method": "POST",
        "path": "/api/payments/pay",
        "request_example": {"amount": 6899, "channel": "BALANCE"},
        "source_refs": [{"kind": "api_operation", "locator": "POST /api/payments/pay"}],
    }
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=operation,
        actor_refs=["actor_buyer"],
        property_spec={},
        family="idempotency",
    )
    assert result["status"] == NOT_APPLICABLE
    assert result["reason_code"] == REASON_NO_SUBJECT_ENTITY


def test_reference_field_without_declared_entity_is_not_applicable() -> None:
    ir = _base_ir()
    operation = {
        "id": "op_other",
        "method": "POST",
        "path": "/api/unknown/action",
        "request_example": {"widgetRef": "<widget_ref>"},
        "source_refs": [{"kind": "api_operation", "locator": "POST /api/unknown/action"}],
    }
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=operation,
        actor_refs=["actor_buyer"],
        property_spec={},
        family="idempotency",
    )
    assert result["status"] == NOT_APPLICABLE


# ── create operation resolution ─────────────────────────────────────────────


def test_missing_create_operation_blocks_with_named_reason() -> None:
    ir = _base_ir()
    ir["operations"] = [
        op for op in ir["operations"] if op["id"] != "op_create_order"
    ]
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=_pay_operation(ir),
        actor_refs=["actor_buyer"],
        property_spec={},
        family="conservation",
    )
    assert result["status"] == BLOCKED
    assert result["reason_code"] == REASON_NO_CREATE_OPERATION


def test_create_without_request_example_is_not_a_create() -> None:
    ir = _base_ir()
    for op in ir["operations"]:
        if op["id"] == "op_create_order":
            op.pop("request_example", None)
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=_pay_operation(ir),
        actor_refs=["actor_buyer"],
        property_spec={},
        family="idempotency",
    )
    assert result["status"] == BLOCKED
    assert result["reason_code"] == REASON_NO_CREATE_OPERATION


def test_missing_actor_blocks_with_named_reason() -> None:
    ir = _base_ir()
    # Remove every source-declared actor authority for the create op so the
    # fallback cannot silently pick an actor.
    ir["relations"] = [
        rel
        for rel in ir["relations"]
        if not (
            rel.get("relation_type") == "permits"
            and rel.get("operation_ref") == "op_create_order"
        )
    ]
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=_pay_operation(ir),
        actor_refs=["actor_unknown"],
        property_spec={},
        family="idempotency",
    )
    assert result["status"] == BLOCKED
    assert result["reason_code"] == REASON_NO_ACTOR


def test_missing_cleanup_blocks_with_named_reason() -> None:
    ir = _base_ir()
    ir["relations"] = [
        rel
        for rel in ir["relations"]
        if not (
            rel.get("relation_type") == "compensates"
            and rel.get("to_ref") == "op_create_order"
        )
    ]
    ir["operations"] = [
        op
        for op in ir["operations"]
        if op["id"] != "op_cancel_order"
    ]
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=_pay_operation(ir),
        actor_refs=["actor_buyer"],
        property_spec={},
        family="idempotency",
    )
    assert result["status"] == BLOCKED
    assert result["reason_code"] == REASON_NO_CLEANUP


# ── state advancement ───────────────────────────────────────────────────────


def test_declared_from_state_appends_advancement_steps() -> None:
    ir = _base_ir()
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=_pay_operation(ir),
        actor_refs=["actor_buyer"],
        property_spec={
            "template": "forbidden_state_transition",
            "from_state": "CANCELLED",
            "expression": {
                "kind": "forbidden_state_transition",
                "operands": [{"from_state": "CANCELLED"}],
            },
        },
        family="idempotency",
    )
    assert result["status"] == PLANNED
    step_ids = [step["step_id"] for step in result["steps"]]
    assert "money_precondition_create" in step_ids
    assert any(
        step.get("intent") == "money_subject_state_advancement"
        for step in result["steps"]
    )
    # Create must come first, then the state advancement.
    assert step_ids.index("money_precondition_create") < step_ids.index(
        [s for s in step_ids if s.startswith("money_precondition_state_")][0]
    )


def test_unreachable_from_state_blocks_visibly() -> None:
    ir = _base_ir()
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=_pay_operation(ir),
        actor_refs=["actor_buyer"],
        property_spec={
            "template": "forbidden_state_transition",
            "from_state": "CLOSED",
            "expression": {
                "kind": "forbidden_state_transition",
                "operands": [{"from_state": "CLOSED"}],
            },
        },
        family="idempotency",
    )
    assert result["status"] == BLOCKED
    assert "STATE_UNREACHABLE" in result["reason_code"]
    assert (result.get("state_goal") or "").lower() == "closed"


# ── cross-industry generality ───────────────────────────────────────────────


def test_different_industry_names_bind_identically() -> None:
    """A warehouse system's 'shipment' is bound the same way as an e-commerce
    'order' — the mechanism is structural, not industry vocabulary."""
    ir = _base_ir()
    ir["entities"] = [
        {"id": "ent_shipment", "name": "shipment", "source_entity_names": ["shipments"]},
        {"id": "ent_user", "name": "user", "source_entity_names": ["users"]},
    ]
    ir["operations"] = [
        {
            "id": "op_create_shipment",
            "method": "POST",
            "path": "/api/shipments",
            "read_write": "write",
            "request_example": {"warehouseCode": "WH-01", "items": [{"sku": "A", "qty": 1}]},
            "source_refs": [{"kind": "api_operation", "locator": "POST /api/shipments"}],
        },
        {
            "id": "op_release_shipment",
            "method": "POST",
            "path": "/api/shipments/{id}/release",
            "read_write": "write",
            "source_refs": [
                {"kind": "api_operation", "locator": "POST /api/shipments/{id}/release"}
            ],
        },
        {
            "id": "op_confirm_shipment",
            "method": "POST",
            "path": "/api/shipments/confirm",
            "read_write": "write",
            "request_example": {"shipmentId": "<shipment_id>"},
            "source_refs": [
                {"kind": "api_operation", "locator": "POST /api/shipments/confirm"}
            ],
        },
        {
            "id": "op_list_shipments",
            "method": "GET",
            "path": "/api/shipments",
            "read_write": "read",
            "source_refs": [{"kind": "api_operation", "locator": "GET /api/shipments"}],
        },
    ]
    ir["relations"] = [
        {
            "relation_type": "compensates",
            "from_ref": "op_release_shipment",
            "to_ref": "op_create_shipment",
            "status": "accepted",
            "source_refs": [{"kind": "document", "locator": "prd"}],
        },
    ]
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=next(
            op for op in ir["operations"] if op["id"] == "op_confirm_shipment"
        ),
        actor_refs=["actor_buyer"],
        property_spec={},
        family="idempotency",
    )
    assert result["status"] == PLANNED
    assert result["identity_binding_target"] == "shipmentId"
    assert result["create_operation_ref"] == "op_create_shipment"


def test_plan_is_unchanged_by_industry_terms_inside_descriptions() -> None:
    ir = _base_ir()
    ir["operations"][0]["summary"] = "创建订单（电商行业）"
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=_pay_operation(ir),
        actor_refs=["actor_buyer"],
        property_spec={},
        family="conservation",
    )
    assert result["status"] == PLANNED
    assert result["create_operation_ref"] == "op_create_order"
