from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_relation_delta_experiment_projection import (
    _stable_id,
)
from ai_test_asset_center.database_relation_delta_lineage import (
    evaluate_database_relation_delta_with_lineage as evaluate_database_relation_delta_conservation,
)
from ai_test_asset_center.database_relation_delta_projection_gate import (
    SEMANTIC_PAIR_SCHEMA,
    semantic_relation_delta_pair_id,
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
    relation_pair_id: str,
    scope_count: object = 1,
    execution_id: str = "execution-1",
    identity: str = "identity-a-1",
    aggregate: str = "SUM",
    field_id: str = "field:ledger_entries:amount",
    field_name: str = "amount",
    relationship_id: str = "fk:ledger:accounts",
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
    row = {
        "assertion_id": "assert:balance-ledger-delta",
        "source_assertion_kind": "conservation",
        "source_refs": [
            {"kind": "business_rule", "locator": "BR-BALANCE-LEDGER"}
        ],
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
        **overrides,
    }
    pair_id = semantic_relation_delta_pair_id(row)
    before_draft_id = _stable_id(
        "database_relation_observer_execution_draft",
        row["database_relation_observer_ref"],
        row["assertion_id"],
        "BEFORE",
        pair_id,
    )
    after_draft_id = _stable_id(
        "database_relation_observer_execution_draft",
        row["database_relation_observer_ref"],
        row["assertion_id"],
        "AFTER",
        pair_id,
    )
    row["relation_pair_id"] = pair_id
    row["relation_before_draft_id"] = before_draft_id
    row["relation_after_draft_id"] = after_draft_id
    row["database_relation_delta_binding"].update(
        {
            "semantic_pair_schema": SEMANTIC_PAIR_SCHEMA,
            "relation_pair_id": pair_id,
            "relation_before_draft_id": before_draft_id,
            "relation_after_draft_id": after_draft_id,
            "pair_covers_complete_assertion_semantics": True,
        }
    )
    return row


def _observations(
    *,
    spec: dict,
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
                draft_id=spec["root_before_draft_id"],
            ),
            _root_phase(
                root_after,
                phase="AFTER",
                draft_id=spec["root_after_draft_id"],
            ),
        ],
        "approved_database_relation_phase_receipts": [
            _relation_phase(
                relation_before,
                phase="BEFORE",
                draft_id=spec["relation_before_draft_id"],
                relation_pair_id=spec["relation_pair_id"],
                scope_count=relation_before_count,
            ),
            _relation_phase(
                relation_after,
                phase="AFTER",
                draft_id=spec["relation_after_draft_id"],
                relation_pair_id=spec["relation_pair_id"],
                scope_count=relation_after_count,
            ),
        ],
    }


def _evaluate(spec: dict, **observation_overrides: object) -> dict:
    return evaluate_database_relation_delta_conservation(
        {
            "spec": spec,
            "observations": _observations(
                spec=spec,
                **observation_overrides,
            ),
        }
    )


def test_root_decrease_equals_child_sum_increase_with_explicit_sign() -> None:
    spec = _spec()
    result = _evaluate(spec)

    assert result["passed"] is True
    assert result["reason_code"] == ""
    assert result["actual"]["binding_match"] is True
    assert result["actual"]["semantic_pair_match"] is True
    assert result["actual"]["relation_pair_match"] is True
    assert result["actual"]["root_delta"] == "-10"
    assert result["actual"]["relation_delta"] == "10"
    assert result["actual"]["weighted_left_delta"] == "10"
    assert result["actual"]["weighted_right_delta"] == "10"
    assert result["actual"]["difference"] == "0"
    assert result["actual"]["observer_performed_oracle_verdict"] is False


def test_mismatched_root_and_relation_delta_is_violation() -> None:
    spec = _spec()
    result = _evaluate(spec, root_after="85")

    assert result["passed"] is False
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED"
    )
    assert result["actual"]["root_delta"] == "-15"
    assert result["actual"]["relation_delta"] == "10"
    assert result["actual"]["difference"] == "5"


def test_empty_before_sum_is_zero_only_with_scope_count_proof() -> None:
    spec = _spec()
    result = _evaluate(
        spec,
        root_before="100",
        root_after="90",
        relation_before=None,
        relation_after="10",
        relation_before_count=0,
        relation_after_count=1,
    )

    assert result["passed"] is True
    assert result["actual"]["relation_before"] == "0"
    assert result["actual"][
        "relation_before_empty_sum_normalized_to_zero"
    ] is True

    rejected = _evaluate(
        spec,
        relation_before=None,
        relation_after="10",
        relation_before_count=None,
        relation_after_count=1,
    )
    assert rejected["passed"] is None
    assert rejected["reason_code"] == (
        "DATABASE_RELATION_DELTA_SCOPE_COUNT_INVALID"
    )


