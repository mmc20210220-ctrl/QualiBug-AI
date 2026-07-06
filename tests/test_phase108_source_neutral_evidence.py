from ai_test_asset_center.evidence_normalizer import normalize_finding_evidence


def test_receipt_driven_binding_never_invents_admin_or_single_tenant():
    calls = [{
        "call": "GET /api/cases/case-42",
        "results": {
            "clinician": {
                "status": 200,
                "body": {"caseId": "case-42", "workspaceId": "ward-a"},
            },
        },
    }]
    result = normalize_finding_evidence({"hypothesis_id": "h1", "verdict": "inconclusive"}, calls)
    runtime = result["runtime"]

    assert runtime["actor_id"]["value"] == "clinician"
    assert runtime["tenant_id"]["value"] == "ward-a"
    assert runtime["entity_id"]["value"] == "case-42"
    assert "single-tenant" not in str(result)
    assert "admin" not in str(result)


def test_write_requires_observed_before_and_after_receipts():
    calls = [
        {"call": "GET /api/claims/claim-7", "results": {"reviewer": {"status": 200, "body": {"claim_id": "claim-7", "status": "OPEN"}}}},
        {"call": "PATCH /api/claims/claim-7", "results": {"reviewer": {"status": 200, "body": {"claim_id": "claim-7", "status": "APPROVED"}}}},
        {"call": "GET /api/claims/claim-7", "results": {"reviewer": {"status": 200, "body": {"claim_id": "claim-7", "status": "APPROVED"}}}},
    ]
    result = normalize_finding_evidence({"hypothesis_id": "h2", "verdict": "confirmed"}, calls)
    runtime = result["runtime"]

    assert runtime["action_ref"]["value"].startswith("PATCH")
    assert runtime["before_candidates"][0]["observer"] == "reviewer"
    assert runtime["after_candidates"][0]["body_snapshot"]["status"] == "APPROVED"
    assert runtime["missing_requirements"] == []


def test_missing_scope_stays_missing_instead_of_a_default_value():
    result = normalize_finding_evidence(
        {"verdict": "confirmed"},
        [{"call": "POST /api/records", "results": {"service_account": {"status": 201, "body": {"id": "record-1"}}}}],
    )
    runtime = result["runtime"]

    assert runtime["tenant_id"]["confidence"] == "missing"
    assert runtime["actor_id"]["value"] == "service_account"
    assert "BEFORE_SNAPSHOT_MISSING" in runtime["missing_requirements"]
