from __future__ import annotations


def test_disposable_materialization_changes_only_explicit_login_locators() -> None:
    from ai_test_asset_center.disposable_identity_authority import (
        materialize_disposable_identity_fields,
    )

    body = {
        "email": "demo@example.com",
        "phone": "13800138000",
        "username": "demo-user",
        "password": "SourcePassword!",
        "account_status": "ACTIVE",
        "companyName": "Acme",
        "productName": "Widget",
        "credential": "source-credential",
        "secret": "source-secret",
    }

    rendered, changed = materialize_disposable_identity_fields(body, "nonce")

    assert rendered["email"] == "qb-auto-nonce@qualibug.local"
    assert rendered["phone"].startswith("155")
    assert rendered["username"] == "qb_auto_username_nonce"
    assert rendered["password"] == "SourcePassword!"
    assert rendered["account_status"] == "ACTIVE"
    assert rendered["companyName"] == "Acme"
    assert rendered["productName"] == "Widget"
    assert rendered["credential"] == "source-credential"
    assert rendered["secret"] == "source-secret"
    assert set(changed) == {"email", "phone", "username"}


def test_account_status_alone_is_not_a_disposable_identity_anchor() -> None:
    from ai_test_asset_center.disposable_identity_authority import (
        has_disposable_identity_anchor,
    )

    assert has_disposable_identity_anchor(
        {"account_status": "ACTIVE", "account_type": "BUSINESS"}
    ) is False


def test_company_name_alone_is_not_an_identity_anchor() -> None:
    from ai_test_asset_center.disposable_identity_authority import (
        has_disposable_identity_anchor,
    )

    assert has_disposable_identity_anchor({"companyName": "Acme"}) is False


def test_sandbox_base_consumes_identity_only_disposable_authority() -> None:
    import ai_test_asset_center.sandbox_write_executor as sandbox

    rendered, changed = sandbox._base.materialize_disposable_identity_fields(
        {
            "email": "demo@example.com",
            "account_status": "ACTIVE",
            "password": "source-password",
        },
        "n1",
    )

    assert rendered["email"] == "qb-auto-n1@qualibug.local"
    assert rendered["account_status"] == "ACTIVE"
    assert rendered["password"] == "source-password"
    assert changed == ["email"]
