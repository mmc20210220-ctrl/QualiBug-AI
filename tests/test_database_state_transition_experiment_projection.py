from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_state_transition_experiment_projection import (
    project_database_state_transition_assertions,
)
from ai_test_asset_center.database_state_transition_oracle import (
    DATABASE_STATE_TRANSITION_ASSERTION_KIND,
)


def _contract(
    *,
    observer_ref: str = "observer:orders",
    field_binding_id: str = "binding:orders:status",
    api_field_id: str = "api-field:Order.status",
    database_field_id: str = "field:orders:status",
    database_field_name: str = "status",
) -> dict:
    return {
        "schema": "qualibug.database-observer-contract.v1",
        "observer_id": observer_ref,
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
                "field_binding_id": field_binding_id,
                "api_field_id": api_field_id,
                "api_field_name": "status",
                "api_property_path": ["status"],
                "database_field_id": database_field_id,
                "database_field_name": database_field_name,
                "mapping_decision_id": f"decision:{field_binding_id}",
                "authoritative": True,
                "read_only": True,
                "oracle_authority_allowed": False,
                "evidence": [
                    {
                        "kind": "OPERATOR_DATABASE_MAPPING_AUTHORITY",
                        "decision_id": f"decision:{field_binding_id}",
                        "exact": True,
                    }
                ],
            }
        ],
    }


def _draft(phase: str, contract: dict, *, observer_ref: str = "observer:orders") -> dict:
    return {
        "schema": "qualibug.database-observer-execution-draft.v1",
        "draft_id": f"draft:{observer_ref}:{phase.lower()}",
        "observer_handler_id": "approved_database_readback",
        "observer_contract_ref": observer_ref,
        "observation_phase": phase,
        "database_observer_contract": deepcopy(contract),
        "required": True,
    }


def _experiment(*, field_id: str = "api-field:Order.status") -> dict:
    contract = _contract()
    return {
        "experiment_id": "experiment:orders",
        "obligation_id": "obligation:orders",
        "compile_receipt": {"status": "COMPILED"},
        "compiled_adapters": ["http_api", "db_sql"],
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
                "operator": "must_transition",
                "operands": [{"field_id": field_id, "field": "status"}],
                "rule_id": "BR-ORDER-1",
                "source_refs": [
                    {"kind": "business_rule", "locator": "BR-ORDER-1"}
                ],
            }
        ],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "required_field_ids": [field_id],
            "status": "RESOLVED",
            "assertion_kind": "state_transition",
        },
    }


def _pack(experiment: dict) -> dict:
    return {
        "experiments": [experiment],
        "blocked_experiments": [],
        "block_reason_counts": {},
    }


def test_exact_field_id_replaces_http_state_assertion() -> None:
    result = project_database_state_transition_assertions(_pack(_experiment()))

    assert result["blocked_count"] == 0
    experiment = result["experiments"][0]
    assertion = experiment["assertions"][0]
    assert assertion["kind"] == DATABASE_STATE_TRANSITION_ASSERTION_KIND
    assert assertion["source_assertion_kind"] == "state_transition"
    assert assertion["database_observer_contract_ref"] == "observer:orders"
    assert assertion["database_table_ref"] == "table:orders"
    assert assertion["database_field_id"] == "field:orders:status"
    assert assertion["database_field_name"] == "status"
    assert assertion["database_state_transition_binding"]["match_basis"] == "EXACT_FIELD_ID"
    assert assertion["database_state_transition_binding"]["fuzzy_name_matching"] is False
    assert {row["observer_id"] for row in experiment["observers"]} == {
        "approved_database_phase_aggregate"
    }
    assert experiment["field_oracle_runtime_contract"]["assertion_kind"] == (
        DATABASE_STATE_TRANSITION_ASSERTION_KIND
    )
    assert experiment["compile_receipt"]["database_state_transition_assertion_fingerprint"]


def test_exact_explicit_field_name_can_bind_when_no_field_id_exists() -> None:
    experiment = _experiment(field_id="")
    experiment["field_oracle_runtime_contract"]["required_field_ids"] = []
    experiment["assertions"][0]["operands"] = [{"field": "status"}]

    result = project_database_state_transition_assertions(_pack(experiment))

    assertion = result["experiments"][0]["assertions"][0]
    assert assertion["kind"] == DATABASE_STATE_TRANSITION_ASSERTION_KIND
    assert assertion["database_state_transition_binding"]["match_basis"] == "EXACT_FIELD_NAME"


def test_missing_exact_field_binding_keeps_original_assertion_and_gap() -> None:
    experiment = _experiment(field_id="api-field:Order.lifecycle")
    experiment["assertions"][0]["operands"] = [
        {"field_id": "api-field:Order.lifecycle"}
    ]

    result = project_database_state_transition_assertions(_pack(experiment))

    projected = result["experiments"][0]
    assert projected["assertions"][0]["kind"] == "state_transition"
    assert projected["database_state_transition_projection_status"] == "INCOMPLETE"
    assert projected["database_state_transition_projection_gaps"][0]["reason_code"] == (
        "DATABASE_STATE_EXACT_FIELD_BINDING_MISSING"
    )
    assert result["database_state_transition_experiment_projection"][
        "automatic_field_mapping_count"
    ] == 0


def test_two_exact_approved_bindings_block_without_automatic_winner() -> None:
    experiment = _experiment()
    second = _contract(
        observer_ref="observer:order-history",
        field_binding_id="binding:history:status",
        database_field_id="field:order_history:status",
    )
    second["database_table_id"] = "table:order_history"
    second["database_table_name"] = "order_history"
    experiment["database_observer_execution_drafts"].extend(
        [
            _draft("BEFORE", second, observer_ref="observer:order-history"),
            _draft("AFTER", second, observer_ref="observer:order-history"),
        ]
    )

    result = project_database_state_transition_assertions(_pack(experiment))

    assert result["experiments"] == []
    assert result["blocked_count"] == 1
    blocked = result["blocked_experiments"][0]
    assert blocked["compile_receipt"]["reason_code"] == (
        "BLOCKED_DATABASE_STATE_ORACLE_BINDING_AMBIGUOUS"
    )
    detail = blocked["compile_receipt"]["database_state_oracle_detail"]
    assert detail["candidate_count"] == 2
    assert detail["automatic_winner_allowed"] is False


def test_after_only_contract_cannot_fabricate_transition_pair() -> None:
    experiment = _experiment()
    experiment["database_observer_execution_drafts"] = [
        experiment["database_observer_execution_drafts"][1]
    ]

    result = project_database_state_transition_assertions(_pack(experiment))

    projected = result["experiments"][0]
    assert projected["assertions"][0]["kind"] == "state_transition"
    assert projected["database_state_transition_projection_status"] == "INCOMPLETE"
