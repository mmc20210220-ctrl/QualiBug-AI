from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ai_test_asset_center.database_observer_experiment_runtime import (
    aggregate_database_observer_phase_receipts,
    execute_database_observer_phase,
)


def _database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO orders(id, status) VALUES ('o-1', 'PENDING')")
    conn.commit()
    conn.close()


def _config(root: Path, project: str, path: Path) -> None:
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
                            "path": str(path),
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
        "schema": "qualibug.database-observer-contract.v1",
        "observer_id": "observer:orders",
        "operation_schema_binding_id": "binding:update-order",
        "interface_id": "api:PATCH:/orders/{id}",
        "database_table_id": "table:orders",
        "database_table_name": "orders",
        "database_schema_name": "",
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
                "operator": "=",
                "value_source": "request.body.id",
                "field_binding_id": "field-binding:id",
            }
        ],
        "query_plan": {
            "operation": "SELECT_ONE",
            "projection": ["id", "status"],
            "parameterized": True,
            "maximum_rows": 2,
            "raw_sql": "",
        },
    }


def _draft(phase: str) -> dict:
    return {
        "schema": "qualibug.database-observer-execution-draft.v1",
        "draft_id": f"draft:orders:{phase.lower()}",
        "runtime_materialization_ref": "materialization:orders",
        "observer_handler_id": "approved_database_readback",
        "observer_contract_ref": "observer:orders",
        "observation_phase": phase,
        "database_observer_contract": _contract(),
        "database_connection_ref": "",
        "identity_value_sources": ["request.body.id"],
        "required": True,
    }


def _experiment() -> dict:
    return {
        "experiment_id": "experiment:update-order",
        "database_observer_execution_drafts": [_draft("BEFORE"), _draft("AFTER")],
        "treatment_plan": [
            {
                "step_id": "treatment:1",
                "operation_ref": "api:PATCH:/orders/{id}",
                "body": {"id": "{id}", "status": "PAID"},
            }
        ],
    }


def test_true_before_and_after_snapshots_are_captured_before_cleanup(tmp_path: Path) -> None:
    project = "database_phase_runtime"
    path = tmp_path / "orders.sqlite3"
    _database(path)
    _config(tmp_path, project, path)
    exp = _experiment()
    observations: dict = {}

    before = execute_database_observer_phase(
        exp,
        phase="BEFORE",
        root=tmp_path,
        project=project,
        runtime_contract={},
        runtime_bindings={"id": "o-1"},
        observations=observations,
        steps_out=[],
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    assert before["status"] == "OBSERVED"
    assert before["executed_before_transport"] is True

    conn = sqlite3.connect(path)
    conn.execute("UPDATE orders SET status='PAID' WHERE id='o-1'")
    conn.commit()
    conn.close()

    after = execute_database_observer_phase(
        exp,
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
    assert after["status"] == "OBSERVED"
    assert after["executed_after_transport_before_cleanup"] is True

    phase_receipts = observations["approved_database_observer_phase_receipts"]
    before_payload = next(
        row["evidence"]["approved_database_snapshot"]
        for row in phase_receipts
        if row["observation_phase"] == "BEFORE"
    )
    after_payload = next(
        row["evidence"]["approved_database_snapshot"]
        for row in phase_receipts
        if row["observation_phase"] == "AFTER"
    )
    assert before_payload["rows"] == [{"id": "o-1", "status": "PENDING"}]
    assert after_payload["rows"] == [{"id": "o-1", "status": "PAID"}]

    # Remove the database before finalization: aggregation must use phase receipts and never requery.
    path.unlink()
    aggregate = aggregate_database_observer_phase_receipts(
        {
            "experiment": exp,
            "observations": observations,
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )
    assert aggregate["status"] == "OBSERVED"
    evidence = aggregate["evidence"]
    assert evidence["phase_pair_complete"] is True
    assert evidence["finalizer_database_requery_count"] == 0
    assert evidence["before_phase_count"] == 1
    assert evidence["after_phase_count"] == 1
    assert evidence["oracle_verdict_emitted"] is False


def test_missing_before_identity_blocks_before_transport(tmp_path: Path) -> None:
    project = "database_phase_missing_identity"
    path = tmp_path / "orders.sqlite3"
    _database(path)
    _config(tmp_path, project, path)
    observations: dict = {}

    result = execute_database_observer_phase(
        _experiment(),
        phase="BEFORE",
        root=tmp_path,
        project=project,
        runtime_contract={},
        runtime_bindings={},
        observations=observations,
        steps_out=[],
        campaign_id="campaign-1",
        execution_id="execution-1",
    )

    assert result["status"] == "INDETERMINATE"
    assert result["blocked"] is True
    assert result["reason_code"] == "DATABASE_OBSERVER_BEFORE_PHASE_INCOMPLETE"
    assert observations["harness_error"] is True


def test_finalizer_refuses_to_requery_when_after_phase_is_missing(tmp_path: Path) -> None:
    project = "database_phase_missing_after"
    path = tmp_path / "orders.sqlite3"
    _database(path)
    _config(tmp_path, project, path)
    exp = _experiment()
    observations: dict = {}
    execute_database_observer_phase(
        exp,
        phase="BEFORE",
        root=tmp_path,
        project=project,
        runtime_contract={},
        runtime_bindings={"id": "o-1"},
        observations=observations,
        steps_out=[],
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    path.unlink()

    aggregate = aggregate_database_observer_phase_receipts(
        {"experiment": exp, "observations": observations}
    )

    assert aggregate["status"] == "INDETERMINATE"
    assert aggregate["reason_code"] == "DATABASE_OBSERVER_REQUIRED_PHASE_RECEIPT_MISSING"
    assert aggregate["evidence"]["finalizer_database_requery_count"] == 0
    assert aggregate["evidence"]["missing_required_phases"] == [
        {"draft_id": "draft:orders:after", "phase": "AFTER"}
    ]
