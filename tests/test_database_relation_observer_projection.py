from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.database_relation_observer_projection import (
    enrich_asset_with_database_relation_observer_candidates,
)


def _root(*, selected_identity: tuple[str, ...] = ("id",)) -> dict:
    fields = [
        {
            "field_binding_id": "binding:orders:id",
            "database_field_id": "field:orders:id",
            "database_field_name": "id",
            "mapping_decision_id": "decision:orders:id",
            "value_source": "request.parameter.id",
            "authoritative": True,
            "read_only": True,
            "oracle_authority_allowed": False,
        },
        {
            "field_binding_id": "binding:orders:external_id",
            "database_field_id": "field:orders:external_id",
            "database_field_name": "external_id",
            "mapping_decision_id": "decision:orders:external_id",
            "value_source": "request.body.external_id",
            "authoritative": True,
            "read_only": True,
            "oracle_authority_allowed": False,
        },
    ]
    return {
        "schema": "qualibug.database-observer-contract.v1",
        "observer_id": "observer:orders",
        "operation_schema_binding_id": "binding:update-order",
        "interface_id": "api:PATCH:/orders/{id}",
        "method": "PATCH",
        "path": "/orders/{id}",
        "database_table_id": "table:orders",
        "database_table_name": "orders",
        "table_mapping_decision_id": "decision:orders",
        "selected_identity_key": list(selected_identity),
        "field_bindings": fields,
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
    }


def _relationship(
    *,
    parent_columns: tuple[str, ...] = ("id",),
    child_columns: tuple[str, ...] = ("order_id",),
) -> dict:
    return {
        "relationship_id": "fk:order_lines:orders",
        "relationship_type": "foreign_key",
        "child_table_id": "table:order_lines",
        "child_schema": "main",
        "child_table": "order_lines",
        "child_columns": list(child_columns),
        "parent_table_id": "table:orders",
        "parent_schema": "main",
        "parent_table": "orders",
        "parent_columns": list(parent_columns),
        "source_id": "source:pdm",
        "source_locator": "#/foreignKeys/order_lines_orders",
        "evidence_address": {"source_id": "source:pdm", "exact": True},
        "contract_authority": "DATABASE_MODEL_SOURCE_DECLARATION",
    }


def _asset(root: dict, relationship: dict) -> dict:
    return {
        "database_observer_contracts": [root],
        "database_model_relationships": [relationship],
        "tables": [
            {"table_id": "table:orders", "name": "orders"},
            {"table_id": "table:order_lines", "name": "order_lines"},
        ],
        "field_dictionary": [
            {
                "field_id": "field:order_lines:order_id",
                "table_id": "table:order_lines",
                "field": "order_id",
                "type": "TEXT",
                "source_id": "source:pdm",
                "source_locator": "#/tables/order_lines/order_id",
            },
            {
                "field_id": "field:order_lines:amount",
                "table_id": "table:order_lines",
                "field": "amount",
                "type": "NUMERIC(12,2)",
                "source_id": "source:pdm",
                "source_locator": "#/tables/order_lines/amount",
            },
        ],
        "coverage_gaps": [],
    }


def test_exact_fk_to_selected_root_identity_creates_pending_candidate() -> None:
    result = enrich_asset_with_database_relation_observer_candidates(
        _asset(_root(), _relationship())
    )

    candidates = result["database_relation_observer_candidates"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["status"] == "PENDING_RELATION_AUTHORITY"
    assert candidate["root_selected_identity_key"] == ["id"]
    assert candidate["parent_columns"] == ["id"]
    assert candidate["child_columns"] == ["order_id"]
    assert candidate["predicate_pairs"] == [
        {
            "ordinal": 0,
            "child_database_field_name": "order_id",
            "parent_database_field_name": "id",
            "parent_database_field_id": "field:orders:id",
            "parent_field_binding_id": "binding:orders:id",
            "value_source": "request.parameter.id",
        }
    ]
    assert candidate["observer_authority_allowed"] is False
    assert candidate["write_target_allowed"] is False
    assert candidate["oracle_authority_allowed"] is False
    assert result["database_relation_observer_candidate_projection"][
        "automatic_relation_mapping_count"
    ] == 0


def test_fk_to_alternate_parent_column_is_blocked_not_treated_as_root_identity() -> None:
    result = enrich_asset_with_database_relation_observer_candidates(
        _asset(_root(selected_identity=("id",)), _relationship(parent_columns=("external_id",)))
    )

    candidate = result["database_relation_observer_candidates"][0]
    assert candidate["status"] == "BLOCKED_PARENT_IDENTITY_SCOPE_MISMATCH"
    assert candidate["reason_code"] == "DATABASE_RELATION_PARENT_IDENTITY_SCOPE_REQUIRED"
    assert candidate["observer_authority_allowed"] is False


def test_composite_fk_requires_exact_selected_identity_order_and_complete_pairs() -> None:
    root = _root(selected_identity=("tenant_id", "id"))
    root["field_bindings"] = [
        {
            "field_binding_id": "binding:orders:tenant_id",
            "database_field_id": "field:orders:tenant_id",
            "database_field_name": "tenant_id",
            "mapping_decision_id": "decision:orders:tenant_id",
            "value_source": "request.parameter.tenant_id",
            "authoritative": True,
            "read_only": True,
            "oracle_authority_allowed": False,
        },
        root["field_bindings"][0],
    ]
    relation = _relationship(
        parent_columns=("tenant_id", "id"),
        child_columns=("tenant_id", "order_id"),
    )
    asset = _asset(root, relation)
    asset["field_dictionary"].append(
        {
            "field_id": "field:order_lines:tenant_id",
            "table_id": "table:order_lines",
            "field": "tenant_id",
            "type": "TEXT",
        }
    )

    result = enrich_asset_with_database_relation_observer_candidates(asset)

    candidate = result["database_relation_observer_candidates"][0]
    assert candidate["status"] == "PENDING_RELATION_AUTHORITY"
    assert [row["value_source"] for row in candidate["predicate_pairs"]] == [
        "request.parameter.tenant_id",
        "request.parameter.id",
    ]

    broken = deepcopy(asset)
    broken["database_model_relationships"][0]["child_columns"] = ["order_id"]
    broken_result = enrich_asset_with_database_relation_observer_candidates(broken)
    broken_candidate = broken_result["database_relation_observer_candidates"][0]
    assert broken_candidate["status"] == "BLOCKED_FOREIGN_KEY_COLUMN_PAIR_INCOMPLETE"


def test_child_to_parent_direction_is_not_reversed_automatically() -> None:
    reverse_root = _root()
    reverse_root["database_table_id"] = "table:order_lines"
    reverse_root["database_table_name"] = "order_lines"
    reverse_root["observer_id"] = "observer:order-lines"

    result = enrich_asset_with_database_relation_observer_candidates(
        _asset(reverse_root, _relationship())
    )

    assert result["database_relation_observer_candidates"] == []
    assert result["database_relation_observer_candidate_projection"][
        "automatic_relation_mapping_count"
    ] == 0
