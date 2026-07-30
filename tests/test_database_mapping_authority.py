from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_knowledge_center.database_mapping_authority import (
    ACTION_APPROVE_READ_ONLY_OBSERVER,
    ACTION_REJECT_MAPPING,
    apply_database_mapping_authority_decisions,
    record_database_mapping_authority_decision,
)
from ai_test_asset_center.enterprise_knowledge_center.database_observer_contract_projection import (
    DATABASE_OBSERVER_CONTRACT_SCHEMA,
    enrich_asset_with_database_observer_contracts,
)


def _table_candidate() -> dict:
    return {
        "candidate_id": "table-candidate:create-order",
        "operation_schema_binding_id": "binding:create-order:request",
        "interface_id": "api:POST:/orders",
        "method": "POST",
        "path": "/orders",
        "direction": "request",
        "media_type": "application/json",
        "api_schema_entity_id": "api_schema_entity:schema:Order",
        "api_schema_id": "schema:Order",
        "api_schema_name": "Order",
        "entity_alignment_candidate_id": "entity-candidate:Order",
        "database_table_id": "table:main.Order",
        "database_schema_name": "main",
        "database_qualified_name": "main.Order",
        "status": "PENDING_STORAGE_AUTHORITY",
        "observer_candidate_only": True,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
        "automatic_mapping_allowed": False,
    }


def _field_candidate(name: str, declared_type: str = "TEXT") -> dict:
    return {
        "candidate_id": f"field-candidate:create-order:{name}",
        "operation_schema_binding_id": "binding:create-order:request",
        "interface_id": "api:POST:/orders",
        "method": "POST",
        "path": "/orders",
        "direction": "request",
        "media_type": "application/json",
        "api_schema_entity_id": "api_schema_entity:schema:Order",
        "api_field_id": f"api-field:Order:{name}",
        "api_field_name": name,
        "api_property_path": [name],
        "field_alignment_candidate_id": f"field-alignment:Order:{name}",
        "database_table_id": "table:main.Order",
        "database_field_id": f"database-field:main.Order:{name}",
        "database_field_name": name,
        "type_compatibility": {
            "status": "COMPATIBLE",
            "api_declared_type": "string" if name != "amount" else "number",
            "database_declared_type": declared_type,
        },
        "status": "PENDING_STORAGE_FIELD_AUTHORITY",
        "observer_candidate_only": True,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
        "automatic_mapping_allowed": False,
    }


