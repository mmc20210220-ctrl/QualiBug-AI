"""Tests for the validation decision-endpoint chain.

Covers (a) the consumption-state / amount-boundary treatment arms that give
an object-eligibility rule a REAL violating input resolved from the
environment's own rows, (b) the decision-flag parsing that turns the target's
own response decision into a rejection signal, (c) the zero-effect oracle
verdict for decision endpoints (response IS the effect), and (d) the
json_path_compare expected_path bound for cap rules. Synthetic data only.
"""

from __future__ import annotations

from ai_test_asset_center.assertion_dsl_validation_base import evaluate_assertion
from ai_test_asset_center.experiment_protocols_base import (
    _validation_protocol_material,
)
from ai_test_asset_center.observer_contracts_base import (
    _business_outcome_from_body,
)


def _behavior_ir() -> dict:
    return {
        "operations": [
            {
                "id": "op_coupons_list",
                "method": "GET",
                "path": "/api/coupons",
                "summary": "查询可用优惠券",
                "entity_refs": ["coupons"],
            },
            {
                "id": "op_coupons_validate",
                "method": "POST",
                "path": "/api/coupons/validate",
                "summary": "校验优惠券",
                "entity_refs": ["coupons"],
            },
        ],
        "actors": [],
    }


def _validate_operation() -> dict:
    return {
        "id": "op_coupons_validate",
        "method": "POST",
        "path": "/api/coupons/validate",
        "summary": "校验优惠券并试算优惠",
        "request_example": {
            "code": "NEW100",
            "totalAmount": 6999,
            "items": [
                {"sku": "SKU-001", "qty": 1, "price": 6999, "category": "数码"},
            ],
        },
        "request_schema": {
            "type": "object",
            "required": ["code", "items", "totalAmount"],
            "properties": {
                "code": {"type": "string", "example": "NEW100"},
                "totalAmount": {"type": "integer", "example": 6999},
                "items": {"type": "array"},
            },
        },
    }


def _property_spec(statement: str) -> dict:
    return {
        "template": "single_dimension_mutation",
        "expression": {
            "kind": "business_rule",
            "operator": "must_hold",
            "operands": [],
            "raw": statement,
        },
        "description": statement,
        "subject_entity_refs": ["coupon"],
    }


def test_validity_rule_builds_expiry_state_arm():
    control, treatment, mutation = _validation_protocol_material(
        _validate_operation(),
        _property_spec("优惠券必须在有效期内"),
        actor_catalog=[],
        behavior_ir=_behavior_ir(),
    )
    assert control["code"] == "NEW100"
    assert mutation["class"] == "runtime_entity_state_violation"
    assert mutation["violation_mode"] == "expiry"
    assert mutation["identity_field"] == "code"
    assert mutation["resolver_operations"][0]["path"] == "/api/coupons"


def test_status_rule_builds_status_state_arm():
    control, treatment, mutation = _validation_protocol_material(
        _validate_operation(),
        _property_spec("优惠券状态必须为 ACTIVE"),
        actor_catalog=[],
        behavior_ir=_behavior_ir(),
    )
    assert mutation["class"] == "runtime_entity_state_violation"
    assert mutation["violation_mode"] == "status"


def test_min_amount_rule_builds_amount_boundary_arm():
    control, treatment, mutation = _validation_protocol_material(
        _validate_operation(),
        _property_spec("必须满足最低订单金额"),
        actor_catalog=[],
        behavior_ir=_behavior_ir(),
    )
    assert mutation["class"] == "runtime_amount_boundary_violation"
    assert mutation["boundary_kind"] == "min_amount"
    assert mutation["amount_field"] == "totalAmount"
    assert mutation["identity_field"] == "code"


def test_cap_rule_builds_cap_boundary_arm():
    control, treatment, mutation = _validation_protocol_material(
        _validate_operation(),
        _property_spec("折扣券必须遵守封顶金额"),
        actor_catalog=[],
        behavior_ir=_behavior_ir(),
    )
    assert mutation["class"] == "runtime_amount_boundary_violation"
    assert mutation["boundary_kind"] == "max_cap"


