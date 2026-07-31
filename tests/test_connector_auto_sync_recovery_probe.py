from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import connector_auto_sync as auto


PROJECT = "enterprise-project"
CONNECTOR = "feishu-main"


def _paths(root: Path) -> tuple[Path, Path]:
    workspace = (
        root
        / "platform_workspace"
        / PROJECT
        / "enterprise_knowledge_center"
    )
    return (
        workspace / "connector_checkpoint_journal" / f"{CONNECTOR}.json",
        workspace / "connector_sync_ownership" / f"{CONNECTOR}.json",
    )


def _instance(started: str = "") -> dict:
    return {
        "connector_instance_id": CONNECTOR,
        "connector_type": "feishu",
        "status": "ACTIVE",
        "active_sync_epoch_id": "sync_live",
        "last_sync_started_at_utc": started,
        "last_committed_cursor_fingerprint": "old",
    }


def test_healthy_progressing_owner_is_not_treated_as_recovery(
    monkeypatch,
    tmp_path: Path,
):
    journal, owner = _paths(tmp_path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    owner.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("{}", encoding="utf-8")
    owner.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        auto,
        "inspect_connector_sync_ownership",
        lambda *a, **k: {
            "state": "ACTIVE",
            "owner_alive": True,
            "owner_dead": False,
            "heartbeat_stale": False,
            "progress_stale": False,
        },
    )

    assert auto._recovery_pending(
        tmp_path,
        PROJECT,
        CONNECTOR,
        _instance(),
        {"checkpoint_fingerprint": ""},
        now=10_000.0,
    ) is False


def test_live_but_stalled_owner_is_selected_for_fenced_takeover(
    monkeypatch,
    tmp_path: Path,
):
    _, owner = _paths(tmp_path)
    owner.parent.mkdir(parents=True, exist_ok=True)
    owner.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        auto,
        "inspect_connector_sync_ownership",
        lambda *a, **k: {
            "state": "STALLED_OWNER_THREAD",
            "owner_alive": True,
            "owner_dead": False,
            "heartbeat_stale": False,
            "progress_stale": True,
        },
    )

    assert auto._recovery_pending(
        tmp_path,
        PROJECT,
        CONNECTOR,
        _instance(),
        {"checkpoint_fingerprint": "old"},
        now=10_000.0,
    ) is True


def test_legacy_running_without_owner_waits_for_safe_age(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(auto, "_legacy_stale_seconds", lambda: 1800)
    now = 10_000.0
    recent = _instance(auto._utc(now - 120))
    old = _instance(auto._utc(now - 2000))

    assert auto._recovery_pending(
        tmp_path,
        PROJECT,
        CONNECTOR,
        recent,
        {"checkpoint_fingerprint": "old"},
        now=now,
    ) is False
    assert auto._recovery_pending(
        tmp_path,
        PROJECT,
        CONNECTOR,
        old,
        {"checkpoint_fingerprint": "old"},
        now=now,
    ) is True


def test_crash_journal_without_active_owner_requires_recovery(tmp_path: Path):
    journal, _ = _paths(tmp_path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("{}", encoding="utf-8")
    instance = _instance()
    instance["active_sync_epoch_id"] = ""

    assert auto._recovery_pending(
        tmp_path,
        PROJECT,
        CONNECTOR,
        instance,
        {"checkpoint_fingerprint": "old"},
        now=10_000.0,
    ) is True
