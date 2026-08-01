from __future__ import annotations

import pytest

import ai_test_asset_center.connector_lifecycle_commit_authority as authority
import ai_test_asset_center.feishu_connector_capability_sync as facade


def test_facade_binds_atomic_lifecycle_only_for_the_delegated_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = facade._core.reconcile_connector_remote_lifecycle
    captured = {}

    def fake_sync(*args, **kwargs):
        captured["during_call"] = facade._core.reconcile_connector_remote_lifecycle
        return {"status": "COMPLETE"}

    monkeypatch.setattr(facade._core, "sync_feishu_connector", fake_sync)
    facade.reconcile_connector_remote_lifecycle = (
        authority.reconcile_connector_remote_lifecycle_atomic
    )

    result = facade.sync_feishu_connector(
        "enterprise-project",
        connector_instance_id="feishu-prod",
    )

    assert result["status"] == "COMPLETE"
    assert captured["during_call"] is (
        authority.reconcile_connector_remote_lifecycle_atomic
    )
    assert facade._core.reconcile_connector_remote_lifecycle is previous
