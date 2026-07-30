from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.database_observer_contract_projection import (
    enrich_asset_with_database_observer_contracts,
)


def _approved_table() -> dict:
    return {
        "candidate_id": "table-candidate",
        "operation_schema_binding_id": "binding:create-order",
        "interface_id": "api:POST:/orders",
        "method": "POST",
        "path": "/orders",
        "direction": "request",
        "api_schema_entity_id": "api-schema:Order",
        "database_table_id": "table:main.Order",
        "database_schema_name": "main",
        "database_qualified_name": "main.Order",
        "status": "APPROVED_READ_ONLY_OBSERVER_TABLE",
        "observer_authority_allowed": True,
        "mapping_authority": {"decision_id": "decision:table"},
    }


def _approved_field(name: str) -> dict:
    return {
        "candidate_id": f"field-candidate:{name}",
        "operation_schema_binding_id": "binding:create-order",
        "interface_id": "api:POST:/orders",
        "method": "POST",
        "path": "/orders",
        "direction": "request",
        "api_schema_entity_id": "api-schema:Order",
        "api_field_id": f"api-field:{name}",
        "api_field_name": name,
        "api_property_path": [name],
        "database_table_id": "table:main.Order",
        "database_field_id": f"db-field:{name}",
        "database_field_name": name,
        "type_compatibility": {"status": "COMPATIBLE"},
        "status": "APPROVED_READ_ONLY_OBSERVER_FIELD",
        "observer_authority_allowed": True,
        "mapping_authority": {"decision_id": f"decision:{name}"},
    }


def test_exact_model_table_overrides_compatibility_table_and_explicit_keys_win() -> None:
    asset = {
        "api_operation_database_table_candidates": [_approved_table()],
        "api_operation_database_field_candidates": [
            _approved_field("id"),
            _approved_field("external_code"),
        ],
        # Legacy compatibility asset incorrectly aggregates both identities as one tuple.
        "data_tables": [
            {
                "table_id": "table:main.Order",
                "name": "Order",
                "identity_fields": ["id", "external_code"],
            }
        ],
        # Exact database-model asset preserves two source-declared alternative keys.
        "tables": [
            {
                "table_id": "table:main.Order",
                "source_id": "src_pdm",
                "name": "Order",
                "schema_name": "main",
                "qualified_name": "main.Order",
                "identity_fields": ["external_code", "id"],
                "identity_keys": [
                    {
                        "identity_key_id": "identity-key:pk-order",
                        "kind": "PRIMARY_INDEX",
                        "columns": ["id"],
                        "source_locator": "schema.pdm#index=pk_order",
                    },
                    {
                        "identity_key_id": "identity-key:uk-order-code",
                        "kind": "UNIQUE_INDEX",
                        "columns": ["external_code"],
                        "source_locator": "schema.pdm#index=uk_order_code",
                    },
                ],
                "source_locator": "schema.pdm#table=main.Order",
            }
        ],
        "field_dictionary": [
            {
                "field_id": "db-field:id",
                "table_id": "table:main.Order",
                "field": "id",
            },
            {
                "field_id": "db-field:external_code",
                "table_id": "table:main.Order",
                "field": "external_code",
            },
        ],
        "openapi_schema_fields": [
            {"field_fact_id": "api-field:id", "field_name": "id"},
            {
                "field_fact_id": "api-field:external_code",
                "field_name": "external_code",
            },
        ],
        "api_operation_schema_bindings": [
            {
                "binding_id": "binding:create-order",
                "interface_id": "api:POST:/orders",
            }
        ],
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_database_observer_contracts(asset)

    observer = result["database_observer_contracts"][0]
    assert observer["status"] == "READY_FOR_RUNTIME_CONNECTION_BINDING"
    assert observer["selected_identity_key"] == ["id"]
    assert observer["selected_identity_key_id"] == "identity-key:pk-order"
    assert observer["selected_identity_source"] == "PRIMARY_INDEX"
    assert observer["selected_identity_is_explicit_group"] is True
    assert observer["identity_key_options"] == [
        {
            "identity_key_id": "identity-key:pk-order",
            "columns": ["id"],
            "source": "PRIMARY_INDEX",
            "explicit_group": True,
        },
        {
            "identity_key_id": "identity-key:uk-order-code",
            "columns": ["external_code"],
            "source": "UNIQUE_INDEX",
            "explicit_group": True,
        },
    ]
    assert result["database_observer_projection"]["explicit_identity_keys_preferred"] is True
    assert result["database_observer_projection"][
        "exact_database_model_tables_override_compatibility_tables"
    ] is True
