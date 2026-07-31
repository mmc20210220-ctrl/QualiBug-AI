from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_relation_numeric_experiment_projection import (
    ASSERTION_KIND,
    DRAFT_SCHEMA,
    project_database_relation_numeric_assertions,
)


def _root_contract() -> dict:
    return {
        "schema": "qualibug.database-observer-contract.v1",
        "observer_id": "observer:orders",
        "database_table_id": "table:orders",
        "database_table_name": "orders",
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
        "field_bindings": [
            {
                "field_binding_id": "binding:orders:id",
                "api_field_id": "api-field:Order.id",
                "api_field_name": "id",
                "database_field_id": "field:orders:id",
                "database_field_name": "id",
                "authoritative": True,
                "read_only": True,
            },
            {
                "field_binding_id": "binding:orders:total",
                "api_field_id": "api-field:Order.total",
                "api_field_name": "total",
                "database_field_id": "field:orders:total",
                "database_field_name": "total",
                "authoritative": True,
                "read_only": True,
            },
        ],
    }


def _relation_contract(*, relation_id: str = "relation-observer:order-lines") -> dict:
    return {
        "schema": "qualibug.database-relation-observer-contract.v1",
        "relation_observer_id": relation_id,
        "relation_mapping_decision_id": f"decision:{relation_id}",
        "root_observer_id": "observer:orders",
        "database_relationship_id": f"fk:{relation_id}",
        "parent_table_id": "table:orders",
        "parent_table_name": "orders",
        "child_table_id": "table:order_lines",
        "child_table_name": "order_lines",
        "relation_predicates": [
            {
                "child_database_field_name": "order_id",
                "parent_database_field_name": "id",
                "parent_field_binding_id": "binding:orders:id",
                "value_source": "request.parameter.id",
                "operator": "=",
            }
        ],
        "allowed_child_fields": [
            {
                "database_field_id": "field:order_lines:amount",
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


def _assertion(*, child_field_id: str = "field:order_lines:amount") -> dict:
    return {
        "assertion_id": "assert:order-total",
        "kind": "conservation",
        "structured_expression": {
            "type": "conservation",
            "operator": "EQ",
            "left": {
                "node_type": "field_ref",
                "entity": "orders",
                "field_id": "api-field:Order.total",
            },
            "right": {
                "node_type": "aggregate",
                "function": "SUM",
                "source_entity_name": "order_lines",
                "field_id": child_field_id,
            },
        },
        "source_refs": [{"kind": "business_rule", "locator": "BR-ORDER-TOTAL"}],
    }


def _root_draft(draft_id: str = "draft:orders:after") -> dict:
    return {
        "schema": "qualibug.database-observer-execution-draft.v1",
        "draft_id": draft_id,
        "observer_contract_ref": "observer:orders",
        "observation_phase": "AFTER",
        "database_observer_contract": _root_contract(),
        "required": True,
    }


def _experiment(assertion: dict | None = None) -> dict:
    return {
        "experiment_id": "experiment:order-total",
        "obligation_id": "obligation:order-total",
        "compile_receipt": {"status": "COMPILED"},
        "observers": [
            {"observer_id": "before_state", "adapter": "http_api"},
            {"observer_id": "after_state", "adapter": "http_api"},
            {"observer_id": "approved_database_phase_aggregate", "adapter": "db_sql"},
        ],
        "database_observer_execution_drafts": [_root_draft()],
        "database_relation_observer_contracts": [_relation_contract()],
        "assertions": [assertion or _assertion()],
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


def test_exact_root_field_child_field_and_relation_create_after_aggregate_draft() -> None:
    result = project_database_relation_numeric_assertions(_pack(_experiment()))

    assert result["blocked_count"] == 0
    experiment = result["experiments"][0]
    assertion = experiment["assertions"][0]
    assert assertion["kind"] == ASSERTION_KIND
    assert assertion["root_database_draft_id"] == "draft:orders:after"
    assert assertion["database_relationship_id"] == (
        "fk:relation-observer:order-lines"
    )
    assert assertion["relation_key"] == [
        {
            "child_database_field_name": "order_id",
            "parent_database_field_name": "id",
        }
    ]
    assert assertion["root_database_field_id"] == "field:orders:total"
    assert assertion["child_database_field_id"] == "field:order_lines:amount"
    assert assertion["aggregate"] == "SUM"
    assert assertion["comparison_operator"] == "EQ"
    binding = assertion["database_relation_binding"]
    assert binding["root_database_draft_id"] == "draft:orders:after"
    assert binding["relation_key"] == assertion["relation_key"]
    assert binding["automatic_relation_mapping"] is False
    assert binding["client_side_filtering"] is False
    drafts = experiment["database_relation_observer_execution_drafts"]
    assert len(drafts) == 1
    assert drafts[0]["schema"] == DRAFT_SCHEMA
    assert drafts[0]["observation_phase"] == "AFTER"
    assert drafts[0]["aggregate_requests"] == [
        {
            "aggregate": "SUM",
            "database_field_id": "field:order_lines:amount",
            "database_field_name": "amount",
            "alias": "related_value",
        }
    ]
    assert "approved_database_relation_phase_aggregate" in {
        row["observer_id"] for row in experiment["observers"]
    }
    assert experiment["compile_receipt"][
        "database_relation_numeric_assertion_fingerprint"
    ]


def test_explicit_child_field_id_never_downgrades_to_matching_name() -> None:
    assertion = _assertion(child_field_id="field:other_table:amount")
    assertion["structured_expression"]["right"]["field"] = "amount"

    result = project_database_relation_numeric_assertions(
        _pack(_experiment(assertion))
    )

    projected = result["experiments"][0]
    assert projected["assertions"][0]["kind"] == "conservation"
    assert projected["database_relation_numeric_projection_status"] == "INCOMPLETE"
    assert projected["database_relation_numeric_projection_gaps"][0]["reason_code"] == (
        "DATABASE_RELATION_EXACT_AGGREGATE_BINDING_MISSING"
    )


def test_two_exact_approved_relations_block_without_automatic_winner() -> None:
    experiment = _experiment()
    second = _relation_contract(relation_id="relation-observer:order-lines-copy")
    experiment["database_relation_observer_contracts"].append(second)

    result = project_database_relation_numeric_assertions(_pack(experiment))

    assert result["experiments"] == []
    assert result["blocked_count"] == 1
    blocked = result["blocked_experiments"][0]
    assert blocked["compile_receipt"]["reason_code"] == (
        "BLOCKED_DATABASE_RELATION_ORACLE_BINDING_AMBIGUOUS"
    )
    detail = blocked["compile_receipt"]["database_relation_oracle_detail"]
    assert detail["candidate_count"] == 2
    assert detail["automatic_winner_allowed"] is False


def test_two_exact_root_after_drafts_block_without_automatic_winner() -> None:
    experiment = _experiment()
    experiment["database_observer_execution_drafts"].append(
        _root_draft("draft:orders:after:copy")
    )

    result = project_database_relation_numeric_assertions(_pack(experiment))

    assert result["experiments"] == []
    assert result["blocked_count"] == 1
    detail = result["blocked_experiments"][0]["compile_receipt"][
        "database_relation_oracle_detail"
    ]
    assert detail["candidate_count"] == 2
    assert detail["candidate_root_draft_ids"] == [
        "draft:orders:after",
        "draft:orders:after:copy",
    ]
    assert detail["automatic_winner_allowed"] is False


def test_entity_scope_mismatch_does_not_select_relation_by_field_name_only() -> None:
    assertion = deepcopy(_assertion())
    assertion["structured_expression"]["right"]["source_entity_name"] = (
        "ledger_entries"
    )

    result = project_database_relation_numeric_assertions(
        _pack(_experiment(assertion))
    )

    projected = result["experiments"][0]
    assert projected["assertions"][0]["kind"] == "conservation"
    assert projected["database_relation_numeric_projection_status"] == "INCOMPLETE"
    assert result["database_relation_numeric_experiment_projection"][
        "automatic_relation_mapping_count"
    ] == 0
