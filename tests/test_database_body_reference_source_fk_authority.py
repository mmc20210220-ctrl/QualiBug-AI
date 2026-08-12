from __future__ import annotations

from ai_test_asset_center.database_body_reference_projection import project_database_body_reference_relations
from ai_test_asset_center.body_reference_authority import resolve_body_reference
from ._database_body_reference_identity_fixtures import _model, _asset

def test_source_bound_operation_entity_plus_exact_fk_can_authorize_body_reference_without_db_observer_mapping() -> None:
    model = _model()
    model["entities"][0]["table"] = "payments"
    model["entities"][1]["table"] = "orders"
    model["relations"][0]["relation_type"] = "produces"
    asset = {
        "data_tables": [
            {
                "table_id": "table:payments",
                "source_id": "db-schema",
                "source_locator": "schema.sql#line=10;table=payments",
                "evidence_address": {"exact": True},
                "derivation": "database_model_document_ir",
                "columns": ["id", "order_id"],
                "identity_fields": ["id"],
            },
            {
                "table_id": "table:orders",
                "source_id": "db-schema",
                "source_locator": "schema.sql#line=1;table=orders",
                "evidence_address": {"exact": True},
                "derivation": "database_model_document_ir",
                "columns": ["id"],
                "identity_fields": ["id"],
            },
        ],
        "database_model_relationships": _asset()["database_model_relationships"],
    }

    projected = project_database_body_reference_relations(model, asset)
    relation = projected["body_reference_relations"][0]
    assert relation["operation_ref"] == "op-payment-write"
    assert relation["body_path"] == "orderId"
    assert relation["target_entity_ref"] == "ent-order"
    assert relation["authority"] == "source_bound_operation_entity+database_model_exact_fk"
    assert relation["child_database_column"] == "order_id"

    receipt = resolve_body_reference(
        projected["operations"][0], "$.orderId", behavior_ir=projected
    )
    assert receipt["status"] == "RESOLVED"
    assert receipt["target_entity_ref"] == "ent-order"


def test_source_bound_operation_fk_path_rejects_missing_fk_even_when_body_and_table_names_match() -> None:
    model = _model()
    model["entities"][0]["table"] = "payments"
    model["entities"][1]["table"] = "orders"
    model["relations"][0]["relation_type"] = "produces"
    asset = {
        "data_tables": [
            {
                "table_id": "table:payments",
                "source_id": "db-schema",
                "source_locator": "schema.sql#line=10;table=payments",
                "evidence_address": {"exact": True},
                "derivation": "database_model_document_ir",
                "columns": ["id", "order_id"],
                "identity_fields": ["id"],
            },
            {
                "table_id": "table:orders",
                "source_id": "db-schema",
                "source_locator": "schema.sql#line=1;table=orders",
                "evidence_address": {"exact": True},
                "derivation": "database_model_document_ir",
                "columns": ["id"],
                "identity_fields": ["id"],
            },
        ],
        "database_model_relationships": [],
    }

    projected = project_database_body_reference_relations(model, asset)
    assert projected["body_reference_relations"] == []


def test_source_bound_operation_fk_path_rejects_non_identity_parent_column() -> None:
    model = _model()
    model["entities"][0]["table"] = "payments"
    model["entities"][1]["table"] = "orders"
    model["relations"][0]["relation_type"] = "produces"
    asset = {
        "data_tables": [
            {
                "table_id": "table:payments",
                "source_id": "db-schema",
                "source_locator": "schema.sql#line=10;table=payments",
                "evidence_address": {"exact": True},
                "derivation": "database_model_document_ir",
                "columns": ["id", "order_id"],
                "identity_fields": ["id"],
            },
            {
                "table_id": "table:orders",
                "source_id": "db-schema",
                "source_locator": "schema.sql#line=1;table=orders",
                "evidence_address": {"exact": True},
                "derivation": "database_model_document_ir",
                "columns": ["id"],
                "identity_fields": ["order_no"],
            },
        ],
        "database_model_relationships": _asset()["database_model_relationships"],
    }

    projected = project_database_body_reference_relations(model, asset)
    assert projected["body_reference_relations"] == []
