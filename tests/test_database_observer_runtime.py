from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ai_test_asset_center.database_observer_runtime import (
    EVIDENCE_KEY,
    OBSERVER_ID,
    execute_database_observer_contract,
    install_approved_database_observer,
    resolve_declared_read_only_database_profiles,
)
from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY


def _database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE orders (id TEXT, external_ref TEXT, status TEXT, password TEXT)"
    )
    conn.executemany(
        "INSERT INTO orders(id, external_ref, status, password) VALUES (?, ?, ?, ?)",
        [
            ("o-1", "dup", "PAID", "secret-1"),
            ("o-2", "dup", "PENDING", "secret-2"),
        ],
    )
    conn.commit()
    conn.close()


def _config(
    root: Path,
    project: str,
    services: list[dict],
    *,
    environment: str = "test",
) -> None:
    directory = root / "platform_workspace" / project
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "multi_service_config.json").write_text(
        json.dumps({"environment": environment, "services": services}),
        encoding="utf-8",
    )


def _service(
    name: str,
    path: Path,
    *,
    read_only: bool = True,
    connection_ref: str = "",
) -> dict:
    db = {
        "dialect": "sqlite",
        "path": str(path),
        "read_only": read_only,
    }
    if connection_ref:
        db["connection_ref"] = connection_ref
    return {"name": name, "environment": "test", "db": db}


def _contract(
    *,
    identity_field: str = "id",
    projection: list[str] | None = None,
) -> dict:
    projection = projection or ["id", "status"]
    return {
        "schema": "qualibug.database-observer-contract.v1",
        "observer_id": "database_observer:create-order",
        "operation_schema_binding_id": "binding:create-order:request",
        "interface_id": "api:POST:/orders",
        "database_table_id": "table:main.orders",
        "database_schema_name": "",
        "database_table_name": "orders",
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
        "selected_identity_key": [identity_field],
        "identity_predicates": [
            {
                "database_field_name": identity_field,
                "database_field_id": f"field:orders:{identity_field}",
                "operator": "=",
                "value_source": f"request.body.{identity_field}",
                "field_binding_id": f"binding:orders:{identity_field}",
            }
        ],
        "query_plan": {
            "operation": "SELECT_ONE",
            "database_table_id": "table:main.orders",
            "projection": projection,
            "predicates": [],
            "parameterized": True,
            "maximum_rows": 2,
            "raw_sql": "",
        },
    }


def test_executes_one_parameterized_read_only_sqlite_observer(tmp_path: Path) -> None:
    project = "runtime_db_observer"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, [_service("orders", database)])

    receipt = execute_database_observer_contract(
        _contract(),
        root=tmp_path,
        project=project,
        runtime_values={"request.body.id": "o-1"},
    )

    assert receipt["observer_id"] == OBSERVER_ID
    assert receipt["status"] == "OBSERVED"
    payload = receipt["evidence"][EVIDENCE_KEY]
    assert payload["match_status"] == "MATCHED_ONE"
    assert payload["rows"] == [{"id": "o-1", "status": "PAID"}]
    assert payload["parameterized"] is True
    assert payload["read_only"] is True
    assert payload["write_attempted"] is False
    assert payload["transaction_rolled_back"] is True
    assert payload["raw_sql_retained"] is False
    assert payload["predicate_values_retained"] is False
    assert payload["secret_values_retained"] is False
    assert payload["dsn_retained"] is False
    assert payload["oracle_verdict_emitted"] is False
    serialized = json.dumps(receipt)
    assert "secret-1" not in serialized
    assert str(database) not in serialized


def test_zero_rows_is_observed_not_clean_or_failed(tmp_path: Path) -> None:
    project = "runtime_db_not_found"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, [_service("orders", database)])

    receipt = execute_database_observer_contract(
        _contract(),
        root=tmp_path,
        project=project,
        runtime_values={"request.body.id": "missing"},
    )

    assert receipt["status"] == "OBSERVED"
    payload = receipt["evidence"][EVIDENCE_KEY]
    assert payload["match_status"] == "NOT_FOUND"
    assert payload["row_count"] == 0
    assert payload["oracle_verdict_emitted"] is False


