from __future__ import annotations

from ai_test_asset_center.process_step_execution import ProcessStepLedger
from ai_test_asset_center.process_step_semantic_view import ProcessStepSemanticView


def _ledger(*step_ids: str) -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id="exp_semantic_view",
        required_step_ids=list(step_ids),
    )
    for index, step_id in enumerate(step_ids, 1):
        ledger.record_step_execution(
            step_id=step_id,
            phase="treatment",
            operation_ref=f"op_{index}",
            actor_ref="actor_1",
            status_code=200,
            final_status="EXECUTED",
            target_reached=True,
            after_state_receipt_id=f"after_{index}",
        )
    return ledger


def test_matching_observer_receipt_promotes_one_step() -> None:
    ledger = _ledger("step_1")
    observations = {
        "observer_receipts": [
            {
                "receipt_id": "obs_1",
                "status": "OBSERVED",
                "evidence": {
                    "source_step_id": "step_1",
                    "target_reached": True,
                },
            }
        ]
    }

    view = ProcessStepSemanticView(ledger, observations)

    assert view.executed_step_ids() == ["step_1"]
    assert ledger.get_step_row("step_1")["semantic_verdict_receipt_id"] == "obs_1"


def test_observed_status_without_boolean_verdict_does_not_promote() -> None:
    ledger = _ledger("step_1")
    observations = {
        "observer_receipts": [
            {
                "receipt_id": "obs_status_only",
                "status": "OBSERVED",
                "evidence": {"source_step_id": "step_1"},
            }
        ]
    }

    view = ProcessStepSemanticView(ledger, observations)

    assert view.executed_step_ids() == []


def test_receipt_without_exact_step_scope_does_not_fan_out() -> None:
    ledger = _ledger("step_1", "step_2")
    observations = {
        "observer_receipts": [
            {
                "receipt_id": "obs_unscoped",
                "status": "OBSERVED",
                "evidence": {"target_reached": True},
            }
        ]
    }

    view = ProcessStepSemanticView(ledger, observations)

    assert view.executed_step_ids() == []


def test_multi_step_scope_is_not_treated_as_one_step_verdict() -> None:
    ledger = _ledger("step_1", "step_2")
    observations = {
        "observer_receipts": [
            {
                "receipt_id": "obs_multi_scope",
                "status": "OBSERVED",
                "evidence": {
                    "step_ids": ["step_1", "step_2"],
                    "target_reached": True,
                },
            }
        ]
    }

    view = ProcessStepSemanticView(ledger, observations)

    assert view.executed_step_ids() == []


def test_scoped_negative_oracle_verdict_marks_step_failed() -> None:
    ledger = _ledger("step_1")
    observations = {
        "oracle_receipts": [
            {
                "receipt_id": "oracle_1",
                "step_id": "step_1",
                "passed": False,
            }
        ]
    }

    view = ProcessStepSemanticView(ledger, observations)

    assert view.executed_step_ids() == []
    assert view.failed_step_ids() == ["step_1"]


def test_unknown_step_identity_is_ignored() -> None:
    ledger = _ledger("step_1")
    observations = {
        "observer_receipts": [
            {
                "receipt_id": "obs_unknown",
                "evidence": {
                    "source_step_id": "step_404",
                    "target_reached": True,
                },
            }
        ]
    }

    view = ProcessStepSemanticView(ledger, observations)

    assert view.executed_step_ids() == []


def test_legacy_evidence_broadcast_is_a_side_effect_free_noop() -> None:
    ledger = _ledger("step_1")
    view = ProcessStepSemanticView(ledger, {})

    assert view.append_receipt_ref(
        "step_1",
        "oracle_receipt_ids",
        "oracle_unscoped",
    ) is False
    assert ledger.receipt_scope_rejections == []
    assert ledger.get_step_row("step_1")["scoped_oracle_receipt_ids"] == []

    assert view.append_receipt_ref(
        "step_1",
        "transport_receipt_id",
        "transport_exact",
    ) is True
    assert ledger.get_step_row("step_1")["transport_receipt_id"] == "transport_exact"
