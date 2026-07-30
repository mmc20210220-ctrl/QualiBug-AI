from __future__ import annotations

from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
)
from ai_test_asset_center.database_numeric_oracle import (
    DATABASE_NUMERIC_DELTA_ASSERTION_KIND,
)
from ai_test_asset_center.database_observer_experiment_runtime import (
    aggregate_database_observer_phase_receipts,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers


install_non_http_observers()


def _phase(phase: str, balance: str) -> dict:
    return {
        "receipt_id": f"readback-{phase.lower()}",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "observer_id": "approved_database_readback",
        "status": "OBSERVED",
        "reason_code": "",
        "evidence": {
            "approved_database_snapshot": {
                "database_table_ref": "table:accounts",
                "database_table_name": "accounts",
                "identity_key": ["id"],
                "identity_parameter_fingerprints": ["identity-a-1"],
                "match_status": "MATCHED_ONE",
                "row_count": 1,
                "rows": [{"id": "a-1", "balance": balance}],
                "row_fingerprint": f"row-{phase.lower()}-{balance}",
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": f"draft:accounts:{phase.lower()}",
        "observer_contract_ref": "observer:accounts",
        "observation_phase": phase,
        "oracle_verdict_emitted": False,
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
                "step_id": "treatment_1",
                "operation_ref": "api:POST:/accounts/{id}/debit",
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
                "draft_id": "draft:accounts:before",
                "observer_contract_ref": "observer:accounts",
                "observation_phase": "BEFORE",
                "required": True,
            },
            {
                "schema": "qualibug.database-observer-execution-draft.v1",
                "draft_id": "draft:accounts:after",
                "observer_contract_ref": "observer:accounts",
                "observation_phase": "AFTER",
                "required": True,
            },
        ],
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
                        "expected_delta": "-10.00",
                        "tolerance": "0",
                    }
                ],
            }
        ],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "assertion_kinds": [DATABASE_NUMERIC_DELTA_ASSERTION_KIND],
            "status": "RESOLVED",
        },
        "safety_contract": {"governed_write": False},
    }


def test_contract_oracle_promotes_numeric_violation_not_observer() -> None:
    experiment = _experiment()
    phase_receipts = [_phase("BEFORE", "100.00"), _phase("AFTER", "95.00")]
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
        experiment_id="experiment:accounts",
        obligation_id="obligation:accounts",
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
    assert len(result["failed_assertions"]) == 1
    assertion = result["failed_assertions"][0]
    assert assertion["kind"] == DATABASE_NUMERIC_DELTA_ASSERTION_KIND
    assert assertion["status"] == "VIOLATION"
    assert assertion["reason_code"] == "DATABASE_NUMERIC_DELTA_MISMATCH"
    assert assertion["actual"]["observer_performed_oracle_verdict"] is False
    assert result["customer_deliverable"] is False
    assert result["customer_deliverable_candidate"] is True
