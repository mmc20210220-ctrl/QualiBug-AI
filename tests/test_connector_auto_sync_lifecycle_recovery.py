from __future__ import annotations

from pathlib import Path

import pytest

import ai_test_asset_center.connector_auto_sync as auto

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
ACTOR = {"name": "tester", "role": "knowledge_admin"}


def test_managed_recovery_orders_profile_lifecycle_and_profile_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    order = []

    def legacy(*args, **kwargs):
        order.append("profile")
        return {"action": "CONSISTENT"}

    def lifecycle(*args, **kwargs):
        order.append("lifecycle")
        return {
            "status": "COMPLETE",
            "recovery_action": "RECOVERED_COMMITTED_CHECKPOINT",
            "cursor_checkpoint_committed": True,
        }

    monkeypatch.setattr(auto, "_CORE_RECOVER_MANAGED_CHECKPOINT", legacy)
    monkeypatch.setattr(
        auto,
        "recover_pending_connector_lifecycle_checkpoint",
        lifecycle,
    )

    result = auto.recover_managed_feishu_checkpoint(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
    )

    assert order == ["profile", "lifecycle", "profile"]
    assert result["lifecycle_checkpoint_recovery_action"] == (
        "RECOVERED_COMMITTED_CHECKPOINT"
    )
    assert result["lifecycle_checkpoint_recovery_is_automatic"] is True
    assert result["raw_error_persisted"] is False
    assert result["customer_material_mutation_executed"] is False


def test_pending_lifecycle_epoch_forces_recovery_sweep(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(auto, "_CORE_RECOVERY_PENDING", lambda *a, **k: False)
    instance = {
        "pending_lifecycle_sync_epoch_id": "sync-pending",
        "lifecycle_recovery_attention_required": False,
    }

    assert auto._recovery_pending(
        tmp_path,
        PROJECT,
        CONNECTOR,
        instance,
        {},
        now=1_000.0,
    ) is True


def test_attention_state_is_projected_without_raw_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        auto,
        "_CORE_AUTO_SYNC_STATUS",
        lambda *a, **k: {
            "enabled": True,
            "state": "retrying",
            "message": "更新暂时中断，系统会自动恢复并重试",
            "maintenance_required_by_user": False,
            "failure_count": 1,
            "raw_error_returned": False,
        },
    )
    monkeypatch.setattr(
        auto._core,
        "_instance",
        lambda *a, **k: {
            "lifecycle_recovery_state": "BLOCKED_RECOVERY_INTENT_MISSING",
            "lifecycle_recovery_attention_required": True,
            "lifecycle_recovery_failure_count": 2,
            "lifecycle_recovery_last_error_category": (
                "BLOCKED_RECOVERY_INTENT_MISSING"
            ),
        },
    )
    monkeypatch.setattr(
        auto,
        "inspect_pending_connector_lifecycle_checkpoint",
        lambda *a, **k: {
            "status": "BLOCKED_RECOVERY_INTENT_MISSING",
            "pending_sync_epoch_id": "sync-pending",
            "pending_age_seconds": 901,
            "stale": True,
            "attention_required": True,
        },
    )

    status = auto.connector_auto_sync_status(tmp_path, PROJECT, CONNECTOR)

    assert status["state"] == "attention_required"
    assert status["lifecycle_recovery_attention_required"] is True
    assert status["maintenance_required_by_user"] is True
    assert status["lifecycle_recovery_failure_count"] == 2
    assert status["lifecycle_recovery_last_error_category"] == (
        "BLOCKED_RECOVERY_INTENT_MISSING"
    )
    assert status["lifecycle_recovery_raw_error_returned"] is False


def test_unbound_fresh_intent_blocks_new_sync_until_snapshot_binding_finishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        auto,
        "_CORE_RECOVER_MANAGED_CHECKPOINT",
        lambda *a, **k: {"action": "CONSISTENT"},
    )
    monkeypatch.setattr(
        auto,
        "recover_pending_connector_lifecycle_checkpoint",
        lambda *a, **k: {
            "status": "ORPHAN_INTENT",
            "recovery_action": "WAITING_FOR_SNAPSHOT_BIND",
        },
    )

    with pytest.raises(
        auto.ConnectorLifecycleRecoverySupervisorError,
        match="waiting_for_snapshot_bind",
    ):
        auto.recover_managed_feishu_checkpoint(
            PROJECT,
            CONNECTOR,
            root=tmp_path,
            actor=ACTOR,
        )
