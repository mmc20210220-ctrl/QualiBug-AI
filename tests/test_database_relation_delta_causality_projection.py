from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_relation_delta_causality_mainline import (
    project_database_relation_delta_causality,
)
from ai_test_asset_center.database_relation_delta_causality_projection import (
    ASSERTION_KIND,
    ATTRIBUTION_SCHEMA,
)
from ai_test_asset_center.database_relation_delta_experiment_projection import (
    ASSERTION_KIND as BASE_ASSERTION_KIND,
)

_RELATION_DECISION_ID = "decision:ledger-relation"


def _relation_contract() -> dict:
    return {
        "schema": "qualibug.database-relation-observer-contract.v1",
        "relation_observer_id": "relation-observer:ledger",
        "relation_mapping_decision_id": _RELATION_DECISION_ID,
        "root_observer_id": "observer:accounts",
        "database_relationship_id": "fk:ledger:accounts",
        "relation_predicates": [
            {
                "child_database_field_name": "account_id",
                "parent_database_field_name": "id",
                "operator": "=",
                "value_source": "request.parameter.id",
            }
        ],
        "allowed_child_fields": [
            {
                "database_field_id": "field:ledger_entries:amount",
                "database_field_name": "amount",
            },
            {
                "database_field_id": "field:ledger_entries:request_id",
                "database_field_name": "request_id",
            },
        ],
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
    }


def _assertion(**causal_overrides: object) -> dict:
    causal = {
        "schema": ATTRIBUTION_SCHEMA,
        "operation_ref": "api:POST:/ledger",
        "value_source": "request.body.request_id",
        "child_database_field_id": "field:ledger_entries:request_id",
        "child_database_field_name": "request_id",
        "mapping_decision_id": _RELATION_DECISION_ID,
        "source_refs": [
            {"kind": "business_rule", "locator": "BR-LEDGER-REQUEST-ID"}
        ],
        **causal_overrides,
    }
    return {
        "assertion_id": "assert:balance-ledger-delta",
        "kind": BASE_ASSERTION_KIND,
        "source_assertion_kind": "conservation",
        "source_refs": [
            {"kind": "business_rule", "locator": "BR-BALANCE-LEDGER"}
        ],
        "relation_pair_id": "pair:base",
        "relation_before_draft_id": "draft:relation:before",
        "relation_after_draft_id": "draft:relation:after",
        "database_relation_observer_ref": "relation-observer:ledger",
        "database_relation_delta_binding": {
            "relation_mapping_decision_id": _RELATION_DECISION_ID,
        },
        "causal_attribution": causal,
    }


def _draft(phase: str) -> dict:
    return {
        "schema": "qualibug.database-relation-observer-execution-draft.v1",
        "draft_id": f"draft:relation:{phase.lower()}",
        "relation_pair_id": "pair:base",
        "relation_observer_contract_ref": "relation-observer:ledger",
        "root_observer_contract_ref": "observer:accounts",
        "observation_phase": phase,
        "database_relation_observer_contract": _relation_contract(),
        "aggregate_requests": [
            {
                "aggregate": "SUM",
                "database_field_id": "field:ledger_entries:amount",
                "database_field_name": "amount",
                "alias": "related_value",
            }
        ],
        "identity_value_sources": ["request.parameter.id"],
        "required": True,
    }


def _experiment(assertion: dict | None = None) -> dict:
    return {
        "experiment_id": "experiment:causal-ledger",
        "compile_receipt": {"status": "COMPILED"},
        "treatment_plan": [
            {
                "step_id": "treatment-1",
                "operation_ref": "api:POST:/ledger",
                "body": {"request_id": "req-1", "amount": 10},
            }
        ],
        "observers": [
            {
                "observer_id": "approved_database_relation_phase_aggregate",
                "adapter": "db_sql",
            }
        ],
        "database_relation_observer_execution_drafts": [
            _draft("BEFORE"),
            _draft("AFTER"),
        ],
        "assertions": [assertion or _assertion()],
    }


def _pack(experiment: dict) -> dict:
    return {
        "experiments": [experiment],
        "blocked_experiments": [],
        "block_reason_counts": {},
    }