def test_scope_rule_builds_scope_arm():
    # 类目券只能用于指定类目: the treatment names a scoped entity + a
    # distinct observed scope for the line-item category — resolved at
    # runtime from the entity's own list read.
    control, treatment, mutation = _validation_protocol_material(
        _validate_operation(),
        _property_spec("类目券只能用于指定类目"),
        actor_catalog=[],
        behavior_ir=_behavior_ir(),
    )
    assert mutation["class"] == "runtime_scope_violation"
    assert mutation["identity_field"] == "code"
    assert mutation["scope_field"] == "category_scope"
    assert mutation["json_path"] == "$.items[0].category"
    assert mutation["resolver_operations"][0]["path"] == "/api/coupons"


def test_unrelated_rule_keeps_generic_mutation():
    control, treatment, mutation = _validation_protocol_material(
        _validate_operation(),
        _property_spec("退款金额不能大于实际支付金额"),
        actor_catalog=[],
        behavior_ir=_behavior_ir(),
    )
    # No state/amount markers: generic mutation (or none), never the
    # entity-state or amount-boundary arms.
    assert mutation.get("class") not in {
        "runtime_entity_state_violation",
        "runtime_amount_boundary_violation",
    }


def test_decision_flag_parsing():
    outcome = _business_outcome_from_body(
        {"valid": False, "code": "EXPIRED50", "error": "coupon expired"}
    )
    assert outcome["business_rejected"] is True
    outcome2 = _business_outcome_from_body(
        {"valid": True, "code": "EXPIRED50", "discountAmount": 50}
    )
    assert outcome2["business_rejected"] is False


def test_decision_endpoint_zero_effect_is_violation_when_marked():
    # Marked decision endpoint: accepted 2xx + acceptance decision while the
    # rule required rejection = violation (the response IS the effect).
    receipt = evaluate_assertion(
        {
            "kind": "validation_rejection",
            "expected_class": 4,
            "expected_effect_count": 0,
            "response_decision": True,
        },
        observations={
            "status_code": 200,
            "business_rejected": False,
            "business_outcome": {"business_rejected": False},
            "zero_effect_on_accepted_write": True,
        },
        campaign_id="cmp",
        execution_id="exec",
    )
    assert receipt["status"] == "VIOLATION"
    assert receipt["reason_code"] == "VALIDATION_REJECTION_NOT_ENFORCED"


def test_decision_endpoint_rejection_is_pass_when_marked():
    receipt = evaluate_assertion(
        {
            "kind": "validation_rejection",
            "expected_class": 4,
            "expected_effect_count": 0,
            "response_decision": True,
        },
        observations={
            "status_code": 200,
            "business_rejected": True,
            "business_outcome": {"business_rejected": True},
            "zero_effect_on_accepted_write": True,
        },
        campaign_id="cmp",
        execution_id="exec",
    )
    assert receipt["status"] == "PASS"
    assert receipt["reason_code"] == "VALIDATION_BUSINESS_REJECTED"


def test_unmarked_zero_effect_stays_indeterminate():
    # Without the decision-endpoint marker the historical fail-closed
    # INDETERMINATE is preserved.
    receipt = evaluate_assertion(
        {
            "kind": "validation_rejection",
            "expected_class": 4,
            "expected_effect_count": 0,
        },
        observations={
            "status_code": 200,
            "business_rejected": False,
            "business_outcome": {"business_rejected": False},
            "zero_effect_on_accepted_write": True,
        },
        campaign_id="cmp",
        execution_id="exec",
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "VALIDATION_EFFECT_AMBIGUOUS"


def test_json_path_compare_expected_path_bound():
    from ai_test_asset_center.assertion_dsl_base import evaluate_assertion as base_eval

    receipt = base_eval(
        {
            "kind": "json_path_compare",
            "path": "$.discountAmount",
            "expected_path": "$.coupon.max_discount",
            "operator": "lte",
        },
        observations={
            "status_code": 200,
            "body": {
                "valid": True,
                "discountAmount": 300.2,
                "coupon": {"code": "ELEC20", "max_discount": "300.00"},
            },
        },
        campaign_id="cmp",
        execution_id="exec",
    )
    assert receipt["status"] == "VIOLATION"

    receipt_ok = base_eval(
        {
            "kind": "json_path_compare",
            "path": "$.discountAmount",
            "expected_path": "$.coupon.max_discount",
            "operator": "lte",
        },
        observations={
            "status_code": 200,
            "body": {
                "valid": True,
                "discountAmount": 250.0,
                "coupon": {"code": "ELEC20", "max_discount": "300.00"},
            },
        },
        campaign_id="cmp",
        execution_id="exec",
    )
    assert receipt_ok["status"] == "PASS"
