from __future__ import annotations


def _create() -> dict:
    return {"id": "create-order", "method": "POST", "path": "/api/orders"}


def test_two_same_collection_delete_routes_are_ambiguous() -> None:
    from ai_test_asset_center.cleanup_operation_authority import resolve_cleanup_operation

    receipt = resolve_cleanup_operation(
        _create(),
        behavior_ir={
            "operations": [
                _create(),
                {"id": "delete-a", "method": "DELETE", "path": "/api/orders/{id}"},
                {"id": "delete-b", "method": "DELETE", "path": "/api/orders/{orderId}"},
            ],
            "relations": [],
        },
    )

    assert receipt["status"] == "UNRESOLVED"
    assert receipt["reason_code"] == "CLEANUP_DELETE_ROUTE_AMBIGUOUS"
    assert receipt["candidate_operation_ids"] == ["delete-a", "delete-b"]
    assert receipt["source_order_selection_allowed"] is False


def test_source_less_compensates_relation_is_not_cleanup_authority() -> None:
    from ai_test_asset_center.cleanup_operation_authority import resolve_cleanup_operation

    receipt = resolve_cleanup_operation(
        _create(),
        behavior_ir={
            "operations": [
                _create(),
                {"id": "cancel-order", "method": "POST", "path": "/api/orders/cancel"},
            ],
            "relations": [
                {
                    "relation_type": "compensates",
                    "operation_ref": "cancel-order",
                    "to_ref": "create-order",
                    "status": "accepted",
                    "source_refs": [],
                }
            ],
        },
    )

    assert receipt["status"] == "UNRESOLVED"
    assert receipt["reason_code"] == "CLEANUP_OPERATION_MISSING"


def test_source_backed_explicit_compensator_wins_over_structural_delete() -> None:
    from ai_test_asset_center.cleanup_operation_authority import resolve_cleanup_operation

    receipt = resolve_cleanup_operation(
        _create(),
        behavior_ir={
            "operations": [
                _create(),
                {"id": "cancel-order", "method": "POST", "path": "/api/orders/cancel"},
                {"id": "delete-order", "method": "DELETE", "path": "/api/orders/{id}"},
            ],
            "relations": [
                {
                    "relation_type": "compensates",
                    "operation_ref": "cancel-order",
                    "to_ref": "create-order",
                    "status": "accepted",
                    "source_refs": [{"source_id": "api-doc"}],
                }
            ],
        },
    )

    assert receipt["status"] == "RESOLVED"
    assert receipt["authority"] == "source_compensates_relation"
    assert receipt["cleanup_operation"]["operation_ref"] == "cancel-order"


def test_bulk_delete_is_never_automatic_fixture_cleanup() -> None:
    from ai_test_asset_center.cleanup_operation_authority import resolve_cleanup_operation

    receipt = resolve_cleanup_operation(
        _create(),
        behavior_ir={
            "operations": [
                _create(),
                {"id": "delete-all", "method": "DELETE", "path": "/api/orders"},
            ],
            "relations": [],
        },
    )

    assert receipt["status"] == "UNRESOLVED"
    assert receipt["reason_code"] == "CLEANUP_OPERATION_MISSING"
