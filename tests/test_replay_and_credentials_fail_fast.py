from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center import private_pilot_service as service
from ai_test_asset_center import private_pilot_credentials_patch as credential_patch


class _JsonCaptureHandler:
    def __init__(self) -> None:
        self.status_code: int | None = None
        self.payload: dict | None = None

    def _json(self, body, status: int = 200) -> None:
        self.status_code = status
        self.payload = body

    def _build_command_center(self, project: str, root: Path) -> dict:
        return {
            "data": {
                "risks": [
                    {"id": "finding-1", "finding_persistence_id": "finding-1"}
                ]
            }
        }

    def _request_tenant(self) -> str:
        return "tenant-demo"


class _ReplayEngineResult:
    result: dict = {
        "ok": True,
        "success": False,
        "verdict": "not_reproduced",
        "evidence": {"status": 404},
    }

    def __init__(self, root: Path, project: str) -> None:
        self.root = root
        self.project = project

    def replay(self, finding_id: str, risks: list[dict], base_url: str) -> dict:
        return dict(self.result)


def test_replay_status_persistence_failure_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("ai_test_asset_center.replay_engine.ReplayEngine", _ReplayEngineResult)
    monkeypatch.setattr(
        service.db_persist,
        "update_finding_status",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("finding database locked")
        ),
    )
    handler = _JsonCaptureHandler()

    service.PrivatePilotHandler._handle_replay(
        handler,
        "demo",
        tmp_path,
        {"finding_id": "finding-1"},
    )

    assert handler.status_code == 500
    assert handler.payload is not None
    assert handler.payload["ok"] is False
    assert handler.payload["error"] == "REPLAY_STATUS_PERSIST_FAILED"
    assert handler.payload["finding_id"] == "finding-1"
    assert handler.payload["replay_result"]["success"] is False
    receipt = json.loads(
        (tmp_path / "platform_outputs" / "demo" / "replay_last_error.json").read_text(encoding="utf-8")
    )
    assert receipt["phase"] == "status_persistence"
    assert receipt["target_status"] == "resolved"
    assert receipt["error"] == "finding database locked"


def test_replay_persists_status_before_claiming_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ReplayEngineResult.result = {
        "ok": True,
        "success": True,
        "verdict": "reproduced",
    }
    persisted: list[tuple[str, str]] = []
    monkeypatch.setattr("ai_test_asset_center.replay_engine.ReplayEngine", _ReplayEngineResult)

    def persist_status(
        root: Path,
        finding_id: str,
        status: str,
        **kwargs: object,
    ) -> bool:
        persisted.append((finding_id, status))
        return True

    monkeypatch.setattr(
        service.db_persist,
        "update_finding_status",
        persist_status,
    )
    handler = _JsonCaptureHandler()

    service.PrivatePilotHandler._handle_replay(
        handler,
        "demo",
        tmp_path,
        {"finding_id": "finding-1"},
    )

    assert handler.status_code == 200
    assert handler.payload is not None
    assert handler.payload["ok"] is True
    assert handler.payload["finding_status"] == "open"
    assert persisted == [("finding-1", "open")]


def test_replay_rejects_a_false_status_update_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ReplayEngineResult.result = {
        "ok": True,
        "success": False,
        "verdict": "not_reproduced",
    }
    monkeypatch.setattr("ai_test_asset_center.replay_engine.ReplayEngine", _ReplayEngineResult)
    monkeypatch.setattr(
        service.db_persist,
        "update_finding_status",
        lambda *args, **kwargs: False,
    )
    handler = _JsonCaptureHandler()

    service.PrivatePilotHandler._handle_replay(
        handler,
        "demo",
        tmp_path,
        {"finding_id": "finding-1"},
    )

    assert handler.status_code == 500
    assert handler.payload is not None
    assert handler.payload["error"] == "REPLAY_STATUS_PERSIST_FAILED"
    assert "did not update" in handler.payload["message"]


