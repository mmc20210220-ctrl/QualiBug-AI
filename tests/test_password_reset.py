from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import db_persistence as db


def test_reset_tenant_password_requires_matching_workspace_and_username(tmp_path: Path) -> None:
    created = db.create_tenant(
        tmp_path,
        "acme-prod",
        "Acme",
        username="owner",
        password="OldPassw0rd!",
    )
    assert created["ok"] is True

    # Without proof of the current password the reset is refused up front
    # (no username enumeration oracle for unauthenticated callers).
    unauth = db.reset_tenant_password(
        tmp_path,
        tenant_id="wrong-workspace",
        username="owner",
        new_password="NewPassw0rd!",
    )
    assert unauth == {"ok": False, "error": "RESET_AUTH_REQUIRED"}

    denied = db.reset_tenant_password(
        tmp_path,
        tenant_id="wrong-workspace",
        username="owner",
        new_password="NewPassw0rd!",
        current_password="OldPassw0rd!",
    )
    assert denied == {"ok": False, "error": "RESET_DENIED"}
    assert db.authenticate_tenant(tmp_path, "owner", "OldPassw0rd!")

    short = db.reset_tenant_password(
        tmp_path,
        tenant_id="acme-prod",
        username="owner",
        new_password="short",
        current_password="OldPassw0rd!",
    )
    assert short == {"ok": False, "error": "PASSWORD_TOO_SHORT"}

    reset = db.reset_tenant_password(
        tmp_path,
        tenant_id="acme-prod",
        username="owner",
        new_password="NewPassw0rd!",
        current_password="OldPassw0rd!",
    )
    assert reset["ok"] is True
    assert reset["tenant_id"] == "acme-prod"
    assert reset["username"] == "owner"
    assert reset["api_key_revoked"] is True
    assert reset["session_version"] == 2
    assert db.authenticate_tenant(tmp_path, "owner", "OldPassw0rd!") is None
    assert db.authenticate_tenant(tmp_path, "owner", "NewPassw0rd!")
