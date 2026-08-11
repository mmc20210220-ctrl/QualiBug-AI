from __future__ import annotations

import json


def _install_local_key(tmp_path, monkeypatch):
    from ai_test_asset_center import credential_crypto

    key = "run27-test-key-material"
    monkeypatch.setenv("QUALIBUG_CRED_ENC_KEY", key)
    encrypted = credential_crypto.encrypt("real-secret-password")
    monkeypatch.delenv("QUALIBUG_CRED_ENC_KEY", raising=False)
    key_path = (
        tmp_path
        / "platform_workspace"
        / ".secrets"
        / "credential_encryption.key"
    )
    key_path.parent.mkdir(parents=True)
    key_path.write_text(key, encoding="utf-8")
    return encrypted


def test_declared_material_uses_existing_local_key_without_provisioning(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center.declared_credential_material import (
        resolve_declared_credential_material,
    )

    encrypted = _install_local_key(tmp_path, monkeypatch)
    value, receipt = resolve_declared_credential_material(
        encrypted,
        root=tmp_path,
    )

    assert value == "real-secret-password"
    assert receipt["status"] == "RESOLVED"
    assert receipt["encrypted_at_rest"] is True
    assert receipt["key_source"] == "existing_local_key_file"
    assert receipt["secret_value_persisted"] is False
    assert "real-secret-password" not in repr(receipt)


def test_encrypted_material_without_existing_key_fails_closed(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center import credential_crypto
    from ai_test_asset_center.declared_credential_material import (
        resolve_declared_credential_material,
    )

    monkeypatch.setenv("QUALIBUG_CRED_ENC_KEY", "other-build-key")
    encrypted = credential_crypto.encrypt("do-not-send-this")
    monkeypatch.delenv("QUALIBUG_CRED_ENC_KEY", raising=False)

    value, receipt = resolve_declared_credential_material(
        encrypted,
        root=tmp_path,
    )

    assert value == ""
    assert receipt["status"] == "UNRESOLVED"
    assert receipt["reason_code"] == "DECLARED_CREDENTIAL_KEY_UNAVAILABLE"
    assert receipt["secret_value_persisted"] is False


def test_request_credential_resolver_decrypts_test_account_before_transport(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center.request_credential_authority import (
        resolve_request_credentials,
    )

    encrypted = _install_local_key(tmp_path, monkeypatch)
    account_dir = tmp_path / "platform_inputs" / "demo"
    account_dir.mkdir(parents=True)
    (account_dir / "test_accounts.json").write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_ref": "buyer-1",
                        "email": "buyer@example.test",
                        "password": encrypted,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    body, receipt = resolve_request_credentials(
        {"password": "secret_ref:test_accounts:buyer-1"},
        root=tmp_path,
        project="demo",
    )

    assert body["password"] == "real-secret-password"
    assert receipt["status"] == "RESOLVED"
    assert receipt["rows"][0]["encrypted_at_rest"] is True
    assert "real-secret-password" not in repr(receipt)
    assert "enc$v1$" not in repr(body)


def test_actor_live_login_receives_decrypted_password(tmp_path, monkeypatch) -> None:
    import ai_test_asset_center.experiment_runtime_support as runtime

    encrypted = _install_local_key(tmp_path, monkeypatch)
    account_dir = tmp_path / "platform_inputs" / "demo"
    account_dir.mkdir(parents=True)
    (account_dir / "test_accounts.json").write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_ref": "buyer-1",
                        "email": "buyer@example.test",
                        "role": "buyer",
                        "status": "ACTIVE",
                        "password": encrypted,
                        "login_path": "/login",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    observed = {}

    def fake_login(*, base_url, login_path, email, password):
        observed["password"] = password
        return "live-token", 200

    monkeypatch.setattr(runtime, "_ORIGINAL_LOGIN_DECLARED_ACCOUNT", fake_login)
    tokens = runtime.load_actor_tokens(
        tmp_path,
        "demo",
        base_url="http://example.test",
    )

    assert observed["password"] == "real-secret-password"
    assert not observed["password"].startswith("enc$v1$")
    assert tokens["buyer-1"] == "live-token"
