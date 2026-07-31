from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ai_test_asset_center import connector_auto_sync as auto
from ai_test_asset_center import connector_checkpoint_recovery as recovery


PROJECT = "enterprise-project"
CONNECTOR = "feishu-main"


def _prepare_managed_sync(monkeypatch, observed: dict[str, object]) -> None:
    monkeypatch.setattr(
        auto,
        "recover_managed_feishu_checkpoint",
        lambda *a, **k: {"action": "CONSISTENT", "replay_required": False},
    )
    monkeypatch.setattr(
        auto,
        "begin_connector_checkpoint_commit",
        lambda *a, **k: {"attempt_id": "checkpoint_1"},
    )

    def stage(project, connector, attempt, checkpoint, **kwargs):
        observed.setdefault("order", []).append("stage")
        observed["staged"] = (
            project,
            connector,
            attempt,
            checkpoint,
            kwargs,
        )
        return {"ok": True}

    def clear(project, connector, **kwargs):
        observed.setdefault("order", []).append("clear")
        observed["cleared"] = (project, connector, kwargs)
        return {"ok": True}

    monkeypatch.setattr(auto, "stage_connector_checkpoint_result", stage)
    monkeypatch.setattr(auto, "clear_connector_checkpoint_journal", clear)


def test_managed_sync_is_the_single_recoverable_checkpoint_commit_path(
    monkeypatch,
    tmp_path: Path,
):
    observed: dict[str, object] = {}
    _prepare_managed_sync(monkeypatch, observed)
    monkeypatch.setattr(
        auto,
        "load_connector_sync_checkpoint",
        lambda *a, **k: "old-cursor",
    )

    def validate(project, connector, checkpoint, *, root):
        observed["validated"] = (project, connector, checkpoint, root)

    next_cursor = "feishu-snapshot-v1:" + "a" * 64
    monkeypatch.setattr(auto, "validate_connector_checkpoint", validate)
    monkeypatch.setattr(
        auto,
        "sync_feishu_connector",
        lambda *a, **k: {
            "status": "COMPLETE",
            "sync_epoch_id": "sync_1",
            "next_cursor": next_cursor,
            "committed_cursor_fingerprint": hashlib.sha256(
                next_cursor.encode("utf-8")
            ).hexdigest(),
        },
    )

    def commit(project, connector, checkpoint, **kwargs):
        observed.setdefault("order", []).append("commit")
        observed["committed"] = (project, connector, checkpoint, kwargs)
        return {"ok": True}

    monkeypatch.setattr(auto, "commit_connector_sync_checkpoint", commit)
    result = auto.run_managed_feishu_sync(PROJECT, CONNECTOR, root=tmp_path)

    assert result["status"] == "COMPLETE"
    assert result["checkpoint_commit_protocol"] == "RECOVERABLE_TWO_STAGE"
    assert observed["validated"][:3] == (PROJECT, CONNECTOR, "old-cursor")
    assert observed["staged"][:4] == (
        PROJECT,
        CONNECTOR,
        "checkpoint_1",
        next_cursor,
    )
    assert observed["committed"][:3] == (PROJECT, CONNECTOR, next_cursor)
    assert observed["order"] == ["stage", "commit", "clear"]


def test_managed_sync_never_advances_checkpoint_for_incomplete_run(
    monkeypatch,
    tmp_path: Path,
):
    observed: dict[str, object] = {}
    _prepare_managed_sync(monkeypatch, observed)
    monkeypatch.setattr(auto, "load_connector_sync_checkpoint", lambda *a, **k: "")
    monkeypatch.setattr(
        auto,
        "validate_connector_checkpoint",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        auto,
        "sync_feishu_connector",
        lambda *a, **k: {
            "status": "PARTIAL",
            "sync_epoch_id": "sync_partial",
        },
    )
    monkeypatch.setattr(
        auto,
        "commit_connector_sync_checkpoint",
        lambda *a, **k: pytest.fail("checkpoint must not advance"),
    )

    result = auto.run_managed_feishu_sync(PROJECT, CONNECTOR, root=tmp_path)
    assert result["status"] == "PARTIAL"
    assert "stage" not in observed.get("order", [])
    assert observed["order"] == ["clear"]


