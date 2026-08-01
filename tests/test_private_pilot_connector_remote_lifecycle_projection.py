from __future__ import annotations

import json
from pathlib import Path

import pytest

import ai_test_asset_center.private_pilot_connector_handlers as handlers

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"


def test_remote_lifecycle_projection_returns_only_bounded_summary() -> None:
    run = {
        "remote_lifecycle": {
            "status": "COMPLETE",
            "authoritative_snapshot_complete": True,
            "present_count": 12,
            "absent_count": 2,
            "unconfirmed_missing_count": 1,
            "retirement_eligible_count": 1,
            "retired_count": 1,
            "renamed_resource_count": 3,
            "moved_resource_count": 4,
            "reappeared_resource_count": 1,
            "retire_after_complete_snapshots": 2,
            "requested_deletion_policy": "RETIRE_MISSING",
            "effective_deletion_policy": "GUARDED_REMOTE_SCOPE_RETIREMENT",
            "absence_interpretation": (
                "ABSENT_FROM_CONFIGURED_SCOPE_NOT_REMOTE_DELETE_PROOF"
            ),
            "sync_receipt_persisted": True,
            "evidence_persistence_status": "COMPLETE",
            "retired_source_occurrences": [
                {
                    "remote_resource_id": "SECRET-REMOTE-ID",
                    "source_ref": "connector://secret/source",
                    "display_title": "Customer confidential title",
                }
            ],
            "errors": [
                {
                    "source_ref": "connector://secret/error",
                    "detail": "Customer confidential diagnostic",
                }
            ],
        }
    }

    projected = handlers._remote_lifecycle_projection(run)
    encoded = json.dumps(projected, ensure_ascii=False)

    assert projected["status"] == "COMPLETE"
    assert projected["absent_count"] == 2
    assert projected["retired_count"] == 1
    assert projected["remote_deletion_inferred"] is False
    assert projected["permission_loss_inferred"] is False
    assert projected["historical_source_bytes_retained"] is True
    assert projected["customer_material_mutation_executed"] is False
    assert projected["remote_resource_identities_returned"] is False
    assert projected["source_refs_returned"] is False
    assert "SECRET-REMOTE-ID" not in encoded
    assert "connector://secret" not in encoded
    assert "Customer confidential" not in encoded
    assert "retired_source_occurrences" not in projected
    assert "errors" not in projected


def test_coverage_projection_restores_lifecycle_from_persisted_sync_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        handlers,
        "load_connector_sync_run",
        lambda *args, **kwargs: {
            "materialized_item_count": 8,
            "unchanged_item_count": 2,
            "coverage_observation_count": 0,
            "knowledge_coverage_status": "COMPLETE",
            "completed_at_utc": "2026-08-01T10:00:00Z",
            "remote_lifecycle": {
                "status": "PARTIAL_RECEIPT_NOT_PERSISTED",
                "authoritative_snapshot_complete": True,
                "present_count": 10,
                "absent_count": 1,
                "unconfirmed_missing_count": 1,
                "retirement_eligible_count": 0,
                "retired_count": 0,
                "renamed_resource_count": 0,
                "moved_resource_count": 0,
                "reappeared_resource_count": 0,
                "retire_after_complete_snapshots": 2,
                "requested_deletion_policy": "RETAIN",
                "effective_deletion_policy": "RETAIN",
                "sync_receipt_persisted": False,
                "evidence_persistence_status": "FAILED",
            },
        },
    )

    coverage = handlers._coverage_projection(
        PROJECT,
        CONNECTOR,
        {"last_successful_sync_epoch_id": "sync-one"},
        tmp_path,
    )

    assert coverage["status"] == "COMPLETE"
    assert coverage["covered_count"] == 10
    assert coverage["remote_lifecycle"]["status"] == (
        "PARTIAL_RECEIPT_NOT_PERSISTED"
    )
    assert coverage["remote_lifecycle"]["sync_receipt_persisted"] is False
    assert coverage["remote_lifecycle"]["evidence_persistence_status"] == (
        "FAILED"
    )
    assert coverage["remote_lifecycle"]["remote_deletion_inferred"] is False


def test_missing_sync_receipt_does_not_fabricate_lifecycle_certainty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def missing(*args, **kwargs):
        raise handlers.ConnectorSyncError("connector_sync_run_not_found")

    monkeypatch.setattr(handlers, "load_connector_sync_run", missing)

    coverage = handlers._coverage_projection(
        PROJECT,
        CONNECTOR,
        {"last_successful_sync_epoch_id": "missing"},
        tmp_path,
    )

    assert coverage["status"] == "UNKNOWN"
    assert coverage["remote_lifecycle"]["status"] == "UNKNOWN"
    assert coverage["remote_lifecycle"]["authoritative_snapshot_complete"] is False
    assert coverage["remote_lifecycle"]["remote_deletion_inferred"] is False
    assert coverage["remote_lifecycle"]["permission_loss_inferred"] is False
