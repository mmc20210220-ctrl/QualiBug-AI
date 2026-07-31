from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
)
from ai_test_asset_center.database_numeric_oracle import (
    DATABASE_NUMERIC_DELTA_ASSERTION_KIND,
)
from ai_test_asset_center.database_observer_experiment_runtime import (
    aggregate_database_observer_phase_receipts,
    execute_database_observer_phase,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers


install_non_http_observers()


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE accounts (id TEXT PRIMARY KEY, balance NUMERIC NOT NULL)"
    )
    connection.execute(
        "INSERT INTO accounts(id, balance) VALUES ('a-1', '100.00')"
    )
    connection.commit()
    connection.close()


def _config(root: Path, project: str, path: Path) -> None:
    directory = root / "platform_workspace" / project
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "multi_service_config.json").write_text(
        json.dumps(
            {
                "environment": "test",
                "services": [
                    {
                        "name": "accounts-db",
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
        "observer_id": "observer:accounts",
        "operation_schema_binding_id": "binding:debit-account",
        "interface_id": "api:POST:/accounts/{id}/debit",
        "database_table_id": "table:accounts",
        "database_table_name": "accounts",
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
            "projection": ["id", "balance"],
            "parameterized": True,
            "maximum_rows": 2,
            "raw_sql": "",
        },
    }


def _draft(phase: str) -> dict:
    return {
        "schema": "qualibug.database-observer-execution-draft.v1",
        "draft_id": f"draft:accounts:{phase.lower()}",
        "runtime_materialization_ref": "materialization:accounts",
        "observer_handler_id": "approved_database_readback",
        "observer_contract_ref": "observer:accounts",
        "observation_phase": phase,
        "database_observer_contract": _contract(),
        "database_connection_ref": "",
        "identity_value_sources": ["request.body.id"],
        "required": True,
    }


def _experiment() -> dict:
    return {
        "experiment_id": "experiment:accounts",
        "obligation_id": "obligation:accounts",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "source_refs": [{"kind": "business_rule", "locator": "BR-BALANCE-1"}],
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": "treatment-1",
                "operation_ref": "api:POST:/accounts/{id}/debit",
                "body": {"id": "{id}", "amount": "10.00"},
            }
        ],
        "observers": [
            {
                "observer_id": "approved_database_phase_aggregate",
                "adapter": "db_sql",
            }
        ],
        "database_observer_execution_drafts": [_draft("BEFORE"), _draft("AFTER")],
        "assertions": [
            {
                "assertion_id": "assert:accounts:balance",
                "kind": DATABASE_NUMERIC_DELTA_ASSERTION_KIND,
                "source_assertion_kind": "field_delta",
                "require_control": False,
                "numeric_policy": "FIELD_DELTA",
                "numeric_terms": [
                    {
                        "term_id": "term:balance",
                        "database_observer_contract_ref": "observer:accounts",
                        "before_draft_id": "draft:accounts:before",
                        "after_draft_id": "draft:accounts:after",
                        "database_table_ref": "table:accounts",
                        "database_table_name": "accounts",
                        "database_field_id": "field:accounts:balance",
                        "database_field_name": "balance",
                        "field_binding_id": "binding:accounts:balance",
                        # Deliberately wrong expectation. Real DB delta is -5.
                        "expected_delta": "-10.00",
                        "tolerance": "0",
                    }
                ],
            }
        ],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "assertion_kind": DATABASE_NUMERIC_DELTA_ASSERTION_KIND,
            "status": "RESOLVED",
        },
        "safety_contract": {"governed_write": False},
    }


def test_real_sqlite_before_after_reaches_contract_oracle(tmp_path: Path) -> None:
    project = "database_numeric_runtime"
    database_path = tmp_path / "accounts.sqlite3"
    _database(database_path)
    _config(tmp_path, project, database_path)
    experiment = _experiment()
    observations: dict = {}

    before = execute_database_observer_phase(
        experiment,
        phase="BEFORE",
        root=tmp_path,
        project=project,
        runtime_contract={},
        runtime_bindings={"id": "a-1"},
        observations=observations,
        steps_out=[],
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    assert before["status"] == "OBSERVED"

    connection = sqlite3.connect(database_path)
    connection.execute("UPDATE accounts SET balance='95.00' WHERE id='a-1'")
    connection.commit()
    connection.close()

    after = execute_database_observer_phase(
        experiment,
        phase="AFTER",
        root=tmp_path,
        project=project,
        runtime_contract={},
        runtime_bindings={"id": "a-1"},
        observations=observations,
        steps_out=[
            {
                "phase": "treatment",
                "response": {"status_code": 200, "body": {"id": "a-1"}},
            }
        ],
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    assert after["status"] == "OBSERVED"

    aggregate = aggregate_database_observer_phase_receipts(
        {
            "experiment": experiment,
            "observations": observations,
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )
    assert aggregate["status"] == "OBSERVED"
    assert aggregate["evidence"]["oracle_verdict_emitted"] is False

    treatment = build_contract_evidence_receipt(
        kind="treatment",
        experiment_id="experiment:accounts",
        obligation_id="obligation:accounts",
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
            "observer_receipts": [aggregate],
            "approved_database_observer_phase_receipts": observations[
                "approved_database_observer_phase_receipts"
            ],
            "harness_error": False,
        },
    )

    assert result["status"] == "VIOLATION"
    assert result["verdict"] == "customer_deliverable_defect_candidate"
    assertion = result["failed_assertions"][0]
    assert assertion["kind"] == DATABASE_NUMERIC_DELTA_ASSERTION_KIND
    assert assertion["reason_code"] == "DATABASE_NUMERIC_DELTA_MISMATCH"
    term = assertion["actual"]["term_results"][0]
    assert term["observed_before_decimal"] == "100"
    assert term["observed_after_decimal"] == "95"
    assert term["actual_delta"] == "-5"
    assert term["observer_performed_oracle_verdict"] is False