def test_recovery_promotes_encrypted_staged_checkpoint(monkeypatch, tmp_path: Path):
    checkpoint = "feishu-snapshot-v1:" + "b" * 64
    fingerprint = hashlib.sha256(checkpoint.encode("utf-8")).hexdigest()
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        recovery,
        "load_connector_sync_checkpoint",
        lambda *a, **k: "old",
    )
    monkeypatch.setattr(
        recovery,
        "_registry_fingerprint",
        lambda *a, **k: (fingerprint, {}),
    )
    monkeypatch.setattr(
        recovery,
        "_read_journal",
        lambda *a, **k: {
            "attempt_id": "checkpoint_1",
            "previous_checkpoint_fingerprint": hashlib.sha256(
                b"old"
            ).hexdigest(),
            "checkpoint_ciphertext": "ciphertext",
            "checkpoint_fingerprint": fingerprint,
            "sync_epoch_id": "sync_1",
        },
    )
    monkeypatch.setattr(
        recovery,
        "_decrypt_journal_checkpoint",
        lambda journal: checkpoint,
    )

    def commit(project, connector, value, **kwargs):
        observed["commit"] = (project, connector, value, kwargs)
        return {"ok": True}

    monkeypatch.setattr(recovery, "commit_connector_sync_checkpoint", commit)
    monkeypatch.setattr(
        recovery,
        "clear_connector_checkpoint_journal",
        lambda *a, **k: observed.setdefault("cleared", True) or {"ok": True},
    )

    result = recovery.recover_connector_checkpoint_commit(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor={"name": "system", "role": "knowledge_admin"},
    )
    assert result["action"] == "PROMOTED_STAGED_CHECKPOINT"
    assert observed["commit"][:3] == (PROJECT, CONNECTOR, checkpoint)
    assert observed["cleared"] is True


def test_recovery_rolls_registry_back_for_safe_replay(monkeypatch, tmp_path: Path):
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        recovery,
        "load_connector_sync_checkpoint",
        lambda *a, **k: "old",
    )
    monkeypatch.setattr(
        recovery,
        "_registry_fingerprint",
        lambda *a, **k: ("new-registry-fingerprint", {}),
    )
    monkeypatch.setattr(recovery, "_read_journal", lambda *a, **k: {})
    monkeypatch.setattr(
        recovery,
        "_repair_registry_to_profile",
        lambda *a, **k: observed.setdefault("repaired", k),
    )
    monkeypatch.setattr(
        recovery,
        "clear_connector_checkpoint_journal",
        lambda *a, **k: observed.setdefault("cleared", True) or {"ok": True},
    )

    result = recovery.recover_connector_checkpoint_commit(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor={"name": "system", "role": "knowledge_admin"},
        remote_checkpoint_resolver=lambda: "remote-changed",
    )
    assert result["action"] == "ROLLED_BACK_REGISTRY_FOR_SAFE_REPLAY"
    assert result["replay_required"] is True
    assert result["source_snapshots_retained"] is True
    assert observed["repaired"]
    assert observed["cleared"] is True


def test_due_policy_hides_scheduler_decisions_from_users():
    now = 100_000.0
    instance = {
        "status": "ACTIVE",
        "connector_type": "feishu",
        "active_sync_epoch_id": "",
        "last_successful_sync_at_utc": auto._utc(now - 7 * 60 * 60),
        "last_failed_sync_at_utc": "",
    }
    assert auto._due(
        instance,
        {},
        now=now,
        refresh_seconds=6 * 60 * 60,
    )
    assert not auto._due(
        instance,
        {"next_attempt_unix": now + 60},
        now=now,
        refresh_seconds=6 * 60 * 60,
    )
    instance["status"] = "PAUSED"
    assert not auto._due(
        instance,
        {},
        now=now,
        refresh_seconds=6 * 60 * 60,
    )


def test_sweep_retries_with_exponential_backoff_without_new_registry(
    monkeypatch,
    tmp_path: Path,
):
    auto._ATTEMPTS.clear()
    monkeypatch.setattr(auto, "_project_ids", lambda root: [PROJECT])
    monkeypatch.setattr(
        auto,
        "_configured_profiles",
        lambda project, root: {CONNECTOR},
    )
    monkeypatch.setattr(
        auto,
        "list_connector_instances",
        lambda *a, **k: {
            "connector_instances": [
                {
                    "connector_instance_id": CONNECTOR,
                    "connector_type": "feishu",
                    "status": "ACTIVE",
                    "active_sync_epoch_id": "",
                    "last_successful_sync_at_utc": "",
                    "last_failed_sync_at_utc": "",
                }
            ]
        },
    )
    monkeypatch.setattr(
        auto,
        "_policy",
        lambda: {
            "refresh_seconds": 3600,
            "sweep_seconds": 10,
            "initial_delay_seconds": 0,
            "retry_base_seconds": 60,
            "retry_max_seconds": 3600,
        },
    )

    calls: list[float] = []

    def fail(*args, **kwargs):
        calls.append(1.0)
        raise RuntimeError("temporary remote failure")

    first = auto.run_connector_auto_sync_sweep(
        tmp_path,
        now=1_000.0,
        sync_runner=fail,
    )
    blocked = auto.run_connector_auto_sync_sweep(
        tmp_path,
        now=1_030.0,
        sync_runner=fail,
    )
    second = auto.run_connector_auto_sync_sweep(
        tmp_path,
        now=1_061.0,
        sync_runner=fail,
    )

    assert first == {
        "enabled": True,
        "attempted": 1,
        "succeeded": 0,
        "failed": 1,
        "skipped": 0,
        "completed_at_utc": auto._utc(1_000.0),
        "new_registry_created": False,
    }
    assert blocked["attempted"] == 0
    assert blocked["skipped"] == 1
    assert second["attempted"] == 1
    assert len(calls) == 2
    status = auto.connector_auto_sync_status(tmp_path, PROJECT, CONNECTOR)
    assert status["failure_count"] == 2
    assert status["raw_error_returned"] is False
    assert status["message"] == "更新暂时中断，系统会自动恢复并重试"


