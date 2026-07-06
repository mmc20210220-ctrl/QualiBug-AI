from ai_test_asset_center.runtime_customer_report_builder import build_customer_delivery_index


def _ready_finding():
    return {
        "finding_id": "F-1",
        "priority": "P1",
        "severity": "high",
        "risk_type": "contract_violation",
        "method": "PATCH",
        "path": "/api/claims/claim-7",
        "source_refs": ["prd:claims:approval-rule"],
        "expected": "A closed claim must reject approval.",
        "actual": "The approval request succeeded.",
        "reproduction": {"flow_id": "run-1", "required_inputs": ["claim-7"]},
        "semantic_verdict": "SEMANTIC_CONFIRMED",
        "final_review_status": "PENDING_REVIEW",
        "evidence": {
            "calls": [{"call": "GET /api/claims/claim-7"}, {"call": "PATCH /api/claims/claim-7"}],
            "before_snapshot_ref": "snap:before",
            "after_snapshot_ref": "snap:after",
            "cleanup": {"status": "verified", "receipt_ref": "cleanup-1"},
        },
    }


def test_only_proof_complete_findings_count_as_customer_ready():
    pending = _ready_finding()
    pending["finding_id"] = "F-2"
    pending["evidence"] = {"calls": [{"call": "PATCH /api/claims/claim-8"}]}

    report = build_customer_delivery_index([_ready_finding(), pending])

    assert report["input_finding_count"] == 2
    assert report["customer_ready_finding_count"] == 1
    assert report["validated_finding_count"] == 1
    assert report["internal_validation_lead_count"] == 1
    assert report["customer_ready_findings"][0]["finding_id"] == "F-1"


def test_proof_gaps_are_returned_to_the_existing_top_actions_exit():
    finding = _ready_finding()
    finding["evidence"] = {"calls": [{"call": "PATCH /api/claims/claim-7"}]}

    card = build_customer_delivery_index([finding])["top_customer_actions"][0]

    assert card["proof_status"] == "needs_more_evidence"
    assert "BEFORE_SNAPSHOT_MISSING" in card["evidence_gaps"]
    assert "AFTER_SNAPSHOT_MISSING" in card["evidence_gaps"]
    assert "CLEANUP_RECEIPT_MISSING" in card["evidence_gaps"]
