"""V1.6.1 resolved-rule → field oracle trace closure specialized tests."""
from __future__ import annotations

import pytest

from ai_test_asset_center.assertion_dsl_base import (
    evaluate_assertion,
    validate_assertion_receipt,
)
from ai_test_asset_center.experiment_protocols_base import compile_family_protocol
from ai_test_asset_center.observer_contracts_base import _conservation_terms_from_experiment


def _write_op() -> dict:
    return {
        "operation_id": "op_pay",
        "method": "POST",
        "path": "/orders/{id}/pay",
        "request_examples": [{"amount": 10}],
    }


def test_validate_assertion_receipt_preserves_field_oracle_trace():
    raw = evaluate_assertion(
        {
            "assertion_id": "a1",
            "kind": "state_transition",
            "from_state": "OPEN",
            "to_state": "CLOSED",
        },
        observations={"before_state": "OPEN", "after_state": "CLOSED"},
    )
    assert "field_oracle_trace" in raw
    validated = validate_assertion_receipt(raw)
    assert isinstance(validated.get("field_oracle_trace"), dict)
    assert validated["field_oracle_trace"]["kind"] == "state_transition"


def test_forbidden_state_transition_pass_when_not_reached():
    result = evaluate_assertion(
        {
            "assertion_id": "f1",
            "kind": "forbidden_state_transition",
            "operator": "must_not_transition",
            "from_state": "CANCELLED",
            "to_state": "PAID",
        },
        observations={"before_state": "CANCELLED", "after_state": "CANCELLED"},
    )
    assert result["status"] == "PASS"
    assert result["field_oracle_trace"]["status"] == "PASS"


def test_forbidden_state_transition_violation_when_reached():
    result = evaluate_assertion(
        {
            "assertion_id": "f2",
            "kind": "forbidden_state_transition",
            "operator": "must_not_transition",
            "operands": [{"from_state": "CANCELLED", "to_state": "PAID"}],
        },
        observations={"before_state": "CANCELLED", "after_state": "PAID"},
    )
    assert result["status"] == "VIOLATION"
    assert result.get("reason_code") == "FORBIDDEN_STATE_TRANSITION"


@pytest.mark.parametrize(
    "from_state,to_state",
    [
        ("CANCELLED", "PAID"),
        ("CANCELLED", "SHIPPED"),
        ("REFUNDED", "PAID"),
        ("REFUNDED", "SHIPPED"),
    ],
)
def test_state_protocol_lifts_operands_from_expression(from_state, to_state):
    result = compile_family_protocol(
        risk_family="state",
        operation=_write_op(),
        operation_ref="op_pay",
        control_actor_ref="buyer",
        treatment_actor_ref="buyer",
        property_spec={
            "invariant_ref": "bir_rule",
            "expression": {
                "kind": "forbidden_state_transition",
                "operator": "must_not_transition",
                "operands": [
                    {"entity_ref": "order", "from_state": from_state, "to_state": to_state}
                ],
            },
        },
    )
    assert result["status"] == "COMPILED"
    assertion = result["assertion"]
    assert assertion["from_state"] == from_state
    assert assertion["to_state"] == to_state
    assert assertion["kind"] in {"forbidden_state_transition", "state_transition"}


def test_conservation_protocol_maps_cf_terms_to_field_names():
    result = compile_family_protocol(
        risk_family="conservation",
        operation=_write_op(),
        operation_ref="op_pay",
        control_actor_ref="buyer",
        treatment_actor_ref="buyer",
        property_spec={
            "invariant_ref": "bir_cons",
            "expression": {
                "kind": "data_conservation",
                "operator": "must_hold",
                "operands": [
                    {"field": "discount_amount", "field_id": "cf_disc"},
                    {"field": "total_amount", "field_id": "cf_total"},
                ],
                "equation": {
                    "operator": "unchanged_sum",
                    "terms": ["cf_disc", "cf_total"],
                },
            },
        },
    )
    assert result["status"] == "COMPILED"
    terms = result["assertion"]["equation"]["terms"]
    assert "discount_amount" in terms
    assert "total_amount" in terms
    assert "cf_disc" not in terms


def test_conservation_terms_from_experiment_prefer_field_names():
    terms = _conservation_terms_from_experiment(
        {
            "assertions": [
                {
                    "kind": "conservation",
                    "equation": {"terms": ["cf_disc"]},
                    "operands": [
                        {"field_id": "cf_disc", "field": "discount_amount"},
                    ],
                }
            ]
        }
    )
    assert "discount_amount" in terms


