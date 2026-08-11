from __future__ import annotations


def test_boolean_fk_without_target_has_named_missing_target_reason() -> None:
    from ai_test_asset_center.body_reference_authority import resolve_body_reference

    receipt = resolve_body_reference(
        {
            "id": "create-order",
            "request_schema": {
                "type": "object",
                "properties": {"addressId": {"type": "string", "x-foreign-key": True}},
            },
        },
        "addressId",
        behavior_ir={"entities": [], "operations": [], "relations": []},
    )

    assert receipt["status"] == "UNRESOLVED"
    assert receipt["reason_code"] == "BODY_REFERENCE_TARGET_MISSING"


def test_operation_target_needs_source_backed_entity_relation() -> None:
    from ai_test_asset_center.body_reference_authority import resolve_body_reference

    operation = {
        "id": "create-payment",
        "request_schema": {
            "type": "object",
            "properties": {
                "orderId": {
                    "type": "string",
                    "x-reference-target": "list-orders",
                }
            },
        },
    }
    behavior_ir = {
        "entities": [{"id": "entity-order", "name": "order"}],
        "operations": [
            operation,
            {"id": "list-orders", "method": "GET", "path": "/api/orders"},
        ],
        "relations": [
            {
                "relation_type": "observes",
                "from_ref": "list-orders",
                "to_ref": "entity-order",
                "status": "accepted",
                "source_refs": [],
            }
        ],
    }

    receipt = resolve_body_reference(operation, "orderId", behavior_ir=behavior_ir)

    assert receipt["status"] == "UNRESOLVED"
    assert receipt["reason_code"] == "BODY_REFERENCE_TARGET_UNRESOLVED"


def test_source_backed_operation_target_resolves_entity() -> None:
    from ai_test_asset_center.body_reference_authority import resolve_body_reference

    operation = {
        "id": "create-payment",
        "request_schema": {
            "type": "object",
            "properties": {
                "orderId": {
                    "type": "string",
                    "x-reference-target": "list-orders",
                }
            },
        },
    }
    behavior_ir = {
        "entities": [{"id": "entity-order", "name": "order"}],
        "operations": [
            operation,
            {"id": "list-orders", "method": "GET", "path": "/api/orders"},
        ],
        "relations": [
            {
                "relation_type": "observes",
                "from_ref": "list-orders",
                "to_ref": "entity-order",
                "status": "accepted",
                "source_refs": [{"source_id": "api-doc"}],
            }
        ],
    }

    receipt = resolve_body_reference(operation, "orderId", behavior_ir=behavior_ir)

    assert receipt["status"] == "RESOLVED"
    assert receipt["target_entity_ref"] == "entity-order"


def test_direct_entity_ref_does_not_need_an_operation_relation() -> None:
    from ai_test_asset_center.body_reference_authority import resolve_body_reference

    operation = {
        "id": "create-payment",
        "request_schema": {
            "type": "object",
            "properties": {
                "orderId": {"type": "string", "x-entity-ref": "entity-order"}
            },
        },
    }

    receipt = resolve_body_reference(
        operation,
        "orderId",
        behavior_ir={
            "entities": [{"id": "entity-order", "name": "order"}],
            "operations": [operation],
            "relations": [],
        },
    )

    assert receipt["status"] == "RESOLVED"
    assert receipt["target_entity_ref"] == "entity-order"
