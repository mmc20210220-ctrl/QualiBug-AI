"""Regression tests for the read-only state-audit protocol.

Guards the fix for the structural break where ``state_audit_planner``
obligations (``audit_mode: read_only``, template ``readonly_audit_validation``)
had no protocol consumer and every one of them died as
``validation_body_protocol_requires_write_operation``.

Covers the four-link chain end-to-end at unit level:
obligation planner → registered protocol compiler → registered assertion kind
evaluator (tri-state, fail-closed).
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.assertion_dsl_base import (
    evaluate_assertion,
    registered_assertion_kinds,
)
from ai_test_asset_center.experiment_protocol_registry import (
    resolve_family_protocol,
    validate_registered_protocol_result,
)
from ai_test_asset_center.experiment_protocols import compile_family_protocol
from ai_test_asset_center.readonly_audit_protocol import (
    ASSERTION_KIND,
    PROTOCOL_TEMPLATE,
    install_readonly_audit_protocol,
    split_field_ref,
    uniqueness_field_from_expression,
)
from ai_test_asset_center.state_audit_planner import (
    build_readonly_state_audit_obligations,
)


@pytest.fixture(scope="module", autouse=True)
def _installed() -> None:
    first = install_readonly_audit_protocol()
    second = install_readonly_audit_protocol()
    # Idempotent: the second run must not re-register anything.
    assert "assertion" not in second or second["assertion"] == ASSERTION_KIND
    assert first["protocol"].endswith(PROTOCOL_TEMPLATE)


def _audit_property_spec(
    *,
    expression: dict | None = None,
    operation_ref: str = "op_list_orders",
) -> dict:
    return {
        "template": PROTOCOL_TEMPLATE,
        "invariant_ref": "inv_uniqueness_1",
        "expression": expression
        or {
            "kind": "validation_uniqueness",
            "operator": "unique",
            "operands": [{"field_ref": "field:order_items.id"}],
            "raw": "order_items.id 取值必须唯一",
        },
        "operation_ref": operation_ref,
        "entity_type": "order",
        "audit_mode": "read_only",
    }


def _compile(**overrides):
    envelope = {
        "risk_family": "validation",
        "operation": {"id": "op_list_orders", "method": "GET", "path": "/api/entities"},
        "operation_ref": "op_list_orders",
        "control_actor_ref": "",
        "treatment_actor_ref": "actor_admin",
        "property_spec": _audit_property_spec(),
        "behavior_ir": {},
    }
    envelope.update(overrides)
    return compile_family_protocol(
        risk_family=envelope["risk_family"],
        operation=envelope["operation"],
        operation_ref=envelope["operation_ref"],
        control_actor_ref=envelope["control_actor_ref"],
        treatment_actor_ref=envelope["treatment_actor_ref"],
        property_spec=envelope["property_spec"],
        behavior_ir=envelope.get("behavior_ir"),
    )


# ── registration ──


def test_install_registers_assertion_kind_and_protocol() -> None:
    assert ASSERTION_KIND in set(registered_assertion_kinds())
    registration = resolve_family_protocol("validation", PROTOCOL_TEMPLATE)
    assert registration is not None
    assert registration["assertion_kind"] == ASSERTION_KIND
    assert registration["emits_control"] is False
    assert registration["observers"] == ["http_response"]


# ── protocol compiler ──


def test_readonly_audit_compiles_get_only_single_actor_plan() -> None:
    result = _compile()
    assert result["status"] == "COMPILED"
    assert result["control_plan"] == []
    treatment = result["treatment_plan"]
    assert len(treatment) == 1
    assert treatment[0]["actor_ref"] == "actor_admin"
    assert treatment[0]["operation_ref"] == "op_list_orders"
    assert treatment[0]["protocol_step"] == "readonly_audit_read"
    assertion = result["assertion"]
    assert assertion["kind"] == ASSERTION_KIND
    assert assertion["field"] == "id"
    assert assertion["field_qualifier"] == "order_items"
    # Shape must pass the registry's plan validation (step ids, refs).
    registration = resolve_family_protocol("validation", PROTOCOL_TEMPLATE)
    validate_registered_protocol_result(result, registration=registration)


def test_readonly_audit_blocks_write_operation() -> None:
    result = _compile(
        operation={"id": "op_list_orders", "method": "POST", "path": "/api/entities"},
    )
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_MISSING_OPERATION"
    assert "readonly_audit_requires_read_operation" in result["detail"]


def test_readonly_audit_blocks_without_actor() -> None:
    result = _compile(treatment_actor_ref="", control_actor_ref="")
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_MISSING_ACTOR"


def test_readonly_audit_blocks_unsupported_expression_visibly() -> None:
    result = _compile(
        property_spec=_audit_property_spec(
            expression={"kind": "validation_range", "operator": "lte", "operands": []}
        )
    )
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_UNSUPPORTED_AUDIT_EXPRESSION"


def test_readonly_audit_blocks_missing_field_ref() -> None:
    result = _compile(
        property_spec=_audit_property_spec(
            expression={
                "kind": "validation_uniqueness",
                "operator": "unique",
                "operands": [],
            }
        )
    )
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_MISSING_BINDING"


# ── assertion evaluator (tri-state) ──


def _spec() -> dict:
    return {
        "kind": ASSERTION_KIND,
        "assertion_id": "assert_validation",
        "field": "id",
        "field_qualifier": "order_items",
    }


def _obs(body, status_code: int = 200) -> dict:
    return {
        "status_code": status_code,
        "body": body,
        "campaign_id": "campaign_1",
        "execution_id": "execution_1",
    }


def test_uniqueness_violation_on_duplicate_values() -> None:
    receipt = evaluate_assertion(
        _spec(),
        observations=_obs({"order_items": [{"id": 7}, {"id": 7}, {"id": 8}]}),
    )
    assert receipt["status"] == "VIOLATION"
    assert receipt["reason_code"] == "UNIQUENESS_DUPLICATE_VALUES_OBSERVED"


def test_uniqueness_pass_on_distinct_values() -> None:
    receipt = evaluate_assertion(
        _spec(),
        observations=_obs({"order_items": [{"id": 7}, {"id": 8}]}),
    )
    assert receipt["status"] == "PASS"


def test_uniqueness_indeterminate_on_single_row_no_vacuous_pass() -> None:
    receipt = evaluate_assertion(
        _spec(),
        observations=_obs({"order_items": [{"id": 7}]}),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "AUDIT_COLLECTION_TOO_SMALL"


def test_uniqueness_accepts_top_level_collection() -> None:
    receipt = evaluate_assertion(
        _spec(),
        observations=_obs([{"id": 1}, {"id": 2}]),
    )
    assert receipt["status"] == "PASS"


def test_uniqueness_indeterminate_when_field_not_observed() -> None:
    receipt = evaluate_assertion(
        _spec(),
        observations=_obs({"order_items": [{"uid": 1}, {"uid": 2}]}),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "AUDIT_FIELD_NOT_OBSERVED"


def test_uniqueness_indeterminate_when_collection_not_locatable() -> None:
    receipt = evaluate_assertion(
        _spec(),
        observations=_obs({"message": "ok"}),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "AUDIT_COLLECTION_NOT_OBSERVED"


def test_uniqueness_indeterminate_on_rejected_read() -> None:
    receipt = evaluate_assertion(
        _spec(),
        observations=_obs({"error": "denied"}, status_code=403),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "AUDIT_READ_NOT_ACCEPTED"


# ── field ref parsing ──


def test_split_field_ref_structural_only() -> None:
    assert split_field_ref("field:order_items.id") == ("order_items", "id")
    assert split_field_ref("field:id") == ("", "id")
    assert split_field_ref("") == ("", "")
    assert uniqueness_field_from_expression(
        {"operands": [{"field_id": "field:a.b.c"}]}
    ) == ("a.b", "c")


# ── planner endpoint preference ──


def _ir_with_detail_and_list() -> dict:
    return {
        "invariants": [
            {
                "id": "inv_u1",
                "description": "identifier unique",
                "expression": {
                    "kind": "validation_uniqueness",
                    "operator": "unique",
                    "operands": [{"field_ref": "field:orders.id", "entity": "order"}],
                },
            }
        ],
        "operations": [
            {"id": "op_detail", "method": "GET", "path": "/entities/orders/{id}"},
            {"id": "op_list", "method": "GET", "path": "/entities/orders"},
        ],
        "relations": [],
        "actors": [
            {
                "id": "actor_admin",
                "role": "admin",
                "credential_secret_ref": "secret_ref:actor:admin",
            }
        ],
    }


def test_planner_binds_list_endpoint_for_uniqueness_audit() -> None:
    obligations = build_readonly_state_audit_obligations(_ir_with_detail_and_list())
    assert len(obligations) == 1
    obligation = obligations[0]
    assert obligation["required_operations"] == ["op_list"]
    property_spec = obligation["property"]
    assert property_spec["audit_mode"] == "read_only"
    assert property_spec["template"] == PROTOCOL_TEMPLATE
    assert obligation["cleanup_requirement"] == {"required": False}


def test_planner_falls_back_to_detail_when_no_list_endpoint() -> None:
    ir = _ir_with_detail_and_list()
    ir["operations"] = [ir["operations"][0]]
    obligations = build_readonly_state_audit_obligations(ir)
    assert len(obligations) == 1
    assert obligations[0]["required_operations"] == ["op_detail"]


def test_planner_keeps_detail_preference_for_non_uniqueness() -> None:
    ir = _ir_with_detail_and_list()
    ir["invariants"][0]["expression"] = {
        "kind": "validation_presence",
        "operator": "present",
        "operands": [{"field_ref": "field:orders.id", "entity": "order"}],
    }
    obligations = build_readonly_state_audit_obligations(ir)
    assert len(obligations) == 1
    assert obligations[0]["required_operations"] == ["op_detail"]