from __future__ import annotations

from ai_test_asset_center.database_relation_numeric_oracle import (
    evaluate_database_relation_conservation,
)
from ai_test_asset_center.database_relation_observer_experiment_runtime import (
    DRAFT_SCHEMA,
    aggregate_database_relation_phase_receipts,
)


RELATION_KEY = [
    {
        "child_database_field_name": "order_id",
        "parent_database_field_name": "id",
    }
]


def _root_phase(value: object) -> dict:
    return {
        "receipt_id": "root-receipt",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "observer_id": "approved_database_readback",
        "status": "OBSERVED",
        "evidence": {
            "approved_database_snapshot": {
                "database_table_ref": "table:orders",
                "database_table_name": "orders",
                "identity_key": ["id"],
                "identity_parameter_fingerprints": ["identity-o-1"],
                "match_status": "MATCHED_ONE",
                "row_count": 1,
                "rows": [{"id": "o-1", "item_count": value}],
                "row_fingerprint": "root-row",
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": "draft:orders:after",
        "observer_contract_ref": "observer:orders",
        "observation_phase": "AFTER",
        "oracle_verdict_emitted": False,
    }


def _relation_phase(*, request: dict, value: object, receipt_id: str) -> dict:
    return {
        "receipt_id": receipt_id,
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "observer_id": "approved_database_relation_aggregate",
        "status": "OBSERVED",
        "evidence": {
            "approved_database_relation_aggregate_snapshot": {
                "relation_observer_ref": "relation-observer:order-lines",
                "root_observer_ref": "observer:orders",
                "database_relationship_id": "fk:order_lines:orders",
                "parent_table_ref": "table:orders",
                "child_table_ref": "table:order_lines",
                "child_table_name": "order_lines",
                "relation_key": RELATION_KEY,
                "relation_parameter_fingerprints": ["identity-o-1"],
                "aggregate_requests": [request],
                "aggregate_values": {request["alias"]: value},
                "aggregate_fingerprint": f"aggregate-{receipt_id}",
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


def _spec(**overrides: object) -> dict:
    return {
        "database_relation_observer_ref": "relation-observer:order-lines",
        "database_relation_draft_id": "draft:relation:after",
        "database_relationship_id": "fk:order_lines:orders",
        "relation_key": RELATION_KEY,
        "root_observer_contract_ref": "observer:orders",
        "root_database_draft_id": "draft:orders:after",
        "root_table_ref": "table:orders",
        "root_database_field_id": "field:orders:item_count",
        "root_database_field_name": "item_count",
        "child_table_ref": "table:order_lines",
        "child_database_field_id": "field:order_lines:sku",
        "child_database_field_name": "sku",
        "aggregate": "COUNT",
        "aggregate_alias": "related_value",
        "comparison_operator": "EQ",
        "aggregate_on_left": False,
        "tolerance": "0",
        **overrides,
    }


def test_exact_count_field_is_supported() -> None:
    request = {
        "aggregate": "COUNT",
        "database_field_id": "field:order_lines:sku",
        "database_field_name": "sku",
        "alias": "related_value",
    }
    result = evaluate_database_relation_conservation(
        {
            "spec": _spec(),
            "observations": {
                "approved_database_observer_phase_receipts": [_root_phase(2)],
                "approved_database_relation_phase_receipts": [
                    _relation_phase(
                        request=request,
                        value=2,
                        receipt_id="relation-1",
                    )
                ],
            },
        }
    )

    assert result["passed"] is True
    assert result["actual"]["aggregate_request_match"] is True


def test_count_field_requires_both_exact_id_and_name() -> None:
    request = {
        "aggregate": "COUNT",
        "database_field_id": "field:order_lines:sku",
        "database_field_name": "sku",
        "alias": "related_value",
    }
    result = evaluate_database_relation_conservation(
        {
            "spec": _spec(child_database_field_name=""),
            "observations": {
                "approved_database_observer_phase_receipts": [_root_phase(2)],
                "approved_database_relation_phase_receipts": [
                    _relation_phase(
                        request=request,
                        value=2,
                        receipt_id="relation-1",
                    )
                ],
            },
        }
    )

    assert result["passed"] is None
    assert result["reason_code"] == "DATABASE_RELATION_ASSERTION_SPEC_INCOMPLETE"


def test_phase_aggregate_rejects_duplicate_receipts_without_winner() -> None:
    request = {
        "aggregate": "COUNT",
        "database_field_id": "",
        "database_field_name": "",
        "alias": "related_value",
    }
    experiment = {
        "database_relation_observer_execution_drafts": [
            {
                "schema": DRAFT_SCHEMA,
                "draft_id": "draft:relation:after",
                "relation_observer_contract_ref": "relation-observer:order-lines",
                "root_observer_contract_ref": "observer:orders",
                "observation_phase": "AFTER",
                "required": True,
            }
        ]
    }
    result = aggregate_database_relation_phase_receipts(
        {
            "experiment": experiment,
            "observations": {
                "approved_database_relation_phase_receipts": [
                    _relation_phase(
                        request=request,
                        value=2,
                        receipt_id="relation-1",
                    ),
                    _relation_phase(
                        request=request,
                        value=3,
                        receipt_id="relation-2",
                    ),
                ]
            },
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )

    assert result["status"] == "INDETERMINATE"
    assert result["reason_code"] == "DATABASE_RELATION_PHASE_RECEIPT_DUPLICATE"
    evidence = result["evidence"]
    assert evidence["automatic_receipt_winner_count"] == 0
    assert evidence["approved_database_relation_snapshots"] == []
    assert evidence["duplicate_phase_receipts"] == [
        {
            "draft_id": "draft:relation:after",
            "phase": "AFTER",
            "receipt_count": 2,
            "receipt_ids": ["relation-1", "relation-2"],
        }
    ]