def test_two_rows_expose_non_unique_identity_without_emitting_verdict(tmp_path: Path) -> None:
    project = "runtime_db_duplicate"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, [_service("orders", database)])

    receipt = execute_database_observer_contract(
        _contract(identity_field="external_ref", projection=["id", "external_ref"]),
        root=tmp_path,
        project=project,
        runtime_values={"request.body.external_ref": "dup"},
    )

    assert receipt["status"] == "OBSERVED"
    payload = receipt["evidence"][EVIDENCE_KEY]
    assert payload["match_status"] == "NON_UNIQUE_IDENTITY"
    assert payload["row_count"] == 2
    assert payload["maximum_rows"] == 2
    assert payload["oracle_verdict_emitted"] is False


def test_production_read_is_blocked_before_connection(tmp_path: Path) -> None:
    project = "runtime_db_prod"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(
        tmp_path,
        project,
        [_service("orders", database)],
        environment="production",
    )

    receipt = execute_database_observer_contract(
        _contract(),
        root=tmp_path,
        project=project,
        runtime_values={"request.body.id": "o-1"},
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "DATABASE_OBSERVER_READ_NOT_PERMITTED"
    assert "production_environment_blocked" in receipt["evidence"]["detail"]


def test_read_only_declaration_is_required(tmp_path: Path) -> None:
    project = "runtime_db_not_read_only"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, [_service("orders", database, read_only=False)])

    receipt = execute_database_observer_contract(
        _contract(),
        root=tmp_path,
        project=project,
        runtime_values={"request.body.id": "o-1"},
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "DATABASE_OBSERVER_CONNECTION_BINDING_FAILED"
    assert "read_only_declaration_required" in receipt["evidence"]["detail"]


def test_multi_database_requires_explicit_connection_ref(tmp_path: Path) -> None:
    project = "runtime_db_multi"
    first = tmp_path / "orders.sqlite3"
    second = tmp_path / "archive.sqlite3"
    _database(first)
    _database(second)
    _config(
        tmp_path,
        project,
        [
            _service("orders", first, connection_ref="orders-db"),
            _service("archive", second, connection_ref="archive-db"),
        ],
    )

    blocked = execute_database_observer_contract(
        _contract(),
        root=tmp_path,
        project=project,
        runtime_values={"request.body.id": "o-1"},
    )
    assert blocked["reason_code"] == "DATABASE_OBSERVER_CONNECTION_BINDING_FAILED"
    assert "connection_ref_required_for_multi_database" in blocked["evidence"]["detail"]

    observed = execute_database_observer_contract(
        _contract(),
        root=tmp_path,
        project=project,
        runtime_values={"request.body.id": "o-1"},
        connection_ref="orders-db",
    )
    assert observed["status"] == "OBSERVED"
    profile = observed["evidence"][EVIDENCE_KEY]["connection_profile"]
    assert profile["connection_ref"] == "orders-db"
    assert profile["secret_value_retained"] is False
    assert profile["dsn_retained"] is False


def test_identity_value_must_be_materialized_before_query(tmp_path: Path) -> None:
    project = "runtime_db_missing_value"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, [_service("orders", database)])

    receipt = execute_database_observer_contract(
        _contract(), root=tmp_path, project=project, runtime_values={}
    )

    assert receipt["reason_code"] == "DATABASE_OBSERVER_IDENTITY_VALUE_MISSING"
    assert receipt["evidence"]["missing_value_sources"] == ["request.body.id"]


def test_secret_projection_is_refused_before_connection(tmp_path: Path) -> None:
    receipt = execute_database_observer_contract(
        _contract(projection=["id", "password"]),
        root=tmp_path,
        project="unused",
        runtime_values={"request.body.id": "o-1"},
    )

    assert receipt["reason_code"] == "DATABASE_OBSERVER_CONTRACT_REFUSED"
    assert "secret_field_refused" in receipt["evidence"]["detail"]


def test_profile_resolution_retains_no_dsn_contract(tmp_path: Path) -> None:
    project = "runtime_db_profiles"
    database = tmp_path / "orders.sqlite3"
    _database(database)
    _config(tmp_path, project, [_service("orders", database)])

    profiles = resolve_declared_read_only_database_profiles(tmp_path, project)

    assert profiles[0]["connection_ref"] == "orders"
    assert profiles[0]["dialect"] == "sqlite"
    assert "dsn" not in profiles[0]


def test_registration_uses_existing_db_sql_observer_chain() -> None:
    registered = install_approved_database_observer()
    assert registered == OBSERVER_ID
    assert OBSERVER_REGISTRY[OBSERVER_ID]["adapter"] == "db_sql"
    assert OBSERVER_REGISTRY[OBSERVER_ID]["surface"] == "database_read_only"
    assert EVIDENCE_KEY in OBSERVER_REGISTRY[OBSERVER_ID]["evidence_keys"]
