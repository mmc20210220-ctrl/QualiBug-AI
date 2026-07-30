from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.assertion_dsl import evaluate_assertion
from ai_test_asset_center.database_state_transition_oracle import (
    DATABASE_STATE_TRANSITION_ASSERTION_KIND,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers


install_non_http_observers()


def _phase(
    phase: str,
    value: str,
    *,
    match_status: str = "MATCHED_ONE",
    identity: list[str] | None = None,
    contract_ref: str = "observer:orders",
    campaign_id: str = "campaign-1",
    execution_id: str = "execution-1",
) -> dict:
    rows = [{"id": "o-1", "status": value}] if match_status == "MATCHED_ONE" else []
    row_count = 1 if match_status == "MATCHED_ONE" else 2 if match_status == "NON_UNIQUE_IDENTITY" else 0
    return {
        "schema_version": "qualibug.observer-receipt.v1",
        "receipt_id": f"obs-{phase.lower()}-{value}-{execution_id}",
        "campaign_id": campaign_id,
        "execution_id": execution_id,
        "observer_id": "approved_database_readback",
        "status": "OBSERVED",
        "reason_code": "",
        "evidence": {
            "approved_database_snapshot": {
                "observer_contract_ref": contract_ref,
                "database_table_ref": "table:orders",
                "database_table_name": "orders",
                "identity_key": ["id"],
                "identity_parameter_fingerprints": identity or ["identity-o-1"],
                "match_status": match_status,
                "row_count": row_count,
                "rows": rows,
                "row_fingerprint": f"row-{phase.lower()}-{value}",
                "oracle_verdict_emitted": False,
            }
        },
        "phase_receipt_id": f"phase-{phase.lower()}",
        "draft_id": f"draft:orders:{phase.lower()}",
        "observer_contract_ref": contract_ref,
        "observation_phase": phase,
        "required": True,
        "executed_before_cleanup": True,
        "oracle_verdict_emitted": False,
    }


def _assertion(*, forbidden: bool = False) -> dict:
    return {
        "assertion_id": "assert:orders:status",
        "kind": DATABASE_STATE_TRANSITION_ASSERTION_KIND,
        "source_assertion_kind": (
            "forbidden_state_transition" if forbidden else "state_transition"
        ),
        "operator": "must_not_transition" if forbidden else "must_transition",
        "from_state": "PENDING",
        "to_state": "PAID",
        "database_observer_contract_ref": "observer:orders",
        "before_draft_id": "draft:orders:before",
        "after_draft_id": "draft:orders:after",
        "database_table_ref": "table:orders",
        "database_field_id": "field:orders:status",
        "database_field_name": "status",
        "source_refs": [{"kind": "business_rule", "locator": "BR-ORDER-1"}],
    }


def _evaluate(assertion: dict, before: dict, after: dict) -> dict:
    return evaluate_assertion(
        assertion,
        observations={
            "approved_database_observer_phase_receipts": [before, after],
        },
        campaign_id="campaign-1",
        execution_id="execution-1",
    )


def test_exact_pending_to_paid_transition_passes() -> None:
    receipt = _evaluate(_assertion(), _phase("BEFORE", "PENDING"), _phase("AFTER", "PAID"))

    assert receipt["status"] == "PASS"
    assert receipt["passed"] is True
    assert receipt["actual"]["lineage_match"] is True
    assert receipt["actual"]["identity_match"] is True
    assert receipt["actual"]["observed_before"] == "PENDING"
    assert receipt["actual"]["observed_after"] == "PAID"
    assert receipt["actual"]["observer_performed_oracle_verdict"] is False


def test_missing_required_transition_is_a_violation() -> None:
    receipt = _evaluate(
        _assertion(),
        _phase("BEFORE", "PENDING"),
        _phase("AFTER", "PENDING"),
    )

    assert receipt["status"] == "VIOLATION"
    assert receipt["reason_code"] == "DATABASE_STATE_TRANSITION_NOT_OBSERVED"
    assert receipt["actual"]["before_snapshot"]["row_fingerprint"]
    assert receipt["actual"]["after_snapshot"]["row_fingerprint"]


def test_forbidden_transition_reaching_target_is_a_violation() -> None:
    receipt = _evaluate(
        _assertion(forbidden=True),
        _phase("BEFORE", "PENDING"),
        _phase("AFTER", "PAID"),
    )

    assert receipt["status"] == "VIOLATION"
    assert receipt["reason_code"] == "DATABASE_FORBIDDEN_STATE_TRANSITION_OBSERVED"


def test_precondition_mismatch_is_indeterminate_not_a_bug() -> None:
    receipt = _evaluate(
        _assertion(),
        _phase("BEFORE", "CANCELLED"),
        _phase("AFTER", "PAID"),
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["passed"] is None
    assert receipt["reason_code"] == "DATABASE_STATE_PRECONDITION_NOT_MET"


def test_non_unique_identity_is_indeterminate() -> None:
    receipt = _evaluate(
        _assertion(),
        _phase("BEFORE", "PENDING"),
        _phase("AFTER", "PAID", match_status="NON_UNIQUE_IDENTITY"),
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "DATABASE_STATE_IDENTITY_NOT_UNIQUE"


def test_identity_drift_between_phases_is_indeterminate() -> None:
    before = _phase("BEFORE", "PENDING", identity=["identity-o-1"])
    after = _phase("AFTER", "PAID", identity=["identity-o-2"])
    receipt = _evaluate(_assertion(), before, after)

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "DATABASE_STATE_IDENTITY_MISMATCH"


def test_cross_execution_phase_pair_is_indeterminate() -> None:
    before = _phase("BEFORE", "PENDING", execution_id="execution-1")
    after = _phase("AFTER", "PAID", execution_id="execution-2")
    receipt = _evaluate(_assertion(), before, after)

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "DATABASE_STATE_RECEIPT_LINEAGE_MISMATCH"
    assert receipt["actual"]["lineage_match"] is False


def test_missing_after_phase_is_indeterminate() -> None:
    receipt = evaluate_assertion(
        deepcopy(_assertion()),
        observations={
            "approved_database_observer_phase_receipts": [
                _phase("BEFORE", "PENDING")
            ]
        },
        campaign_id="campaign-1",
        execution_id="execution-1",
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "DATABASE_STATE_SNAPSHOT_PAIR_MISSING"
