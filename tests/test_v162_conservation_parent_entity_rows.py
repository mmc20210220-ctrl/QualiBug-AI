"""V1.6.2: parent entity with embedded children must not lose conservation fields."""
from __future__ import annotations

from ai_test_asset_center.assertion_dsl_base import evaluate_assertion
from ai_test_asset_center.observer_contracts_base import (
    _entity_rows,
    _numeric_snapshot_values,
    _observe_entity_state,
)


def _order_body(*, status: str = "SHIPPED") -> dict:
    return {
        "id": "ord-1",
        "status": status,
        "total_amount": "6999.00",
        "discount_amount": "100.00",
        "payable_amount": "6899.00",
        "items": [
            {
                "id": "line-1",
                "order_id": "ord-1",
                "price": "6999.00",
                "qty": 1,
                "line_amount": "6999.00",
            }
        ],
    }


def test_entity_rows_keeps_parent_when_items_are_embedded_children():
    rows = _entity_rows(_order_body())
    assert rows, "expected at least the parent entity row"
    assert rows[0]["id"] == "ord-1"
    assert rows[0]["discount_amount"] == "100.00"
    assert any(row.get("id") == "line-1" for row in rows)


def test_entity_rows_keeps_parent_with_named_identity_field():
    body = _order_body()
    body["order_id"] = body.pop("id")

    rows = _entity_rows(body)

    assert rows[0]["order_id"] == "ord-1"
    assert rows[0]["discount_amount"] == "100.00"


def test_entity_rows_does_not_treat_business_word_ending_id_as_identity():
    rows = _entity_rows(
        {
            "paid": 1,
            "items": [{"id": "line-1", "quantity": 1}],
        }
    )

    assert rows == [{"id": "line-1", "quantity": 1}]


def test_entity_rows_rejects_boolean_identity_values():
    rows = _entity_rows(
        {
            "order_id": True,
            "status": "SHIPPED",
            "items": [{"id": "line-1", "quantity": 1}],
        }
    )

    assert rows == [{"id": "line-1", "quantity": 1}]


def test_entity_rows_still_unwraps_collection_envelope():
    rows = _entity_rows(
        {
            "items": [{"id": "a", "qty": 1}, {"id": "b", "qty": 2}],
            "total": 2,
        }
    )
    assert [row["id"] for row in rows] == ["a", "b"]


def test_numeric_snapshot_reads_parent_fields_despite_items_array():
    vals = _numeric_snapshot_values(
        _order_body(),
        ["discount_amount", "total_amount"],
    )
    assert vals == {"discount_amount": 100.0, "total_amount": 6999.0}


def test_numeric_snapshot_still_reads_nested_line_terms():
    vals = _numeric_snapshot_values(_order_body(), ["qty", "line_amount"])
    assert vals == {"line_amount": 6999.0, "qty": 1}


def test_entity_state_observes_conservation_values_on_parent_with_items():
    experiment = {
        "assertions": [
            {
                "kind": "conservation",
                "equation": {
                    "operator": "unchanged_sum",
                    "terms": ["discount_amount"],
                },
            }
        ],
        "observers": [
            {
                "observer_id": "entity_state",
                "required_field_ids": ["discount_amount"],
            }
        ],
    }
    step = {
        "phase": "treatment",
        "method": "POST",
        "path": "/api/orders/ord-1/ship",
        "governance_receipt": {
            "before": {"status": 200, "body": _order_body(status="PAID")},
            "after": {"status": 200, "body": _order_body(status="SHIPPED")},
            "write": {"status": 200, "body": {"ok": True}},
        },
    }
    receipt = _observe_entity_state([step], experiment=experiment)
    assert receipt["status"] == "OBSERVED"
    assert receipt["reason_code"] == ""
    evidence = receipt["evidence"]
    assert evidence["before_values"] == {"discount_amount": 100.0}
    assert evidence["after_values"] == {"discount_amount": 100.0}


def test_conservation_assertion_determines_when_parent_values_present():
    assertion = {
        "assertion_id": "assert_conservation",
        "kind": "conservation",
        "equation": {"operator": "unchanged_sum", "terms": ["discount_amount"]},
    }
    result = evaluate_assertion(
        assertion,
        observations={
            "before_values": {"discount_amount": 100.0},
            "after_values": {"discount_amount": 100.0},
        },
    )
    assert result["status"] == "PASS"
    assert result["passed"] is True
    assert result.get("actual", {}).get("before_sum") == 100.0
    assert result.get("actual", {}).get("after_sum") == 100.0


def test_entity_state_fail_closed_when_terms_absent_from_snapshots():
    experiment = {
        "assertions": [
            {
                "kind": "conservation",
                "equation": {
                    "operator": "unchanged_sum",
                    "terms": ["missing_balance"],
                },
            }
        ],
    }
    step = {
        "phase": "treatment",
        "method": "POST",
        "governance_receipt": {
            "before": {"status": 200, "body": {"id": "x", "status": "OPEN"}},
            "after": {"status": 200, "body": {"id": "x", "status": "CLOSED"}},
            "write": {"status": 200, "body": {}},
        },
    }
    receipt = _observe_entity_state([step], experiment=experiment)
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "CONSERVATION_VALUES_MISSING"
    assert receipt["evidence"]["conservation_terms"] == ["missing_balance"]