def test_success_resets_retry_state(monkeypatch, tmp_path: Path):
    auto._ATTEMPTS.clear()
    monkeypatch.setattr(auto, "_project_ids", lambda root: [PROJECT])
    monkeypatch.setattr(
        auto,
        "_configured_profiles",
        lambda project, root: {CONNECTOR},
    )
    monkeypatch.setattr(
        auto,
        "list_connector_instances",
        lambda *a, **k: {
            "connector_instances": [
                {
                    "connector_instance_id": CONNECTOR,
                    "connector_type": "feishu",
                    "status": "ACTIVE",
                    "active_sync_epoch_id": "",
                    "last_successful_sync_at_utc": "",
                    "last_failed_sync_at_utc": "",
                }
            ]
        },
    )
    monkeypatch.setattr(
        auto,
        "_policy",
        lambda: {
            "refresh_seconds": 3600,
            "sweep_seconds": 10,
            "initial_delay_seconds": 0,
            "retry_base_seconds": 60,
            "retry_max_seconds": 3600,
        },
    )

    result = auto.run_connector_auto_sync_sweep(
        tmp_path,
        now=2_000.0,
        sync_runner=lambda *a, **k: {
            "status": "COMPLETE",
            "sync_epoch_id": "sync_ok",
        },
    )
    status = auto.connector_auto_sync_status(tmp_path, PROJECT, CONNECTOR)
    assert result["succeeded"] == 1
    assert status["state"] == "healthy"
    assert status["failure_count"] == 0
    assert status["attention"] == ""


def test_supervisor_is_idempotent_and_can_be_disabled(
    monkeypatch,
    tmp_path: Path,
):
    auto.stop_all_connector_auto_sync_supervisors()
    monkeypatch.setenv("QUALIBUG_CONNECTOR_AUTO_SYNC_ENABLED", "0")
    assert auto.ensure_connector_auto_sync_supervisor(tmp_path)["enabled"] is False

    monkeypatch.setenv("QUALIBUG_CONNECTOR_AUTO_SYNC_ENABLED", "1")
    monkeypatch.setenv(
        "QUALIBUG_CONNECTOR_AUTO_SYNC_INITIAL_DELAY_SECONDS",
        "600",
    )
    first = auto.ensure_connector_auto_sync_supervisor(tmp_path)
    second = auto.ensure_connector_auto_sync_supervisor(tmp_path)
    stopped = auto.stop_connector_auto_sync_supervisor(tmp_path)
    assert first["started"] is True
    assert second["already_running"] is True
    assert stopped["stopped"] is True
    assert stopped["thread_alive"] is False


def test_server_lifecycle_owns_supervisor_start_and_stop():
    service = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center"
        / "private_pilot_service.py"
    ).read_text(encoding="utf-8")
    assert "ensure_connector_auto_sync_supervisor(resolved_root)" in service
    assert "def server_close(self)" in service
    assert "stop_connector_auto_sync_supervisor(root" in service
    assert service.index("ensure_connector_auto_sync_supervisor(resolved_root)") < service.index(
        "return server"
    )


def test_auto_sync_authority_creates_no_parallel_registry():
    source = Path(auto.__file__).read_text(encoding="utf-8")
    journal = Path(recovery.__file__).read_text(encoding="utf-8")
    assert "connector_connection_profiles.json" in source
    assert "connector_sync_registry" not in source
    assert "source_registry" not in source
    assert "run_managed_feishu_sync" in source
    assert "connector_checkpoint_journal" in journal
    assert "new_registry" not in journal
    assert "plaintext_checkpoint_persisted" in journal