def _asset() -> dict:
    return {
        "project_id": "project_mapping_authority",
        "api_operation_database_table_candidates": [_table_candidate()],
        "api_operation_database_field_candidates": [
            _field_candidate("id", "VARCHAR(64)"),
            _field_candidate("amount", "NUMERIC(12,2)"),
        ],
        "tables": [
            {
                "table_id": "table:main.Order",
                "source_id": "src_db",
                "name": "Order",
                "schema_name": "main",
                "qualified_name": "main.Order",
                "identity_fields": ["id"],
                "source_locator": "schema.pdm#table=main.Order",
            }
        ],
        "field_dictionary": [
            {
                "field_id": "database-field:main.Order:id",
                "source_id": "src_db",
                "table_id": "table:main.Order",
                "table": "main.Order",
                "field": "id",
                "source_locator": "schema.pdm#column=main.Order.id",
            },
            {
                "field_id": "database-field:main.Order:amount",
                "source_id": "src_db",
                "table_id": "table:main.Order",
                "table": "main.Order",
                "field": "amount",
                "source_locator": "schema.pdm#column=main.Order.amount",
            },
        ],
        "openapi_schema_fields": [
            {
                "field_fact_id": "api-field:Order:id",
                "source_id": "src_api",
                "field_name": "id",
                "source_locator": "orders.openapi.json#block=json-pointer:/components/schemas/Order/properties/id",
            },
            {
                "field_fact_id": "api-field:Order:amount",
                "source_id": "src_api",
                "field_name": "amount",
                "source_locator": "orders.openapi.json#block=json-pointer:/components/schemas/Order/properties/amount",
            },
        ],
        "api_operation_schema_bindings": [
            {
                "binding_id": "binding:create-order:request",
                "source_id": "src_api",
                "interface_id": "api:POST:/orders",
                "method": "POST",
                "path": "/orders",
                "direction": "request",
                "source_locator": "orders.openapi.json#block=json-pointer:/paths/~1orders/post/requestBody/content/application~1json/schema",
            }
        ],
        "relationships": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def _actor() -> dict:
    return {"name": "qa-owner", "role": "project_owner"}


def _approve(root, asset: dict, kind: str, candidate_id: str) -> None:
    record_database_mapping_authority_decision(
        asset["project_id"],
        candidate_kind=kind,
        candidate_id=candidate_id,
        action=ACTION_APPROVE_READ_ONLY_OBSERVER,
        actor=_actor(),
        rationale="verified against source declarations",
        root=root,
        asset=asset,
    )


def test_no_explicit_decision_never_creates_database_observer(tmp_path) -> None:
    governed = apply_database_mapping_authority_decisions(
        _asset(), project_id="project_mapping_authority", root=tmp_path
    )
    result = enrich_asset_with_database_observer_contracts(governed)

    assert result["database_observer_contracts"] == []
    receipt = result["database_mapping_authority_receipt"]
    assert receipt["approved_table_mapping_count"] == 0
    assert receipt["approved_field_mapping_count"] == 0
    assert receipt["automatic_approval_count"] == 0
    assert receipt["write_target_authority_count"] == 0
    assert receipt["oracle_authority_count"] == 0


def test_approved_table_and_identity_fields_compile_parameterized_read_only_observer(
    tmp_path,
) -> None:
    asset = _asset()
    _approve(tmp_path, asset, "table", "table-candidate:create-order")
    _approve(tmp_path, asset, "field", "field-candidate:create-order:id")
    _approve(tmp_path, asset, "field", "field-candidate:create-order:amount")

    governed = apply_database_mapping_authority_decisions(
        asset, project_id=asset["project_id"], root=tmp_path
    )
    result = enrich_asset_with_database_observer_contracts(governed)

    assert len(result["database_observer_contracts"]) == 1
    observer = result["database_observer_contracts"][0]
    assert observer["schema"] == DATABASE_OBSERVER_CONTRACT_SCHEMA
    assert observer["status"] == "READY_FOR_RUNTIME_CONNECTION_BINDING"
    assert observer["runtime_observer_authoritative"] is True
    assert observer["selected_identity_key"] == ["id"]
    assert observer["identity_predicates"] == [
        {
            "database_field_name": "id",
            "database_field_id": "database-field:main.Order:id",
            "operator": "=",
            "value_source": "request.body.id",
            "field_binding_id": observer["identity_predicates"][0][
                "field_binding_id"
            ],
        }
    ]
    assert observer["query_plan"]["operation"] == "SELECT_ONE"
    assert observer["query_plan"]["parameterized"] is True
    assert observer["query_plan"]["raw_sql"] == ""
    assert observer["query_plan"]["maximum_rows"] == 2
    assert observer["read_only"] is True
    assert observer["mutation_allowed"] is False
    assert observer["write_target_allowed"] is False
    assert observer["oracle_authority_allowed"] is False
    assert observer["connection_secret_embedded"] is False
    assert result["database_observer_projection"]["runtime_bindable_observer_count"] == 1
    assert result["database_observer_projection"]["write_target_authority_count"] == 0
    assert result["database_observer_projection"]["oracle_authority_count"] == 0


def test_field_approval_without_parent_table_approval_fails_closed(tmp_path) -> None:
    asset = _asset()
    _approve(tmp_path, asset, "field", "field-candidate:create-order:id")

    result = apply_database_mapping_authority_decisions(
        asset, project_id=asset["project_id"], root=tmp_path
    )

    field = next(
        row
        for row in result["api_operation_database_field_candidates"]
        if row["api_field_name"] == "id"
    )
    assert field["status"] == "BLOCKED_PARENT_TABLE_AUTHORITY_REQUIRED"
    assert field["observer_authority_allowed"] is False
    assert any(
        row.get("kind") == "DATABASE_MAPPING_PARENT_TABLE_AUTHORITY_REQUIRED"
        for row in result["coverage_gaps"]
    )
    compiled = enrich_asset_with_database_observer_contracts(result)
    assert compiled["database_observer_contracts"] == []


def test_candidate_drift_invalidates_old_approval(tmp_path) -> None:
    asset = _asset()
    _approve(tmp_path, asset, "table", "table-candidate:create-order")
    asset["api_operation_database_table_candidates"][0]["path"] = "/v2/orders"

    result = apply_database_mapping_authority_decisions(
        asset, project_id=asset["project_id"], root=tmp_path
    )

    table = result["api_operation_database_table_candidates"][0]
    assert table["status"] == "PENDING_STORAGE_AUTHORITY"
    assert table["observer_authority_allowed"] is False
    assert table["mapping_authority"]["candidate_drift_detected"] is True
    assert any(
        row.get("kind") == "DATABASE_MAPPING_AUTHORITY_DECISION_STALE"
        for row in result["coverage_gaps"]
    )


def test_incompatible_field_mapping_cannot_be_approved(tmp_path) -> None:
    asset = _asset()
    incompatible = asset["api_operation_database_field_candidates"][1]
    incompatible["type_compatibility"] = {
        "status": "INCOMPATIBLE",
        "api_declared_type": "object",
        "database_declared_type": "NUMERIC(12,2)",
    }

    with pytest.raises(
        ValueError, match="incompatible_database_field_mapping_cannot_be_approved"
    ):
        _approve(tmp_path, asset, "field", incompatible["candidate_id"])


def test_rejected_mapping_never_compiles_observer(tmp_path) -> None:
    asset = _asset()
    record_database_mapping_authority_decision(
        asset["project_id"],
        candidate_kind="table",
        candidate_id="table-candidate:create-order",
        action=ACTION_REJECT_MAPPING,
        actor=_actor(),
        rationale="source owner rejected this storage mapping",
        root=tmp_path,
        asset=asset,
    )

    governed = apply_database_mapping_authority_decisions(
        asset, project_id=asset["project_id"], root=tmp_path
    )
    table = governed["api_operation_database_table_candidates"][0]
    assert table["status"] == "REJECTED_BY_OPERATOR"
    assert table["observer_authority_allowed"] is False
    result = enrich_asset_with_database_observer_contracts(governed)
    assert result["database_observer_contracts"] == []
