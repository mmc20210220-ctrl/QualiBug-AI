from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_relation_delta_experiment_projection import (
    ASSERTION_KIND,
    project_database_relation_delta_assertions,
)


def _root_contract() -> dict:
    return {
        "schema": "qualibug.database-observer-contract.v1",
        "observer_id": "observer:accounts",
        "database_table_id": "table:accounts",
        "database_table_name": "accounts",
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
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
    }


def _relation_contract(*, relation_id: str = "relation-observer:ledger") -> dict:
    return {
        "schema": "qualibug.database-relation-observer-contract.v1",
        "relation_observer_id": relation_id,
        "relation_mapping_decision_id": f"decision:{relation_id}",
        "root_observer_id": "observer:accounts",
        "database_relationship_id": f"fk:{relation_id}",
        "parent_table_id": "table:accounts",
        "parent_table_name": "accounts",
        "child_table_id": "table:ledger_entries",
        "child_table_name": "ledger_entries",
        "relation_predicates": [
            {
                "child_database_field_name": "account_id",
                "parent_database_field_name": "id",
                "parent_field_binding_id": "binding:accounts:id",
                "value_source": "request.parameter.id",
                "operator": "=",
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
            "allowed_aggregates": ["COUNT", "SUM", "MIN", "MAX"],
            "raw_sql": "",
            "client_side_filter": False,
        },
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
    }


def _delta_assertion(*, child_field_id: str = "field:ledger_entries:amount") -> dict:
    return {
        "assertion_id": "assert:balance-ledger-delta",
        "kind": "conservation",
        "structured_expression": {
            "type": "delta_conservation",
            "operator": "EQ",
            "left": {
                "node_type": "delta",
                "coefficient": -1,
                "operand": {
                    "node_type": "field_ref",
                    "entity": "accounts",
                    "field_id": "api-field:Account.balance",
                },
            },
            "right": {
                "node_type": "delta",
                "coefficient": 1,
                "operand": {
                    "node_type": "aggregate",
                    "function": "SUM",
                    "source_entity_name": "ledger_entries",
                    "field_id": child_field_id,
                },
            },
        },
        "source_refs": [{"kind": "business_rule", "locator": "BR-BALANCE-LEDGER"}],
    }


def _experiment(*, include_before: bool = True) -> dict:
    contract = _root_contract()
    drafts = [
        {
            "schema": "qualibug.database-observer-execution-draft.v1",
            "draft_id": "draft:accounts:after",
            "observer_contract_ref": "observer:accounts",
            "observation_phase": "AFTER",
            "database_observer_contract": contract,
            "required": True,
        }
    ]
    if include_before:
        drafts.insert(
            0,
            {
                "schema": "qualibug.database-observer-execution-draft.v1",
                "draft_id": "draft:accounts:before",
                "observer_contract_ref": "observer:accounts",
                "observation_phase": "BEFORE",
                "database_observer_contract": contract,
                "required": True,
            },
        )
    return {
        "experiment_id": "experiment:balance-ledger",
        "compile_receipt": {"status": "COMPILED"},
        "observers": [
            {"observer_id": "before_state", "adapter": "http_api"},
            {"observer_id": "after_state", "adapter": "http_api"},
            {"observer_id": "approved_database_phase_aggregate", "adapter": "db_sql"},
        ],
        "database_observer_execution_drafts": drafts,
        "database_relation_observer_contracts": [_relation_contract()],
        "assertions": [_delta_assertion()],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "status": "RESOLVED",
            "assertion_kind": "conservation",
        },
    }


def _pack(experiment: dict) -> dict:
    return {
        "experiments": [experiment],
        "blocked_experiments": [],
        "block_reason_counts": {},
    }


def test_exact_delta_rule_creates_relation_before_and_after_drafts() -> None:
    result = project_database_relation_delta_assertions(_pack(_experiment()))

    assert result["blocked_count"] == 0
    experiment = result["experiments"][0]
    assertion = experiment["assertions"][0]
    assert assertion["kind"] == ASSERTION_KIND
    assert assertion["root_before_draft_id"] == "draft:accounts:before"
    assert assertion["root_after_draft_id"] == "draft:accounts:after"
    assert assertion["left_coefficient"] == -1
    assert assertion["right_coefficient"] == 1
    assert assertion["scope_count_alias"] == "related_scope_count"
    drafts = experiment["database_relation_observer_execution_drafts"]
    assert {row["observation_phase"] for row in drafts} == {"BEFORE", "AFTER"}
    for draft in drafts:
        assert draft["relation_pair_id"] == assertion["relation_pair_id"]
        assert draft["aggregate_requests"] == [
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
        assert draft["oracle_verdict_emitted"] is False
        assert draft["mutation_allowed"] is False
    assert "approved_database_relation_phase_aggregate" in {
        row["observer_id"] for row in experiment["observers"]
    }
    assert "before_state" not in {
        row["observer_id"] for row in experiment["observers"]
    }


def test_missing_root_before_draft_remains_visible_gap() -> None:
    result = project_database_relation_delta_assertions(
        _pack(_experiment(include_before=False))
    )

    experiment = result["experiments"][0]
    assert experiment["assertions"][0]["kind"] == "conservation"
    assert experiment["database_relation_delta_projection_status"] == "INCOMPLETE"
    gap = experiment["database_relation_delta_projection_gaps"][0]
    assert gap["reason_code"] == "DATABASE_RELATION_DELTA_EXACT_BINDING_MISSING"
    assert gap["before_after_root_pair_required"] is True


def test_explicit_child_field_id_never_downgrades_to_same_name() -> None:
    experiment = _experiment()
    assertion = _delta_assertion(
        child_field_id="field:other_entries:amount"
    )
    assertion["structured_expression"]["right"]["operand"]["field"] = "amount"
    experiment["assertions"] = [assertion]

    result = project_database_relation_delta_assertions(_pack(experiment))

    projected = result["experiments"][0]
    assert projected["assertions"][0]["kind"] == "conservation"
    assert projected["database_relation_delta_projection_status"] == "INCOMPLETE"


def test_two_exact_relations_block_without_automatic_winner() -> None:
    experiment = _experiment()
    second = deepcopy(_relation_contract(relation_id="relation-observer:ledger-copy"))
    experiment["database_relation_observer_contracts"].append(second)

    result = project_database_relation_delta_assertions(_pack(experiment))

    assert result["experiments"] == []
    assert result["blocked_count"] == 1
    blocked = result["blocked_experiments"][0]
    assert blocked["compile_receipt"]["reason_code"] == (
        "BLOCKED_DATABASE_RELATION_DELTA_ORACLE_BINDING_AMBIGUOUS"
    )
    detail = blocked["compile_receipt"][
        "database_relation_delta_oracle_detail"
    ]
    assert detail["candidate_count"] == 2
    assert detail["automatic_winner_allowed"] is False
