"""Flow-data freeze resolves subject identity alias spellings.

A money-precondition chain establishes the subject entity (order) under the
request reference field (``orderId``) and declares ``identity_binding_aliases``
covering the entity's own identity field (``id``). A downstream state step
cancels via ``/api/orders/{id}/cancel`` — the path names the entity identity
field directly. The freeze check must accept the alias token as satisfied when
the primary identity target is bound, instead of blocking the whole experiment
with BLOCKED_FLOW_DATA_BINDING_INCOMPLETE.
"""

from __future__ import annotations

from ai_test_asset_center.flow_data_requirement import (
    STATUS_BLOCKED,
    STATUS_FROZEN,
    build_flow_data_requirement,
)


def _ir() -> dict:
    return {
        "operations": [
            {
                "id": "op_create_order",
                "method": "POST",
                "path": "/api/orders",
                "request_example": {
                    "items": [{"sku": "SKU-1", "qty": 1}],
                },
            },
            {
                "id": "op_cancel_order",
                "method": "POST",
                "path": "/api/orders/{id}/cancel",
                "request_example": {},
            },
            {
                "id": "op_pay",
                "method": "POST",
                "path": "/api/payments/pay",
                "request_example": {
                    "orderId": "{orderId}",
                    "amount": 100,
                    "channel": "BALANCE",
                },
            },
        ]
    }


def _experiment(*, with_aliases: bool) -> dict:
    create_step: dict = {
        "step_id": "precondition_1",
        "phase": "fixture",
        "actor_ref": "actor_buyer",
        "operation_ref": "op_create_order",
        "intent": "money_subject_establishment",
        "protocol_step": "precondition_write",
        "step_ordinal": 1,
        "identity_binding_target": "orderId",
    }
    cancel_step: dict = {
        "step_id": "precondition_2",
        "phase": "fixture",
        "actor_ref": "actor_buyer",
        "operation_ref": "op_cancel_order",
        "intent": "money_subject_state_advancement",
        "protocol_step": "precondition_write",
        "step_ordinal": 2,
    }
    if with_aliases:
        create_step["identity_binding_aliases"] = ["orderId", "id"]
        create_step["identity_output_binding"] = {
            "schema_version": "qualibug.identity-output-binding.v1",
            "status": "FROZEN",
            "entity_ref": "ent_subject",
            "source_identity_field": "id",
            "source_path": "id",
            "consumer_targets": ["orderId"],
            "alias_targets": ["orderId", "id"],
            "source_authority": "behavior_ir.entities.identity_fields",
        }
        cancel_step["identity_binding_aliases"] = ["orderId", "id"]
    return {
        "obligation_id": "obl_alias_test",
        "precondition_plan": [create_step, cancel_step],
        "treatment_plan": [
            {
                "step_id": "treatment_1",
                "actor_ref": "actor_buyer",
                "operation_ref": "op_pay",
                "intent": "state_transition",
                "protocol_step": "state_transition_write",
                "body": {"orderId": "{orderId}", "amount": 100, "channel": "BALANCE"},
            }
        ],
        "binding_plan": [
            {
                "target": "orderId",
                "status": "runtime_resolvable",
                "source_priority": "money_precondition_chain",
                "identity_binding_target": "orderId",
                "precondition_provided": True,
                "body_template_paths": ["orderId"],
            }
        ],
        "cleanup_plan": [],
    }


def test_alias_spelling_resolves_subject_path_identity() -> None:
    """With identity_binding_aliases the cancel {id} path binds -> FROZEN."""
    result = build_flow_data_requirement(_experiment(with_aliases=True), behavior_ir=_ir())
    assert result.get("status") == STATUS_FROZEN
    unresolved = result.get("unresolved_steps") or []
    assert unresolved == []


def test_without_alias_spelling_the_cancel_path_stays_blocked() -> None:
    """Regression guard: the freeze check must still fail closed without the
    alias declaration (the runtime cannot materialize the {id} placeholder)."""
    result = build_flow_data_requirement(_experiment(with_aliases=False), behavior_ir=_ir())
    assert result.get("status") == STATUS_BLOCKED
    assert result.get("reason_code") == "BLOCKED_FLOW_DATA_BINDING_INCOMPLETE"
    detail = result.get("detail") or ""
    assert "precondition:precondition_2:id" in detail
