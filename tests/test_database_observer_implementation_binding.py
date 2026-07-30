from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.database_observer_implementation_binding import (
    apply_approved_database_observers_to_slot,
)


def _api_rows() -> list[dict]:
    return [
        {
            "interface_id": "api:POST:/orders",
            "status": "BOUND",
            "authoritative": True,
        }
    ]


def _raw_slot(field: str = "amount") -> dict:
    return {
        "slot_ref": "effect:amount",
        "purpose": "EFFECT_OBSERVER",
        "source_field_candidate": field,
        "status": "BOUND",
        "bindings": [
            {
                "binding_kind": "DATABASE_FIELD",
                "field_id": "raw-db-field:orders:amount",
                "table_id": "table:legacy.orders",
                "field": "amount",
                "authoritative": True,
                "derivation": "exact_field_identity",
            },
            {
                "binding_kind": "API_CONTRACT_FIELD",
                "interface_id": "api:POST:/orders",
                "field": "amount",
                "authoritative": True,
            },
        ],
    }


def _scoped_asset(with_approved: bool) -> dict:
    asset = {
        "api_operation_database_table_candidates": [
            {
                "candidate_id": "table-candidate",
                "interface_id": "api:POST:/orders",
            }
        ],
        "api_operation_database_field_candidates": [
            {
                "candidate_id": "field-candidate",
                "interface_id": "api:POST:/orders",
            }
        ],
        "approved_database_observer_field_bindings": [],
    }
    if with_approved:
        asset["approved_database_observer_field_bindings"] = [
            {
                "field_binding_id": "approved-binding:amount",
                "observer_id": "database-observer:create-order",
                "observer_status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
                "runtime_observer_authoritative": True,
                "interface_id": "api:POST:/orders",
                "database_table_id": "table:main.Order",
                "database_field_id": "database-field:main.Order:amount",
                "database_field_name": "amount",
                "api_field_id": "api-field:Order:amount",
                "api_field_name": "amount",
                "api_property_path": ["amount"],
                "value_source": "request.body.amount",
                "mapping_decision_id": "decision:amount",
                "evidence": [{"kind": "OPERATOR_DATABASE_MAPPING_AUTHORITY"}],
            }
        ]
    return asset


def test_raw_database_field_is_removed_when_operation_enters_mapping_authority_scope() -> None:
    result = apply_approved_database_observers_to_slot(
        _raw_slot(), asset=_scoped_asset(False), api_rows=_api_rows()
    )

    assert result["database_mapping_authority_scope_applied"] is True
    assert result["raw_database_field_candidates_removed"] is True
    assert all(
        row.get("binding_kind") != "DATABASE_FIELD"
        for row in result["bindings"]
    )
    assert any(
        row.get("binding_kind") == "API_CONTRACT_FIELD"
        for row in result["bindings"]
    )


def test_only_matching_approved_observer_field_is_injected() -> None:
    result = apply_approved_database_observers_to_slot(
        _raw_slot(), asset=_scoped_asset(True), api_rows=_api_rows()
    )

    database = [
        row for row in result["bindings"] if row.get("binding_kind") == "DATABASE_FIELD"
    ]
    assert len(database) == 1
    binding = database[0]
    assert binding["observer_id"] == "database-observer:create-order"
    assert binding["field_id"] == "database-field:main.Order:amount"
    assert binding["table_id"] == "table:main.Order"
    assert binding["value_source"] == "request.body.amount"
    assert binding["authoritative"] is True
    assert binding["read_only"] is True
    assert binding["write_target_allowed"] is False
    assert binding["oracle_authority_allowed"] is False
    assert binding["derivation"] == "operator_approved_database_observer_contract"
    assert result["approved_database_observer_binding_count"] == 1


def test_approved_observer_for_different_field_does_not_bind_slot() -> None:
    result = apply_approved_database_observers_to_slot(
        _raw_slot("status"), asset=_scoped_asset(True), api_rows=_api_rows()
    )

    assert all(
        row.get("binding_kind") != "DATABASE_FIELD"
        for row in result["bindings"]
    )
    assert result["approved_database_observer_binding_count"] == 0


def test_unscoped_interface_preserves_legacy_source_candidates() -> None:
    result = apply_approved_database_observers_to_slot(
        _raw_slot(), asset={}, api_rows=_api_rows()
    )

    assert result["database_mapping_authority_scope_applied"] is False
    assert any(
        row.get("binding_kind") == "DATABASE_FIELD"
        for row in result["bindings"]
    )
