from __future__ import annotations

from pathlib import Path

import pytest

import ai_test_asset_center.connector_remote_lifecycle as lifecycle

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
ACTOR = {"name": "tester", "role": "knowledge_admin"}


def _occurrence(
    remote_id: str,
    *,
    status: str = "active",
    title: str = "Old title",
    parent: str = "parent-old",
    missing_count: int = 0,
    lifecycle_state: str = "PRESENT",
    version: int = 1,
) -> dict:
    return {
        "source_occurrence_id": f"occurrence-{remote_id}-{version}",
        "source_ref": f"connector://{CONNECTOR}/feishu-wiki-docx/{remote_id}",
        "canonical_source_id": f"canonical-{remote_id}-{version}",
        "content_asset_id": f"content-{remote_id}-{version}",
        "interpretation_asset_id": f"interpretation-{remote_id}-{version}",
        "content_hash": f"hash-{remote_id}-{version}",
        "source_type": "other_document",
        "format_identity": "docx",
        "filename": "document.docx",
        "version": version,
        "status": status,
        "created_at_utc": f"2026-08-01T10:00:0{version}Z",
        "source_metadata": {
            "connector_instance_id": CONNECTOR,
            "remote_resource_id": remote_id,
            "resource_kind": "feishu-wiki-docx",
            "remote_display_title": title,
            "parent_remote_id": parent,
            "remote_space_id": "space-a",
            "remote_lifecycle_state": lifecycle_state,
            "remote_missing_complete_snapshot_count": missing_count,
        },
    }


def _registry(*rows: dict) -> dict:
    return {
        "sources": [
            {
                "source_id": row["canonical_source_id"],
                "status": "active",
                "content_hash": row["content_hash"],
            }
            for row in rows
        ],
        "source_occurrences": list(rows),
        "content_assets": [],
        "interpretation_assets": [],
        "audit_events": [],
        "governance": {},
    }


def _resource(
    remote_id: str,
    *,
    title: str = "Old title",
    parent: str = "parent-old",
    materialization_state: str = "MATERIALIZABLE",
) -> dict:
    return {
        "remote_resource_id": remote_id,
        "resource_kind": "feishu-wiki-docx",
        "display_title": title,
        "parent_remote_id": parent,
        "remote_space_id": "space-a",
        "remote_revision": "2",
        "materialization_state": materialization_state,
    }


def _install_registry(
    monkeypatch: pytest.MonkeyPatch,
    registry: dict,
    *,
    receipt_persisted: bool = True,
) -> None:
    monkeypatch.setattr(lifecycle, "_load_registry", lambda *args, **kwargs: registry)
    monkeypatch.setattr(lifecycle, "_save_registry", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        lifecycle,
        "_attach_to_sync_receipt",
        lambda *args, **kwargs: receipt_persisted,
    )


