from ai_test_asset_center.discovery_finding_gate import gate_discovery_findings


def _complete_write_finding():
    return {
        "hypothesis_id": "claim-transition",
        "title": "Closed claim accepts approval",
        "severity": "P1",
        "verdict": "confirmed",
        "expected": "CLOSED claims must reject approval.",
        "actual": "PATCH returned 200 and state became APPROVED.",
        "source_refs": ["business_rules.md#line-10"],
        "evidence": {
            "cleanup": {"status": "verified", "receipt_ref": "cleanup-42"},
            "verifier_rule": "forbidden_transition_blocked",
            "calls": [
                {"call": "GET /api/claims/claim-42", "results": {"reviewer": {"status": 200, "body": {"claimId": "claim-42", "status": "CLOSED"}}}},
                {"call": "PATCH /api/claims/claim-42", "results": {"reviewer": {"status": 200, "body": {"claimId": "claim-42", "status": "APPROVED"}}}},
                {"call": "GET /api/claims/claim-42", "results": {"reviewer": {"status": 200, "body": {"claimId": "claim-42", "status": "APPROVED"}}}},
            ],
        },
    }


def test_gate_uses_real_receipts_without_admin_or_single_tenant_defaults():
    contracts, summary = gate_discovery_findings([_complete_write_finding()], project_id="generic_case_project")

    assert summary["validated_candidate_count"] == 1
    contract = contracts[0]
    assert contract["verdict"] == "VALIDATED_CANDIDATE"
    assert contract["entity_binding"]["entity_id"] == "claim-42"
    assert "admin" not in str(contract).lower()
    assert "single-tenant" not in str(contract).lower()


def test_gate_keeps_missing_runtime_evidence_out_of_customer_candidates():
    finding = _complete_write_finding()
    finding["evidence"] = {"verifier_rule": "forbidden_transition_blocked"}

    contracts, summary = gate_discovery_findings([finding], project_id="generic_case_project")

    assert summary["blocked_runtime_count"] == 1
    assert contracts[0]["verdict"] == "NEEDS_MORE_EVIDENCE"
    assert contracts[0]["business_evidence_status"] == "BLOCKED_BY_RUNTIME_EVIDENCE"


def test_gate_requires_after_snapshot_when_write_is_not_the_first_receipt():
    finding = _complete_write_finding()
    finding["evidence"]["calls"] = finding["evidence"]["calls"][:2]

    contracts, summary = gate_discovery_findings([finding], project_id="generic_case_project")

    assert summary["validated_candidate_count"] == 0
    assert contracts[0]["verdict"] == "NEEDS_MORE_EVIDENCE"
    assert "AFTER_SNAPSHOT_MISSING" in contracts[0]["business_gate_missing"]