def test_semantic_or_binding_tamper_is_indeterminate() -> None:
    original = _spec()
    observations = _observations(spec=original)

    semantic_tamper = deepcopy(original)
    semantic_tamper["left_coefficient"] = -2
    result = evaluate_database_relation_delta_conservation(
        {"spec": semantic_tamper, "observations": observations}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_SEMANTIC_PAIR_MISMATCH"
    )

    binding_tamper = deepcopy(original)
    binding_tamper["database_relation_delta_binding"][
        "relation_after_draft_id"
    ] = "draft:other"
    result = evaluate_database_relation_delta_conservation(
        {"spec": binding_tamper, "observations": observations}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_BINDING_MISMATCH"
    )


def test_vacuous_or_invalid_coefficients_are_rejected() -> None:
    vacuous = _spec(left_coefficient=0, right_coefficient=0)
    result = _evaluate(vacuous)
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_VACUOUS_COEFFICIENTS"
    )

    invalid = _spec(left_coefficient="not-a-number")
    result = _evaluate(invalid)
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_COEFFICIENT_INVALID"
    )


def test_cross_run_or_identity_drift_is_indeterminate() -> None:
    spec = _spec()
    cross_run = _observations(spec=spec)
    cross_run["approved_database_relation_phase_receipts"][1][
        "execution_id"
    ] = "execution-2"
    result = evaluate_database_relation_delta_conservation(
        {"spec": spec, "observations": cross_run}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_RECEIPT_LINEAGE_MISMATCH"
    )

    identity_drift = _observations(spec=spec)
    identity_drift["approved_database_relation_phase_receipts"][1][
        "evidence"
    ]["approved_database_relation_aggregate_snapshot"][
        "relation_parameter_fingerprints"
    ] = ["identity-other"]
    result = evaluate_database_relation_delta_conservation(
        {"spec": spec, "observations": identity_drift}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_RELATION_IDENTITY_MISMATCH"
    )


def test_pair_lineage_must_match_both_phases_and_rule() -> None:
    spec = _spec()
    missing = _observations(spec=spec)
    missing["approved_database_relation_phase_receipts"][0][
        "relation_pair_id"
    ] = ""
    result = evaluate_database_relation_delta_conservation(
        {"spec": spec, "observations": missing}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_PAIR_LINEAGE_MISSING"
    )

    mismatch = _observations(spec=spec)
    mismatch["approved_database_relation_phase_receipts"][1][
        "relation_pair_id"
    ] = "relation-pair-other"
    result = evaluate_database_relation_delta_conservation(
        {"spec": spec, "observations": mismatch}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_PAIR_LINEAGE_MISMATCH"
    )


def test_wrong_aggregate_or_fk_scope_cannot_impersonate_delta() -> None:
    spec = _spec()
    wrong_aggregate = _observations(spec=spec)
    payload = wrong_aggregate["approved_database_relation_phase_receipts"][1][
        "evidence"
    ]["approved_database_relation_aggregate_snapshot"]
    payload["aggregate_requests"][0]["aggregate"] = "MAX"
    result = evaluate_database_relation_delta_conservation(
        {"spec": spec, "observations": wrong_aggregate}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_AGGREGATE_REQUEST_SCOPE_MISMATCH"
    )

    wrong_fk = _observations(spec=spec)
    wrong_fk["approved_database_relation_phase_receipts"][1]["evidence"][
        "approved_database_relation_aggregate_snapshot"
    ]["database_relationship_id"] = "fk:other:accounts"
    result = evaluate_database_relation_delta_conservation(
        {"spec": spec, "observations": wrong_fk}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_RELATION_SCOPE_MISMATCH"
    )


def test_missing_or_duplicate_phase_receipt_never_becomes_bug() -> None:
    spec = _spec()
    missing = _observations(spec=spec)
    missing["approved_database_relation_phase_receipts"] = missing[
        "approved_database_relation_phase_receipts"
    ][:1]
    result = evaluate_database_relation_delta_conservation(
        {"spec": spec, "observations": missing}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_PHASE_RECEIPT_MISSING"
    )

    duplicate = _observations(spec=spec)
    duplicate["approved_database_relation_phase_receipts"].append(
        deepcopy(duplicate["approved_database_relation_phase_receipts"][1])
    )
    duplicate["approved_database_relation_phase_receipts"][-1][
        "receipt_id"
    ] = "relation-after-receipt-copy"
    result = evaluate_database_relation_delta_conservation(
        {"spec": spec, "observations": duplicate}
    )
    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_PHASE_RECEIPT_AMBIGUOUS"
    )


def test_sign_is_not_inferred() -> None:
    no_sign = _spec(left_coefficient=1)
    result = _evaluate(no_sign)
    assert result["passed"] is False
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED"
    )
