from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center import private_pilot_service as service
from ai_test_asset_center import private_pilot_tenant_auth as tenant_auth


def test_invalid_bearer_token_does_not_fall_back_to_default_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.jwt_auth, "verify_token", lambda token: None)
    monkeypatch.setattr(service, "_current_tenant", lambda: "default-tenant")

    with pytest.raises(service.TenantAuthenticationError, match="bearer token"):
        service._tenant_from_headers({"Authorization": "Bearer invalid"})


def test_invalid_bearer_cannot_fall_through_to_a_valid_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.jwt_auth, "verify_token", lambda token: None)
    monkeypatch.setattr(service.db_persist, "verify_api_key", lambda root, key: "api-tenant")

    with pytest.raises(service.TenantAuthenticationError, match="bearer token"):
        service._tenant_from_headers(
            {"Authorization": "Bearer invalid", "X-API-Key": "valid-key"},
            root=Path("."),
        )


def test_unsupported_authorization_scheme_is_rejected() -> None:
    with pytest.raises(service.TenantAuthenticationError, match="bearer token"):
        service._tenant_from_headers({"Authorization": "Basic dXNlcjpwYXNz"})


def test_bearer_without_tenant_subject_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(service.jwt_auth, "verify_token", lambda token: {"role": "admin"})

    with pytest.raises(
        service.TenantAuthenticationError, match="bearer account no longer exists"
    ):
        service._tenant_from_headers({"Authorization": "Bearer subjectless"}, root=tmp_path)


def test_invalid_tenant_cookie_is_rejected_instead_of_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.jwt_auth, "verify_token", lambda token: None)

    with pytest.raises(service.TenantAuthenticationError, match="cookie token"):
        service._tenant_from_headers({"Cookie": "qualibug_token=invalid"})


def test_api_key_verification_failure_is_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        service.db_persist,
        "authenticate_tenant",
        lambda root, key, password="": (_ for _ in ()).throw(
            RuntimeError("tenant database unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="tenant database unavailable"):
        service._tenant_from_headers({"X-API-Key": "key"}, root=tmp_path)


def test_empty_explicit_api_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(service.TenantAuthenticationError, match="API key"):
        service._tenant_from_headers({"X-API-Key": ""}, root=tmp_path)


def test_no_explicit_tenant_credential_keeps_local_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Patch where the resolver is defined: _tenant_from_headers calls its own
    # module-level _current_tenant, so rebinding the re-export on the facade
    # would leave the delegation under test untouched. No explicit credential
    # now fail-closes to the local development principal, which is only issued
    # when local development actor mode is enabled.
    monkeypatch.delenv("QUALIBUG_ALLOW_PUBLIC_BIND", raising=False)
    monkeypatch.setenv("QUALIBUG_LOCAL_DEV_ACTOR", "1")
    monkeypatch.setattr(tenant_auth, "_current_tenant", lambda: "local-default")

    assert service._tenant_from_headers({}) == "local-default"


def test_handler_returns_401_for_invalid_explicit_tenant_credential(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(service.jwt_auth, "verify_token", lambda token: None)

    class Handler(service.AuthScopeMixin):
        headers = {"Authorization": "Bearer invalid"}
        server = None

        def __init__(self) -> None:
            self.status: int | None = None
            self.payload: dict | None = None

        def _json(self, payload: dict, status: int = 200) -> None:
            self.status = status
            self.payload = payload

    handler = Handler()

    tenant = service.PrivatePilotHandler._require_tenant(handler, tmp_path)

    assert tenant is None
    assert handler.status == 401
    assert handler.payload is not None
    assert handler.payload["error"] == "INVALID_TENANT_CREDENTIAL"
