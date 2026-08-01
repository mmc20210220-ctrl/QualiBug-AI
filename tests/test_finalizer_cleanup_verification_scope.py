from __future__ import annotations

from ai_test_asset_center import experiment_outcome_finalizer as finalizer
from ai_test_asset_center.process_step_execution import ProcessStepLedger
from ai_test_asset_center.process_step_semantic_view import (
    ProcessStepSemanticView,
)


def _ledger() -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id="exp_1",
        campaign_id="campaign_1",
        run_id="run_1",
        obligation_id="obligation_1",
        protocol_id="protocol_1",
        required_step_ids=["write_a", "write_b"],
    )
    for step_id in ("write_a", "write_b"):
        ledger.record_step_execution(
            step_id=step_id,
            phase="treatment",
            operation_ref=f"op_{step_id}",
            actor_ref="actor_1",
            request_receipt_id=f"request_{step_id}",
            response_receipt_id=f"response_{step_id}",
            transport_receipt_id=f"transport_{step_id}",
            status_code=200,
            final_status="EXECUTED",
            operation_accepted=True,
        )
    return ledger


def _aggregate(*, duplicate_id: bool = False) -> dict:
    return {
        "schema_version": (
            "qualibug.process-graph-cleanup-equivalence-receipt.v1"
        ),
        "receipt_id": "graph_equivalence_1",
        "step_equivalence_receipts_by_id": {
            "write_a": {
                "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
                "receipt_id": "equivalence_shared"
                if duplicate_id
                else "equivalence_a",
                "source_step_id": "write_a",
                "equivalence_status": "EQUIVALENT",
            },
            "write_b": {
                "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
                "receipt_id": "equivalence_shared"
                if duplicate_id
                else "equivalence_b",
                "source_step_id": "write_b",
                "equivalence_status": "NOT_APPLICABLE",
            },
        },
    }


def _call_hook(monkeypatch, aggregate: dict) -> tuple[dict, ProcessStepLedger]:
    observations: dict = {}
    ledger = _ledger()
    view = ProcessStepSemanticView(ledger, observations=observations)
    monkeypatch.setattr(
        finalizer._scope,
        "_original_evaluate_cleanup_equivalence",
        lambda *args, **kwargs: aggregate,
    )
    token = finalizer._active_finalizer_scope.set((observations, view))
    try:
        result = finalizer._evaluate_cleanup_equivalence_exact()
    finally:
        finalizer._active_finalizer_scope.reset(token)
    assert result is aggregate
    return observations, ledger


def test_finalizer_binds_each_cleanup_verification_to_exact_source_step(
    monkeypatch,
) -> None:
    observations, ledger = _call_hook(monkeypatch, _aggregate())

    binding = observations["process_step_cleanup_verification_binding"]
    assert binding["complete"] is True
    assert binding["unbound"] == []
    assert binding["bound"] == [
        {
            "receipt_id": "equivalence_a",
            "step_id": "write_a",
            "evidence_kind": "cleanup",
        },
        {
            "receipt_id": "equivalence_b",
            "step_id": "write_b",
            "evidence_kind": "cleanup",
        },
    ]
    assert observations["cleanup_verification_receipts"] == [
        _aggregate(),
        _aggregate()["step_equivalence_receipts_by_id"]["write_a"],
        _aggregate()["step_equivalence_receipts_by_id"]["write_b"],
    ]
    assert ledger.get_step_row("write_a")[
        "scoped_cleanup_receipt_ids"
    ] == ["equivalence_a"]
    assert ledger.get_step_row("write_b")[
        "scoped_cleanup_receipt_ids"
    ] == ["equivalence_b"]
    assert observations["process_step_ledger_hash"] == ledger.compute_hash()
    assert observations["process_step_receipts"]


def test_finalizer_rejects_cleanup_verification_receipt_reuse(
    monkeypatch,
) -> None:
    observations, ledger = _call_hook(
        monkeypatch,
        _aggregate(duplicate_id=True),
    )

    binding = observations["process_step_cleanup_verification_binding"]
    assert binding["complete"] is False
    assert binding["bound"] == []
    assert binding["unbound"][0]["status"] == (
        "RECEIPT_REUSED_ACROSS_STEPS"
    )
    assert binding["unbound"][0]["declared_step_ids"] == [
        "write_a",
        "write_b",
    ]
    assert ledger.get_step_row("write_a")[
        "scoped_cleanup_receipt_ids"
    ] == []
    assert ledger.get_step_row("write_b")[
        "scoped_cleanup_receipt_ids"
    ] == []
