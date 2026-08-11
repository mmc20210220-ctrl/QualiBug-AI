from __future__ import annotations

import json


def _write_accounts(tmp_path) -> None:
    path = tmp_path / "platform_inputs" / "demo"
    path.mkdir(parents=True)
    (path / "test_accounts.json").write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_ref": "buyer-1",
                        "email": "buyer@example.test",
                        "username": "buyer",
                        "password": "declared-test-password",
                        "api_key": "declared-api-key",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_test_account_password_ref_resolves_without_secret_in_receipt(tmp_path) -> None:
    from ai_test_asset_center.request_credential_authority import (
        resolve_request_credentials,
    )

    _write_accounts(tmp_path)
    body, receipt = resolve_request_credentials(
        {
            "email": "buyer@example.test",
            "password": "secret_ref:test_accounts:buyer@example.test",
        },
        root=tmp_path,
        project="demo",
    )

    assert body["password"] == "declared-test-password"
    assert receipt["status"] == "RESOLVED"
    assert receipt["resolved_count"] == 1
    assert receipt["secret_value_persisted"] is False
    assert "declared-test-password" not in repr(receipt)


def test_api_key_field_does_not_receive_password(tmp_path) -> None:
    from ai_test_asset_center.request_credential_authority import (
        resolve_request_credentials,
    )

    _write_accounts(tmp_path)
    body, receipt = resolve_request_credentials(
        {"apiKey": "secret_ref:test_accounts:buyer-1"},
        root=tmp_path,
        project="demo",
    )

    assert body["apiKey"] == "declared-api-key"
    assert receipt["rows"][0]["material_source"] == "test_accounts:api_key"
    assert "declared-api-key" not in repr(receipt)


def test_generic_secret_is_ambiguous_when_multiple_secret_materials_exist(tmp_path) -> None:
    from ai_test_asset_center.request_credential_authority import (
        resolve_request_credentials,
    )

    _write_accounts(tmp_path)
    body, receipt = resolve_request_credentials(
        {"clientSecret": "secret_ref:test_accounts:buyer-1"},
        root=tmp_path,
        project="demo",
    )

    assert body["clientSecret"] == "secret_ref:test_accounts:buyer-1"
    assert receipt["status"] == "UNRESOLVED"
    assert receipt["rows"][0]["reason_code"] == (
        "REQUEST_CREDENTIAL_MATERIAL_UNRESOLVED"
    )


def test_missing_account_ref_is_unresolved_not_fabricated(tmp_path) -> None:
    from ai_test_asset_center.request_credential_authority import (
        resolve_request_credentials,
    )

    _write_accounts(tmp_path)
    body, receipt = resolve_request_credentials(
        {"password": "secret_ref:test_accounts:not-there"},
        root=tmp_path,
        project="demo",
    )

    assert body["password"] == "secret_ref:test_accounts:not-there"
    assert receipt["status"] == "UNRESOLVED"
    assert receipt["rows"][0]["reason_code"] == (
        "REQUEST_CREDENTIAL_ACCOUNT_NOT_FOUND"
    )


def test_runtime_wrapper_masks_unresolved_ref_into_pretransport_placeholder(tmp_path) -> None:
    from ai_test_asset_center.experiment_runtime_support import (
        _resolve_body_credential_refs,
        _unresolved_body_placeholders,
    )

    _write_accounts(tmp_path)
    body = _resolve_body_credential_refs(
        {"password": "secret_ref:test_accounts:not-there"},
        root=tmp_path,
        project="demo",
    )

    assert body["password"] == "{QUALIBUG_CREDENTIAL_REF_UNRESOLVED}"
    unresolved = _unresolved_body_placeholders(body, {})
    assert "QUALIBUG_CREDENTIAL_REF_UNRESOLVED" in unresolved
    assert "secret_ref:" not in repr(body)
