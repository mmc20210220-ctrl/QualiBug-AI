from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_state_transition_experiment_projection import (
    project_database_state_transition_assertions,
)
from ai_test_asset_center.database_state_transition_oracle import (
    DATABASE_STATE_TRANSITION_ASSERTION_KIND,
)


def _contract() -> dict:
    return {
        "schema": "qualibug.database-observer-contract.v1",
        "observer_id": "observer:orders",
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
        "database_table_id": "table:orders",
        "database_table_name": "orders",
        "field_bindings": [
            {
                "field_binding_id": "binding:orders:status",
                "api_field_id": "api-field:Order.status",
                "api_field_name": "status",
                "api_property_path": ["status"],
                "database_field_id": "field:orders:status",
                "database_field_name": "status",
                "mapping_decision_id": "decision:orders:status",
                "authoritative": True,
                "read_only": True,
                "oracle_authority_allowed": False,
            }
        ],
    }


def _draft(phase: str, contract: dict) -> dict:
    return {
        "schema": "qualibug.database-observer-execution-draft.v1",
        "draft_id": f"draft:orders:{phase.lower()}",
        "observer_handler_id": "approved_database_readback",
        "observer_contract_ref": "observer:orders",
        "observation_phase": phase,
        "database_observer_contract": deepcopy(contract),
        "required": True,
    }


def test_one_bound_and_one_unresolved_state_assertion_keeps_http_observers() -> None:
    contract = _contract()
    experiment = {
        "experiment_id": "experiment:mixed-order-rules",
        "obligation_id": "obligation:mixed-order-rules",
        "compile_receipt": {"status": "COMPILED"},
        "observers": [
            {"observer_id": "before_state", "adapter": "http_api"},
            {"observer_id": "after_state", "adapter": "http_api"},
            {
                "observer_id": "approved_database_phase_aggregate",
                "adapter": "db_sql",
            },
        ],
        "database_observer_execution_drafts": [
            _draft("BEFORE", contract),
            _draft("AFTER", contract),
        ],
        "assertions": [
            {
                "assertion_id": "assert:orders:status",
                "kind": "state_transition",
                "from_state": "PENDING",
                "to_state": "PAID",
                "operands": [
                    {"field_id": "api-field:Order.status", "field": "status"}
                ],
            },
            {
                "assertion_id": "assert:orders:lifecycle",
                "kind": "state_transition",
                "from_state": "OPEN",
                "to_state": "CLOSED",
                "operands": [
                    {
                        "field_id": "api-field:Order.lifecycle",
                        "field": "lifecycle",
                    }
                ],
            },
        ],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "required_field_ids": ["api-field:Order.status"],
            "status": "RESOLVED",
            "assertion_kind": "state_transition",
        },
    }

    result = project_database_state_transition_assertions(
        {
            "experiments": [experiment],
            "blocked_experiments": [],
            "block_reason_counts": {},
        }
    )

    projected = result["experiments"][0]
    assert projected["assertions"][0]["kind"] == (
        DATABASE_STATE_TRANSITION_ASSERTION_KIND
    )
    assert projected["assertions"][1]["kind"] == "state_transition"
    assert projected["database_state_transition_projection_status"] == "PARTIAL"
    assert projected["compile_receipt"][
        "database_state_transition_http_observers_removed"
    ] is False
    assert {row["observer_id"] for row in projected["observers"]} == {
        "before_state",
        "after_state",
        "approved_database_phase_aggregate",
    }
    gap = projected["database_state_transition_projection_gaps"][0]
    assert gap["assertion_id"] == "assert:orders:lifecycle"
    assert gap["explicit_field_ids"] == ["api-field:Order.lifecycle"]
    assert result["database_state_transition_experiment_projection"]["status"] == (
        "PARTIAL"
    )
