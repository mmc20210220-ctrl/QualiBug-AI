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
from ai_test_asset_center.database_relation_delta_experiment_projection import (
    ASSERTION_KIND,
)
from ai_test_asset_center.database_relation_observer_experiment_runtime import (
    aggregate_database_relation_phase_receipts,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers


install_non_http_observers()


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE accounts (id TEXT PRIMARY KEY, balance NUMERIC NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE ledger_entries "
        "(id TEXT PRIMARY KEY, account_id TEXT NOT NULL, amount NUMERIC NOT NULL)"
    )
    connection.execute(
        "INSERT INTO accounts(id, balance) VALUES ('a-1', '100.00')"
    )
    connection.execute(
        "INSERT INTO ledger_entries(id, account_id, amount) "
        "VALUES ('l-1', 'a-1', '20.00')"
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
                        "name": "accounts-db",
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
        "observer_id": "observer:accounts",
        "operation_schema_binding_id": "binding:update-account",
        "interface_id": "api:POST:/ledger",
        "database_table_id": "table:accounts",
        "database_schema_name": "",
        "database_table_name": "accounts",
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
                "database_field_id": "field:accounts:id",
                "operator": "=",
                "value_source": "request.parameter.id",
                "field_binding_id": "binding:accounts:id",
            }
        ],
        "query_plan": {
            "operation": "SELECT_ONE",
            "database_table_id": "table:accounts",
            "projection": ["id", "balance"],
            "predicates": [],
            "parameterized": True,
            "maximum_rows": 2,
            "raw_sql": "",
        },
    }


def _relation_contract() -> dict:
    return {
        "schema": "qualibug.database-relation-observer-contract.v1",
        "relation_observer_id": "relation-observer:ledger",
        "candidate_id": "candidate:ledger",
        "relation_mapping_decision_id": "decision:ledger",
        "root_observer_id": "observer:accounts",
        "database_relationship_id": "fk:ledger:accounts",
        "parent_table_id": "table:accounts",
        "parent_table_name": "accounts",
        "parent_columns": ["id"],
        "child_table_id": "table:ledger_entries",
        "child_table_name": "ledger_entries",
        "child_columns": ["account_id"],
        "relation_predicates": [
            {
                "ordinal": 0,
                "child_database_field_name": "account_id",
                "parent_database_field_name": "id",
                "parent_database_field_id": "field:accounts:id",
                "parent_field_binding_id": "binding:accounts:id",
                "operator": "=",
                "value_source": "request.parameter.id",
            }
        ],
        "allowed_child_fields": [
            {
                "database_field_id": "field:ledger_entries:amount",
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


def _relation_requests() -> list[dict]:
    return [
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
    ]


def _assertion() -> dict:
    return {
        "assertion_id": "assert:balance-ledger-delta",
        "kind": ASSERTION_KIND,
        "source_assertion_kind": "conservation",
        "require_control": False,
        "database_relation_observer_ref": "relation-observer:ledger",
        "database_relationship_id": "fk:ledger:accounts",
        "relation_key": [
            {
                "child_database_field_name": "account_id",
                "parent_database_field_name": "id",
            }
        ],
        "relation_pair_id": "pair-1",
        "relation_before_draft_id": "draft:relation:before",
        "relation_after_draft_id": "draft:relation:after",
        "root_observer_contract_ref": "observer:accounts",
        "root_before_draft_id": "draft:accounts:before",
        "root_after_draft_id": "draft:accounts:after",
        "root_table_ref": "table:accounts",
        "root_database_field_id": "field:accounts:balance",
        "root_database_field_name": "balance",
        "child_table_ref": "table:ledger_entries",
        "child_database_field_id": "field:ledger_entries:amount",
        "child_database_field_name": "amount",
        "aggregate": "SUM",
        "aggregate_alias": "related_value",
        "scope_count_alias": "related_scope_count",
        "comparison_operator": "EQ",
        "aggregate_on_left": False,
        "left_coefficient": -1,
        "right_coefficient": 1,
        "tolerance": "0",
    }


def _experiment() -> dict:
    root_contract = _root_contract()
    relation_contract = _relation_contract()
    return {
        "experiment_id": "experiment:balance-ledger",
        "obligation_id": "obligation:balance-ledger",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "source_refs": [{"kind": "business_rule", "locator": "BR-BALANCE-LEDGER"}],
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": "treatment-1",
                "operation_ref": "api:POST:/ledger",
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
                "draft_id": "draft:accounts:before",
                "observer_handler_id": "approved_database_readback",
                "observer_contract_ref": "observer:accounts",
                "observation_phase": "BEFORE",
                "database_observer_contract": root_contract,
                "database_connection_ref": "",
                "identity_value_sources": ["request.parameter.id"],
                "required": True,
            },
            {
                "schema": "qualibug.database-observer-execution-draft.v1",
                "draft_id": "draft:accounts:after",
                "observer_handler_id": "approved_database_readback",
                "observer_contract_ref": "observer:accounts",
                "observation_phase": "AFTER",
                "database_observer_contract": root_contract,
                "database_connection_ref": "",
                "identity_value_sources": ["request.parameter.id"],
                "required": True,
            },
        ],
        "database_relation_observer_execution_drafts": [
            {
                "schema": "qualibug.database-relation-observer-execution-draft.v1",
                "draft_id": "draft:relation:before",
                "relation_pair_id": "pair-1",
                "observer_handler_id": "approved_database_relation_aggregate",
                "relation_observer_contract_ref": "relation-observer:ledger",
                "root_observer_contract_ref": "observer:accounts",
                "observation_phase": "BEFORE",
                "database_relation_observer_contract": relation_contract,
                "aggregate_requests": _relation_requests(),
                "identity_value_sources": ["request.parameter.id"],
                "database_connection_ref": "",
                "required": True,
            },
            {
                "schema": "qualibug.database-relation-observer-execution-draft.v1",
                "draft_id": "draft:relation:after",
                "relation_pair_id": "pair-1",
                "observer_handler_id": "approved_database_relation_aggregate",
                "relation_observer_contract_ref": "relation-observer:ledger",
                "root_observer_contract_ref": "observer:accounts",
                "observation_phase": "AFTER",
                "database_relation_observer_contract": relation_contract,
                "aggregate_requests": _relation_requests(),
                "identity_value_sources": ["request.parameter.id"],
                "database_connection_ref": "",
                "required": True,
            },
        ],
        "assertions": [_assertion()],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "status": "RESOLVED",
            "assertion_kind": ASSERTION_KIND,
        },
        "safety_contract": {"governed_write": False},
    }


