from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_relation_delta_lineage import (
    evaluate_database_relation_delta_with_lineage as evaluate_database_relation_delta_conservation,
)


def _root_phase(
    value: object,
    *,
    phase: str,
    draft_id: str,
    execution_id: str = "execution-1",
    identity: str = "identity-a-1",
) -> dict:
    return {
        "receipt_id": f"root-{phase.lower()}-receipt",
        "campaign_id": "campaign-1",
        "execution_id": execution_id,
        "observer_id": "approved_database_readback",
        "status": "OBSERVED",
        "reason_code": "",
        "evidence": {
            "approved_database_snapshot": {
                "database_table_ref": "table:accounts",
                "database_table_name": "accounts",
                "identity_key": ["id"],
                "identity_parameter_fingerprints": [identity],
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
    value: object,
    *,
    phase: str,
    draft_id: str,
    scope_count: object = 1,
    execution_id: str = "execution-1",
    identity: str = "identity-a-1",
    aggregate: str = "SUM",
    field_id: str = "field:ledger_entries:amount",
    field_name: str = "amount",
    relationship_id: str = "fk:ledger:accounts",
    relation_pair_id: str = "relation-pair-1",
    relation_key: list[dict] | None = None,
    extra_requests: list[dict] | None = None,
) -> dict:
    requests = [
        {
            "aggregate": aggregate,
            "database_field_id": field_id,
            "database_field_name": field_name,
            "alias": "related_value",
        },
        {
            "aggregate": "COUNT",
            "database_field_id": "",
            "database_field_name": "",
            "alias": "related_scope_count",
        },
        *(extra_requests or []),
    ]
    return {
        "receipt_id": f"relation-{phase.lower()}-receipt",
        "phase_receipt_id": f"relation-{phase.lower()}-phase-receipt",
        "campaign_id": "campaign-1",
        "execution_id": execution_id,
        "observer_id": "approved_database_relation_aggregate",
        "status": "OBSERVED",
        "reason_code": "",
        "evidence": {
            "approved_database_relation_aggregate_snapshot": {
                "relation_observer_ref": "relation-observer:ledger",
                "root_observer_ref": "observer:accounts",
                "database_relationship_id": relationship_id,
                "parent_table_ref": "table:accounts",
                "child_table_ref": "table:ledger_entries",
                "child_table_name": "ledger_entries",
                "relation_key": relation_key
                or [
                    {
                        "child_database_field_name": "account_id",
                        "parent_database_field_name": "id",
                    }
                ],
                "relation_parameter_fingerprints": [identity],
                "aggregate_requests": requests,
                "aggregate_values": {
                    "related_value": value,
                    "related_scope_count": scope_count,
                },
                "aggregate_fingerprint": f"aggregate-{phase}-{value}-{scope_count}",
                "client_side_filter_used": False,
                "raw_rows_retained": False,
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": draft_id,
        "relation_pair_id": relation_pair_id,
        "relation_observer_contract_ref": "relation-observer:ledger",
        "root_observer_contract_ref": "observer:accounts",
        "observation_phase": phase,
        "oracle_verdict_emitted": False,
    }


def _spec(**overrides: object) -> dict:
    return {
        "database_relation_observer_ref": "relation-observer:ledger",
        "database_relationship_id": "fk:ledger:accounts",
        "relation_key": [
            {
                "child_database_field_name": "account_id",
                "parent_database_field_name": "id",
            }
        ],
        "relation_pair_id": "relation-pair-1",
        "relation_before_draft_id": "draft:relation:before",
        "relation_after_draft_id": "draft:relation:after",
        "root_observer_contract_ref": "observer:accounts",
        "root_before_draft_id": "draft:accounts:before",
        "root_after_draft_id": "draft:accounts:after",
        "root_table_ref": "table:accounts",
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
        "tolerance": "0",
        **overrides,
    }


def _observations(
    *,
    root_before: object = "100",
    root_after: object = "90",
    relation_before: object = "20",
    relation_after: object = "30",
    relation_before_count: object = 1,
    relation_after_count: object = 2,
) -> dict:
    return {
        "approved_database_observer_phase_receipts": [
            _root_phase(
                root_before,
                phase="BEFORE",
                draft_id="draft:accounts:before",
            ),
            _root_phase(
                root_after,
                phase="AFTER",
                draft_id="draft:accounts:after",
            ),
        ],
        "approved_database_relation_phase_receipts": [
            _relation_phase(
                relation_before,
                phase="BEFORE",
                draft_id="draft:relation:before",
                scope_count=relation_before_count,
            ),
            _relation_phase(
                relation_after,
                phase="AFTER",
                draft_id="draft:relation:after",
                scope_count=relation_after_count,
            ),
        ],
    }


def test_root_decrease_equals_child_sum_increase_with_explicit_sign() -> None:
    result = evaluate_database_relation_delta_conservation(
        {"spec": _spec(), "observations": _observations()}
    )

    assert result["passed"] is True
    assert result["reason_code"] == ""
    assert result["actual"]["relation_pair_match"] is True
    assert result["actual"]["root_delta"] == "-10"
    assert result["actual"]["relation_delta"] == "10"
    assert result["actual"]["weighted_left_delta"] == "10"
    assert result["actual"]["weighted_right_delta"] == "10"
    assert result["actual"]["difference"] == "0"
    assert result["actual"]["observer_performed_oracle_verdict"] is False


def test_mismatched_root_and_relation_delta_is_violation() -> None:
    result = evaluate_database_relation_delta_conservation(
        {
            "spec": _spec(),
            "observations": _observations(root_after="85"),
        }
    )

    assert result["passed"] is False
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED"
    )
    assert result["actual"]["root_delta"] == "-15"
    assert result["actual"]["relation_delta"] == "10"
    assert result["actual"]["difference"] == "5"


def test_empty_before_sum_is_zero_only_with_scope_count_proof() -> None:
    result = evaluate_database_relation_delta_conservation(
        {
            "spec": _spec(),
            "observations": _observations(
                root_before="100",
                root_after="90",
                relation_before=None,
                relation_after="10",
                relation_before_count=0,
                relation_after_count=1,
            ),
        }
    )

    assert result["passed"] is True
    assert result["actual"]["relation_before"] == "0"
    assert result["actual"][
        "relation_before_empty_sum_normalized_to_zero"
    ] is True

    unproven = _observations(
        relation_before=None,
        relation_after="10",
        relation_before_count=None,
        relation_after_count=1,
    )
    rejected = evaluate_database_relation_delta_conservation(
        {"spec": _spec(), "observations": unproven}
    )
    assert rejected["passed"] is None
    assert rejected["reason_code"] == (
        "DATABASE_RELATION_DELTA_SCOPE_COUNT_INVALID"
    )


def test_cross_run_or_identity_drift_is_indeterminate() -> None:
    cross_run = _observations()
    cross_run["approved_database_relation_phase_receipts"][1][
        "execution_id"
    ] = "execution-2"
    result = evaluate_database_relation_delta_conservation(
        {"spec": _spec(), "observations": cross_run}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_RECEIPT_LINEAGE_MISMATCH"
    )

    identity_drift = _observations()
    identity_drift["approved_database_relation_phase_receipts"][1][
        "evidence"
    ]["approved_database_relation_aggregate_snapshot"][
        "relation_parameter_fingerprints"
    ] = ["identity-other"]
    result = evaluate_database_relation_delta_conservation(
        {"spec": _spec(), "observations": identity_drift}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_RELATION_IDENTITY_MISMATCH"
    )


def test_pair_lineage_must_match_both_phases_and_rule() -> None:
    missing = _observations()
    missing["approved_database_relation_phase_receipts"][0][
        "relation_pair_id"
    ] = ""
    result = evaluate_database_relation_delta_conservation(
        {"spec": _spec(), "observations": missing}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_PAIR_LINEAGE_MISSING"
    )

    mismatch = _observations()
    mismatch["approved_database_relation_phase_receipts"][1][
        "relation_pair_id"
    ] = "relation-pair-other"
    result = evaluate_database_relation_delta_conservation(
        {"spec": _spec(), "observations": mismatch}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_PAIR_LINEAGE_MISMATCH"
    )


def test_wrong_aggregate_or_fk_scope_cannot_impersonate_delta() -> None:
    wrong_aggregate = _observations()
    payload = wrong_aggregate["approved_database_relation_phase_receipts"][1][
        "evidence"
    ]["approved_database_relation_aggregate_snapshot"]
    payload["aggregate_requests"][0]["aggregate"] = "MAX"
    result = evaluate_database_relation_delta_conservation(
        {"spec": _spec(), "observations": wrong_aggregate}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_AGGREGATE_REQUEST_SCOPE_MISMATCH"
    )

    wrong_fk = _observations()
    wrong_fk["approved_database_relation_phase_receipts"][1]["evidence"][
        "approved_database_relation_aggregate_snapshot"
    ]["database_relationship_id"] = "fk:other:accounts"
    result = evaluate_database_relation_delta_conservation(
        {"spec": _spec(), "observations": wrong_fk}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_RELATION_SCOPE_MISMATCH"
    )


def test_missing_or_duplicate_phase_receipt_never_becomes_bug() -> None:
    missing = _observations()
    missing["approved_database_relation_phase_receipts"] = missing[
        "approved_database_relation_phase_receipts"
    ][:1]
    result = evaluate_database_relation_delta_conservation(
        {"spec": _spec(), "observations": missing}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_PHASE_RECEIPT_MISSING"
    )

    duplicate = _observations()
    duplicate["approved_database_relation_phase_receipts"].append(
        deepcopy(duplicate["approved_database_relation_phase_receipts"][1])
    )
    duplicate["approved_database_relation_phase_receipts"][-1][
        "receipt_id"
    ] = "relation-after-receipt-copy"
    result = evaluate_database_relation_delta_conservation(
        {"spec": _spec(), "observations": duplicate}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_PHASE_RECEIPT_AMBIGUOUS"
    )


def test_invalid_or_missing_sign_coefficient_is_not_inferred() -> None:
    invalid = evaluate_database_relation_delta_conservation(
        {
            "spec": _spec(left_coefficient="not-a-number"),
            "observations": _observations(),
        }
    )
    assert invalid["passed"] is None
    assert invalid["reason_code"] == (
        "DATABASE_RELATION_DELTA_COEFFICIENT_INVALID"
    )

    no_sign = evaluate_database_relation_delta_conservation(
        {
            "spec": _spec(left_coefficient=1),
            "observations": _observations(),
        }
    )
    assert no_sign["passed"] is False
    assert no_sign["reason_code"] == (
        "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED"
    )
