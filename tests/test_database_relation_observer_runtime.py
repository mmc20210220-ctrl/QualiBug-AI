from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ai_test_asset_center.database_relation_observer_runtime import (
    EVIDENCE_KEY,
    OBSERVER_ID,
    execute_database_relation_observer_contract,
)


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, total NUMERIC)")
    connection.execute(
        "CREATE TABLE order_lines (id TEXT PRIMARY KEY, order_id TEXT, amount NUMERIC, password TEXT)"
    )
    connection.executemany(
        "INSERT INTO orders(id, total) VALUES (?, ?)",
        [("o-1", "30.00"), ("o-2", "99.00")],
    )
    connection.executemany(
        "INSERT INTO order_lines(id, order_id, amount, password) VALUES (?, ?, ?, ?)",
        [
            ("l-1", "o-1", "10.00", "secret-a"),
            ("l-2", "o-1", "20.00", "secret-b"),
            ("l-3", "o-2", "99.00", "secret-c"),
        ],
    )
    connection.commit()
    connection.close()


def _config(root: Path, project: str, database: Path, *, environment: str = "test") -> None:
    directory = root / "platform_workspace" / project
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "multi_service_config.json").write_text(
        json.dumps(
            {
                "environment": environment,
                "services": [
                    {
                        "name": "orders-db",
                        "environment": environment,
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
            },
            {
                "database_field_id": "field:order_lines:password",
                "database_field_name": "password",
                "database_declared_type": "TEXT",
            },
        ],
        "query_plan": {
            "operation": "SELECT_MANY",
            "parameterized": True,
            "maximum_rows": 10000,
            "allowed_aggregates": ["COUNT", "SUM", "MIN", "MAX"],
            "order_by": [],
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


def test_database_executes_fk_filtered_sum_and_count_without_retaining_rows(
    tmp_path: Path,
) -> None:
    project = "relation_runtime"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, database)

    receipt = execute_database_relation_observer_contract(
        _contract(),
        aggregate_requests=[
            {
                "aggregate": "SUM",
                "database_field_id": "field:order_lines:amount",
                "database_field_name": "amount",
                "alias": "line_total",
            },
            {"aggregate": "COUNT", "alias": "line_count"},
        ],
        root=tmp_path,
        project=project,
        runtime_values={"request.parameter.id": "o-1"},
    )

    assert receipt["observer_id"] == OBSERVER_ID
    assert receipt["status"] == "OBSERVED"
    payload = receipt["evidence"][EVIDENCE_KEY]
    assert str(payload["aggregate_values"]["line_total"]) == "30"
    assert payload["aggregate_values"]["line_count"] == 2
    assert payload["relation_parameter_fingerprints"]
    assert payload["client_side_filter_used"] is False
    assert payload["raw_rows_retained"] is False
    assert payload["raw_sql_retained"] is False
    assert payload["predicate_values_retained"] is False
    assert payload["secret_values_retained"] is False
    assert payload["dsn_retained"] is False
    assert payload["write_attempted"] is False
    assert payload["transaction_rolled_back"] is True
    assert payload["oracle_verdict_emitted"] is False
    serialized = json.dumps(receipt)
    assert "secret-a" not in serialized
    assert "secret-b" not in serialized
    assert "secret-c" not in serialized
    assert "o-1" not in serialized
    assert str(database) not in serialized


def test_relation_aggregate_does_not_include_rows_from_another_parent(tmp_path: Path) -> None:
    project = "relation_scope"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, database)

    receipt = execute_database_relation_observer_contract(
        _contract(),
        aggregate_requests=[
            {
                "aggregate": "SUM",
                "database_field_id": "field:order_lines:amount",
                "database_field_name": "amount",
                "alias": "line_total",
            }
        ],
        root=tmp_path,
        project=project,
        runtime_values={"request.parameter.id": "o-2"},
    )

    assert receipt["status"] == "OBSERVED"
    assert str(receipt["evidence"][EVIDENCE_KEY]["aggregate_values"]["line_total"]) == "99"


def test_missing_parent_value_is_indeterminate_before_connection(tmp_path: Path) -> None:
    project = "relation_missing_parent"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, database)

    receipt = execute_database_relation_observer_contract(
        _contract(),
        aggregate_requests=[{"aggregate": "COUNT", "alias": "line_count"}],
        root=tmp_path,
        project=project,
        runtime_values={},
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "DATABASE_RELATION_PARENT_VALUE_MISSING"
    assert receipt["evidence"]["write_attempted"] is False


def test_sensitive_child_field_cannot_be_aggregated(tmp_path: Path) -> None:
    project = "relation_sensitive"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, database)

    receipt = execute_database_relation_observer_contract(
        _contract(),
        aggregate_requests=[
            {
                "aggregate": "MAX",
                "database_field_id": "field:order_lines:password",
                "database_field_name": "password",
                "alias": "password_max",
            }
        ],
        root=tmp_path,
        project=project,
        runtime_values={"request.parameter.id": "o-1"},
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "DATABASE_RELATION_OBSERVER_CONTRACT_REFUSED"
    serialized = json.dumps(receipt)
    assert "secret-a" not in serialized
    assert "secret-b" not in serialized
    assert "secret-c" not in serialized


def test_production_relation_read_is_blocked(tmp_path: Path) -> None:
    project = "relation_production"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, database, environment="production")

    receipt = execute_database_relation_observer_contract(
        _contract(),
        aggregate_requests=[{"aggregate": "COUNT", "alias": "line_count"}],
        root=tmp_path,
        project=project,
        runtime_values={"request.parameter.id": "o-1"},
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "DATABASE_RELATION_OBSERVER_READ_NOT_PERMITTED"