def test_exact_request_correlation_promotes_causal_delta() -> None:
    result = project_database_relation_delta_causality(_pack(_experiment()))

    assert result["blocked_count"] == 0
    experiment = result["experiments"][0]
    assertion = experiment["assertions"][0]
    assert assertion["kind"] == ASSERTION_KIND
    causal = assertion["causal_attribution_contract"]
    assert causal["status"] == "BOUND"
    assert causal["value_source"] == "request.body.request_id"
    assert causal["mapping_decision_id"] == _RELATION_DECISION_ID
    assert causal["timestamp_window_attribution_allowed"] is False
    binding = assertion["database_relation_delta_binding"]
    assert binding["causal_scope_exact"] is True
    assert binding["causal_mapping_decision_id"] == _RELATION_DECISION_ID
    assert binding["relation_mapping_decision_id"] == _RELATION_DECISION_ID
    assert "operation_causality_transport" in {
        row["observer_id"] for row in experiment["observers"]
    }

    drafts = experiment["database_relation_observer_execution_drafts"]
    assert len(drafts) == 2
    for draft in drafts:
        assert "request.body.request_id" in draft["identity_value_sources"]
        predicates = draft["database_relation_observer_contract"][
            "relation_predicates"
        ]
        attribution = [
            row for row in predicates if row.get("predicate_kind") == "OPERATION_ATTRIBUTION"
        ]
        assert len(attribution) == 1
        assert attribution[0]["child_database_field_id"] == (
            "field:ledger_entries:request_id"
        )
        assert attribution[0]["mapping_decision_id"] == _RELATION_DECISION_ID
        assert draft["causal_scope_fingerprint"] == assertion[
            "causal_scope_fingerprint"
        ]


def test_response_generated_correlation_cannot_fabricate_before_scope() -> None:
    assertion = _assertion(value_source="response.body.request_id")
    result = project_database_relation_delta_causality(
        _pack(_experiment(assertion))
    )

    assert result["experiments"] == []
    assert result["blocked_count"] == 1
    detail = result["blocked_experiments"][0]["compile_receipt"][
        "database_relation_causality_detail"
    ]
    assert detail["causality_reason_code"] == (
        "DATABASE_RELATION_CAUSAL_RESPONSE_GENERATED_KEY_UNSUPPORTED"
    )
    assert detail["timestamp_window_attribution_allowed"] is False


def test_explicit_field_id_never_downgrades_to_same_name() -> None:
    assertion = _assertion(
        child_database_field_id="field:other_entries:request_id"
    )
    result = project_database_relation_delta_causality(
        _pack(_experiment(assertion))
    )

    assert result["blocked_count"] == 1
    detail = result["blocked_experiments"][0]["compile_receipt"][
        "database_relation_causality_detail"
    ]
    assert detail["causality_reason_code"] == (
        "DATABASE_RELATION_CAUSAL_EXACT_FIELD_BINDING_MISSING"
    )


def test_nonempty_but_different_decision_id_is_not_authority() -> None:
    assertion = _assertion(mapping_decision_id="decision:unrelated-field")
    result = project_database_relation_delta_causality(
        _pack(_experiment(assertion))
    )

    assert result["experiments"] == []
    assert result["blocked_count"] == 1
    detail = result["blocked_experiments"][0]["compile_receipt"][
        "database_relation_causality_detail"
    ]
    assert detail["causality_reason_code"] == (
        "DATABASE_RELATION_CAUSAL_MAPPING_DECISION_MISMATCH"
    )
    assert detail["causal_mapping_decision_id"] == (
        "decision:unrelated-field"
    )
    assert detail["relation_mapping_decision_id"] == _RELATION_DECISION_ID
    assert detail["automatic_authority_selection_allowed"] is False


def test_multiple_matching_treatment_steps_block_without_winner() -> None:
    experiment = _experiment()
    experiment["treatment_plan"].append(
        deepcopy(experiment["treatment_plan"][0])
    )
    experiment["treatment_plan"][1]["step_id"] = "treatment-2"

    result = project_database_relation_delta_causality(_pack(experiment))

    assert result["blocked_count"] == 1
    detail = result["blocked_experiments"][0]["compile_receipt"][
        "database_relation_causality_detail"
    ]
    assert detail["causality_reason_code"] == (
        "DATABASE_RELATION_CAUSAL_OPERATION_AMBIGUOUS"
    )
    assert detail["automatic_operation_selection_allowed"] is False
