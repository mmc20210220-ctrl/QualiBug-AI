from __future__ import annotations


def _create() -> dict:
    return {"id": "create-order", "method": "POST", "path": "/api/orders"}


def test_post_compensator_relation_is_protocol_gap_without_safe_delete() -> None:
    from ai_test_asset_center.cleanup_operation_authority import resolve_cleanup_operation

    receipt = resolve_cleanup_operation(
        _create(),
        behavior_ir={
            "operations": [
                _create(),
                {
                    "id": "cancel-order",
                    "method": "POST",
                    "path": "/api/orders/cancel",
                },
            ],
            "relations": [
                {
                    "relation_type": "compensates",
                    "operation_ref": "cancel-order",
                    "to_ref": "create-order",
                    "source_refs": [{"source_id": "api-doc"}],
                    "status": "accepted",
                }
            ],
        },
    )

    assert receipt["status"] == "UNRESOLVED"
    assert receipt["reason_code"] == "CLEANUP_COMPENSATOR_PROTOCOL_UNPROVEN"
    assert receipt["candidate_operation_ids"] == ["cancel-order"]
    assert receipt["unsupported_compensators"][0]["method"] == "POST"
    assert receipt["unsupported_compensators"][0]["executor_protocol_supported"] is False


def test_identity_free_delete_compensator_is_also_protocol_gap() -> None:
    from ai_test_asset_center.cleanup_operation_authority import resolve_cleanup_operation

    receipt = resolve_cleanup_operation(
        _create(),
        behavior_ir={
            "operations": [
                _create(),
                {
                    "id": "delete-current-order",
                    "method": "DELETE",
                    "path": "/api/orders/current",
                },
            ],
            "relations": [
                {
                    "relation_type": "compensates",
                    "operation_ref": "delete-current-order",
                    "to_ref": "create-order",
                    "source_refs": [{"source_id": "api-doc"}],
                    "status": "accepted",
                }
            ],
        },
    )

    assert receipt["status"] == "UNRESOLVED"
    assert receipt["reason_code"] == "CLEANUP_COMPENSATOR_PROTOCOL_UNPROVEN"
    assert receipt["unsupported_compensators"][0]["path_identity_count"] == 0


def test_unproven_post_compensator_can_fall_back_to_unique_identity_delete() -> None:
    from ai_test_asset_center.cleanup_operation_authority import resolve_cleanup_operation

    receipt = resolve_cleanup_operation(
        _create(),
        behavior_ir={
            "operations": [
                _create(),
                {
                    "id": "cancel-order",
                    "method": "POST",
                    "path": "/api/orders/cancel",
                },
                {
                    "id": "delete-order",
                    "method": "DELETE",
                    "path": "/api/orders/{id}",
                },
            ],
            "relations": [
                {
                    "relation_type": "compensates",
                    "operation_ref": "cancel-order",
                    "to_ref": "create-order",
                    "source_refs": [{"source_id": "api-doc"}],
                    "status": "accepted",
                }
            ],
        },
    )

    assert receipt["status"] == "RESOLVED"
    assert receipt["authority"] == "identity_bound_same_collection_delete"
    assert receipt["cleanup_operation"]["operation_ref"] == "delete-order"
    assert receipt["unsupported_compensators"][0]["operation_ref"] == "cancel-order"


def test_source_backed_identity_delete_compensator_is_executable_authority() -> None:
    from ai_test_asset_center.cleanup_operation_authority import resolve_cleanup_operation

    receipt = resolve_cleanup_operation(
        _create(),
        behavior_ir={
            "operations": [
                _create(),
                {
                    "id": "delete-order",
                    "method": "DELETE",
                    "path": "/api/orders/{orderId}",
                },
            ],
            "relations": [
                {
                    "relation_type": "compensates",
                    "operation_ref": "delete-order",
                    "to_ref": "create-order",
                    "source_refs": [{"source_id": "api-doc"}],
                    "status": "accepted",
                }
            ],
        },
    )

    assert receipt["status"] == "RESOLVED"
    assert receipt["authority"] == "source_compensates_relation"
    assert receipt["cleanup_operation"]["request_materialization_authority"] == (
        "identity_bound_path_delete"
    )
