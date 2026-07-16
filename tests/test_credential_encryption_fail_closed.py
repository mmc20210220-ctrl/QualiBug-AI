from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center import credential_crypto
from ai_test_asset_center import private_pilot_credentials_patch as credential_patch
from ai_test_asset_center import private_pilot_server
from ai_test_asset_center import private_pilot_service as service
from ai_test_asset_center.replay_engine import ReplayEngine


class _JsonCaptureHandler:
    def __init__(self) -> None:
        self.status_code: int | None = None
        self.payload: dict | None = None

    def _json(self, payload: dict, status: int = 200) -> None:
        self.status_code = status
        self.payload = payload


def test_encrypted_credential_without_master_key_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(credential_crypto._MASTER_KEY_ENV, "original-key")
    encrypted = credential_crypto.encrypt("secret")
    monkeypatch.delenv(credential_crypto._MASTER_KEY_ENV)

    with pytest.raises(credential_crypto.CredentialDecryptionError, match="master key"):
        credential_crypto.decrypt(encrypted)


def test_tampered_encrypted_credential_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(credential_crypto._MASTER_KEY_ENV, "stable-key")
    encrypted = credential_crypto.encrypt("secret")
    replacement = "A" if encrypted[-1] != "A" else "B"

    with pytest.raises(credential_crypto.CredentialDecryptionError, match="authentication"):
        credential_crypto.decrypt(encrypted[:-1] + replacement)


def test_replay_does_not_send_undecryptable_ciphertext_as_a_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(credential_crypto._MASTER_KEY_ENV, "original-key")
    encrypted = credential_crypto.encrypt("secret-token")
    config_path = tmp_path / "platform_workspace" / "demo" / "multi_service_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "name": "orders",
                        "auth": {"bearer_token": encrypted},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(credential_crypto._MASTER_KEY_ENV, "wrong-key")

    with pytest.raises(credential_crypto.CredentialDecryptionError, match="authentication"):
        ReplayEngine(tmp_path, "demo")._get_auth_header()


def test_replay_does_not_hide_a_corrupt_service_credential_config(tmp_path: Path) -> None:
    config_path = tmp_path / "platform_workspace" / "demo" / "multi_service_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid replay service config"):
        ReplayEngine(tmp_path, "demo")._get_auth_header()


def test_replay_connector_credential_decryption_failure_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(credential_crypto._MASTER_KEY_ENV, "original-key")
    encrypted = credential_crypto.encrypt("connector-token")
    connector_path = (
        tmp_path
        / "platform_workspace"
        / "demo"
        / "enterprise_pilot_runtime"
        / "connector_registry.json"
    )
    connector_path.parent.mkdir(parents=True)
    connector_path.write_text(
        json.dumps({"connectors": [{"enabled": True, "credential_ref": encrypted}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv(credential_crypto._MASTER_KEY_ENV, "wrong-key")

    with pytest.raises(credential_crypto.CredentialDecryptionError, match="authentication"):
        ReplayEngine(tmp_path, "demo")._get_auth_header()


def test_local_encryption_key_provisioning_failure_is_visible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(credential_patch.CREDENTIAL_KEY_ENV, raising=False)
    original_mkdir = Path.mkdir

    def fail_secrets_directory(path: Path, *args, **kwargs) -> None:
        if path.name == ".secrets":
            raise OSError("workspace is read-only")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_secrets_directory)

    with pytest.raises(
        credential_patch.CredentialEncryptionUnavailableError,
        match="workspace is read-only",
    ):
        credential_patch.ensure_local_credential_encryption_key(tmp_path)


def test_credential_save_is_blocked_when_encryption_key_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called = False

    def original_save(self, project: str, root: Path, body: dict) -> None:
        nonlocal called
        called = True

    def fail_key(root: Path) -> str:
        raise credential_patch.CredentialEncryptionUnavailableError("key store unavailable")

    credential_patch.restore_service_credentials_patch()
    monkeypatch.setattr(service.PrivatePilotHandler, "_handle_save_service_credentials", original_save)
    monkeypatch.setattr(credential_patch, "ensure_local_credential_encryption_key", fail_key)
    credential_patch.install_service_credentials_patch(patch_source="test")
    handler = _JsonCaptureHandler()
    try:
        service.PrivatePilotHandler._handle_save_service_credentials(
            handler,
            "demo",
            tmp_path,
            {"service": {"name": "orders", "admin_pass": "secret"}},
        )
    finally:
        credential_patch.restore_service_credentials_patch()

    assert called is False
    assert handler.status_code == 503
    assert handler.payload is not None
    assert handler.payload["error"] == "CREDENTIAL_ENCRYPTION_UNAVAILABLE"


def test_credential_get_does_not_claim_encryption_when_key_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_key(root: Path) -> str:
        raise credential_patch.CredentialEncryptionUnavailableError("key store unavailable")

    credential_patch.restore_service_credentials_patch()
    monkeypatch.setattr(credential_patch, "ensure_local_credential_encryption_key", fail_key)
    credential_patch.install_service_credentials_patch(patch_source="test")
    handler = _JsonCaptureHandler()
    try:
        service.PrivatePilotHandler._handle_get_service_credentials(handler, "demo", tmp_path)
    finally:
        credential_patch.restore_service_credentials_patch()

    assert handler.status_code == 503
    assert handler.payload is not None
    assert handler.payload["error"] == "CREDENTIAL_ENCRYPTION_UNAVAILABLE"


def test_private_pilot_server_uses_extracted_credential_safety_authority() -> None:
    private_pilot_server.restore_customer_delivery_gate_patch()
    credential_patch.restore_service_credentials_patch()
    try:
        private_pilot_server.install_customer_delivery_gate_patch()

        assert service._SERVICE_CREDENTIALS_PATCH_SOURCE == private_pilot_server.PATCH_SOURCE
    finally:
        private_pilot_server.restore_customer_delivery_gate_patch()