def _patch_credential_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ai_test_asset_center.credential_crypto.encrypt", lambda value: f"enc:{value}")
    monkeypatch.setattr("ai_test_asset_center.credential_crypto.is_encrypted", lambda value: str(value).startswith("enc:"))


def _credential_body() -> dict:
    return {
        "service": {
            "name": "orders",
            "base_url": "http://orders.test",
            "admin_user": "qa-admin",
            "admin_pass": "secret",
        }
    }


def test_credential_verification_exception_returns_saved_but_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_credential_crypto(monkeypatch)

    class FailingManager:
        def __init__(self, project: str, root: Path) -> None:
            pass

        def load_from_file(self, path: Path) -> None:
            pass

        def load_from_env(self) -> None:
            pass

        def login_all_services(self):
            raise RuntimeError("authentication endpoint unavailable")

    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_credential_manager.EnterpriseCredentialManager",
        FailingManager,
    )
    handler = _JsonCaptureHandler()

    service.PrivatePilotHandler._handle_save_service_credentials(
        handler,
        "demo",
        tmp_path,
        _credential_body(),
    )

    assert handler.status_code == 207
    assert handler.payload is not None
    assert handler.payload["ok"] is False
    assert handler.payload["saved"] is True
    assert handler.payload["error"] == "CREDENTIAL_VERIFICATION_FAILED"
    assert handler.payload["auth_check"]["all_ok"] is False
    config = json.loads(
        (tmp_path / "platform_workspace" / "demo" / "multi_service_config.json").read_text(encoding="utf-8")
    )
    assert config["services"][0]["auth_check"]["all_ok"] is False
    assert config["services"][0]["auth"]["admin"]["password"] == "enc:secret"


def test_credential_negative_health_check_is_not_top_level_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_credential_crypto(monkeypatch)

    class NegativeManager:
        def __init__(self, project: str, root: Path) -> None:
            pass

        def load_from_file(self, path: Path) -> None:
            pass

        def load_from_env(self) -> None:
            pass

        def login_all_services(self):
            return {"orders": {"admin": False}}

    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_credential_manager.EnterpriseCredentialManager",
        NegativeManager,
    )
    handler = _JsonCaptureHandler()

    service.PrivatePilotHandler._handle_save_service_credentials(
        handler,
        "demo",
        tmp_path,
        _credential_body(),
    )

    assert handler.status_code == 207
    assert handler.payload is not None
    assert handler.payload["ok"] is False
    assert handler.payload["saved"] is True
    assert handler.payload["auth_check"] == {
        "service": "orders",
        "roles": {"admin": False},
        "all_ok": False,
        "status": "failed",
    }


def test_corrupt_credential_config_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_credential_crypto(monkeypatch)
    config_path = tmp_path / "platform_workspace" / "demo" / "multi_service_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{", encoding="utf-8")
    handler = _JsonCaptureHandler()

    service.PrivatePilotHandler._handle_save_service_credentials(
        handler,
        "demo",
        tmp_path,
        _credential_body(),
    )

    assert handler.status_code == 500
    assert handler.payload is not None
    assert handler.payload["ok"] is False
    assert handler.payload["error"] == "CREDENTIAL_CONFIG_INVALID"
    assert config_path.read_text(encoding="utf-8") == "{"


def test_corrupt_credential_config_is_not_presented_as_empty(tmp_path: Path) -> None:
    config_path = tmp_path / "platform_workspace" / "demo" / "multi_service_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{", encoding="utf-8")
    handler = _JsonCaptureHandler()

    service.PrivatePilotHandler._handle_get_service_credentials(handler, "demo", tmp_path)

    assert handler.status_code == 500
    assert handler.payload is not None
    assert handler.payload["ok"] is False
    assert handler.payload["error"] == "CREDENTIAL_CONFIG_INVALID"


