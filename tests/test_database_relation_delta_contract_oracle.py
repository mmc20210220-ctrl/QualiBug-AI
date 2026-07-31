from __future__ import annotations

from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
)
from ai_test_asset_center.database_observer_experiment_runtime import (
    aggregate_database_observer_phase_receipts,
)
from ai_test_asset_center.database_relation_delta_experiment_projection import (
    ASSERTION_KIND,
    _stable_id,
)
from ai_test_asset_center.database_relation_delta_projection_gate import (
    SEMANTIC_PAIR_SCHEMA,
    semantic_relation_delta_pair_id,
)
from ai_test_asset_center.database_relation_observer_experiment_runtime import (
    aggregate_database_relation_phase_receipts,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers


install_non_http_observers()


def _root_phase(value: str, phase: str, draft_id: str) -> dict:
    return {
        "receipt_id": f"root-{phase.lower()}",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "observer_id": "approved_database_readback",
        "status": "OBSERVED",
        "evidence": {
            "approved_database_snapshot": {
                "database_table_ref": "table:accounts",
                "database_table_name": "accounts",
                "identity_key": ["id"],
                "identity_parameter_fingerprints": ["identity-a-1"],
                "match_status": "MATCHED_ONE",
                "row_count": 1,
                "rows": [{"id": "a-1", "balance": value}],
                "row_fingerprint": f"root-{phase}-{value}",
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": draft_id,
        "observer_contract_ref": "observer:accounts",
        "observation_phase": phase,
        "oracle_verdict_emitted": False,
    }


def _relation_phase(
    value: str,
    count: int,
    phase: str,
    draft_id: str,
    pair_id: str,
) -> dict:
    return {
        "receipt_id": f"relation-{phase.lower()}",
        "phase_receipt_id": f"relation-phase-{phase.lower()}",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "observer_id": "approved_database_relation_aggregate",
        "status": "OBSERVED",
        "evidence": {
            "approved_database_relation_aggregate_snapshot": {
                "relation_observer_ref": "relation-observer:ledger",
                "root_observer_ref": "observer:accounts",
                "database_relationship_id": "fk:ledger:accounts",
                "parent_table_ref": "table:accounts",
                "child_table_ref": "table:ledger_entries",
                "child_table_name": "ledger_entries",
                "relation_key": [
                    {
                        "child_database_field_name": "account_id",
                        "parent_database_field_name": "id",
                    }
                ],
                "relation_parameter_fingerprints": ["identity-a-1"],
                "aggregate_requests": [
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
                ],
                "aggregate_values": {
                    "related_value": value,
                    "related_scope_count": count,
                },
                "aggregate_fingerprint": f"relation-{phase}-{value}-{count}",
                "client_side_filter_used": False,
                "raw_rows_retained": False,
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": draft_id,
        "relation_pair_id": pair_id,
        "relation_observer_contract_ref": "relation-observer:ledger",
        "root_observer_contract_ref": "observer:accounts",
        "observation_phase": phase,
        "oracle_verdict_emitted": False,
    }


def _assertion() -> dict:
    row = {
        "assertion_id": "assert:balance-ledger-delta",
        "kind": ASSERTION_KIND,
        "source_assertion_kind": "conservation",
        "source_refs": [
            {"kind": "business_rule", "locator": "BR-BALANCE-LEDGER"}
        ],
        "require_control": False,
        "database_relation_observer_ref": "relation-observer:ledger",
        "database_relationship_id": "fk:ledger:accounts",
        "relation_key": [
            {
                "child_database_field_name": "account_id",
                "parent_database_field_name": "id",
            }
        ],
        "root_observer_contract_ref": "observer:accounts",
        "root_before_draft_id": "draft:accounts:before",
        "root_after_draft_id": "draft:accounts:after",
        "root_table_ref": "table:accounts",
        "root_field_binding_id": "binding:accounts:balance",
        "root_database_field_id": "field:accounts:balance",
        "root_database_field_name": "balance",
        "child_table_ref": "table:ledger_entries",
        "child_database_field_id": "field:ledger_entries:amount",
        "child_database_field_name": "amount",
        "aggregate": "SUM",
        "aggregate_alias": "related_value",
        "scope_count_alias": "related_scope_count",
        "comparison_operator": "EQ",
        "aggregate_on_left": False,
        "left_coefficient": -1,
        "right_coefficient": 1,
        "comparison_phase_pair": "BEFORE_AFTER",
        "tolerance": "0",
        "database_relation_delta_binding": {
            "relation_mapping_decision_id": "decision:ledger",
        },
    }
    pair_id = semantic_relation_delta_pair_id(row)
    before_id = _stable_id(
        "database_relation_observer_execution_draft",
        row["database_relation_observer_ref"],
        row["assertion_id"],
        "BEFORE",
        pair_id,
    )
    after_id = _stable_id(
        "database_relation_observer_execution_draft",
        row["database_relation_observer_ref"],
        row["assertion_id"],
        "AFTER",
        pair_id,
    )
    row.update(
        {
            "relation_pair_id": pair_id,
            "relation_before_draft_id": before_id,
            "relation_after_draft_id": after_id,
        }
    )
    row["database_relation_delta_binding"].update(
        {
            "semantic_pair_schema": SEMANTIC_PAIR_SCHEMA,
            "relation_pair_id": pair_id,
            "relation_before_draft_id": before_id,
            "relation_after_draft_id": after_id,
            "pair_covers_complete_assertion_semantics": True,
        }
    )
    return row


def _experiment(assertion: dict) -> dict:
    return {
        "experiment_id": "experiment:balance-ledger",
        "obligation_id": "obligation:balance-ledger",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "source_refs": assertion["source_refs"],
        "control_plan": [],
        "treatment_plan": [
            {"step_id": "treatment-1", "operation_ref": "api:POST:/ledger"}
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
        },
        "safety_contract": {"governed_write": False},
    }


def test_contract_oracle_owns_cross_table_delta_violation() -> None:
    assertion = _assertion()
    experiment = _experiment(assertion)
    root_receipts = [
        _root_phase("100", "BEFORE", assertion["root_before_draft_id"]),
        _root_phase("85", "AFTER", assertion["root_after_draft_id"]),
    ]
    relation_receipts = [
        _relation_phase(
            "20",
            1,
            "BEFORE",
            assertion["relation_before_draft_id"],
            assertion["relation_pair_id"],
        ),
        _relation_phase(
            "30",
            2,
            "AFTER",
            assertion["relation_after_draft_id"],
            assertion["relation_pair_id"],
        ),
    ]
    root_aggregate = aggregate_database_observer_phase_receipts(
        {
            "experiment": experiment,
            "observations": {
                "approved_database_observer_phase_receipts": root_receipts
            },
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )
    relation_aggregate = aggregate_database_relation_phase_receipts(
        {
            "experiment": experiment,
            "observations": {
                "approved_database_relation_phase_receipts": relation_receipts
            },
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )
    assert root_aggregate["status"] == "OBSERVED"
    assert relation_aggregate["status"] == "OBSERVED"
    assert relation_aggregate["evidence"]["oracle_verdict_emitted"] is False
    assert {
        row["relation_pair_id"]
        for row in relation_aggregate["evidence"][
            "approved_database_relation_snapshots"
        ]
    } == {assertion["relation_pair_id"]}

    treatment = build_contract_evidence_receipt(
        kind="treatment",
        experiment_id="experiment:balance-ledger",
        obligation_id="obligation:balance-ledger",
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
            "approved_database_observer_phase_receipts": root_receipts,
            "approved_database_relation_phase_receipts": relation_receipts,
            "harness_error": False,
        },
    )

    assert result["status"] == "VIOLATION"
    assert result["verdict"] == "customer_deliverable_defect_candidate"
    failed = result["failed_assertions"][0]
    assert failed["kind"] == ASSERTION_KIND
    assert failed["reason_code"] == (
        "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED"
    )
    assert failed["actual"]["binding_match"] is True
    assert failed["actual"]["semantic_pair_match"] is True
    assert failed["actual"]["relation_pair_match"] is True
    assert failed["actual"]["root_delta"] == "-15"
    assert failed["actual"]["relation_delta"] == "10"
    assert failed["actual"]["observer_performed_oracle_verdict"] is False
    assert result["customer_deliverable"] is False
    assert result["customer_deliverable_candidate"] is True
