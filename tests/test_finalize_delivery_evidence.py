from __future__ import annotations

from ai_test_asset_center.experiment_batch_executor import (
    finalize_finding_evidence_after_delivery_gate,
)


def test_finalize_evidence_pack_after_deliverable_gate() -> None:
    finding = {
        "finding_id": "f1",
        "evidence_quality": {
            "level": "executed_candidate",
            "score": 0,
            "can_reproduce": False,
            "evidence_strength": "typed_contract_violation_pending_gate",
        },
        "evidence_status": {
            "semantic_verdict": "ASSERTION_VIOLATION",
            "business_evidence_status": "PENDING_DELIVERY_GATE",
            "final_review_status": "PENDING_DELIVERY_GATE",
            "missing_requirements": ["independent_delivery_gate_receipt"],
        },
        "business_evidence_status": "PENDING_DELIVERY_GATE",
        "final_review_status": "PENDING_DELIVERY_GATE",
        "reproduction_steps": ["POST /api/x"],
    }
    finalized = finalize_finding_evidence_after_delivery_gate(
        finding,
        gate_receipt={
            "status": "DELIVERABLE",
            "gate_receipt_id": "gate-1",
        },
        reproduction_receipt={"receipt_id": "repro-1", "steps": [{"step_id": "t1"}]},
    )
    assert finalized["business_evidence_status"] == "VALIDATED"
    assert finalized["evidence_status"]["business_evidence_status"] == "VALIDATED"
    assert finalized["evidence_status"]["semantic_verdict"] == "SEMANTIC_CONFIRMED"
    assert "independent_delivery_gate_receipt" not in finalized["evidence_status"][
        "missing_requirements"
    ]
    assert finalized["evidence_quality"]["level"] == "validated"
    assert finalized["evidence_quality"]["score"] >= 90
    assert finalized["evidence_quality"]["can_reproduce"] is True


def test_finalize_evidence_pack_ignores_non_deliverable() -> None:
    finding = {
        "evidence_status": {
            "business_evidence_status": "PENDING_DELIVERY_GATE",
            "missing_requirements": ["independent_delivery_gate_receipt"],
        },
        "business_evidence_status": "PENDING_DELIVERY_GATE",
    }
    finalized = finalize_finding_evidence_after_delivery_gate(
        finding,
        gate_receipt={"status": "REJECTED", "gate_receipt_id": "gate-2"},
    )
    assert finalized["business_evidence_status"] == "PENDING_DELIVERY_GATE"
    assert "independent_delivery_gate_receipt" in finalized["evidence_status"][
        "missing_requirements"
    ]