def test_verified_credentials_persist_the_health_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_credential_crypto(monkeypatch)

    class VerifiedManager:
        def __init__(self, project: str, root: Path) -> None:
            pass

        def load_from_file(self, path: Path) -> None:
            pass

        def load_from_env(self) -> None:
            pass

        def login_all_services(self):
            return {"orders": {"admin": True}}

    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_credential_manager.EnterpriseCredentialManager",
        VerifiedManager,
    )
    handler = _JsonCaptureHandler()

    service.PrivatePilotHandler._handle_save_service_credentials(
        handler,
        "demo",
        tmp_path,
        _credential_body(),
    )

    assert handler.status_code == 200
    assert handler.payload is not None
    assert handler.payload["ok"] is True
    assert handler.payload["saved"] is True
    assert handler.payload["auth_check"]["status"] == "verified"
    config = json.loads(
        (tmp_path / "platform_workspace" / "demo" / "multi_service_config.json").read_text(encoding="utf-8")
    )
    assert config["services"][0]["auth_check"]["all_ok"] is True


def test_masked_role_password_preserves_the_existing_encrypted_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_credential_crypto(monkeypatch)
    config_path = tmp_path / "platform_workspace" / "demo" / "multi_service_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "name": "orders",
                        "base_url": "http://orders.test",
                        "enabled": True,
                        "auth": {
                            "type": "password_login",
                            "login_api": "/auth/login",
                            "admin": {"username": "qa-admin", "password": "enc:old-secret"},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class VerifiedManager:
        def __init__(self, project: str, root: Path) -> None:
            pass

        def load_from_file(self, path: Path) -> None:
            pass

        def load_from_env(self) -> None:
            pass

        def login_all_services(self):
            return {"orders": {"admin": True}}

    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_credential_manager.EnterpriseCredentialManager",
        VerifiedManager,
    )
    handler = _JsonCaptureHandler()
    body = _credential_body()
    body["previous_name"] = "orders"
    body["service"].pop("admin_user")
    body["service"].pop("admin_pass")
    body["service"]["role_accounts"] = [
        {"role": "admin", "username": "qa-admin", "password": service.MASKED_CREDENTIAL_VALUE}
    ]

    service.PrivatePilotHandler._handle_save_service_credentials(
        handler,
        "demo",
        tmp_path,
        body,
    )

    assert handler.status_code == 200
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["services"][0]["auth"]["admin"]["password"] == "enc:old-secret"


def test_runtime_credential_patch_does_not_hide_a_corrupt_config(tmp_path: Path) -> None:
    config_path = tmp_path / "platform_workspace" / "demo" / "multi_service_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{", encoding="utf-8")
    handler = _JsonCaptureHandler()

    credential_patch.restore_service_credentials_patch()
    try:
        credential_patch.install_service_credentials_patch(patch_source="test")
        service.PrivatePilotHandler._handle_get_service_credentials(handler, "demo", tmp_path)
    finally:
        credential_patch.restore_service_credentials_patch()

    assert handler.status_code == 500
    assert handler.payload is not None
    assert handler.payload["ok"] is False
    assert handler.payload["error"] == "CREDENTIAL_CONFIG_INVALID"


def test_static_token_configuration_is_not_mislabeled_as_live_verified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_credential_crypto(monkeypatch)

    class TokenSkippingManager:
        def __init__(self, project: str, root: Path) -> None:
            pass

        def load_from_file(self, path: Path) -> None:
            pass

        def load_from_env(self) -> None:
            pass

        def login_all_services(self):
            return {"orders": {"admin": True}}

    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_credential_manager.EnterpriseCredentialManager",
        TokenSkippingManager,
    )
    body = _credential_body()
    body["service"].pop("admin_user")
    body["service"].pop("admin_pass")
    body["service"]["bearer_token"] = "configured-token"
    handler = _JsonCaptureHandler()

    service.PrivatePilotHandler._handle_save_service_credentials(
        handler,
        "demo",
        tmp_path,
        body,
    )

    assert handler.status_code == 207
    assert handler.payload is not None
    assert handler.payload["ok"] is False
    assert handler.payload["auth_check"]["all_ok"] is False
    assert handler.payload["auth_check"]["status"] == "configured_unverified"
