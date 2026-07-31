from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from ai_test_asset_center import connector_sync_fencing as fencing
from ai_test_asset_center import connector_sync_ownership as ownership
from ai_test_asset_center.connector_write_fence import (
    ConnectorWriteFenceRevoked,
    connector_write_fence,
)


PROJECT = "enterprise-project"
CONNECTOR = "feishu-main"
ACTOR = {"name": "auto", "role": "knowledge_admin"}


def test_write_fence_blocks_mutation_after_token_revocation(tmp_path: Path):
    current = {"token": 1}

    def validate(project: str, connector: str, token: int) -> None:
        assert project == PROJECT
        assert connector == CONNECTOR
        if token != current["token"]:
            raise ConnectorWriteFenceRevoked("connector_sync_fence_revoked")

    allowed = tmp_path / "allowed.json"
    blocked = tmp_path / "blocked.json"
    with connector_write_fence(PROJECT, CONNECTOR, 1, validator=validate):
        allowed.write_text("ok", encoding="utf-8")
        current["token"] = 2
        with pytest.raises(
            ConnectorWriteFenceRevoked,
            match="fence_revoked",
        ):
            blocked.write_text("must-not-commit", encoding="utf-8")

    assert allowed.read_text(encoding="utf-8") == "ok"
    assert not blocked.exists()


def test_write_fence_does_not_change_ordinary_file_writes(tmp_path: Path):
    target = tmp_path / "ordinary.json"
    target.write_text("ordinary", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "ordinary"


def test_healthy_owner_cannot_be_displaced(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        fencing,
        "inspect_connector_sync_ownership",
        lambda *a, **k: {
            "state": "ACTIVE",
            "owner_alive": True,
            "owner_dead": False,
            "heartbeat_stale": False,
        },
    )
    monkeypatch.setattr(
        fencing,
        "_load_connector_registry",
        lambda *a, **k: pytest.fail("healthy owner must fail before token issue"),
    )

    with pytest.raises(
        fencing.ConnectorSyncFenceError,
        match="already_running_owner_active",
    ):
        fencing.acquire_connector_sync_fence(
            PROJECT,
            CONNECTOR,
            root=tmp_path,
            actor=ACTOR,
        )


def test_stale_live_owner_gets_new_token_and_is_fenced_out(
    monkeypatch,
    tmp_path: Path,
):
    instance = {
        "connector_instance_id": CONNECTOR,
        "fencing_generation": 7,
    }
    registry = {
        "connector_instances": [instance],
        "audit_events": [],
        "governance": {},
    }
    saved: list[dict] = []
    fenced: list[dict] = []
    monkeypatch.setattr(
        fencing,
        "inspect_connector_sync_ownership",
        lambda *a, **k: {
            "state": "STALE_HEARTBEAT_OWNER_ALIVE",
            "owner_alive": True,
            "owner_dead": False,
            "heartbeat_stale": True,
            "attempt_id": "checkpoint_old",
        },
    )
    monkeypatch.setattr(
        fencing,
        "_load_connector_registry",
        lambda *a, **k: registry,
    )
    monkeypatch.setattr(
        fencing,
        "_instance_by_id",
        lambda value, connector: value["connector_instances"][0],
    )
    monkeypatch.setattr(
        fencing,
        "_save_connector_registry",
        lambda *a, **k: saved.append(dict(instance)),
    )
    monkeypatch.setattr(
        fencing,
        "fence_out_connector_sync_ownership",
        lambda *a, **k: fenced.append(dict(k)) or {"fenced_out": True},
    )

    result = fencing.acquire_connector_sync_fence(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
    )

    assert result["fencing_token"] == 8
    assert result["takeover"] is True
    assert instance["fencing_generation"] == 8
    assert saved[0]["fencing_generation"] == 8
    assert fenced[0]["fencing_token"] == 8
    assert fenced[0]["takeover_attempt_id"].startswith("fence_")
    assert registry["governance"]["second_fencing_registry_created"] is False


def test_owner_record_inherits_fencing_token_and_fenced_out_is_dead(
    tmp_path: Path,
):
    def validate(project: str, connector: str, token: int) -> None:
        assert (project, connector, token) == (PROJECT, CONNECTOR, 11)

    with connector_write_fence(PROJECT, CONNECTOR, 11, validator=validate):
        begun = ownership.begin_connector_sync_ownership(
            PROJECT,
            CONNECTOR,
            "checkpoint_live",
            root=tmp_path,
        )
        assert begun["fencing_token"] == 11
        recorded = ownership.read_connector_sync_ownership(
            PROJECT,
            CONNECTOR,
            root=tmp_path,
        )
        assert recorded["fencing_token"] == 11

    takeover = ownership.fence_out_connector_sync_ownership(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        takeover_attempt_id="fence_takeover",
        fencing_token=12,
    )
    assert takeover["fenced_out"] is True
    status = ownership.inspect_connector_sync_ownership(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )
    assert status["state"] == "FENCED_OUT"
    assert status["owner_dead"] is True
    assert status["owner_alive"] is False
    ownership.stop_connector_sync_ownership(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        expected_attempt_id="fence_takeover",
    )


def test_managed_sync_source_owns_recovery_and_adapter_inside_one_fence():
    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center"
        / "connector_auto_sync.py"
    ).read_text(encoding="utf-8")
    function = source[
        source.index("def run_managed_feishu_sync"):source.index("def _project_ids")
    ]
    assert "with managed_connector_sync_fence(" in function
    assert function.index("with managed_connector_sync_fence(") < function.index(
        "recover_managed_feishu_checkpoint("
    )
    assert function.index("recover_managed_feishu_checkpoint(") < function.index(
        "sync_feishu_connector("
    )
    assert "MONOTONIC_REGISTRY_TOKEN" in function


def test_audit_hook_is_the_single_generic_write_gate():
    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center"
        / "connector_write_fence.py"
    ).read_text(encoding="utf-8")
    assert "sys.addaudithook(_audit_hook)" in source
    assert 'event == "open"' in source
    assert '"os.rename"' in source
    assert "current_connector_write_fence" in source
    assert "connector_sync_registry.json" not in source
