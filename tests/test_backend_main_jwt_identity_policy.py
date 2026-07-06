from __future__ import annotations

import importlib
import json

from fastapi.testclient import TestClient

from backend import main


API_SPEC = '{"openapi":"3.0.0","paths":{"/records":{"get":{"operationId":"listRecords"}}}}'


def _jwt_for(monkeypatch, subject: str, role: str) -> str:
    monkeypatch.setenv("QUALIBUG_JWT_SECRET", "jwt-policy-test-secret")
    module = importlib.import_module("ai_test_asset_center.jwt_auth")
    module = importlib.reload(module)
    return module.create_token(subject, role=role)


def _policy() -> str:
    return json.dumps({
        "browser-subject": {
            "principal_id": "policy-principal",
            "tenant_id": "tenant-a",
            "permissions": ["identity.read", "source.register", "source.read", "campaign.plan"],
            "project_ids": ["project-a"],
        },
    })


def test_verified_jwt_subject_is_mapped_to_deployment_policy_not_jwt_role(monkeypatch, tmp_path):
    monkeypatch.delenv("QUALIBUG_API_TOKEN", raising=False)
    monkeypatch.delenv("QUALIBUG_ACCESS_POLICY_JSON", raising=False)
    monkeypatch.setenv("QUALIBUG_JWT_ACCESS_POLICY_JSON", _policy())
    monkeypatch.setenv("QUALIBUG_WORKSPACE_ROOT", str(tmp_path))
    token = _jwt_for(monkeypatch, "browser-subject", "untrusted-claim")
    client = TestClient(main.app)
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/v1/access/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["principal_id"] == "policy-principal"
    assert me.json()["permissions"] == ["campaign.plan", "identity.read", "source.read", "source.register"]

    registered = client.post(
        "/v1/source-assets/register",
        headers=headers,
        json={"project_id": "project-a", "source_id": "api-contract", "source_type": "openapi", "content": API_SPEC},
    )
    assert registered.status_code == 200

    planned = client.post(
        "/v1/scans",
        headers=headers,
        json={
            "project_id": "project-a",
            "scope_id": "read-records",
            "environment_ref": "approved-test",
            "source_manifest": registered.json(),
        },
    )
    assert planned.status_code == 200

    denied_execution = client.post(
        "/v1/scans",
        headers=headers,
        json={
            "project_id": "project-a",
            "base_url": "https://example.invalid",
            "scope_id": "read-records",
            "environment_ref": "approved-test",
            "source_manifest": registered.json(),
        },
    )
    assert denied_execution.status_code == 403
    assert denied_execution.json()["detail"] == "permission denied"

    cross_project = client.get("/v1/source-assets/project-b", headers=headers)
    assert cross_project.status_code == 403
