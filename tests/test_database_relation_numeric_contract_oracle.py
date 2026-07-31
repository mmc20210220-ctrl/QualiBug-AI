from __future__ import annotations

from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
)
from ai_test_asset_center.database_observer_experiment_runtime import (
    aggregate_database_observer_phase_receipts,
)
from ai_test_asset_center.database_relation_numeric_experiment_projection import ASSERTION_KIND
from ai_test_asset_center.database_relation_observer_experiment_runtime import (
    aggregate_database_relation_phase_receipts,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers


install_non_http_observers()


def _root_phase() -> dict:
    return {
        "receipt_id": "root-after-receipt",
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
                "rows": [{"id": "o-1", "total": "30.00"}],
                "row_fingerprint": "root-row-o-1",
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": "draft:orders:after",
        "observer_contract_ref": "observer:orders",
        "observation_phase": "AFTER",
        "oracle_verdict_emitted": False,
    }


def _relation_phase() -> dict:
    return {
        "receipt_id": "relation-after-receipt",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "observer_id": "approved_database_relation_aggregate",
        "status": "OBSERVED",
        "reason_code": "",
        "evidence": {
            "approved_database_relation_aggregate_snapshot": {
                "relation_observer_ref": "relation-observer:order-lines",
                "root_observer_ref": "observer:orders",
                "database_relationship_id": "fk:order_lines:orders",
                "parent_table_ref": "table:orders",
                "child_table_ref": "table:order_lines",
                "child_table_name": "order_lines",
                "relation_key": [
                    {
                        "child_database_field_name": "order_id",
                        "parent_database_field_name": "id",
                    }
                ],
                "relation_parameter_fingerprints": ["identity-o-1"],
                "aggregate_requests": [
                    {
                        "aggregate": "SUM",
                        "database_field_id": "field:order_lines:amount",
                        "database_field_name": "amount",
                        "alias": "related_value",
                    }
                ],
                "aggregate_values": {"related_value": "25.00"},
                "aggregate_fingerprint": "aggregate-25",
                "client_side_filter_used": False,
                "raw_rows_retained": False,
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": "draft:relation:after",
        "relation_observer_contract_ref": "relation-observer:order-lines",
        "root_observer_contract_ref": "observer:orders",
        "observation_phase": "AFTER",
        "oracle_verdict_emitted": False,
    }


def _experiment() -> dict:
    return {
        "experiment_id": "experiment:order-total",
        "obligation_id": "obligation:order-total",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "source_refs": [{"kind": "business_rule", "locator": "BR-ORDER-TOTAL"}],
        "control_plan": [],
        "treatment_plan": [
            {"step_id": "treatment-1", "operation_ref": "api:PATCH:/orders/{id}"}
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
                "draft_id": "draft:orders:after",
                "observer_contract_ref": "observer:orders",
                "observation_phase": "AFTER",
                "required": True,
            }
        ],
        "database_relation_observer_execution_drafts": [
            {
                "schema": "qualibug.database-relation-observer-execution-draft.v1",
                "draft_id": "draft:relation:after",
                "relation_observer_contract_ref": "relation-observer:order-lines",
                "root_observer_contract_ref": "observer:orders",
                "observation_phase": "AFTER",
                "aggregate_requests": [
                    {
                        "aggregate": "SUM",
                        "database_field_id": "field:order_lines:amount",
                        "database_field_name": "amount",
                        "alias": "related_value",
                    }
                ],
                "required": True,
            }
        ],
        "assertions": [
            {
                "assertion_id": "assert:order-total",
                "kind": ASSERTION_KIND,
                "source_assertion_kind": "conservation",
                "require_control": False,
                "database_relation_observer_ref": "relation-observer:order-lines",
                "database_relation_draft_id": "draft:relation:after",
                "database_relationship_id": "fk:order_lines:orders",
                "relation_key": [
                    {
                        "child_database_field_name": "order_id",
                        "parent_database_field_name": "id",
                    }
                ],
                "root_observer_contract_ref": "observer:orders",
                "root_database_draft_id": "draft:orders:after",
                "root_table_ref": "table:orders",
                "root_database_field_id": "field:orders:total",
                "root_database_field_name": "total",
                "child_table_ref": "table:order_lines",
                "child_database_field_id": "field:order_lines:amount",
                "child_database_field_name": "amount",
                "aggregate": "SUM",
                "aggregate_alias": "related_value",
                "comparison_operator": "EQ",
                "aggregate_on_left": False,
                "comparison_phase": "AFTER",
                "tolerance": "0",
            }
        ],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "status": "RESOLVED",
            "assertion_kind": ASSERTION_KIND,
        },
        "safety_contract": {"governed_write": False},
    }


def test_contract_oracle_promotes_cross_table_violation_not_observer() -> None:
    experiment = _experiment()
    root_phase = _root_phase()
    relation_phase = _relation_phase()
    root_aggregate = aggregate_database_observer_phase_receipts(
        {
            "experiment": experiment,
            "observations": {
                "approved_database_observer_phase_receipts": [root_phase]
            },
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )
    relation_aggregate = aggregate_database_relation_phase_receipts(
        {
            "experiment": experiment,
            "observations": {
                "approved_database_relation_phase_receipts": [relation_phase]
            },
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )
    assert root_aggregate["status"] == "OBSERVED"
    assert relation_aggregate["status"] == "OBSERVED"
    assert relation_aggregate["evidence"]["oracle_verdict_emitted"] is False

    treatment = build_contract_evidence_receipt(
        kind="treatment",
        experiment_id="experiment:order-total",
        obligation_id="obligation:order-total",
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
            "approved_database_observer_phase_receipts": [root_phase],
            "approved_database_relation_phase_receipts": [relation_phase],
            "harness_error": False,
        },
    )

    assert result["status"] == "VIOLATION"
    assert result["verdict"] == "customer_deliverable_defect_candidate"
    assertion = result["failed_assertions"][0]
    assert assertion["kind"] == ASSERTION_KIND
    assert assertion["reason_code"] == "DATABASE_RELATION_CONSERVATION_VIOLATED"
    assert assertion["actual"]["aggregate_request_match"] is True
    assert assertion["actual"]["root_value"] == "30"
    assert assertion["actual"]["aggregate_value"] == "25"
    assert assertion["actual"]["observer_performed_oracle_verdict"] is False
    assert result["customer_deliverable"] is False
    assert result["customer_deliverable_candidate"] is True
