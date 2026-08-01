from __future__ import annotations

from pathlib import Path

import pytest

import ai_test_asset_center.connector_remote_lifecycle as lifecycle

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
ACTOR = {"name": "tester", "role": "knowledge_admin"}
REMOTE_ID = "wiki:space:node"


def test_historical_absence_produces_only_one_reappearance_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    old = {
        "source_occurrence_id": "occurrence-old",
        "source_ref": f"connector://{CONNECTOR}/feishu-wiki-docx/{REMOTE_ID}",
        "canonical_source_id": "canonical-old",
        "version": 1,
        "status": "superseded",
        "created_at_utc": "2026-08-01T10:00:00Z",
        "source_metadata": {
            "connector_instance_id": CONNECTOR,
            "remote_resource_id": REMOTE_ID,
            "remote_lifecycle_state": (
                "ABSENT_FROM_CONFIGURED_SCOPE_CONFIRMED"
            ),
            "remote_missing_complete_snapshot_count": 2,
        },
    }
    current = {
        "source_occurrence_id": "occurrence-current",
        "source_ref": f"connector://{CONNECTOR}/feishu-wiki-docx/{REMOTE_ID}",
        "canonical_source_id": "canonical-current",
        "version": 2,
        "status": "active",
        "created_at_utc": "2026-08-01T11:00:00Z",
        "source_metadata": {
            "connector_instance_id": CONNECTOR,
            "remote_resource_id": REMOTE_ID,
            "resource_kind": "feishu-wiki-docx",
            "remote_display_title": "Document",
            "parent_remote_id": "parent-a",
            "remote_lifecycle_state": "PRESENT",
        },
    }
    registry = {
        "sources": [],
        "source_occurrences": [old, current],
        "content_assets": [],
        "interpretation_assets": [],
        "audit_events": [],
        "governance": {},
    }
    monkeypatch.setattr(lifecycle, "_load_registry", lambda *args, **kwargs: registry)
    monkeypatch.setattr(lifecycle, "_save_registry", lambda *args, **kwargs: None)
    monkeypatch.setattr(lifecycle, "_attach_to_sync_receipt", lambda *args, **kwargs: True)

    resource = {
        "remote_resource_id": REMOTE_ID,
        "resource_kind": "feishu-wiki-docx",
        "display_title": "Document",
        "parent_remote_id": "parent-a",
        "remote_space_id": "space",
        "remote_revision": "3",
        "materialization_state": "MATERIALIZABLE",
    }
    first = lifecycle.reconcile_connector_remote_lifecycle(
        PROJECT,
        connector_instance_id=CONNECTOR,
        present_resources=[resource],
        sync_epoch_id="sync-first-return",
        root=tmp_path,
        actor=ACTOR,
        authoritative_snapshot_complete=True,
    )
    second = lifecycle.reconcile_connector_remote_lifecycle(
        PROJECT,
        connector_instance_id=CONNECTOR,
        present_resources=[resource],
        sync_epoch_id="sync-next",
        root=tmp_path,
        actor=ACTOR,
        authoritative_snapshot_complete=True,
    )

    assert first["reappeared_resource_count"] == 1
    assert old["source_metadata"][
        "remote_reappearance_consumed_at_sync_epoch_id"
    ] == "sync-first-return"
    assert second["reappeared_resource_count"] == 0
    assert current["source_metadata"]["remote_lifecycle_state"] == "PRESENT"
