from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_field_evidence import (
    IDENTITY_FIELD_BINDING_SCHEMA,
    augment_identity_field_evidence,
)


def _evidence(source_id: str, locator: str, quote: str) -> list[dict]:
    return [
        {
            "source_id": source_id,
            "source_locator": locator,
            "quote": quote,
        }
    ]


def _binding(entity_id: str, artifact_type: str, artifact_ref: str) -> dict:
    return {
        "schema": "qualibug.enterprise-identity-binding.v1",
        "binding_id": f"binding:{entity_id}:{artifact_ref}",
        "entity_id": entity_id,
        "artifact_type": artifact_type,
        "artifact_ref": artifact_ref,
        "artifact_label": artifact_ref,
        "relation": "IMPLEMENTS_ENTITY",
        "status": "RESOLVED",
        "identity_field_bindings": [],
        "evidence": _evidence("source", artifact_ref, "显式对象级绑定"),
    }


def _result(*bindings: dict, unknown_artifact: str = "") -> dict:
    unknowns = []
    if unknown_artifact:
        unknowns.append(
            {
                "unknown_id": f"unknown:{unknown_artifact}",
                "kind": "CROSS_SOURCE_IDENTITY_UNRESOLVED",
                "reason_code": "CROSS_SOURCE_IDENTITY_UNRESOLVED",
                "details": {"artifact_ref": unknown_artifact},
            }
        )
    return {
        "bindings": list(bindings),
        "unknowns": unknowns,
        "conflicts": [],
        "gate": {
            "status": "PARTIAL_ENTERPRISE_IDENTITY_BINDING" if unknowns else "PASS",
            "entry_allowed": True,
            "business_understanding_allowed": True,
            "metrics": {},
        },
    }


def test_exact_declared_identity_key_binds_unbound_api_to_existing_entity() -> None:
    asset = {
        "data_tables": [
            {
                "table_id": "table:orders",
                "name": "orders",
                "source_id": "db",
                "source_locator": "schema.orders",
                "business_object": "销售订单",
                "evidence": _evidence("db", "schema.orders", "orders 表实现销售订单"),
                "columns": [
                    {
                        "column_id": "orders.id",
                        "name": "id",
                        "primary_key": True,
                        "identity_key_ref": "sales-order-id",
                        "business_field_ref": "销售订单.id",
                    }
                ],
            }
        ],
        "interfaces": [
            {
                "interface_id": "api:get-order",
                "method": "GET",
                "path": "/orders/{orderId}",
                "source_id": "openapi",
                "source_locator": "GET /orders/{orderId}",
                "evidence": _evidence("openapi", "GET /orders/{orderId}", "按订单标识读取订单"),
                "parameter_contracts": [
                    {
                        "field_id": "path.orderId",
                        "name": "orderId",
                        "location": "PATH",
                        "required": True,
                        "is_identifier": True,
                        "identity_key_ref": "sales-order-id",
                        "business_field_ref": "销售订单.id",
                    }
                ],
            }
        ],
    }
    result = _result(
        _binding("entity:sales-order", "DATABASE_TABLE", "table:orders"),
        unknown_artifact="api:get-order",
    )

    projected = augment_identity_field_evidence(asset, result)

    table_binding = next(
        row for row in projected["bindings"] if row["artifact_ref"] == "table:orders"
    )
    assert table_binding["identity_field_bindings"][0]["schema"] == IDENTITY_FIELD_BINDING_SCHEMA
    assert table_binding["identity_field_bindings"][0]["identity_key_refs"] == [
        "sales-order-id"
    ]
    api_binding = next(
        row for row in projected["bindings"] if row["artifact_ref"] == "api:get-order"
    )
    assert api_binding["entity_id"] == "entity:sales-order"
    assert api_binding["relation"] == "IDENTIFIED_BY_KEY"
    assert api_binding["authority"] == "CROSS_TECHNICAL_EXACT_IDENTITY_KEY_REF"
    assert api_binding["automatic_entity_union_allowed"] is False
    assert projected["unknowns"] == []
    assert projected["gate"]["status"] == "PASS"
    assert projected["identity_field_evidence"]["cross_technical_binding_count"] == 1


