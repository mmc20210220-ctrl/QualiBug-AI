from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.database_observer_contract_projection import (
    enrich_asset_with_database_observer_contracts,
)


def test_stale_observer_contracts_and_relationships_are_not_merged_back() -> None:
    asset = {
        # Current candidate exists but is no longer approved.
        "api_operation_database_table_candidates": [
            {
                "candidate_id": "table-candidate",
                "operation_schema_binding_id": "binding:create-order",
                "interface_id": "api:POST:/orders",
                "database_table_id": "table:main.Order",
                "status": "PENDING_STORAGE_AUTHORITY",
                "observer_authority_allowed": False,
            }
        ],
        "api_operation_database_field_candidates": [],
        # These rows simulate a previously persisted final asset.
        "database_observer_contracts": [
            {
                "observer_id": "database-observer:stale",
                "interface_id": "api:POST:/orders",
                "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
                "runtime_observer_authoritative": True,
            }
        ],
        "approved_database_observer_field_bindings": [
            {
                "field_binding_id": "database-observer-field:stale",
                "observer_id": "database-observer:stale",
                "runtime_observer_authoritative": True,
            }
        ],
        "relationships": [
            {
                "edge_id": "edge:api-operation-database-observer:database-observer:stale",
                "from": "api:POST:/orders",
                "to": "database-observer:stale",
                "relation": "api_operation_has_database_observer",
                "status": "accepted_read_only_observer",
            },
            {
                "edge_id": "edge:keep",
                "from": "rule:1",
                "to": "api:POST:/orders",
                "relation": "rule_to_interface",
                "status": "accepted",
            },
        ],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    result = enrich_asset_with_database_observer_contracts(asset)

    assert result["database_observer_contracts"] == []
    assert result["approved_database_observer_field_bindings"] == []
    assert [row["edge_id"] for row in result["relationships"]] == ["edge:keep"]
    receipt = result["database_observer_projection"]
    assert receipt["status"] == "NOT_APPLICABLE"
    assert receipt["runtime_bindable_observer_count"] == 0
    assert receipt["derived_observer_assets_rebuilt_from_current_authority"] is True
    assert receipt["stale_observer_authority_retained"] is False
    assert result["governance"][
        "database_observer_derived_authority_is_rebuilt_each_build"
    ] is True