def test_first_complete_absence_is_unconfirmed_and_retained(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    row = _occurrence("wiki:space:node")
    registry = _registry(row)
    _install_registry(monkeypatch, registry)
    retired = []
    monkeypatch.setattr(
        lifecycle,
        "delete_enterprise_knowledge_source",
        lambda *args, **kwargs: retired.append(kwargs) or {},
    )

    result = lifecycle.reconcile_connector_remote_lifecycle(
        PROJECT,
        connector_instance_id=CONNECTOR,
        present_resources=[],
        sync_epoch_id="sync-one",
        root=tmp_path,
        actor=ACTOR,
        deletion_policy="RETIRE_MISSING",
        authoritative_snapshot_complete=True,
        retire_after_complete_snapshots=2,
    )

    assert result["status"] == "COMPLETE"
    assert result["unconfirmed_missing_count"] == 1
    assert result["retirement_eligible_count"] == 0
    assert result["retired_count"] == 0
    assert retired == []
    assert row["status"] == "active"
    assert row["source_metadata"]["remote_missing_complete_snapshot_count"] == 1
    assert row["source_metadata"]["remote_lifecycle_state"] == (
        "ABSENT_FROM_CONFIGURED_SCOPE_UNCONFIRMED"
    )
    assert row["source_metadata"]["remote_deletion_inferred"] is False


def test_second_complete_absence_can_retire_internally_without_remote_delete_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    row = _occurrence(
        "wiki:space:node",
        missing_count=1,
        lifecycle_state="ABSENT_FROM_CONFIGURED_SCOPE_UNCONFIRMED",
    )
    registry = _registry(row)
    _install_registry(monkeypatch, registry)
    captured = {}

    def retire(project, source_ref, **kwargs):
        captured.update(kwargs)
        row["status"] = "retired_remote_scope"
        return {
            "source_occurrence_id": row["source_occurrence_id"],
            "lifecycle_status": row["status"],
        }

    monkeypatch.setattr(lifecycle, "delete_enterprise_knowledge_source", retire)

    result = lifecycle.reconcile_connector_remote_lifecycle(
        PROJECT,
        connector_instance_id=CONNECTOR,
        present_resources=[],
        sync_epoch_id="sync-two",
        root=tmp_path,
        actor=ACTOR,
        deletion_policy="RETIRE_MISSING",
        authoritative_snapshot_complete=True,
        retire_after_complete_snapshots=2,
        max_retire_ratio=1.0,
    )

    assert result["status"] == "COMPLETE"
    assert result["retirement_eligible_count"] == 1
    assert result["retired_count"] == 1
    assert row["status"] == "retired_remote_scope"
    assert captured["purge_bytes"] is False
    assert captured["retirement_evidence"]["absence_is_remote_deletion_proof"] is False
    assert result["remote_deletion_inferred"] is False
    assert result["permission_loss_inferred"] is False
    assert result["customer_material_mutation_executed"] is False


def test_incomplete_snapshot_does_not_advance_missing_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    row = _occurrence("wiki:space:node")
    registry = _registry(row)
    _install_registry(monkeypatch, registry)

    result = lifecycle.reconcile_connector_remote_lifecycle(
        PROJECT,
        connector_instance_id=CONNECTOR,
        present_resources=[],
        sync_epoch_id="sync-incomplete",
        root=tmp_path,
        actor=ACTOR,
        deletion_policy="RETIRE_MISSING",
        authoritative_snapshot_complete=False,
    )

    assert result["absent_count"] == 0
    assert result["retired_count"] == 0
    assert row["status"] == "active"
    assert row["source_metadata"]["remote_missing_complete_snapshot_count"] == 0


def test_rename_and_move_preserve_occurrence_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    row = _occurrence("wiki:space:node")
    occurrence_id = row["source_occurrence_id"]
    registry = _registry(row)
    _install_registry(monkeypatch, registry)

    result = lifecycle.reconcile_connector_remote_lifecycle(
        PROJECT,
        connector_instance_id=CONNECTOR,
        present_resources=[
            _resource(
                "wiki:space:node",
                title="New title",
                parent="parent-new",
            )
        ],
        sync_epoch_id="sync-rename-move",
        root=tmp_path,
        actor=ACTOR,
        deletion_policy="RETAIN",
        authoritative_snapshot_complete=True,
    )

    assert result["renamed_resource_count"] == 1
    assert result["moved_resource_count"] == 1
    assert row["source_occurrence_id"] == occurrence_id
    assert row["status"] == "active"
    assert row["source_metadata"]["remote_display_title"] == "New title"
    assert row["source_metadata"]["parent_remote_id"] == "parent-new"
    assert row["source_metadata"]["remote_lifecycle_state"] == (
        "PRESENT_RENAMED_AND_MOVED_WITHIN_SCOPE"
    )


def test_reappearance_resets_missing_evidence_and_reuses_retired_occurrence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    row = _occurrence(
        "wiki:space:node",
        status="retired_remote_scope",
        missing_count=2,
        lifecycle_state="ABSENT_FROM_CONFIGURED_SCOPE_CONFIRMED",
    )
    registry = _registry(row)
    _install_registry(monkeypatch, registry)

    def reactivate(project, root, target_registry, occurrence, actor):
        assert occurrence is row
        occurrence["status"] = "active"
        return True

    monkeypatch.setattr(lifecycle, "_reactivate_retired_occurrence", reactivate)

    result = lifecycle.reconcile_connector_remote_lifecycle(
        PROJECT,
        connector_instance_id=CONNECTOR,
        present_resources=[_resource("wiki:space:node")],
        sync_epoch_id="sync-returned",
        root=tmp_path,
        actor=ACTOR,
        deletion_policy="RETAIN",
        authoritative_snapshot_complete=True,
    )

    assert result["reappeared_resource_count"] == 1
    assert row["status"] == "active"
    assert row["source_metadata"]["remote_lifecycle_state"] == "REAPPEARED"
    assert row["source_metadata"]["remote_missing_complete_snapshot_count"] == 0


def test_mass_retirement_is_blocked_by_threshold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = _occurrence(
        "wiki:space:first",
        missing_count=1,
        lifecycle_state="ABSENT_FROM_CONFIGURED_SCOPE_UNCONFIRMED",
    )
    second = _occurrence(
        "wiki:space:second",
        missing_count=1,
        lifecycle_state="ABSENT_FROM_CONFIGURED_SCOPE_UNCONFIRMED",
    )
    registry = _registry(first, second)
    _install_registry(monkeypatch, registry)
    retired = []
    monkeypatch.setattr(
        lifecycle,
        "delete_enterprise_knowledge_source",
        lambda *args, **kwargs: retired.append(kwargs) or {},
    )

    result = lifecycle.reconcile_connector_remote_lifecycle(
        PROJECT,
        connector_instance_id=CONNECTOR,
        present_resources=[],
        sync_epoch_id="sync-mass-missing",
        root=tmp_path,
        actor=ACTOR,
        deletion_policy="RETIRE_MISSING",
        authoritative_snapshot_complete=True,
        retire_after_complete_snapshots=2,
        max_retire_count=1,
        max_retire_ratio=1.0,
    )

    assert result["status"] == "BLOCKED_THRESHOLD"
    assert result["retirement_eligible_count"] == 2
    assert result["retired_count"] == 0
    assert retired == []
    assert first["status"] == "active"
    assert second["status"] == "active"


def test_receipt_persistence_failure_is_visible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    row = _occurrence("wiki:space:node")
    registry = _registry(row)
    _install_registry(monkeypatch, registry, receipt_persisted=False)

    result = lifecycle.reconcile_connector_remote_lifecycle(
        PROJECT,
        connector_instance_id=CONNECTOR,
        present_resources=[_resource("wiki:space:node")],
        sync_epoch_id="sync-receipt-failed",
        root=tmp_path,
        actor=ACTOR,
        authoritative_snapshot_complete=True,
    )

    assert result["sync_receipt_persisted"] is False
    assert result["customer_material_mutation_executed"] is False
