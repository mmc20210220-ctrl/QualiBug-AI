from __future__ import annotations

from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
)
from ai_test_asset_center.database_observer_experiment_runtime import (
    aggregate_database_observer_phase_receipts,
)
from ai_test_asset_center.database_relation_delta_causality_integrity import (
    observe_operation_causality_with_integrity,
)
from ai_test_asset_center.database_relation_delta_causality_oracle import OBSERVER_ID
from ai_test_asset_center.database_relation_delta_causality_projection import (
    ASSERTION_KIND,
)
from ai_test_asset_center.database_relation_observer_experiment_runtime import (
    aggregate_database_relation_phase_receipts,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers
from tests.database_relation_delta_causality_fixtures import (
    build_observations,
    build_spec,
)


install_non_http_observers()


def _experiment(assertion: dict) -> dict:
    return {
        "experiment_id": "experiment:causal-balance-ledger",
        "obligation_id": "obligation:causal-balance-ledger",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "source_refs": assertion["source_refs"],
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": "treatment-1",
                "operation_ref": "api:POST:/ledger",
                "body": {"request_id": "req-1", "amount": 10},
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
            {"observer_id": OBSERVER_ID, "adapter": "process_ledger"},
        ],
        "database_observer_execution_drafts": [
            {
                "schema": "qualibug.database-observer-execution-draft.v1",
                "draft_id": assertion["root_before_draft_id"],
                "observer_contract_ref": "observer:accounts",
                "observation_phase": "BEFORE",
                "required": True,
            },
            {
                "schema": "qualibug.database-observer-execution-draft.v1",
                "draft_id": assertion["root_after_draft_id"],
                "observer_contract_ref": "observer:accounts",
                "observation_phase": "AFTER",
                "required": True,
            },
        ],
        "database_relation_observer_execution_drafts": [
            {
                "schema": "qualibug.database-relation-observer-execution-draft.v1",
                "draft_id": assertion["relation_before_draft_id"],
                "relation_pair_id": assertion["relation_pair_id"],
                "relation_observer_contract_ref": "relation-observer:ledger",
                "root_observer_contract_ref": "observer:accounts",
                "observation_phase": "BEFORE",
                "required": True,
            },
            {
                "schema": "qualibug.database-relation-observer-execution-draft.v1",
                "draft_id": assertion["relation_after_draft_id"],
                "relation_pair_id": assertion["relation_pair_id"],
                "relation_observer_contract_ref": "relation-observer:ledger",
                "root_observer_contract_ref": "observer:accounts",
                "observation_phase": "AFTER",
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


def test_contract_oracle_owns_operation_attributed_delta_violation() -> None:
    assertion = build_spec()
    experiment = _experiment(assertion)
    observations = build_observations(assertion)
    root_receipts = observations["approved_database_observer_phase_receipts"]
    relation_receipts = observations[
        "approved_database_relation_phase_receipts"
    ]

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
    causal_observer = observe_operation_causality_with_integrity(
        {
            "experiment": experiment,
            "observations": observations,
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )

    assert root_aggregate["status"] == "OBSERVED"
    assert relation_aggregate["status"] == "OBSERVED"
    assert causal_observer["status"] == "OBSERVED"
    assert causal_observer["observer_id"] == OBSERVER_ID
    assert causal_observer["evidence"]["integrity_failure_count"] == 0
    assert causal_observer["evidence"]["oracle_verdict_emitted"] is False

    treatment = build_contract_evidence_receipt(
        kind="treatment",
        experiment_id=experiment["experiment_id"],
        obligation_id=experiment["obligation_id"],
        campaign_id="campaign-1",
        execution_id="execution-1",
        subject_id="treatment-1",
        status="OBSERVED",
        evidence={
            "response_observed": True,
            "status_code": 201,
            "write_reached_transport": True,
        },
    )
    result = evaluate_contract_oracle(
        experiment=experiment,
        evidence={
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
            "contract_evidence_receipts": [treatment],
            "observer_receipts": [
                root_aggregate,
                relation_aggregate,
                causal_observer,
            ],
            "approved_database_observer_phase_receipts": root_receipts,
            "approved_database_relation_phase_receipts": relation_receipts,
            "operation_causality_transport_receipts": observations[
                "operation_causality_transport_receipts"
            ],
            "harness_error": False,
        },
    )

    assert result["status"] == "VIOLATION"
    assert result["verdict"] == "customer_deliverable_defect_candidate"
    assert result["customer_deliverable"] is False
    assert result["customer_deliverable_candidate"] is True
    failed = result["failed_assertions"][0]
    assert failed["kind"] == ASSERTION_KIND
    assert failed["reason_code"] == (
        "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED"
    )
    assert failed["actual"]["causal_scope_semantic_match"] is True
    assert failed["actual"]["transport_receipt_integrity_valid"] is True
    assert failed["actual"]["transport_scope_match"] is True
    assert failed["actual"]["causal_value_fingerprint_match"] is True
    assert failed["actual"]["causal_lineage_match"] is True
    assert failed["actual"]["root_delta"] == "-15"
    assert failed["actual"]["relation_delta"] == "10"
    assert failed["actual"]["observer_performed_oracle_verdict"] is False