def test_real_sqlite_before_after_relation_delta_reaches_contract_oracle(
    tmp_path: Path,
) -> None:
    project = "relation_delta_runtime"
    database = tmp_path / "accounts.sqlite3"
    _database(database)
    _config(tmp_path, project, database)
    experiment = _experiment()
    observations: dict = {}

    before_summary = phase_runtime.execute_database_observer_phase(
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
    assert before_summary["status"] == "OBSERVED"
    assert before_summary["blocked"] is False

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE accounts SET balance = '85.00' WHERE id = 'a-1'"
    )
    connection.execute(
        "INSERT INTO ledger_entries(id, account_id, amount) "
        "VALUES ('l-2', 'a-1', '10.00')"
    )
    connection.commit()
    connection.close()

    after_summary = phase_runtime.execute_database_observer_phase(
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
    assert after_summary["status"] == "OBSERVED"
    assert len(observations["approved_database_observer_phase_receipts"]) == 2
    assert len(observations["approved_database_relation_phase_receipts"]) == 2

    relation_payloads = {
        row["observation_phase"]: row["evidence"][
            "approved_database_relation_aggregate_snapshot"
        ]
        for row in observations["approved_database_relation_phase_receipts"]
    }
    assert str(relation_payloads["BEFORE"]["aggregate_values"]["related_value"]) == "20"
    assert str(relation_payloads["AFTER"]["aggregate_values"]["related_value"]) == "30"
    assert relation_payloads["BEFORE"]["aggregate_values"]["related_scope_count"] == 1
    assert relation_payloads["AFTER"]["aggregate_values"]["related_scope_count"] == 2
    assert relation_payloads["AFTER"]["raw_rows_retained"] is False
    assert relation_payloads["AFTER"]["oracle_verdict_emitted"] is False

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
        experiment_id="experiment:balance-ledger",
        obligation_id="obligation:balance-ledger",
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
    failed = result["failed_assertions"][0]
    assert failed["kind"] == ASSERTION_KIND
    assert failed["actual"]["root_before"] == "100"
    assert failed["actual"]["root_after"] == "85"
    assert failed["actual"]["root_delta"] == "-15"
    assert failed["actual"]["relation_before"] == "20"
    assert failed["actual"]["relation_after"] == "30"
    assert failed["actual"]["relation_delta"] == "10"
    assert failed["actual"]["difference"] == "5"
    assert failed["actual"]["observer_performed_oracle_verdict"] is False
