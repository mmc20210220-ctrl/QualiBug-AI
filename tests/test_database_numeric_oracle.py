from __future__ import annotations

from ai_test_asset_center.database_numeric_oracle import (
    evaluate_database_numeric_conservation,
    evaluate_database_numeric_delta,
)


def _phase(phase: str, row: dict, *, execution_id: str = "execution-1") -> dict:
    return {
        "receipt_id": f"readback-{phase.lower()}",
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
                "identity_parameter_fingerprints": ["identity-a-1"],
                "match_status": "MATCHED_ONE",
                "row_count": 1,
                "rows": [row],
                "row_fingerprint": f"row-{phase.lower()}",
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": f"draft:accounts:{phase.lower()}",
        "observer_contract_ref": "observer:accounts",
        "observation_phase": phase,
        "oracle_verdict_emitted": False,
    }


def _term(field: str, **expectation: object) -> dict:
    return {
        "term_id": f"term:{field}",
        "database_observer_contract_ref": "observer:accounts",
        "before_draft_id": "draft:accounts:before",
        "after_draft_id": "draft:accounts:after",
        "database_table_ref": "table:accounts",
        "database_table_name": "accounts",
        "database_field_id": f"field:accounts:{field}",
        "database_field_name": field,
        "field_binding_id": f"binding:accounts:{field}",
        **expectation,
    }


def _observations(before: dict, after: dict) -> dict:
    return {
        "approved_database_observer_phase_receipts": [
            _phase("BEFORE", before),
            _phase("AFTER", after),
        ]
    }


def test_exact_decimal_delta_passes_without_float_rounding() -> None:
    result = evaluate_database_numeric_delta(
        {
            "spec": {
                "numeric_terms": [
                    _term("balance", expected_delta="-0.10", tolerance="0")
                ]
            },
            "observations": _observations(
                {"id": "a-1", "balance": "100.10"},
                {"id": "a-1", "balance": "100.00"},
            ),
        }
    )

    assert result["passed"] is True
    assert result["reason_code"] == ""
    term = result["actual"]["term_results"][0]
    assert term["actual_delta"] == "-0.1"
    assert term["expected_delta"] == "-0.1"
    assert term["observer_performed_oracle_verdict"] is False


def test_wrong_inventory_delta_is_a_violation() -> None:
    result = evaluate_database_numeric_delta(
        {
            "spec": {
                "numeric_terms": [_term("available_qty", expected_delta=-2)]
            },
            "observations": _observations(
                {"id": "a-1", "available_qty": 10},
                {"id": "a-1", "available_qty": 9},
            ),
        }
    )

    assert result["passed"] is False
    assert result["reason_code"] == "DATABASE_NUMERIC_DELTA_MISMATCH"
    assert result["actual"]["term_results"][0]["actual_delta"] == "-1"


def test_non_numeric_value_is_indeterminate_not_bug() -> None:
    result = evaluate_database_numeric_delta(
        {
            "spec": {
                "numeric_terms": [_term("balance", expected_delta=-10)]
            },
            "observations": _observations(
                {"id": "a-1", "balance": "unknown"},
                {"id": "a-1", "balance": "90"},
            ),
        }
    )

    assert result["passed"] is None
    assert result["reason_code"] == "DATABASE_NUMERIC_VALUE_NOT_NUMERIC"


def test_cross_run_numeric_snapshots_are_indeterminate() -> None:
    observations = {
        "approved_database_observer_phase_receipts": [
            _phase("BEFORE", {"id": "a-1", "balance": 100}),
            _phase(
                "AFTER",
                {"id": "a-1", "balance": 90},
                execution_id="execution-2",
            ),
        ]
    }
    result = evaluate_database_numeric_delta(
        {
            "spec": {
                "numeric_terms": [_term("balance", expected_delta=-10)]
            },
            "observations": observations,
        }
    )

    assert result["passed"] is None
    assert result["reason_code"] == "DATABASE_NUMERIC_RECEIPT_LINEAGE_MISMATCH"


def test_same_row_weighted_conservation_passes() -> None:
    result = evaluate_database_numeric_conservation(
        {
            "spec": {
                "numeric_policy": "UNCHANGED_WEIGHTED_SUM",
                "numeric_terms": [
                    _term("available_qty", coefficient=1),
                    _term("reserved_qty", coefficient=1),
                ],
            },
            "observations": _observations(
                {"id": "a-1", "available_qty": 80, "reserved_qty": 20},
                {"id": "a-1", "available_qty": 70, "reserved_qty": 30},
            ),
        }
    )

    assert result["passed"] is True
    assert result["actual"]["before_weighted_sum"] == "100"
    assert result["actual"]["after_weighted_sum"] == "100"
    assert result["actual"]["difference"] == "0"


def test_same_row_conservation_violation_is_detected() -> None:
    result = evaluate_database_numeric_conservation(
        {
            "spec": {
                "numeric_policy": "UNCHANGED_WEIGHTED_SUM",
                "numeric_terms": [
                    _term("available_qty"),
                    _term("reserved_qty"),
                ],
            },
            "observations": _observations(
                {"id": "a-1", "available_qty": 80, "reserved_qty": 20},
                {"id": "a-1", "available_qty": 70, "reserved_qty": 20},
            ),
        }
    )

    assert result["passed"] is False
    assert result["reason_code"] == "DATABASE_NUMERIC_CONSERVATION_VIOLATED"
    assert result["actual"]["difference"] == "-10"
