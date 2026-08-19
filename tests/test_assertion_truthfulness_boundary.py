from __future__ import annotations

from ai_test_asset_center.assertion_dsl import evaluate_assertion


def test_framework_generic_404_is_indeterminate_not_violation() -> None:
    # A framework-level route-not-found (Starlette/FastAPI {"detail": "Not
    # Found"}) proves nothing about the target's business behavior: the request
    # never reached a handler. Cross-service routing artifacts must never be
    # reported as authorization defects.
    receipt = evaluate_assertion(
        {
            "assertion_id": "status-404fw",
            "kind": "http_status_class",
            "expected": 2,
            "authorization_semantics": "authorization",
        },
        observations={
            "status_code": 404,
            "body": {"detail": "Not Found"},
        },
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["passed"] is None
    assert receipt["reason_code"] == "HTTP_ROUTE_NOT_FOUND_INDETERMINATE"


def test_framework_generic_404_raw_body_is_indeterminate() -> None:
    receipt = evaluate_assertion(
        {
            "assertion_id": "status-404raw",
            "kind": "http_status_class",
            "expected": 2,
        },
        observations={
            "status_code": 404,
            "body": '{"detail": "Not Found"}',
        },
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "HTTP_ROUTE_NOT_FOUND_INDETERMINATE"


def test_business_404_remains_violation() -> None:
    # A 404 with a specific business detail is the target's own response and
    # keeps its pre-existing semantics.
    receipt = evaluate_assertion(
        {
            "assertion_id": "status-404biz",
            "kind": "http_status_class",
            "expected": 2,
            "authorization_semantics": "authorization",
        },
        observations={
            "status_code": 404,
            "body": {"detail": "order 5 not found"},
        },
    )
    assert receipt["status"] == "VIOLATION"
    assert receipt["passed"] is False


def test_postcondition_missing_field_evidence_is_not_violation() -> None:
    receipt = evaluate_assertion(
        {
            "assertion_id": "post-1",
            "kind": "postcondition",
            "operator": "must_become",
            "operands": [
                {
                    "entity_ref": "order",
                    "field": "status",
                    "expected_value": "PAID",
                }
            ],
        },
        observations={
            "entity_state_observed": True,
            "state_change_count": 1,
            "effect_count": 1,
        },
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["passed"] is None
    assert receipt["reason_code"] == "POSTCONDITION_FIELD_EVIDENCE_MISSING"


def test_field_delta_missing_operand_is_not_violation() -> None:
    receipt = evaluate_assertion(
        {
            "assertion_id": "delta-1",
            "kind": "field_delta",
            "fields": [{"field": "qty", "expected_delta": -1}],
        },
        observations={
            "before_values": {"qty": 2},
            "after_values": {},
        },
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["passed"] is None
    assert receipt["reason_code"] == "FIELD_DELTA_EVIDENCE_MISSING"
    assert receipt["actual"]["field_results"] == [
        {"field": "qty", "result": "MISSING"}
    ]


def test_field_delta_real_mismatch_remains_violation() -> None:
    receipt = evaluate_assertion(
        {
            "assertion_id": "delta-2",
            "kind": "field_delta",
            "fields": [{"field": "qty", "expected_delta": -1}],
        },
        observations={
            "before_values": {"qty": 2},
            "after_values": {"qty": 2},
        },
    )

    assert receipt["status"] == "VIOLATION"
    assert receipt["passed"] is False
    assert receipt["reason_code"] == "FIELD_DELTA_MISMATCH"


def test_json_compare_missing_expected_path_is_not_violation() -> None:
    receipt = evaluate_assertion(
        {
            "assertion_id": "compare-1",
            "kind": "json_path_compare",
            "path": "$.actual",
            "expected_path": "$.limit",
            "operator": "lte",
        },
        observations={"body": {"actual": 10}},
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["passed"] is None
    assert receipt["reason_code"] == "JSON_COMPARE_EXPECTED_PATH_MISSING"


def test_row_state_filter_requires_state_evidence_for_every_returned_row() -> None:
    receipt = evaluate_assertion(
        {
            "assertion_id": "filter-1",
            "kind": "response_rows_state_filter",
            "allowed_states": ["ACTIVE"],
        },
        observations={
            "body": {"items": [{"id": "1", "name": "row-without-state"}]}
        },
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["passed"] is None
    assert receipt["reason_code"] == "ROW_STATE_FILTER_STATE_EVIDENCE_MISSING"


def test_row_state_filter_real_out_of_scope_state_remains_violation() -> None:
    receipt = evaluate_assertion(
        {
            "assertion_id": "filter-2",
            "kind": "response_rows_state_filter",
            "allowed_states": ["ACTIVE"],
        },
        observations={"body": {"items": [{"id": "1", "status": "BLOCKED"}]}},
    )

    assert receipt["status"] == "VIOLATION"
    assert receipt["passed"] is False
    assert receipt["reason_code"] == "RESPONSE_ROW_STATE_OUTSIDE_ALLOWED"
