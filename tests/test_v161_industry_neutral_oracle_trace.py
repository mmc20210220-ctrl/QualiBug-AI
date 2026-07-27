"""V1.6.1 industry-neutral field oracle trace integration (no benchmark names)."""
from __future__ import annotations

from ai_test_asset_center.assertion_dsl_base import (
    evaluate_assertion,
    validate_assertion_receipt,
)
from ai_test_asset_center.experiment_protocols_base import compile_family_protocol


def test_generic_state_forbidden_trace_chain():
    protocol = compile_family_protocol(
        risk_family="state",
        operation={
            "operation_id": "op_x",
            "method": "POST",
            "path": "/v1/entities/{id}/act",
            "request_examples": [{"delta": 1}],
        },
        operation_ref="op_x",
        control_actor_ref="actor_a",
        treatment_actor_ref="actor_a",
        property_spec={
            "invariant_ref": "rule_state_x",
            "expression": {
                "kind": "forbidden_state_transition",
                "operator": "must_not_transition",
                "operands": [
                    {"entity_ref": "EntityA", "from_state": "LOCKED", "to_state": "OPEN"}
                ],
            },
        },
    )
    assert protocol["status"] == "COMPILED"
    receipt = evaluate_assertion(
        {
            "assertion_id": "generic_state",
            **protocol["assertion"],
        },
        observations={"before_state": "LOCKED", "after_state": "LOCKED"},
    )
    validated = validate_assertion_receipt(receipt)
    assert validated["field_oracle_trace"]["status"] == "PASS"
    blob = str(validated).lower()
    assert "order" not in blob
    assert "coupon" not in blob
    assert "inventory" not in blob


def test_generic_conservation_trace_chain():
    protocol = compile_family_protocol(
        risk_family="conservation",
        operation={
            "operation_id": "op_y",
            "method": "POST",
            "path": "/v1/entities/{id}/mutate",
            "request_examples": [{"units": 1}],
        },
        operation_ref="op_y",
        control_actor_ref="actor_a",
        treatment_actor_ref="actor_a",
        property_spec={
            "invariant_ref": "rule_cons_x",
            "expression": {
                "kind": "data_conservation",
                "operands": [
                    {"field": "balance", "field_id": "cf_balance"},
                    {"field": "held", "field_id": "cf_held"},
                ],
                "equation": {"operator": "unchanged_sum", "terms": ["cf_balance", "cf_held"]},
            },
        },
    )
    assert protocol["status"] == "COMPILED"
    terms = protocol["assertion"]["equation"]["terms"]
    receipt = evaluate_assertion(
        {
            "assertion_id": "generic_cons",
            "kind": "conservation",
            "equation": {"operator": "unchanged_sum", "terms": terms},
            "rule_id": "rule_cons_x",
        },
        observations={
            "before_values": {"balance": 4, "held": 6},
            "after_values": {"balance": 3, "held": 7},
        },
    )
    validated = validate_assertion_receipt(receipt)
    assert validated["field_oracle_trace"]["kind"] == "conservation"
    assert validated["status"] == "PASS"


def test_generic_causal_field_delta_trace_chain():
    # Causal coverage for industry-neutral integration when golden RESOLVED
    # set has zero causal rules (SOURCE_ASSET_LIMITED on product golden set).
    receipt = evaluate_assertion(
        {
            "assertion_id": "generic_causal",
            "kind": "field_delta",
            "rule_id": "causal.generic.operation_x",
            "fields": [{"field": "quantity", "expected_delta": -2}],
        },
        observations={
            "before_values": {"quantity": 10},
            "after_values": {"quantity": 8},
        },
    )
    validated = validate_assertion_receipt(receipt)
    assert validated["field_oracle_trace"]["status"] == "PASS"
    assert validated["field_oracle_trace"]["kind"] == "field_delta"
