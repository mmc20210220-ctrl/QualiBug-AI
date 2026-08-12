from __future__ import annotations


def _model() -> dict:
    return {
        "entities": [
            {"id": "ent-payment", "name": "payment"},
            {"id": "ent-order", "name": "order"},
        ],
        "operations": [
            {
                "id": "op-payment-write",
                "operation_id": "manual_payment",
                "method": "POST",
                "path": "/api/payments/manual",
                "read_write": "write",
                "request_schema": {
                    "type": "object",
                    "required": ["orderId"],
                    "properties": {"orderId": {"type": "string"}},
                },
                "request_example": {"orderId": "<order_id>"},
                "source_refs": [{"source_id": "api", "locator": "POST /api/payments/manual"}],
            },
            {
                "id": "op-order-list",
                "operation_id": "list_orders",
                "method": "GET",
                "path": "/api/orders",
                "read_write": "read",
                "response_schema": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/Order"},
                                }
                            }
                        }
                    }
                },
                "source_refs": [{"source_id": "api", "locator": "GET /api/orders"}],
            },
        ],
        "relations": [
            {
                "id": "rel-payment-entity",
                "relation_type": "affects",
                "operation_ref": "op-payment-write",
                "from_ref": "op-payment-write",
                "to_ref": "ent-payment",
                "entity_ref": "ent-payment",
                "source_refs": [{"source_id": "api", "locator": "Payment"}],
                "status": "accepted",
            },
            {
                "id": "rel-order-entity",
                "relation_type": "observes",
                "operation_ref": "op-order-list",
                "from_ref": "op-order-list",
                "to_ref": "ent-order",
                "entity_ref": "ent-order",
                "source_refs": [{"source_id": "api", "locator": "Order"}],
                "status": "accepted",
            },
        ],
        "actors": [{"id": "actor", "role": "admin"}],
    }


def _asset() -> dict:
    return {
        "database_observer_contracts": [
            {
                "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
                "mapping_authoritative": True,
                "table_mapping_decision_id": "decision:payments",
                "database_table_id": "table:payments",
                "method": "POST",
                "path": "/api/payments/manual",
                "selected_identity_key": ["id"],
                "field_bindings": [
                    {
                        "authoritative": True,
                        "mapping_decision_id": "decision:payments:order_id",
                        "api_field_name": "orderId",
                        "api_property_path": ["orderId"],
                        "database_field_name": "order_id",
                        "value_source": "request.body.orderId",
                    }
                ],
            },
            {
                "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
                "mapping_authoritative": True,
                "table_mapping_decision_id": "decision:orders",
                "database_table_id": "table:orders",
                "method": "GET",
                "path": "/api/orders",
                "selected_identity_key": ["id"],
                "field_bindings": [
                    {
                        "authoritative": True,
                        "mapping_decision_id": "decision:orders:id",
                        "api_field_name": "id",
                        "api_property_path": ["id"],
                        "database_field_name": "id",
                        "value_source": "response.body.id",
                    }
                ],
            },
        ],
        "database_model_relationships": [
            {
                "relationship_id": "fk:payments:orders",
                "child_table_id": "table:payments",
                "child_columns": ["order_id"],
                "parent_table_id": "table:orders",
                "parent_columns": ["id"],
                "source_id": "db-schema",
                "source_locator": "#/payments/order_id",
                "evidence_address": {"source_id": "db-schema", "exact": True},
                "contract_authority": "DATABASE_MODEL_SOURCE_DECLARATION",
            }
        ],
    }
