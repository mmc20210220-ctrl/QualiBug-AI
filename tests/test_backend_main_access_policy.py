from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend import main


API_SPEC = '{"openapi":"3.0.0","paths":{"/records":{"get":{"operationId":"listRecords"}}}}'


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _policy() -> str:
    return json.dumps({
        "scoped-token": {
            "principal_id": "principal-a",
            "tenant_id": "tenant-a",
            "permissions": ["identity.read", "source.register", "source.read", "campaign.plan"],
            "project_ids": ["project-a"],
        },
        "register-only-token": {
            "principal_id": "principal-b",
            "tenant_id": "tenant-b",
            "permissions": ["source.register"],
            "project_ids": ["project-a"],
        },
    })


def test_policy_scopes_official_api_to_project_and_derives_actor_from_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_ACCESS_POLICY_JSON", _policy())
    monkeypatch.delenv("QUALIBUG_API_TOKEN", raising=False)
    monkeypatch.setenv("QUALIBUG_WORKSPACE_ROOT", str(tmp_path))
    client = TestClient(main.app)

    me = client.get("/v1/access/me", headers=_headers("scoped-token"))
    assert me.status_code == 200
    assert me.json()["principal_id"] == "principal-a"
    assert me.json()["project_ids"] == ["project-a"]

    registered = client.post(
        "/v1/source-assets/register",
        headers=_headers("scoped-token"),
        json={
            "project_id": "project-a",
            "source_id": "api-contract",
            "source_type": "openapi",
            "content": API_SPEC,
            "actor": {"name": "forged-client-actor", "role": "forged-client-role"},
        },
    )
    assert registered.status_code == 200
    registry = json.loads((tmp_path / "platform_workspace" / "project-a" / "source_registry" / "registry.json").read_text(encoding="utf-8"))
    version = registry["assets"]["api-contract"]["versions"][0]
    assert version["registered_by"]["name"] == "principal-a"
    assert version["registered_by"]["name"] != "forged-client-actor"

    cross_project = client.get("/v1/source-assets/project-b", headers=_headers("scoped-token"))
    assert cross_project.status_code == 403

    planned = client.post(
        "/v1/scans",
        headers=_headers("scoped-token"),
        json={
            "project_id": "project-a",
            "scope_id": "read-records",
            "environment_ref": "approved-test",
            "source_manifest": registered.json(),
        },
    )
    assert planned.status_code == 200
    assert planned.json()["runtime_contract"]["source_manifest"]["source_id"] == "api-contract"

    blocked_plan = client.post(
        "/v1/scans",
        headers=_headers("scoped-token"),
        json={
            "project_id": "project-b",
            "scope_id": "read-records",
            "environment_ref": "approved-test",
            "source_manifest": registered.json(),
        },
    )
    assert blocked_plan.status_code == 403


def test_policy_permission_denial_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_ACCESS_POLICY_JSON", _policy())
    monkeypatch.delenv("QUALIBUG_API_TOKEN", raising=False)
    monkeypatch.setenv("QUALIBUG_WORKSPACE_ROOT", str(tmp_path))
    client = TestClient(main.app)

    registered = client.post(
        "/v1/source-assets/register",
        headers=_headers("register-only-token"),
        json={"project_id": "project-a", "source_id": "api-contract", "source_type": "openapi", "content": API_SPEC},
    )
    assert registered.status_code == 200

    denied = client.post(
        "/v1/scans",
        headers=_headers("register-only-token"),
        json={
            "project_id": "project-a",
            "scope_id": "read-records",
            "environment_ref": "approved-test",
            "source_manifest": registered.json(),
        },
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "permission denied"


def test_invalid_access_policy_is_a_service_configuration_error(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_ACCESS_POLICY_JSON", "not-json")
    monkeypatch.delenv("QUALIBUG_API_TOKEN", raising=False)
    monkeypatch.setenv("QUALIBUG_WORKSPACE_ROOT", str(tmp_path))
    client = TestClient(main.app)

    response = client.get("/v1/source-assets/project-a", headers=_headers("anything"))

    assert response.status_code == 503
    assert "access policy is invalid" in response.json()["detail"]
