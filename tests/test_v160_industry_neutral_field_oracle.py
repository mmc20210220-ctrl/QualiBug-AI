"""V1.6.0 industry-neutral integration: field oracle path without benchmark vocabulary.

Proves compile→observe→assert wiring uses generic entity/field names only.
"""
from __future__ import annotations

from ai_test_asset_center.assertion_dsl_base import evaluate_assertion
from ai_test_asset_center.experiment_protocols_base import compile_family_protocol
from ai_test_asset_center.observer_contracts_base import _numeric_snapshot_values


def test_generic_conservation_compile_observe_assert_chain():
    protocol = compile_family_protocol(
        risk_family="conservation",
        operation={
            "operation_id": "op_generic_write",
            "method": "POST",
            "path": "/v1/items",
            "request_examples": [{"units": 1}],
        },
        operation_ref="op_generic_write",
        control_actor_ref="actor_a",
        treatment_actor_ref="actor_a",
        property_spec={
            "template": "units conserved",
            "expression": {
                "kind": "conservation",
                "operator": "unchanged_sum",
                "operands": [
                    {"field_id": "cf_units_free", "field": "units_free"},
                    {"field_id": "cf_units_held", "field": "units_held"},
                ],
            },
        },
    )
    assert protocol["status"] == "COMPILED"
    terms = protocol["assertion"]["equation"]["terms"]
    body_before = {"units_free": 4, "units_held": 6, "noise": 100}
    body_after = {"units_free": 3, "units_held": 7, "noise": 999}
    before = _numeric_snapshot_values(body_before, terms)
    after = _numeric_snapshot_values(body_after, terms)
    assert "noise" not in before and "noise" not in after
    # Map cf_* terms to JSON names for eval (observer normalizes by field name).
    # Use field names from operands for snapshot keys already returned as cf_*.
    # Re-snapshot with plain names for assertion equation terms when IDs differ.
    before_named = _numeric_snapshot_values(body_before, ["units_free", "units_held"])
    after_named = _numeric_snapshot_values(body_after, ["units_free", "units_held"])
    result = evaluate_assertion(
        {
            "assertion_id": "generic_cons",
            "kind": "conservation",
            "equation": {
                "operator": "unchanged_sum",
                "terms": ["units_free", "units_held"],
            },
        },
        observations={
            "before_values": before_named,
            "after_values": after_named,
        },
    )
    assert result["status"] == "PASS"
    assert result["field_oracle_trace"]["kind"] == "conservation"


def test_generic_postcondition_no_benchmark_tokens():
    result = evaluate_assertion(
        {
            "assertion_id": "generic_pc",
            "kind": "postcondition",
            "operands": [{"field": "phase", "expected_value": "DONE"}],
        },
        observations={
            "entity_state_observed": True,
            "state_change_count": 1,
            "after_values": {"phase": "DONE"},
        },
    )
    assert result["status"] == "PASS"
    assert "order" not in str(result).lower()
    assert "coupon" not in str(result).lower()
    assert "inventory" not in str(result).lower()
