from __future__ import annotations

from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
)
from ai_test_asset_center.database_observer_experiment_runtime import (
    aggregate_database_observer_phase_receipts,
)
from ai_test_asset_center.database_state_transition_oracle import (
    DATABASE_STATE_TRANSITION_ASSERTION_KIND,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers


install_non_http_observers()


def _phase(phase: str, status: str) -> dict:
    return {
        "receipt_id": f"readback-{phase.lower()}",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "observer_id": "approved_database_readback",
        "status": "OBSERVED",
        "reason_code": "",
        "evidence": {
            "approved_database_snapshot": {
                "database_table_ref": "table:orders",
                "database_table_name": "orders",
                "identity_key": ["id"],
                "identity_parameter_fingerprints": ["identity-o-1"],
                "match_status": "MATCHED_ONE",
                "row_count": 1,
                "rows": [{"id": "o-1", "status": status}],
                "row_fingerprint": f"row-{phase.lower()}-{status}",
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": f"draft:orders:{phase.lower()}",
        "observer_contract_ref": "observer:orders",
        "observation_phase": phase,
        "oracle_verdict_emitted": False,
    }


def _experiment() -> dict:
    return {
        "experiment_id": "experiment:orders",
        "obligation_id": "obligation:orders",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "source_refs": [{"kind": "business_rule", "locator": "BR-ORDER-1"}],
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": "treatment_1",
                "operation_ref": "api:PATCH:/orders/{id}",
            }
        ],
        "observers": [
            {
                "observer_id": "approved_database_phase_aggregate",
                "adapter": "db_sql",
            }
        ],
        "database_observer_execution_drafts": [
            {
                "schema": "qualibug.database-observer-execution-draft.v1",
                "draft_id": "draft:orders:before",
                "observer_contract_ref": "observer:orders",
                "observation_phase": "BEFORE",
                "required": True,
            },
            {
                "schema": "qualibug.database-observer-execution-draft.v1",
                "draft_id": "draft:orders:after",
                "observer_contract_ref": "observer:orders",
                "observation_phase": "AFTER",
                "required": True,
            },
        ],
        "assertions": [
            {
                "assertion_id": "assert:orders:status",
                "kind": DATABASE_STATE_TRANSITION_ASSERTION_KIND,
                "source_assertion_kind": "state_transition",
                "require_control": False,
                "operator": "must_transition",
                "from_state": "PENDING",
                "to_state": "PAID",
                "database_observer_contract_ref": "observer:orders",
                "before_draft_id": "draft:orders:before",
                "after_draft_id": "draft:orders:after",
                "database_table_ref": "table:orders",
                "database_field_id": "field:orders:status",
                "database_field_name": "status",
            }
        ],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "assertion_kind": DATABASE_STATE_TRANSITION_ASSERTION_KIND,
            "status": "RESOLVED",
        },
        "safety_contract": {"governed_write": False},
    }


def test_contract_oracle_promotes_database_assertion_violation_not_observer() -> None:
    experiment = _experiment()
    phase_receipts = [_phase("BEFORE", "PENDING"), _phase("AFTER", "PENDING")]
    aggregate = aggregate_database_observer_phase_receipts(
        {
            "experiment": experiment,
            "observations": {
                "approved_database_observer_phase_receipts": phase_receipts
            },
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )
    assert aggregate["status"] == "OBSERVED"
    assert aggregate["evidence"]["oracle_verdict_emitted"] is False

    treatment = build_contract_evidence_receipt(
        kind="treatment",
        experiment_id="experiment:orders",
        obligation_id="obligation:orders",
        campaign_id="campaign-1",
        execution_id="execution-1",
        subject_id="treatment_1",
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
            "approved_database_observer_phase_receipts": phase_receipts,
            "harness_error": False,
        },
    )

    assert result["status"] == "VIOLATION"
    assert result["verdict"] == "customer_deliverable_defect_candidate"
    assert result["failed_assertions"] == ["assert:orders:status"]
    assertion = result["assertions"][0]
    assert assertion["kind"] == DATABASE_STATE_TRANSITION_ASSERTION_KIND
    assert assertion["status"] == "VIOLATION"
    assert assertion["reason_code"] == "DATABASE_STATE_TRANSITION_NOT_OBSERVED"
    assert assertion["actual"]["observer_performed_oracle_verdict"] is False
