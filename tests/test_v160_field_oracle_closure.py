"""V1.6.0 field-level oracle closure — specialized unit tests (Stage A).

Industry-neutral fixtures only. No benchmark GT, no invented business formulas.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.assertion_dsl_base import evaluate_assertion
from ai_test_asset_center.experiment_protocols_base import compile_family_protocol
from ai_test_asset_center.observer_contracts_base import _numeric_snapshot_values
from ai_test_asset_center.oracle_expression_resolver import resolve_expression_from_invariant


def _op() -> dict:
    return {
        "operation_id": "op_write",
        "method": "POST",
        "path": "/api/resource",
        "request_schema": {
            "type": "object",
            "properties": {"qty": {"type": "number"}, "status": {"type": "string"}},
        },
        "request_examples": [{"qty": 1, "status": "OPEN"}],
    }


def _compile_conservation(property_spec: dict) -> dict:
    return compile_family_protocol(
        risk_family="conservation",
        operation=_op(),
        operation_ref="op_write",
        control_actor_ref="a1",
        treatment_actor_ref="a1",
        property_spec=property_spec,
    )


def test_numeric_snapshot_empty_terms_fail_closed():
    body = {"available_qty": 10, "locked_qty": 2, "noise": 99}
    assert _numeric_snapshot_values(body, []) == {}


def test_numeric_snapshot_required_terms_only():
    body = {"available_qty": 10, "locked_qty": 2, "noise": 99}
    vals = _numeric_snapshot_values(body, ["available_qty"])
    assert vals == {"available_qty": 10}
    assert "noise" not in vals


def test_numeric_snapshot_unscoped_fingerprint_opt_in():
    body = {"available_qty": 10, "locked_qty": 2}
    vals = _numeric_snapshot_values(body, [], allow_unscoped_numeric=True)
    assert vals["available_qty"] == 10
    assert vals["locked_qty"] == 2


def test_conservation_protocol_blocks_empty_terms():
    result = _compile_conservation(
        {"template": "sum must hold", "expression": {"kind": "conservation", "raw": "x=y"}},
    )
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_EMPTY_CONSERVATION_TERMS"


def test_conservation_protocol_compiles_with_field_operands():
    result = _compile_conservation(
        {
            "template": "qty conserved",
            "expression": {
                "kind": "conservation",
                "operator": "unchanged_sum",
                "operands": [
                    {"field_id": "cf_qty", "field": "qty"},
                    {"field_id": "cf_locked", "field": "locked_qty"},
                ],
            },
        },
    )
    assert result["status"] == "COMPILED"
    terms = result["assertion"]["equation"]["terms"]
    # V1.6.1: equation terms normalize to JSON field names for observer/oracle key alignment.
    assert "qty" in terms
    assert "locked_qty" in terms
    assert "cf_qty" not in terms
    assert "cf_locked" not in terms


def test_unknown_state_transition_not_any_change_pass():
    result = evaluate_assertion(
        {
            "assertion_id": "a1",
            "kind": "state_transition",
            "from_state": "unknown_state",
            "to_state": "unknown_state",
        },
        observations={"before_state": "A", "after_state": "B"},
    )
    assert result.get("passed") is not True
    assert result.get("reason_code") == "STATE_RULE_PRECONDITION_NOT_ESTABLISHED"


def test_postcondition_without_field_not_any_change_pass():
    result = evaluate_assertion(
        {
            "assertion_id": "a2",
            "kind": "postcondition",
            "operator": "must_hold",
            "operands": [{"entity_ref": "Order", "field": "", "expected_value": None}],
        },
        observations={
            "entity_state_observed": True,
            "state_change_count": 3,
            "effect_count": 2,
        },
    )
    assert result.get("passed") is not True
    assert result.get("reason_code") == "FIELD_LEVEL_RULE_NOT_EXECUTABLE"


def test_postcondition_value_match_with_field_evidence():
    result = evaluate_assertion(
        {
            "assertion_id": "a3",
            "kind": "postcondition",
            "operands": [{
                "entity_ref": "Order",
                "field": "status",
                "expected_value": "PAID",
            }],
        },
        observations={
            "entity_state_observed": True,
            "state_change_count": 1,
            "after_values": {"status": "PAID"},
        },
    )
    assert result.get("status") == "PASS"
    assert result.get("passed") is True


def test_postcondition_fingerprint_not_substitute_for_field_evidence():
    result = evaluate_assertion(
        {
            "assertion_id": "a4",
            "kind": "postcondition",
            "operands": [{
                "entity_ref": "Order",
                "field": "status",
                "expected_value": "PAID",
            }],
        },
        observations={
            "entity_state_observed": True,
            "state_change_count": 5,
            "effect_count": 5,
        },
    )
    assert result.get("passed") is not True
    assert result.get("reason_code") == "POSTCONDITION_FIELD_EVIDENCE_MISSING"


def test_resolver_skips_nl_guess_when_structure_present():
    out = resolve_expression_from_invariant(
        {
            "expression": {
                "raw": "available equals reserved",
                "operands": [{"field": "available_qty"}],
            }
        },
        {"entities": [{"entity_id": "e1", "name": "Inventory", "fields": []}]},
    )
    assert out["status"] == "UNRESOLVED"
    assert out["error_code"] == "STRUCTURED_EXPRESSION_PRESENT_SKIP_NL_GUESS"


def test_field_delta_requires_fields():
    result = evaluate_assertion(
        {"assertion_id": "a5", "kind": "field_delta", "fields": []},
        observations={"before_values": {"qty": 1}, "after_values": {"qty": 2}},
    )
    assert result.get("reason_code") == "FIELD_DELTA_NO_FIELDS_SPECIFIED"


def test_conservation_empty_terms_blocked_at_eval():
    result = evaluate_assertion(
        {
            "assertion_id": "a6",
            "kind": "conservation",
            "equation": {"operator": "unchanged_sum", "terms": []},
        },
        observations={"before_values": {"a": 1}, "after_values": {"a": 1}},
    )
    assert result.get("reason_code") == "BLOCKED_EMPTY_CONSERVATION_TERMS"


def test_field_oracle_trace_emitted_for_state_transition():
    result = evaluate_assertion(
        {
            "assertion_id": "trace1",
            "kind": "state_transition",
            "from_state": "OPEN",
            "to_state": "CLOSED",
        },
        observations={"before_state": "OPEN", "after_state": "CLOSED"},
    )
    assert result["status"] == "PASS"
    trace = result.get("field_oracle_trace") or {}
    assert trace.get("schema_version") == "qualibug.field-oracle-trace.v1"
    assert trace.get("kind") == "state_transition"
    assert trace.get("status") == "PASS"


@pytest.mark.parametrize(
    "from_state,to_state",
    [
        ("unknown_state", "PAID"),
        ("UNKNOWN", "PAID"),
        ("", "PAID"),
        ("OPEN", "unknown_state"),
        ("OPEN", "unknown"),
        ("OPEN", ""),
    ],
)
def test_state_placeholders_fail_closed(from_state, to_state):
    from ai_test_asset_center.experiment_compiler_obligation import (
        _field_level_rule_completeness_gate,
    )

    gate = _field_level_rule_completeness_gate(
        family="state",
        assertion_kind="state_transition",
        protocol_assertion={"from_state": from_state, "to_state": to_state},
        prop={},
        observers=[{"observer_id": "before_state"}, {"observer_id": "after_state"}],
        cleanup_plan=[{"action": "delete"}],
        is_write=True,
    )
    assert gate is not None
    assert gate[0] == "STATE_RULE_PRECONDITION_NOT_ESTABLISHED"


@pytest.mark.parametrize(
    "terms",
    [
        [],
        None,
    ],
)
def test_conservation_gate_empty_terms(terms):
    from ai_test_asset_center.experiment_compiler_obligation import (
        _field_level_rule_completeness_gate,
    )

    equation = {"operator": "unchanged_sum"}
    if terms is not None:
        equation["terms"] = terms
    gate = _field_level_rule_completeness_gate(
        family="conservation",
        assertion_kind="conservation",
        protocol_assertion={"equation": equation},
        prop={},
        observers=[{"observer_id": "entity_state"}],
        cleanup_plan=[{"action": "delete"}],
        is_write=True,
    )
    assert gate is not None
    assert gate[0] == "BLOCKED_EMPTY_CONSERVATION_TERMS"


@pytest.mark.parametrize(
    "body,terms,expected_keys",
    [
        ({"a": 1, "b": 2, "c": 3}, ["a"], {"a"}),
        ({"a": 1, "b": 2, "c": 3}, ["b", "c"], {"b", "c"}),
        ({"a": 1, "b": 2}, ["missing"], set()),
        ({"qty": "5", "other": 9}, ["qty"], {"qty"}),
        ({"flag": True, "n": 3}, ["flag"], set()),
        ({"id": 7, "n": 3}, ["id"], set()),
        ({"user_id": 1, "n": 3}, ["user_id"], set()),
        ({"nested": [{"n": 4}]}, ["n"], set()),
    ],
)
def test_numeric_snapshot_term_filtering_matrix(body, terms, expected_keys):
    vals = _numeric_snapshot_values(body, terms)
    assert set(vals) == expected_keys


@pytest.mark.parametrize(
    "name,expected",
    [
        ("available_qty", "QUANTITY_BALANCE"),
        ("reserved_qty", "QUANTITY_BALANCE"),
        ("locked_qty", "QUANTITY_BALANCE"),
        ("adjust_qty", "QUANTITY_DELTA"),
        ("change_amount", "AMOUNT_DELTA"),
        ("payable_amount", "AMOUNT_BALANCE"),
        ("status", "STATE"),
        ("is_active", "BOOLEAN_FLAG"),
        ("created_at", "TIMESTAMP"),
        ("version", "VERSION"),
        ("tenant_id", "TENANT_ID"),
        ("owner_id", "OWNER_ID"),
        ("idempotency_key", "IDEMPOTENCY_KEY"),
    ],
)
def test_balance_delta_semantic_classification(name, expected):
    from ai_test_asset_center.behavior_ir import _classify_field_semantics

    semantic, _confidence = _classify_field_semantics(name, data_type="number")
    assert semantic == expected


@pytest.mark.parametrize(
    "operands,expect_block",
    [
        ([], True),
        ([{"field": ""}], True),
        ([{"field": "status", "expected_value": "PAID"}], False),
        ([{"must_create": True}], False),
        ([{"field_id": "cf_status", "expected_value": "X"}], False),
    ],
)
def test_postcondition_completeness_gate(operands, expect_block):
    from ai_test_asset_center.experiment_compiler_obligation import (
        _field_level_rule_completeness_gate,
    )

    gate = _field_level_rule_completeness_gate(
        family="state",
        assertion_kind="postcondition",
        protocol_assertion={"operands": operands},
        prop={"expression": {"kind": "postcondition", "operands": operands}},
        observers=[{"observer_id": "entity_state"}],
        cleanup_plan=[{"action": "delete"}],
        is_write=True,
    )
    if expect_block:
        assert gate is not None
        assert gate[0] == "FIELD_LEVEL_RULE_NOT_EXECUTABLE"
    else:
        assert gate is None


@pytest.mark.parametrize(
    "kind",
    ["http_status", "http_status_class", "authorization", "validation_rejection"],
)
def test_shallow_kinds_bypass_field_completeness_gate(kind):
    from ai_test_asset_center.experiment_compiler_obligation import (
        _field_level_rule_completeness_gate,
    )

    gate = _field_level_rule_completeness_gate(
        family="authorization",
        assertion_kind=kind,
        protocol_assertion={},
        prop={},
        observers=[{"observer_id": "http_response"}],
        cleanup_plan=[],
        is_write=True,
    )
    assert gate is None


@pytest.mark.parametrize(
    "before,after,terms,expect_pass",
    [
        ({"a": 1, "b": 2}, {"a": 1, "b": 2}, ["a", "b"], True),
        ({"a": 1, "b": 2}, {"a": 0, "b": 3}, ["a", "b"], True),
        ({"a": 1, "b": 2}, {"a": 1, "b": 3}, ["a", "b"], False),
        ({"a": 5}, {"a": 5}, ["a"], True),
        ({"a": 5}, {"a": 6}, ["a"], False),
    ],
)
def test_conservation_unchanged_sum_matrix(before, after, terms, expect_pass):
    result = evaluate_assertion(
        {
            "assertion_id": "cons_m",
            "kind": "conservation",
            "equation": {"operator": "unchanged_sum", "terms": terms},
        },
        observations={"before_values": before, "after_values": after},
    )
    if expect_pass:
        assert result["status"] == "PASS"
    else:
        assert result["status"] == "VIOLATION"


@pytest.mark.parametrize(
    "field,before,after,expected_delta,expect_pass",
    [
        ("qty", 10, 7, -3, True),
        ("qty", 10, 8, -3, False),
        ("qty", 10, 10, 0, True),
        ("amount", 100, 140, 40, True),
        ("amount", 100, 139, 40, False),
    ],
)
def test_field_delta_matrix(field, before, after, expected_delta, expect_pass):
    result = evaluate_assertion(
        {
            "assertion_id": "fd_m",
            "kind": "field_delta",
            "fields": [{"field": field, "expected_delta": expected_delta}],
        },
        observations={
            "before_values": {field: before},
            "after_values": {field: after},
        },
    )
    if expect_pass:
        assert result["status"] == "PASS"
    else:
        assert result["status"] == "VIOLATION"


@pytest.mark.parametrize(
    "observed,expected,expect_pass",
    [
        ("PAID", "PAID", True),
        ("paid", "PAID", True),
        ("OPEN", "PAID", False),
        ("CANCELLED", "PAID", False),
    ],
)
def test_postcondition_value_matrix(observed, expected, expect_pass):
    result = evaluate_assertion(
        {
            "assertion_id": "pc_m",
            "kind": "postcondition",
            "operands": [{"field": "status", "expected_value": expected}],
        },
        observations={
            "entity_state_observed": True,
            "state_change_count": 1,
            "after_values": {"status": observed},
        },
    )
    if expect_pass:
        assert result["status"] == "PASS"
    else:
        assert result.get("reason_code") == "POSTCONDITION_VALUE_MISMATCH"
        assert result["status"] == "VIOLATION"


def test_canonical_field_id_prefix():
    from ai_test_asset_center.behavior_ir import _canonical_field_id

    fid = _canonical_field_id("cf", "Order", "payable_amount")
    assert fid.startswith("cf_")


def test_conservation_missing_observer_gate():
    from ai_test_asset_center.experiment_compiler_obligation import (
        _field_level_rule_completeness_gate,
    )

    gate = _field_level_rule_completeness_gate(
        family="conservation",
        assertion_kind="conservation",
        protocol_assertion={"equation": {"operator": "unchanged_sum", "terms": ["qty"]}},
        prop={},
        observers=[{"observer_id": "http_response"}],
        cleanup_plan=[{"action": "delete"}],
        is_write=True,
    )
    assert gate is not None
    assert gate[0] == "FIELD_LEVEL_RULE_NOT_EXECUTABLE"
    assert "observer" in gate[1]


def test_conservation_missing_cleanup_gate():
    from ai_test_asset_center.experiment_compiler_obligation import (
        _field_level_rule_completeness_gate,
    )

    gate = _field_level_rule_completeness_gate(
        family="conservation",
        assertion_kind="conservation",
        protocol_assertion={"equation": {"operator": "unchanged_sum", "terms": ["qty"]}},
        prop={},
        observers=[{"observer_id": "entity_state"}],
        cleanup_plan=[],
        is_write=True,
    )
    assert gate is not None
    assert gate[0] == "FIELD_LEVEL_RULE_NOT_EXECUTABLE"
    assert "cleanup" in gate[1]


def test_field_delta_missing_operands_gate():
    from ai_test_asset_center.experiment_compiler_obligation import (
        _field_level_rule_completeness_gate,
    )

    gate = _field_level_rule_completeness_gate(
        family="state",
        assertion_kind="field_delta",
        protocol_assertion={"fields": []},
        prop={},
        observers=[{"observer_id": "entity_state"}],
        cleanup_plan=[{"action": "delete"}],
        is_write=True,
    )
    assert gate is not None
    assert gate[0] == "FIELD_LEVEL_RULE_NOT_EXECUTABLE"


def test_http_status_has_no_field_oracle_trace():
    result = evaluate_assertion(
        {"assertion_id": "http1", "kind": "http_status", "expected": 200},
        observations={"status_code": 200},
    )
    assert result["status"] == "PASS"
    assert "field_oracle_trace" not in result
