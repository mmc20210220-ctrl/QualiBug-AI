from __future__ import annotations

import json

import pytest

from ai_test_asset_center.connector_connection_profiles import (
    ConnectorProfileError,
    MASKED_SECRET,
    commit_connector_sync_checkpoint,
    configure_feishu_connector,
    connector_profile_ref,
    list_connector_connection_profiles,
    load_connector_sync_checkpoint,
    resolve_connector_connection_profile,
)

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
ACTOR = {"name": "qa-owner", "role": "qa_lead"}


@pytest.fixture(autouse=True)
def isolated_credential_key(monkeypatch):
    monkeypatch.delenv("QUALIBUG_CRED_ENC_KEY", raising=False)


def _configure(tmp_path):
    return configure_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resource_scope="wiki-space:space1",
        profile={
            "auth_mode": "internal_app",
            "app_id": "cli_app_identifier_123",
            "app_secret": "very-secret-application-value",
        },
        root=tmp_path,
        actor=ACTOR,
        display_name="飞书正式资料库",
    )


def _profile_path(tmp_path):
    return (
        tmp_path
        / "platform_workspace"
        / PROJECT
        / "enterprise_knowledge_center"
        / "connector_connection_profiles.json"
    )


def test_feishu_profile_is_encrypted_and_frontend_projection_is_masked(tmp_path):
    receipt = _configure(tmp_path)

    persisted = _profile_path(tmp_path).read_text(encoding="utf-8")
    assert "very-secret-application-value" not in persisted
    assert "cli_app_identifier_123" not in persisted
    assert "enc$v1$" in persisted

    profile = receipt["connection_profile"]
    assert profile["profile_ref"] == connector_profile_ref(CONNECTOR)
    assert profile["credentials_configured"] is True
    assert profile["configured_fields"] == {
        "app_id": True,
        "app_secret": True,
        "webhook_secret": False,
    }
    assert profile["plaintext_returned"] is False

    listed = list_connector_connection_profiles(PROJECT, root=tmp_path)
    serialized = json.dumps(listed, ensure_ascii=False, sort_keys=True)
    assert "very-secret-application-value" not in serialized
    assert "cli_app_identifier_123" not in serialized
    assert MASKED_SECRET not in serialized

    resolved = resolve_connector_connection_profile(
        PROJECT,
        connector_profile_ref(CONNECTOR),
        root=tmp_path,
    )
    assert resolved == {
        "auth_mode": "internal_app",
        "app_id": "cli_app_identifier_123",
        "app_secret": "very-secret-application-value",
    }


def test_masked_update_preserves_existing_ciphertexts(tmp_path):
    _configure(tmp_path)
    before = json.loads(_profile_path(tmp_path).read_text(encoding="utf-8"))
    encrypted_before = dict(before["profiles"][0]["encrypted_values"])

    configure_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resource_scope="wiki-space:space2",
        profile={
            "auth_mode": "internal_app",
            "app_id": MASKED_SECRET,
            "app_secret": MASKED_SECRET,
        },
        root=tmp_path,
        actor=ACTOR,
        display_name="飞书需求资料",
    )

    after = json.loads(_profile_path(tmp_path).read_text(encoding="utf-8"))
    assert after["profiles"][0]["encrypted_values"] == encrypted_before
    resolved = resolve_connector_connection_profile(
        PROJECT,
        connector_profile_ref(CONNECTOR),
        root=tmp_path,
    )
    assert resolved["app_secret"] == "very-secret-application-value"


def test_encrypted_checkpoint_survives_process_restart_contract(tmp_path, monkeypatch):
    _configure(tmp_path)
    checkpoint = "feishu-snapshot-v1:" + "a" * 64
    commit = commit_connector_sync_checkpoint(
        PROJECT,
        CONNECTOR,
        checkpoint,
        sync_epoch_id="sync_epoch_1",
        root=tmp_path,
        actor=ACTOR,
    )

    persisted = _profile_path(tmp_path).read_text(encoding="utf-8")
    assert checkpoint not in persisted
    assert "enc$v1$" in persisted
    assert commit["checkpoint_encrypted_at_rest"] is True
    assert load_connector_sync_checkpoint(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    ) == checkpoint

    monkeypatch.delenv("QUALIBUG_CRED_ENC_KEY", raising=False)
    assert load_connector_sync_checkpoint(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    ) == checkpoint


def test_checkpoint_integrity_mismatch_fails_closed(tmp_path):
    _configure(tmp_path)
    commit_connector_sync_checkpoint(
        PROJECT,
        CONNECTOR,
        "feishu-snapshot-v1:" + "b" * 64,
        sync_epoch_id="sync_epoch_2",
        root=tmp_path,
        actor=ACTOR,
    )
    store = json.loads(_profile_path(tmp_path).read_text(encoding="utf-8"))
    store["profiles"][0]["checkpoint_fingerprint"] = "0" * 64
    _profile_path(tmp_path).write_text(
        json.dumps(store, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(
        ConnectorProfileError,
        match="checkpoint_integrity_failed",
    ):
        load_connector_sync_checkpoint(PROJECT, CONNECTOR, root=tmp_path)


def test_profile_write_rolls_back_when_instance_binding_fails(tmp_path, monkeypatch):
    import ai_test_asset_center.connector_connection_profiles as authority

    monkeypatch.setattr(
        authority,
        "register_connector_instance",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("registry unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="registry unavailable"):
        configure_feishu_connector(
            PROJECT,
            connector_instance_id=CONNECTOR,
            resource_scope="wiki-space:space1",
            profile={
                "auth_mode": "tenant_access_token",
                "tenant_access_token": "tenant-token-value-12345",
            },
            root=tmp_path,
            actor=ACTOR,
        )

    store = json.loads(_profile_path(tmp_path).read_text(encoding="utf-8"))
    assert store["profiles"] == []
    assert "tenant-token-value-12345" not in json.dumps(store)
