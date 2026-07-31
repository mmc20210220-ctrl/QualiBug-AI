from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ai_test_asset_center import database_observer_experiment_runtime as phase_runtime
from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
)
from ai_test_asset_center.database_observer_experiment_runtime import (
    aggregate_database_observer_phase_receipts,
)
from ai_test_asset_center.database_relation_numeric_experiment_projection import ASSERTION_KIND
from ai_test_asset_center.database_relation_observer_experiment_runtime import (
    aggregate_database_relation_phase_receipts,
    install_database_relation_phase_execution,
    install_database_relation_phase_observer,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers


install_non_http_observers()
install_database_relation_phase_observer()
install_database_relation_phase_execution()


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE orders (id TEXT PRIMARY KEY, total NUMERIC NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE order_lines (id TEXT PRIMARY KEY, order_id TEXT NOT NULL, amount NUMERIC NOT NULL)"
    )
    connection.execute("INSERT INTO orders(id, total) VALUES ('o-1', '30.00')")
    connection.executemany(
        "INSERT INTO order_lines(id, order_id, amount) VALUES (?, ?, ?)",
        [("l-1", "o-1", "10.00"), ("l-2", "o-1", "15.00")],
    )
    connection.commit()
    connection.close()


def _config(root: Path, project: str, database: Path) -> None:
    directory = root / "platform_workspace" / project
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "multi_service_config.json").write_text(
        json.dumps(
            {
                "environment": "test",
                "services": [
                    {
                        "name": "orders-db",
                        "environment": "test",
                        "db": {
                            "dialect": "sqlite",
                            "path": str(database),
                            "read_only": True,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _root_contract() -> dict:
    return {
        "schema": "qualibug.database-observer-contract.v1",
        "observer_id": "observer:orders",
        "operation_schema_binding_id": "binding:update-order",
        "interface_id": "api:PATCH:/orders/{id}",
        "database_table_id": "table:orders",
        "database_schema_name": "",
        "database_table_name": "orders",
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
        "connection_secret_embedded": False,
        "selected_identity_key": ["id"],
        "identity_predicates": [
            {
                "database_field_name": "id",
                "database_field_id": "field:orders:id",
                "operator": "=",
                "value_source": "request.parameter.id",
                "field_binding_id": "binding:orders:id",
            }
        ],
        "query_plan": {
            "operation": "SELECT_ONE",
            "database_table_id": "table:orders",
            "projection": ["id", "total"],
            "predicates": [],
            "parameterized": True,
            "maximum_rows": 2,
            "raw_sql": "",
        },
    }


def _relation_contract() -> dict:
    return {
        "schema": "qualibug.database-relation-observer-contract.v1",
        "relation_observer_id": "relation-observer:order-lines",
        "candidate_id": "candidate:order-lines",
        "relation_mapping_decision_id": "decision:order-lines",
        "root_observer_id": "observer:orders",
        "database_relationship_id": "fk:order_lines:orders",
        "parent_table_id": "table:orders",
        "parent_table_name": "orders",
        "parent_columns": ["id"],
        "child_table_id": "table:order_lines",
        "child_table_name": "order_lines",
        "child_columns": ["order_id"],
        "relation_predicates": [
            {
                "ordinal": 0,
                "child_database_field_name": "order_id",
                "parent_database_field_name": "id",
                "parent_database_field_id": "field:orders:id",
                "parent_field_binding_id": "binding:orders:id",
                "operator": "=",
                "value_source": "request.parameter.id",
            }
        ],
        "allowed_child_fields": [
            {
                "database_field_id": "field:order_lines:amount",
                "database_field_name": "amount",
                "database_declared_type": "NUMERIC",
            }
        ],
        "query_plan": {
            "operation": "SELECT_MANY",
            "parameterized": True,
            "maximum_rows": 10000,
            "allowed_aggregates": ["COUNT", "SUM", "MIN", "MAX"],
            "raw_sql": "",
            "client_side_filter": False,
        },
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "mapping_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
        "business_mapping_authority_allowed": False,
        "runtime_connection_binding_required": True,
        "connection_secret_embedded": False,
    }


def _experiment() -> dict:
    relation_key = [
        {
            "child_database_field_name": "order_id",
            "parent_database_field_name": "id",
        }
    ]
    aggregate_requests = [
        {
            "aggregate": "SUM",
            "database_field_id": "field:order_lines:amount",
            "database_field_name": "amount",
            "alias": "related_value",
        }
    ]
    return {
        "experiment_id": "experiment:order-total",
        "obligation_id": "obligation:order-total",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "source_refs": [{"kind": "business_rule", "locator": "BR-ORDER-TOTAL"}],
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": "treatment-1",
                "operation_ref": "api:PATCH:/orders/{id}",
                "path_parameters": {"id": "{id}"},
            }
        ],
        "observers": [
            {"observer_id": "approved_database_phase_aggregate", "adapter": "db_sql"},
            {
                "observer_id": "approved_database_relation_phase_aggregate",
                "adapter": "db_sql",
            },
        ],
        "database_observer_execution_drafts": [
            {
                "schema": "qualibug.database-observer-execution-draft.v1",
                "draft_id": "draft:orders:after",
                "observer_handler_id": "approved_database_readback",
                "observer_contract_ref": "observer:orders",
                "observation_phase": "AFTER",
                "database_observer_contract": _root_contract(),
                "database_connection_ref": "",
                "identity_value_sources": ["request.parameter.id"],
                "required": True,
            }
        ],
        "database_relation_observer_execution_drafts": [
            {
                "schema": "qualibug.database-relation-observer-execution-draft.v1",
                "draft_id": "draft:relation:after",
                "observer_handler_id": "approved_database_relation_aggregate",
                "relation_observer_contract_ref": "relation-observer:order-lines",
                "root_observer_contract_ref": "observer:orders",
                "observation_phase": "AFTER",
                "database_relation_observer_contract": _relation_contract(),
                "aggregate_requests": aggregate_requests,
                "identity_value_sources": ["request.parameter.id"],
                "database_connection_ref": "",
                "required": True,
            }
        ],
        "assertions": [
            {
                "assertion_id": "assert:order-total",
                "kind": ASSERTION_KIND,
                "source_assertion_kind": "conservation",
                "require_control": False,
                "database_relation_observer_ref": "relation-observer:order-lines",
                "database_relation_draft_id": "draft:relation:after",
                "database_relationship_id": "fk:order_lines:orders",
                "relation_key": relation_key,
                "root_observer_contract_ref": "observer:orders",
                "root_database_draft_id": "draft:orders:after",
                "root_table_ref": "table:orders",
                "root_database_field_id": "field:orders:total",
                "root_database_field_name": "total",
                "child_table_ref": "table:order_lines",
                "child_database_field_id": "field:order_lines:amount",
                "child_database_field_name": "amount",
                "aggregate": "SUM",
                "aggregate_alias": "related_value",
                "comparison_operator": "EQ",
                "aggregate_on_left": False,
                "comparison_phase": "AFTER",
                "tolerance": "0",
            }
        ],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "status": "RESOLVED",
            "assertion_kind": ASSERTION_KIND,
        },
        "safety_contract": {"governed_write": False},
    }


def test_real_sqlite_root_and_child_aggregate_reach_contract_oracle(
    tmp_path: Path,
) -> None:
    project = "relation_runtime_integration"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, database)
    experiment = _experiment()
    observations: dict = {}

    summary = phase_runtime.execute_database_observer_phase(
        experiment,
        phase="AFTER",
        root=tmp_path,
        project=project,
        runtime_contract={},
        runtime_bindings={"id": "o-1"},
        observations=observations,
        steps_out=[
            {
                "phase": "treatment",
                "response": {"status_code": 200, "body": {"id": "o-1"}},
            }
        ],
        campaign_id="campaign-1",
        execution_id="execution-1",
    )

    assert summary["status"] == "OBSERVED"
    assert len(observations["approved_database_observer_phase_receipts"]) == 1
    assert len(observations["approved_database_relation_phase_receipts"]) == 1
    relation_receipt = observations["approved_database_relation_phase_receipts"][0]
    relation_payload = relation_receipt["evidence"][
        "approved_database_relation_aggregate_snapshot"
    ]
    assert str(relation_payload["aggregate_values"]["related_value"]) == "25"
    assert relation_payload["aggregate_requests"] == [
        {
            "aggregate": "SUM",
            "database_field_id": "field:order_lines:amount",
            "database_field_name": "amount",
            "alias": "related_value",
        }
    ]
    assert relation_payload["raw_rows_retained"] is False
    assert relation_payload["oracle_verdict_emitted"] is False

    root_aggregate = aggregate_database_observer_phase_receipts(
        {
            "experiment": experiment,
            "observations": observations,
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )
    relation_aggregate = aggregate_database_relation_phase_receipts(
        {
            "experiment": experiment,
            "observations": observations,
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )
    treatment = build_contract_evidence_receipt(
        kind="treatment",
        experiment_id="experiment:order-total",
        obligation_id="obligation:order-total",
        campaign_id="campaign-1",
        execution_id="execution-1",
        subject_id="treatment-1",
        status="OBSERVED",
        evidence={
            "response_observed": True,
            "status_code": 200,
            "write_reached_transport": True,
        },
    )
    result = evaluate_contract_oracle(
        experiment=experiment,
        evidence={
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
            "contract_evidence_receipts": [treatment],
            "observer_receipts": [root_aggregate, relation_aggregate],
            **observations,
            "harness_error": False,
        },
    )

    assert result["status"] == "VIOLATION"
    assert result["verdict"] == "customer_deliverable_defect_candidate"
    assertion = result["failed_assertions"][0]
    assert assertion["kind"] == ASSERTION_KIND
    assert assertion["actual"]["relation_key_match"] is True
    assert assertion["actual"]["aggregate_request_match"] is True
    assert assertion["actual"]["root_value"] == "30"
    assert assertion["actual"]["aggregate_value"] == "25"
    assert assertion["actual"]["difference"] == "5"
    assert assertion["actual"]["observer_performed_oracle_verdict"] is False
