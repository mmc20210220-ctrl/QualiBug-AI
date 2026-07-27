"""V1.6.2: parent entity mutations must require cleanup despite embedded items[]."""
from __future__ import annotations

from ai_test_asset_center.experiment_cleanup import (
    _entity_rows,
    _governed_write_changed_state,
    _single_entity_for_restoration,
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


def _attempt(*, write_body: dict, before_status: str, after_status: str) -> dict:
    return {
        "accepted": True,
        "before": {"status": 200, "body": _order_body(status=before_status)},
        "after": {"status": 200, "body": _order_body(status=after_status)},
        "write": {"status": 200, "body": write_body},
    }


def test_cleanup_entity_rows_keeps_parent_when_items_are_embedded_children():
    rows = _entity_rows(_order_body())
    assert rows[0]["id"] == "ord-1"
    assert rows[0]["status"] == "SHIPPED"
    assert any(row.get("id") == "line-1" for row in rows)


def test_cleanup_entity_rows_still_unwraps_collection_envelope():
    rows = _entity_rows(
        {
            "items": [{"id": "a", "qty": 1}, {"id": "b", "qty": 2}],
            "total": 2,
        }
    )
    assert [row["id"] for row in rows] == ["a", "b"]


def test_single_entity_prefers_primary_id_over_foreign_key_match():
    entity = _single_entity_for_restoration(_order_body(), {"ord-1"})
    assert entity.get("id") == "ord-1"
    assert entity.get("status") == "SHIPPED"
    assert "items" in entity


def test_parent_status_mutation_requires_cleanup_even_when_write_body_has_no_id():
    # Pre-fix failure mode: empty write identities + single line-item unwrap
    # compared only the unchanged child and returned False.
    assert (
        _governed_write_changed_state(
            _attempt(
                write_body={"ok": True},
                before_status="SHIPPED",
                after_status="COMPLETED",
            )
        )
        is True
    )


def test_parent_status_mutation_requires_cleanup_when_write_echoes_order_id():
    assert (
        _governed_write_changed_state(
            _attempt(
                write_body={"id": "ord-1", "ok": True},
                before_status="SHIPPED",
                after_status="COMPLETED",
            )
        )
        is True
    )


def test_honestly_unchanged_parent_with_items_still_not_required():
    assert (
        _governed_write_changed_state(
            _attempt(
                write_body={"id": "ord-1", "ok": True},
                before_status="SHIPPED",
                after_status="SHIPPED",
            )
        )
        is False
    )


def test_amount_mutation_on_parent_requires_cleanup():
    before = _order_body(status="PAID")
    after = _order_body(status="PAID")
    after["discount_amount"] = "0.00"
    after["payable_amount"] = "6999.00"
    attempt = {
        "accepted": True,
        "before": {"status": 200, "body": before},
        "after": {"status": 200, "body": after},
        "write": {"status": 200, "body": {"id": "ord-1"}},
    }
    assert _governed_write_changed_state(attempt) is True
