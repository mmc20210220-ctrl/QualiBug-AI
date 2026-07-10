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

    denied = db.reset_tenant_password(
        tmp_path,
        tenant_id="wrong-workspace",
        username="owner",
        new_password="NewPassw0rd!",
    )
    assert denied == {"ok": False, "error": "RESET_DENIED"}
    assert db.authenticate_tenant(tmp_path, "owner", "OldPassw0rd!")

    short = db.reset_tenant_password(
        tmp_path,
        tenant_id="acme-prod",
        username="owner",
        new_password="short",
    )
    assert short == {"ok": False, "error": "PASSWORD_TOO_SHORT"}

    reset = db.reset_tenant_password(
        tmp_path,
        tenant_id="acme-prod",
        username="owner",
        new_password="NewPassw0rd!",
    )
    assert reset == {"ok": True, "tenant_id": "acme-prod", "username": "owner"}
    assert db.authenticate_tenant(tmp_path, "owner", "OldPassw0rd!") is None
    assert db.authenticate_tenant(tmp_path, "owner", "NewPassw0rd!")
