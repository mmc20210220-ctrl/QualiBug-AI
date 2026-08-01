from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.connector_lifecycle_recovery_intent import (
    ConnectorLifecycleRecoveryIntentError,
    clear_connector_lifecycle_recovery_intent,
    lifecycle_recovery_intent_path,
    load_connector_lifecycle_recovery_intent,
    stage_connector_lifecycle_recovery_intent,
    update_connector_lifecycle_recovery_intent_state,
)

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
ACTOR = {"name": "tester", "role": "knowledge_admin"}
CURSOR_FINGERPRINT = "a" * 64


def _resources() -> list[dict]:
    return [
        {
            "remote_resource_id": "wiki:space-a:node-a",
            "resource_kind": "feishu-wiki-docx",
            "display_title": "Customer order document",
            "parent_remote_id": "parent-a",
            "remote_space_id": "space-a",
            "remote_revision": "17",
            "materialization_state": "MATERIALIZABLE",
        },
        {
            "remote_resource_id": "wiki:space-a:node-b",
            "resource_kind": "feishu-wiki-mindnote",
            "display_title": "Order workflow map",
            "parent_remote_id": "parent-a",
            "remote_space_id": "space-a",
            "remote_revision": "18",
            "materialization_state": "UNSUPPORTED",
        },
    ]


def test_intent_round_trip_contains_only_bounded_lifecycle_metadata(
    tmp_path: Path,
) -> None:
    intent = stage_connector_lifecycle_recovery_intent(
        PROJECT,
        CONNECTOR,
        present_resources=_resources(),
        next_cursor_fingerprint=CURSOR_FINGERPRINT,
        root=tmp_path,
        actor=ACTOR,
        deletion_policy="RETIRE_MISSING",
        retire_after_complete_snapshots=3,
        max_retire_count=7,
        max_retire_ratio=0.2,
        sync_epoch_id="sync-intent-one",
    )

    loaded = load_connector_lifecycle_recovery_intent(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )
    assert loaded == intent
    assert loaded["state"] == "STAGED"
    assert loaded["present_resource_count"] == 2
    assert loaded["source_content_persisted"] is False
    assert loaded["raw_cursor_persisted"] is False
    assert loaded["credentials_persisted"] is False
    assert loaded["customer_material_mutation_executed"] is False

    raw = lifecycle_recovery_intent_path(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    ).read_text(encoding="utf-8")
    assert "customer document body" not in raw
    assert "access-token" not in raw
    assert "tenant_access_token" not in raw
    assert "feishu-snapshot-v1:" not in raw
    assert CURSOR_FINGERPRINT in raw

    updated = update_connector_lifecycle_recovery_intent_state(
        PROJECT,
        CONNECTOR,
        state="SNAPSHOT_COMMITTED_PENDING_LIFECYCLE",
        root=tmp_path,
        actor=ACTOR,
        expected_sync_epoch_id="sync-intent-one",
    )
    assert updated["state"] == "SNAPSHOT_COMMITTED_PENDING_LIFECYCLE"

    cleared = clear_connector_lifecycle_recovery_intent(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        expected_sync_epoch_id="sync-intent-one",
    )
    assert cleared["cleared"] is True
    assert load_connector_lifecycle_recovery_intent(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    ) == {}


def test_tampered_resource_digest_fails_closed(tmp_path: Path) -> None:
    stage_connector_lifecycle_recovery_intent(
        PROJECT,
        CONNECTOR,
        present_resources=_resources(),
        next_cursor_fingerprint=CURSOR_FINGERPRINT,
        root=tmp_path,
        actor=ACTOR,
        sync_epoch_id="sync-intent-tamper",
    )
    path = lifecycle_recovery_intent_path(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["present_resources"][0]["display_title"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        ConnectorLifecycleRecoveryIntentError,
        match="digest_mismatch",
    ):
        load_connector_lifecycle_recovery_intent(
            PROJECT,
            CONNECTOR,
            root=tmp_path,
        )


def test_different_intent_cannot_overwrite_existing_recovery_evidence(
    tmp_path: Path,
) -> None:
    stage_connector_lifecycle_recovery_intent(
        PROJECT,
        CONNECTOR,
        present_resources=_resources(),
        next_cursor_fingerprint=CURSOR_FINGERPRINT,
        root=tmp_path,
        actor=ACTOR,
        sync_epoch_id="sync-intent-existing",
    )
    changed = _resources()
    changed[0]["remote_revision"] = "19"

    with pytest.raises(
        ConnectorLifecycleRecoveryIntentError,
        match="already_exists",
    ):
        stage_connector_lifecycle_recovery_intent(
            PROJECT,
            CONNECTOR,
            present_resources=changed,
            next_cursor_fingerprint="b" * 64,
            root=tmp_path,
            actor=ACTOR,
            sync_epoch_id="sync-intent-new",
        )
