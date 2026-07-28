from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center import ui_upload_fixture_registry as registry
from ai_test_asset_center.ui_upload_fixture_registry_integrity import (
    install_upload_fixture_registry_integrity,
)


_PROJECT = "fixture-integrity"
_ACTOR = {"name": "qa-owner", "role": "qa_lead"}


def test_reapproval_after_approved_copy_revoke_gets_new_binding_ref(tmp_path: Path) -> None:
    install_upload_fixture_registry_integrity()
    source = tmp_path / "platform_inputs" / _PROJECT / "fixture.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("id\n1\n", encoding="utf-8")
    candidate = registry.register_upload_fixture(
        _PROJECT,
        file_path=source,
        fixture_name="fixture",
        root=tmp_path,
        actor=_ACTOR,
    )["fixture"]

    first = registry.approve_upload_fixture(
        _PROJECT,
        fixture_id=candidate["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )["fixture"]
    registry.revoke_upload_fixture(
        _PROJECT,
        fixture_id=first["fixture_id"],
        reason="approval superseded",
        root=tmp_path,
        actor=_ACTOR,
    )
    second = registry.approve_upload_fixture(
        _PROJECT,
        fixture_id=candidate["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )["fixture"]

    assert first["binding_ref"] != second["binding_ref"]
    with pytest.raises(KeyError, match="active_approved_upload_fixture_not_found"):
        registry.approved_upload_fixture_binding(
            _PROJECT,
            first["binding_ref"],
            root=tmp_path,
        )
    resolved = registry.approved_upload_fixture_binding(
        _PROJECT,
        second["binding_ref"],
        root=tmp_path,
    )
    assert resolved["binding_ref"] == second["binding_ref"]


def test_duplicate_active_approval_keeps_existing_binding_ref(tmp_path: Path) -> None:
    install_upload_fixture_registry_integrity()
    source = tmp_path / "platform_inputs" / _PROJECT / "fixture.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{"id": 1}', encoding="utf-8")
    candidate = registry.register_upload_fixture(
        _PROJECT,
        file_path=source,
        fixture_name="fixture-json",
        root=tmp_path,
        actor=_ACTOR,
    )["fixture"]

    first = registry.approve_upload_fixture(
        _PROJECT,
        fixture_id=candidate["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )
    duplicate = registry.approve_upload_fixture(
        _PROJECT,
        fixture_id=candidate["fixture_id"],
        root=tmp_path,
        actor=_ACTOR,
    )

    assert duplicate["status"] == "DUPLICATE_ACTIVE"
    assert duplicate["fixture"]["binding_ref"] == first["fixture"]["binding_ref"]