@pytest.mark.parametrize(
    "kind",
    ["conservation", "field_delta", "postcondition", "state_transition"],
)
def test_deep_kinds_emit_trace(kind):
    if kind == "conservation":
        spec = {
            "assertion_id": "c",
            "kind": kind,
            "equation": {"operator": "unchanged_sum", "terms": ["a"]},
        }
        obs = {"before_values": {"a": 1}, "after_values": {"a": 1}}
    elif kind == "field_delta":
        spec = {
            "assertion_id": "d",
            "kind": kind,
            "fields": [{"field": "qty", "expected_delta": -1}],
        }
        obs = {"before_values": {"qty": 2}, "after_values": {"qty": 1}}
    elif kind == "postcondition":
        spec = {
            "assertion_id": "p",
            "kind": kind,
            "operands": [{"field": "status", "expected_value": "DONE"}],
        }
        obs = {
            "entity_state_observed": True,
            "state_change_count": 1,
            "after_values": {"status": "DONE"},
        }
    else:
        spec = {
            "assertion_id": "s",
            "kind": kind,
            "from_state": "A",
            "to_state": "B",
        }
        obs = {"before_state": "A", "after_state": "B"}
    result = evaluate_assertion(spec, observations=obs)
    assert "field_oracle_trace" in result
    validated = validate_assertion_receipt(result)
    assert validated["field_oracle_trace"]["kind"] == kind


@pytest.mark.parametrize("n", list(range(12)))
def test_trace_receipt_roundtrip_matrix(n):
    result = evaluate_assertion(
        {
            "assertion_id": f"m{n}",
            "kind": "state_transition",
            "from_state": "S0",
            "to_state": "S1",
        },
        observations={"before_state": "S0", "after_state": "S1"},
    )
    again = validate_assertion_receipt(result)
    assert again["field_oracle_trace"]["status"] == "PASS"


def test_empty_conservation_still_blocked():
    result = compile_family_protocol(
        risk_family="conservation",
        operation=_write_op(),
        operation_ref="op_pay",
        control_actor_ref="a",
        treatment_actor_ref="a",
        property_spec={"expression": {"kind": "conservation", "raw": "x"}},
    )
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_EMPTY_CONSERVATION_TERMS"


def test_http_status_has_no_trace():
    result = evaluate_assertion(
        {"assertion_id": "h", "kind": "http_status", "expected": 200},
        observations={"status_code": 200},
    )
    assert "field_oracle_trace" not in result


@pytest.mark.parametrize(
    "before,after,expect",
    [
        ("CANCELLED", "CANCELLED", "PASS"),
        ("CANCELLED", "PAID", "VIOLATION"),
        ("OPEN", "PAID", "INDETERMINATE"),
    ],
)
def test_forbidden_matrix(before, after, expect):
    result = evaluate_assertion(
        {
            "assertion_id": "fm",
            "kind": "forbidden_state_transition",
            "operator": "must_not_transition",
            "from_state": "CANCELLED",
            "to_state": "PAID",
        },
        observations={"before_state": before, "after_state": after},
    )
    assert result["status"] == expect


def test_rule_id_propagates_into_trace():
    result = evaluate_assertion(
        {
            "assertion_id": "rid",
            "kind": "state_transition",
            "rule_id": "bir_rule_x",
            "from_state": "A",
            "to_state": "B",
        },
        observations={"before_state": "A", "after_state": "B"},
    )
    assert result["field_oracle_trace"]["rule_id"] == "bir_rule_x"


def test_invariant_ref_propagates_when_rule_id_absent():
    result = evaluate_assertion(
        {
            "assertion_id": "inv",
            "kind": "state_transition",
            "invariant_ref": "bir_inv_y",
            "from_state": "A",
            "to_state": "B",
        },
        observations={"before_state": "A", "after_state": "B"},
    )
    assert result["field_oracle_trace"]["rule_id"] == "bir_inv_y"


@pytest.mark.parametrize(
    "status_case",
    ["PASS", "VIOLATION", "INDETERMINATE"],
)
def test_trace_status_matches_receipt(status_case):
    if status_case == "PASS":
        obs = {"before_state": "A", "after_state": "B"}
        to_state = "B"
    elif status_case == "VIOLATION":
        obs = {"before_state": "A", "after_state": "C"}
        to_state = "B"
    else:
        obs = {"before_state": "Z", "after_state": "B"}
        to_state = "B"
    result = evaluate_assertion(
        {
            "assertion_id": "ts",
            "kind": "state_transition",
            "from_state": "A",
            "to_state": to_state,
        },
        observations=obs,
    )
    assert result["status"] == status_case
    assert result["field_oracle_trace"]["status"] == status_case


