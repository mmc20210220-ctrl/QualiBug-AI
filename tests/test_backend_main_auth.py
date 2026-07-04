import pytest
from fastapi.testclient import TestClient

from backend import main
from core.engine import Auth


def test_protected_routes_fail_closed_when_token_is_not_configured(monkeypatch):
    monkeypatch.delenv("QUALIBUG_API_TOKEN", raising=False)
    client = TestClient(main.app)

    response = client.get("/run", params={"q": "checkout"})

    assert response.status_code == 503
    assert response.json()["detail"] == "api token is not configured"


def test_protected_routes_reject_missing_and_wrong_tokens(monkeypatch):
    monkeypatch.setenv("QUALIBUG_API_TOKEN", "configured-secret")
    client = TestClient(main.app)

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_configured_bearer_token_reaches_engine_without_guest_fallback(monkeypatch):
    monkeypatch.setenv("QUALIBUG_API_TOKEN", "configured-secret")
    monkeypatch.setenv("QUALIBUG_TENANT_ID", "tenant_enterprise")
    client = TestClient(main.app)

    response = client.get(
        "/run",
        params={"q": "checkout"},
        headers={"Authorization": "Bearer configured-secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tenant"] == "tenant_enterprise"
    assert payload["tenant"] != "tenant_guest"


def test_engine_auth_rejects_invalid_tokens_in_direct_use(monkeypatch):
    monkeypatch.setenv("QUALIBUG_API_TOKEN", "configured-secret")

    with pytest.raises(PermissionError):
        Auth().verify("wrong")
