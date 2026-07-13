from __future__ import annotations

import ai_test_asset_center.experiment_executor as executor
from ai_test_asset_center.real_id_resolver import bind_entity_fields


def _body() -> dict:
    return {
        "data": [
            {
                "id": "order-3",
                "orderStatus": "pending-payment",
            },
            {
                "id": "order-2",
                "orderStatus": "PAID",
            },
            {
                "id": "order-1",
                "orderStatus": "PENDING_PAYMENT",
            },
        ]
    }


def test_public_binding_entrypoint_selects_documented_source_state() -> None:
    bindings = bind_entity_fields(
        _body(),
        "@state=pendingpayment@/orders/{id}/pay",
    )

    assert bindings["id"] == "order-1"


def test_main_executor_import_uses_state_aware_binding_entrypoint() -> None:
    bindings = executor.bind_entity_fields(
        _body(),
        "@state=paid@/orders/{id}/cancel",
    )

    assert bindings["id"] == "order-2"


def test_state_binding_fails_closed_when_no_entity_matches() -> None:
    assert bind_entity_fields(
        _body(),
        "@state=cancelled@/orders/{id}/pay",
    ) == {}


def test_normal_binding_behavior_remains_unchanged() -> None:
    assert bind_entity_fields(
        {"data": [{"id": "order-9", "status": "PAID"}]},
        "/orders/{id}",
    )["id"] == "order-9"
