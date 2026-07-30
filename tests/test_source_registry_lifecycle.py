from __future__ import annotations

from ai_test_asset_center import enterprise_source_registry
from ai_test_asset_center.enterprise_source_registry_lifecycle import (
    rollback_source_asset_activation,
)


def test_first_version_rollback_deactivates_asset_and_keeps_blob(tmp_path) -> None:
    manifest = enterprise_source_registry.register_source_asset(
        "first_version_rollback",
        "knowledge_requirements",
        "first projected requirements",
        source_type="prd",
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )

    rolled_back = rollback_source_asset_activation(
        "first_version_rollback",
        manifest["source_id"],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )

    assert rolled_back["rolled_back"] is True
    assert rolled_back["outcome"] == "deactivated_no_previous_version"
    assert enterprise_source_registry.list_source_assets(
        "first_version_rollback",
        root=tmp_path,
    ) == []
    retained = enterprise_source_registry.load_source_content(
        "first_version_rollback",
        manifest["source_hash"],
        root=tmp_path,
    )
    assert retained == "first projected requirements"


def test_update_rollback_restores_requested_previous_version(tmp_path) -> None:
    first = enterprise_source_registry.register_source_asset(
        "update_rollback",
        "knowledge_requirements",
        "first projected requirements",
        source_type="prd",
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )
    second = enterprise_source_registry.register_source_asset(
        "update_rollback",
        "knowledge_requirements",
        "second projected requirements",
        source_type="prd",
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )
    assert second["source_hash"] != first["source_hash"]

    rolled_back = rollback_source_asset_activation(
        "update_rollback",
        second["source_id"],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
        restore_source_hash=first["source_hash"],
        restore_version_id=first["source_version_id"],
    )

    assert rolled_back["outcome"] == "previous_version_restored"
    assets = enterprise_source_registry.list_source_assets(
        "update_rollback",
        root=tmp_path,
    )
    assert assets[0]["latest_source_hash"] == first["source_hash"]
    assert assets[0]["latest_version_id"] == first["source_version_id"]
    assert enterprise_source_registry.load_source_content(
        "update_rollback",
        second["source_hash"],
        root=tmp_path,
    ) == "second projected requirements"
