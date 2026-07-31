from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

from ai_test_asset_center import database_observer_experiment_runtime as phase_runtime
from ai_test_asset_center.database_relation_delta_causality_authority import (
    evaluate_database_relation_causal_delta_with_authority,
)
from ai_test_asset_center.database_relation_delta_causality_projection import (
    ASSERTION_KIND,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers
from ai_test_asset_center.operation_causality_runtime import (
    finalize_operation_causality_transport,
)
from tests.database_relation_delta_causality_fixtures import build_spec


install_non_http_observers()
_RELATION_DECISION_ID = "decision:relation"


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE accounts (id TEXT PRIMARY KEY, balance NUMERIC NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE ledger_entries ("
        "id TEXT PRIMARY KEY, account_id TEXT NOT NULL, "
        "request_id TEXT NOT NULL, amount NUMERIC NOT NULL)"
    )
    connection.execute(
        "INSERT INTO accounts(id, balance) VALUES ('a-1', 100)"
    )
    connection.execute(
        "INSERT INTO ledger_entries(id, account_id, request_id, amount) "
        "VALUES ('existing-noise', 'a-1', 'older-request', 20)"
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
                "database_field_id": "field:accounts:id",
                "operator": "=",
                "value_source": "request.parameter.id",
                "field_binding_id": "binding:accounts:id",
            }
        ],
        "field_bindings": [
            {
                "field_binding_id": "binding:accounts:id",
                "api_field_id": "api-field:Account.id",
                "api_field_name": "id",
                "database_field_id": "field:accounts:id",
                "database_field_name": "id",
                "authoritative": True,
                "read_only": True,
            },
            {
                "field_binding_id": "binding:accounts:balance",
                "api_field_id": "api-field:Account.balance",
                "api_field_name": "balance",
                "database_field_id": "field:accounts:balance",
                "database_field_name": "balance",
                "authoritative": True,
                "read_only": True,
            },
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


def _relation_contract(assertion: dict) -> dict:
    causal = deepcopy(assertion["causal_attribution_contract"])
    return {
        "schema": "qualibug.database-relation-observer-contract.v1",
        "relation_observer_id": "relation-observer:ledger",
        "relation_mapping_decision_id": _RELATION_DECISION_ID,
        "root_observer_id": "observer:accounts",
        "database_relationship_id": "fk:ledger:accounts",
        "parent_table_id": "table:accounts",
        "parent_table_name": "accounts",
        "child_table_id": "table:ledger_entries",
        "child_table_name": "ledger_entries",
        "relation_predicates": [
            {
                "child_database_field_name": "account_id",
                "parent_database_field_name": "id",
                "parent_field_binding_id": "binding:accounts:id",
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
                "mapping_decision_id": _RELATION_DECISION_ID,
                "operation_ref": "api:POST:/ledger",
            },
        ],
        "allowed_child_fields": [
            {
                "database_field_id": "field:ledger_entries:amount",
                "database_field_name": "amount",
                "database_declared_type": "NUMERIC",
            },
            {
                "database_field_id": "field:ledger_entries:request_id",
                "database_field_name": "request_id",
                "database_declared_type": "TEXT",
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
        "causal_scope_fingerprint": assertion["causal_scope_fingerprint"],
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
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


def _experiment() -> dict:
    assertion = build_spec()
    root_contract = _root_contract()
    relation_contract = _relation_contract(assertion)
    return {
        "experiment_id": "experiment:causal-ledger",
        "obligation_id": "obligation:causal-ledger",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "source_refs": assertion["source_refs"],
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": "treatment-1",
                "operation_ref": "api:POST:/ledger",
                "body": {
                    "request_id": "req-1",
                    "account_id": "{id}",
                    "amount": 10,
                },
            }
        ],
        "observers": [
            {
                "observer_id": "approved_database_phase_aggregate",
                "adapter": "db_sql",
            },
            {
                "observer_id": "approved_database_relation_phase_aggregate",
                "adapter": "db_sql",
            },
            {
                "observer_id": "operation_causality_transport",
                "adapter": "process_ledger",
            },
        ],
        "database_observer_execution_drafts": [
            {
                "schema": "qualibug.database-observer-execution-draft.v1",
                "draft_id": assertion["root_before_draft_id"],
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
                "draft_id": assertion["root_after_draft_id"],
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
                "draft_id": assertion["relation_before_draft_id"],
                "relation_pair_id": assertion["relation_pair_id"],
                "observer_handler_id": "approved_database_relation_aggregate",
                "relation_observer_contract_ref": "relation-observer:ledger",
                "root_observer_contract_ref": "observer:accounts",
                "observation_phase": "BEFORE",
                "database_relation_observer_contract": relation_contract,
                "causal_attribution_contract": deepcopy(
                    assertion["causal_attribution_contract"]
                ),
                "causal_scope_fingerprint": assertion[
                    "causal_scope_fingerprint"
                ],
                "aggregate_requests": _relation_requests(),
                "identity_value_sources": [
                    "request.parameter.id",
                    "request.body.request_id",
                ],
                "database_connection_ref": "",
                "required": True,
            },
            {
                "schema": "qualibug.database-relation-observer-execution-draft.v1",
                "draft_id": assertion["relation_after_draft_id"],
                "relation_pair_id": assertion["relation_pair_id"],
                "observer_handler_id": "approved_database_relation_aggregate",
                "relation_observer_contract_ref": "relation-observer:ledger",
                "root_observer_contract_ref": "observer:accounts",
                "observation_phase": "AFTER",
                "database_relation_observer_contract": relation_contract,
                "causal_attribution_contract": deepcopy(
                    assertion["causal_attribution_contract"]
                ),
                "causal_scope_fingerprint": assertion[
                    "causal_scope_fingerprint"
                ],
                "aggregate_requests": _relation_requests(),
                "identity_value_sources": [
                    "request.parameter.id",
                    "request.body.request_id",
                ],
                "database_connection_ref": "",
                "required": True,
            },
        ],
        "assertions": [assertion],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "status": "RESOLVED",
            "assertion_kind": ASSERTION_KIND,
            "operation_causality_bound": True,
        },
        "safety_contract": {"governed_write": False},
    }


def _transport_result() -> dict:
    return {
        "request_bodies_for_cleanup": {
            "treatment-1": {
                "request_id": "req-1",
                "account_id": "a-1",
                "amount": 10,
            }
        },
        "steps": [
            {
                "phase": "treatment",
                "step_id": "treatment-1",
                "operation_ref": "api:POST:/ledger",
                "status_code": 201,
                "request_body_fingerprint": "body-fingerprint",
                "request_semantics_fingerprint": "semantics-fingerprint",
                "governance_receipt": {"receipt_id": "transport-receipt-1"},
            }
        ],
    }


def test_real_sqlite_causal_delta_excludes_concurrent_noise(
    tmp_path: Path,
) -> None:
    project = "causal_delta_runtime"
    database = tmp_path / "causal.sqlite3"
    _database(database)
    _config(tmp_path, project, database)
    experiment = _experiment()
    observations: dict = {}

    before = phase_runtime.execute_database_observer_phase(
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

    transport = finalize_operation_causality_transport(
        exp=experiment,
        result=_transport_result(),
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )[0]
    assert transport["status"] == "ATTRIBUTED"

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE accounts SET balance = 90 WHERE id = 'a-1'"
    )
    connection.execute(
        "INSERT INTO ledger_entries(id, account_id, request_id, amount) "
        "VALUES ('current-operation', 'a-1', 'req-1', 10)"
    )
    connection.execute(
        "INSERT INTO ledger_entries(id, account_id, request_id, amount) "
        "VALUES ('concurrent-noise', 'a-1', 'other-request', 500)"
    )
    connection.commit()
    connection.close()

    after = phase_runtime.execute_database_observer_phase(
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
                "step_id": "treatment-1",
                "operation_ref": "api:POST:/ledger",
                "status_code": 201,
                "body": {"accepted": True},
                "response": {
                    "status_code": 201,
                    "body": {"accepted": True},
                },
            }
        ],
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    assert after["status"] == "OBSERVED"

    payloads = {
        row["observation_phase"]: row["evidence"][
            "approved_database_relation_aggregate_snapshot"
        ]
        for row in observations[
            "approved_database_relation_phase_receipts"
        ]
    }
    assert str(payloads["AFTER"]["aggregate_values"]["related_value"]) == "10"
    assert payloads["AFTER"]["aggregate_values"]["related_scope_count"] == 1
    assert payloads["AFTER"]["causal_attribution_scope"][
        "relation_authority_match"
    ] is True

    result = evaluate_database_relation_causal_delta_with_authority(
        {
            "spec": experiment["assertions"][0],
            "observations": observations,
        }
    )
    assert result["passed"] is True
    assert result["reason_code"] == ""
    assert result["actual"]["root_delta"] == "-10"
    assert result["actual"]["relation_delta"] == "10"
    assert result["actual"]["difference"] == "0"
    assert result["actual"]["relation_authority_match"] is True
    assert result["actual"]["causal_value_fingerprint_match"] is True