def test_exact_field_name_without_declared_key_is_candidate_only() -> None:
    asset = {
        "data_tables": [
            {
                "table_id": "table:orders",
                "name": "orders",
                "columns": [
                    {
                        "column_id": "orders.order_id",
                        "name": "order_id",
                        "primary_key": True,
                    }
                ],
            }
        ],
        "interfaces": [
            {
                "interface_id": "api:get-order",
                "parameter_contracts": [
                    {
                        "field_id": "path.order_id",
                        "name": "order_id",
                        "location": "PATH",
                        "is_identifier": True,
                    }
                ],
            }
        ],
    }
    result = _result(
        _binding("entity:sales-order", "DATABASE_TABLE", "table:orders"),
        unknown_artifact="api:get-order",
    )

    projected = augment_identity_field_evidence(asset, result)

    assert not any(
        row["artifact_ref"] == "api:get-order" for row in projected["bindings"]
    )
    assert projected["unknowns"]
    receipt = projected["identity_field_evidence"]
    assert receipt["candidate_only_count"] == 1
    candidate = receipt["candidate_bindings"][0]
    assert candidate["status"] == "CANDIDATE_ONLY"
    assert candidate["candidate_entity_ids"] == ["entity:sales-order"]
    assert candidate["automatic_resolution_allowed"] is False
    assert receipt["automatic_field_name_binding_allowed"] is False


def test_same_declared_identity_key_on_multiple_entities_blocks_formal_understanding() -> None:
    asset = {
        "data_tables": [
            {
                "table_id": "table:sales-orders",
                "columns": [
                    {
                        "column_id": "sales_orders.id",
                        "name": "id",
                        "primary_key": True,
                        "identity_key_ref": "shared-order-id",
                    }
                ],
            },
            {
                "table_id": "table:purchase-orders",
                "columns": [
                    {
                        "column_id": "purchase_orders.id",
                        "name": "id",
                        "primary_key": True,
                        "identity_key_ref": "shared-order-id",
                    }
                ],
            },
        ]
    }
    result = _result(
        _binding("entity:sales-order", "DATABASE_TABLE", "table:sales-orders"),
        _binding("entity:purchase-order", "DATABASE_TABLE", "table:purchase-orders"),
    )

    projected = augment_identity_field_evidence(asset, result)

    conflict = next(
        row
        for row in projected["conflicts"]
        if row["kind"] == "IDENTITY_KEY_REF_MULTIPLE_ENTITIES"
    )
    assert conflict["identity_key_ref"] == "shared-order-id"
    assert conflict["candidate_entity_ids"] == [
        "entity:purchase-order",
        "entity:sales-order",
    ]
    assert conflict["automatic_resolution_allowed"] is False
    assert projected["gate"]["status"] == "BLOCKED_ENTERPRISE_IDENTITY_CONFLICT"
    assert projected["gate"]["entry_allowed"] is False


def test_field_evidence_projection_is_deterministic() -> None:
    asset = {
        "data_tables": [
            {
                "table_id": "table:orders",
                "columns": [
                    {
                        "column_id": "orders.id",
                        "name": "id",
                        "primary_key": True,
                        "identity_key_ref": "sales-order-id",
                    }
                ],
            }
        ]
    }
    initial = _result(
        _binding("entity:sales-order", "DATABASE_TABLE", "table:orders")
    )

    left = augment_identity_field_evidence(deepcopy(asset), deepcopy(initial))
    right = augment_identity_field_evidence(deepcopy(asset), deepcopy(initial))

    assert left["bindings"] == right["bindings"]
    assert left["identity_field_evidence"] == right["identity_field_evidence"]
