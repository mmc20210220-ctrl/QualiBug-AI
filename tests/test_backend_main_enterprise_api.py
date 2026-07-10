from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend import main


API_SPEC = '{"openapi":"3.0.0","paths":{"/api/cases/{case_id}/approve":{"patch":{"operationId":"approveCase"}}}}'


def _headers(token: str = "configured-secret") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_enterprise_api_registers_source_plans_scan_and_verifies_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_API_TOKEN", "configured-secret")
    monkeypatch.setenv("QUALIBUG_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("QUALIBUG_ACCESS_POLICY_JSON", raising=False)
    monkeypatch.delenv("QUALIBUG_ALLOWED_TARGET_ORIGINS", raising=False)
    client = TestClient(main.app)

    source_response = client.post(
        "/v1/source-assets/register",
        headers=_headers(),
        json={
            "project_id": "enterprise-project",
            "source_id": "api-contract",
            "source_type": "openapi",
            "content": API_SPEC,
            "actor": {"name": "qa", "role": "qa_lead"},
        },
    )
    assert source_response.status_code == 200
    manifest = source_response.json()

    listing = client.get("/v1/source-assets/enterprise-project", headers=_headers())
    assert listing.status_code == 200
    assert listing.json()["assets"][0]["source_id"] == "api-contract"

    scan_response = client.post(
        "/v1/scans",
        headers=_headers(),
        json={
            "project_id": "enterprise-project",
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
        },
    )
    assert scan_response.status_code == 200
    scan = scan_response.json()
    assert scan["grade"] == "inconclusive"
    assert scan["runtime_contract"]["source_manifest"]["source_id"] == "api-contract"
    assert scan["evidence_bundle"]["status"] == "persisted"
    assert scan["release_gate"]["verdict"] == "not_ready"

    evidence = client.get(
        f"/v1/evidence-bundles/enterprise-project/{scan['evidence_bundle']['bundle_id']}/verify",
        headers=_headers(),
    )
    assert evidence.status_code == 200
    assert evidence.json()["valid"] is True


def test_enterprise_api_enforces_allowlist_and_campaign_approval_workflow(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_API_TOKEN", "configured-secret")
    monkeypatch.setenv("QUALIBUG_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("QUALIBUG_ACCESS_POLICY_JSON", raising=False)
    client = TestClient(main.app)

    manifest = client.post(
        "/v1/source-assets/register",
        headers=_headers(),
        json={"project_id": "enterprise-project", "source_id": "api-contract", "source_type": "openapi", "content": API_SPEC},
    ).json()
    plan = client.post(
        "/v1/scans",
        headers=_headers(),
        json={
            "project_id": "enterprise-project",
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
        },
    ).json()

    blocked_by_allowlist = client.post(
        "/v1/scans",
        headers=_headers(),
        json={
            "project_id": "enterprise-project",
            "base_url": "https://example.invalid",
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
        },
    )
    assert blocked_by_allowlist.status_code == 403

    monkeypatch.setenv("QUALIBUG_ALLOWED_TARGET_ORIGINS", "https://example.invalid")
    approval = client.post(
        "/v1/execution-approvals",
        headers=_headers(),
        json={
            "project_id": "enterprise-project",
            "campaign_id": plan["campaign"]["campaign_id"],
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_hash": manifest["source_hash"],
            "target_base_url": "https://example.invalid/path",
            "execution_mode": "approved_sandbox_write",
            "expires_at_utc": _expiry(),
            "actor": {"name": "release-manager", "role": "approver"},
        },
    )
    assert approval.status_code == 200
    assert approval.json()["approval_id"].startswith("eap_")

    source_bound_scan = client.post(
        "/v1/scans",
        headers=_headers(),
        json={
            "project_id": "enterprise-project",
            "base_url": "https://example.invalid/path",
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": manifest,
        },
    )
    assert source_bound_scan.status_code == 200
    body = source_bound_scan.json()
    assert body["grade"] == "blocked"
    assert body["runtime_contract"]["status"] == "approved"
    assert body["runtime_contract"]["execution_approval"]["execution_mode"] == "approved_sandbox_write"
    assert body["auto_har"]["status"] == "no_traffic"


def test_enterprise_api_issues_metadata_only_test_data_receipts(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_API_TOKEN", "configured-secret")
    monkeypatch.setenv("QUALIBUG_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("QUALIBUG_ACCESS_POLICY_JSON", raising=False)
    client = TestClient(main.app)

    response = client.post(
        "/v1/test-data-receipts",
        headers=_headers(),
        json={
            "project_id": "enterprise-project",
            "kind": "creation",
            "campaign_id": "CMP_1",
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "data_scope_ref": "isolated-sandbox",
            "actor": {"name": "qa", "role": "qa_lead"},
        },
    )

    assert response.status_code == 200
    receipt = response.json()
    assert receipt["receipt_id"].startswith("tdr_")
    assert receipt["data_scope_ref"] == "isolated-sandbox"
    assert "content" not in receipt


def test_enterprise_api_access_policy_enforces_permission_and_project_scope(monkeypatch, tmp_path):
    policy = {
        "planner-token": {
            "principal_id": "planner-a",
            "tenant_id": "tenant-a",
            "permissions": ["identity.read", "source.read", "campaign.plan"],
            "project_ids": ["project-a"],
        },
        "operator-token": {
            "principal_id": "operator-a",
            "tenant_id": "tenant-a",
            "permissions": ["identity.read", "source.register", "source.read", "campaign.plan", "evidence.verify"],
            "project_ids": ["project-a"],
        },
    }
    monkeypatch.delenv("QUALIBUG_API_TOKEN", raising=False)
    monkeypatch.setenv("QUALIBUG_ACCESS_POLICY_JSON", json.dumps(policy))
    monkeypatch.setenv("QUALIBUG_WORKSPACE_ROOT", str(tmp_path))
    client = TestClient(main.app)

    me = client.get("/v1/access/me", headers=_headers("planner-token"))
    assert me.status_code == 200
    assert me.json()["principal_id"] == "planner-a"

    denied_register = client.post(
        "/v1/source-assets/register",
        headers=_headers("planner-token"),
        json={"project_id": "project-a", "source_id": "api-contract", "source_type": "openapi", "content": API_SPEC},
    )
    assert denied_register.status_code == 403

    cross_project = client.get("/v1/source-assets/project-b", headers=_headers("operator-token"))
    assert cross_project.status_code == 403

    allowed_register = client.post(
        "/v1/source-assets/register",
        headers=_headers("operator-token"),
        json={"project_id": "project-a", "source_id": "api-contract", "source_type": "openapi", "content": API_SPEC},
    )
    assert allowed_register.status_code == 200
    assert allowed_register.json()["source_id"] == "api-contract"