def test_conservation_trace_keeps_before_after_values():
    result = evaluate_assertion(
        {
            "assertion_id": "cv",
            "kind": "conservation",
            "equation": {"operator": "unchanged_sum", "terms": ["x", "y"]},
        },
        observations={
            "before_values": {"x": 1, "y": 2},
            "after_values": {"x": 0, "y": 3},
        },
    )
    trace = validate_assertion_receipt(result)["field_oracle_trace"]
    assert trace["before_values"] == {"x": 1, "y": 2}
    assert trace["after_values"] == {"x": 0, "y": 3}


def test_missing_required_state_not_treated_as_zero():
    result = evaluate_assertion(
        {
            "assertion_id": "mz",
            "kind": "state_transition",
            "from_state": "A",
            "to_state": "B",
        },
        observations={},
    )
    assert result["status"] == "INDETERMINATE"
    assert result["reason_code"] == "STATE_TRANSITION_EVIDENCE_MISSING"
    assert result["field_oracle_trace"]["status"] == "INDETERMINATE"


def test_unknown_state_fail_closed_with_trace():
    result = evaluate_assertion(
        {
            "assertion_id": "unk",
            "kind": "state_transition",
            "from_state": "unknown_state",
            "to_state": "PAID",
        },
        observations={"before_state": "A", "after_state": "PAID"},
    )
    assert result["reason_code"] == "STATE_RULE_PRECONDITION_NOT_ESTABLISHED"
    assert "field_oracle_trace" in result


def test_state_protocol_attaches_rule_id():
    result = compile_family_protocol(
        risk_family="state",
        operation=_write_op(),
        operation_ref="op_pay",
        control_actor_ref="buyer",
        treatment_actor_ref="buyer",
        property_spec={
            "invariant_ref": "bir_84f580d31e814d01",
            "expression": {
                "kind": "forbidden_state_transition",
                "operator": "must_not_transition",
                "operands": [
                    {"entity_ref": "order", "from_state": "CANCELLED", "to_state": "PAID"}
                ],
            },
        },
    )
    assert result["assertion"]["rule_id"] == "bir_84f580d31e814d01"


@pytest.mark.parametrize("family", ["state", "conservation"])
def test_deep_families_require_observers(family):
    result = compile_family_protocol(
        risk_family=family,
        operation=_write_op(),
        operation_ref="op_pay",
        control_actor_ref="a",
        treatment_actor_ref="a",
        property_spec={
            "invariant_ref": "bir_x",
            "expression": (
                {
                    "kind": "forbidden_state_transition",
                    "operator": "must_not_transition",
                    "operands": [{"from_state": "A", "to_state": "B"}],
                }
                if family == "state"
                else {
                    "kind": "data_conservation",
                    "operands": [{"field": "q", "field_id": "cf_q"}],
                    "equation": {"operator": "unchanged_sum", "terms": ["cf_q"]},
                }
            ),
        },
    )
    assert result["status"] == "COMPILED"
    observers = {row["observer_id"] for row in result.get("observers") or []}
    assert observers & {"before_state", "after_state", "entity_state"}


def test_no_second_dispatcher_in_contract_oracles_module():
    # The single oracle dispatcher now lives in the outcome mechanics module;
    # the public facade re-exports it. The check that no alternate dispatcher
    # (field_oracle_v2 / alternate_oracle) sneaks back in, and that
    # evaluate_assertion remains the single assertion entry, applies to the
    # module that actually evaluates.
    import ai_test_asset_center._contract_oracles_mechanics as mod

    src = open(mod.__file__, encoding="utf-8").read()
    assert "field_oracle_v2" not in src
    assert "alternate_oracle" not in src
    assert "evaluate_assertion(" in src


def test_invocation_receipt_trace_balance_unit():
    receipts = []
    for i in range(3):
        raw = evaluate_assertion(
            {
                "assertion_id": f"bal{i}",
                "kind": "field_delta",
                "fields": [{"field": "n", "expected_delta": 0}],
            },
            observations={"before_values": {"n": 1}, "after_values": {"n": 1}},
        )
        receipts.append(validate_assertion_receipt(raw))
    assert len(receipts) == 3
    assert sum(1 for r in receipts if r.get("field_oracle_trace")) == 3


@pytest.mark.parametrize("delta", [-1, 0, 1, 2])
def test_field_delta_trace_matrix(delta):
    result = evaluate_assertion(
        {
            "assertion_id": f"fd{delta}",
            "kind": "field_delta",
            "fields": [{"field": "qty", "expected_delta": delta}],
        },
        observations={
            "before_values": {"qty": 5},
            "after_values": {"qty": 5 + delta},
        },
    )
    assert result["status"] == "PASS"
    assert result["field_oracle_trace"]["kind"] == "field_delta"
