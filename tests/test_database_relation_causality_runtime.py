from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ai_test_asset_center import database_relation_observer_runtime as runtime
from ai_test_asset_center.database_relation_causality_runtime import (
    install_database_relation_causality_runtime,
)


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE ledger_entries ("
        "id TEXT PRIMARY KEY, account_id TEXT NOT NULL, "
        "request_id TEXT NOT NULL, amount NUMERIC NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO ledger_entries(id, account_id, request_id, amount) "
        "VALUES (?, ?, ?, ?)",
        [
            ("l-1", "a-1", "req-1", "10.00"),
            ("l-2", "a-1", "other-request", "999.00"),
            ("l-3", "a-2", "req-1", "500.00"),
        ],
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
                        "name": "ledger-db",
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


def _contract() -> dict:
    causal = {
        "schema": "qualibug.operation-causal-attribution.v1",
        "status": "BOUND",
        "operation_ref": "api:POST:/ledger",
        "treatment_step_id": "treatment-1",
        "value_source": "request.body.request_id",
        "child_database_field_id": "field:ledger_entries:request_id",
        "child_database_field_name": "request_id",
        "mapping_decision_id": "decision:ledger-request-id",
        "attribution_mode": "EXACT_REQUEST_CORRELATION",
        "causal_scope_fingerprint": "causal-scope-1",
    }
    return {
        "schema": "qualibug.database-relation-observer-contract.v1",
        "relation_observer_id": "relation-observer:ledger",
        "root_observer_id": "observer:accounts",
        "database_relationship_id": "fk:ledger:accounts",
        "parent_table_id": "table:accounts",
        "child_table_id": "table:ledger_entries",
        "child_table_name": "ledger_entries",
        "relation_predicates": [
            {
                "child_database_field_name": "account_id",
                "parent_database_field_name": "id",
                "operator": "=",
                "value_source": "request.parameter.id",
            },
            {
                "predicate_kind": "OPERATION_ATTRIBUTION",
                "child_database_field_id": "field:ledger_entries:request_id",
                "child_database_field_name": "request_id",
                "parent_database_field_name": "",
                "operator": "=",
                "value_source": "request.body.request_id",
                "mapping_decision_id": "decision:ledger-request-id",
                "operation_ref": "api:POST:/ledger",
            },
        ],
        "allowed_child_fields": [
            {
                "database_field_id": "field:ledger_entries:amount",
                "database_field_name": "amount",
            },
            {
                "database_field_id": "field:ledger_entries:request_id",
                "database_field_name": "request_id",
            },
        ],
        "query_plan": {
            "operation": "SELECT_MANY",
            "parameterized": True,
            "allowed_aggregates": ["COUNT", "SUM", "MIN", "MAX"],
            "raw_sql": "",
            "client_side_filter": False,
        },
        "causal_attribution_contract": causal,
        "causal_scope_fingerprint": "causal-scope-1",
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
        "connection_secret_embedded": False,
    }


def test_exact_operation_key_excludes_concurrent_rows(tmp_path: Path) -> None:
    project = "causal_relation_runtime"
    database = tmp_path / "ledger.sqlite3"
    _database(database)
    _config(tmp_path, project, database)
    install_database_relation_causality_runtime()

    receipt = runtime.execute_database_relation_observer_contract(
        _contract(),
        aggregate_requests=[
            {
                "aggregate": "SUM",
                "database_field_id": "field:ledger_entries:amount",
                "database_field_name": "amount",
                "alias": "related_value",
            },
            {
                "aggregate": "COUNT",
                "database_field_id": "",
                "database_field_name": "",
                "alias": "related_scope_count",
            },
        ],
        root=tmp_path,
        project=project,
        runtime_values={
            "request.parameter.id": "a-1",
            "request.body.request_id": "req-1",
        },
        runtime_contract={},
        campaign_id="campaign-1",
        execution_id="execution-1",
    )

    assert receipt["status"] == "OBSERVED"
    payload = receipt["evidence"][
        "approved_database_relation_aggregate_snapshot"
    ]
    assert str(payload["aggregate_values"]["related_value"]) == "10"
    assert payload["aggregate_values"]["related_scope_count"] == 1
    assert payload["relation_key"] == [
        {
            "child_database_field_name": "account_id",
            "parent_database_field_name": "id",
        }
    ]
    assert len(payload["relation_parameter_fingerprints"]) == 1
    assert payload["causal_attribution_applied"] is True
    assert payload["causal_attribution_predicate_count"] == 1
    assert len(payload["causal_attribution_parameter_fingerprints"]) == 1
    scope = payload["causal_attribution_scope"]
    assert scope["causal_scope_fingerprint"] == "causal-scope-1"
    assert scope["operation_ref"] == "api:POST:/ledger"
    assert scope["child_database_field_name"] == "request_id"
    assert scope["raw_causal_value_retained"] is False
    serialized = json.dumps(receipt)
    assert "req-1" not in serialized
    assert "other-request" not in serialized
    assert "999.00" not in serialized
