from __future__ import annotations

from ai_test_asset_center.database_relation_numeric_oracle import (
    evaluate_database_relation_conservation,
)


def _root_phase(total: object, *, execution_id: str = "execution-1") -> dict:
    return {
        "receipt_id": "root-after-receipt",
        "campaign_id": "campaign-1",
        "execution_id": execution_id,
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
                "rows": [{"id": "o-1", "total": total}],
                "row_fingerprint": f"root-row-{total}",
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": "draft:orders:after",
        "observer_contract_ref": "observer:orders",
        "observation_phase": "AFTER",
        "oracle_verdict_emitted": False,
    }


def _relation_phase(
    aggregate: object,
    *,
    execution_id: str = "execution-1",
    identity: str = "identity-o-1",
    client_side_filter_used: bool = False,
    raw_rows_retained: bool = False,
) -> dict:
    return {
        "receipt_id": "relation-after-receipt",
        "campaign_id": "campaign-1",
        "execution_id": execution_id,
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
                "relation_parameter_fingerprints": [identity],
                "aggregate_values": {"related_value": aggregate},
                "aggregate_fingerprint": f"aggregate-{aggregate}",
                "client_side_filter_used": client_side_filter_used,
                "raw_rows_retained": raw_rows_retained,
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
        "root_observer_contract_ref": "observer:orders",
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
        **overrides,
    }


def _observations(root: dict, relation: dict) -> dict:
    return {
        "approved_database_observer_phase_receipts": [root],
        "approved_database_relation_phase_receipts": [relation],
    }


def test_root_total_equals_child_sum_passes() -> None:
    result = evaluate_database_relation_conservation(
        {
            "spec": _spec(),
            "observations": _observations(_root_phase("30.00"), _relation_phase("30")),
        }
    )

    assert result["passed"] is True
    assert result["reason_code"] == ""
    assert result["actual"]["root_value"] == "30"
    assert result["actual"]["aggregate_value"] == "30"
    assert result["actual"]["difference"] == "0"
    assert result["actual"]["observer_performed_oracle_verdict"] is False


def test_root_total_not_equal_child_sum_is_violation() -> None:
    result = evaluate_database_relation_conservation(
        {
            "spec": _spec(),
            "observations": _observations(_root_phase("30"), _relation_phase("25")),
        }
    )

    assert result["passed"] is False
    assert result["reason_code"] == "DATABASE_RELATION_CONSERVATION_VIOLATED"
    assert result["actual"]["difference"] == "5"


def test_cross_run_root_and_relation_receipts_are_indeterminate() -> None:
    result = evaluate_database_relation_conservation(
        {
            "spec": _spec(),
            "observations": _observations(
                _root_phase("30", execution_id="execution-1"),
                _relation_phase("30", execution_id="execution-2"),
            ),
        }
    )

    assert result["passed"] is None
    assert result["reason_code"] == "DATABASE_RELATION_RECEIPT_LINEAGE_MISMATCH"


def test_relation_parent_identity_must_equal_root_identity() -> None:
    result = evaluate_database_relation_conservation(
        {
            "spec": _spec(),
            "observations": _observations(
                _root_phase("30"),
                _relation_phase("30", identity="identity-other"),
            ),
        }
    )

    assert result["passed"] is None
    assert result["reason_code"] == "DATABASE_RELATION_ROOT_IDENTITY_MISMATCH"


def test_client_side_filter_or_raw_child_rows_invalidates_evidence() -> None:
    filtered = evaluate_database_relation_conservation(
        {
            "spec": _spec(),
            "observations": _observations(
                _root_phase("30"),
                _relation_phase("30", client_side_filter_used=True),
            ),
        }
    )
    assert filtered["passed"] is None
    assert filtered["reason_code"] == "DATABASE_RELATION_AGGREGATE_EVIDENCE_POLICY_INVALID"

    raw_rows = evaluate_database_relation_conservation(
        {
            "spec": _spec(),
            "observations": _observations(
                _root_phase("30"),
                _relation_phase("30", raw_rows_retained=True),
            ),
        }
    )
    assert raw_rows["passed"] is None
    assert raw_rows["reason_code"] == "DATABASE_RELATION_AGGREGATE_EVIDENCE_POLICY_INVALID"


def test_non_numeric_or_invalid_tolerance_is_indeterminate_not_bug() -> None:
    non_numeric = evaluate_database_relation_conservation(
        {
            "spec": _spec(),
            "observations": _observations(_root_phase("unknown"), _relation_phase("30")),
        }
    )
    assert non_numeric["passed"] is None
    assert non_numeric["reason_code"] == "DATABASE_RELATION_NUMERIC_VALUE_INVALID"

    invalid_tolerance = evaluate_database_relation_conservation(
        {
            "spec": _spec(tolerance="not-a-number"),
            "observations": _observations(_root_phase("30"), _relation_phase("30")),
        }
    )
    assert invalid_tolerance["passed"] is None
    assert invalid_tolerance["reason_code"] == "DATABASE_RELATION_TOLERANCE_INVALID"
