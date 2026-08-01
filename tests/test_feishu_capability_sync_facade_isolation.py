from __future__ import annotations

import pytest

import ai_test_asset_center.feishu_connector_capability_sync as facade
import ai_test_asset_center.feishu_lifecycle_recovery_runtime as runtime


def test_facade_keeps_permanent_context_dispatchers_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {
        "discovery": facade._core.discover_feishu_wiki_resources,
        "snapshot": facade._core.sync_connector_snapshot_batch,
        "lifecycle": facade._core.reconcile_connector_remote_lifecycle,
    }
    captured = {}

    def fake_sync(*args, **kwargs):
        captured.update(
            {
                "discovery": facade._core.discover_feishu_wiki_resources,
                "snapshot": facade._core.sync_connector_snapshot_batch,
                "lifecycle": facade._core.reconcile_connector_remote_lifecycle,
            }
        )
        return {"status": "COMPLETE"}

    monkeypatch.setattr(facade._core, "sync_feishu_connector", fake_sync)

    result = facade.sync_feishu_connector(
        "enterprise-project",
        connector_instance_id="feishu-prod",
    )

    assert result["status"] == "COMPLETE"
    assert captured == before
    assert before == {
        "discovery": runtime.discover_feishu_resources_with_recovery_intent,
        "snapshot": runtime.sync_feishu_snapshot_with_recovery_intent,
        "lifecycle": runtime.reconcile_feishu_lifecycle_with_recovery_intent,
    }
    assert facade._core.discover_feishu_wiki_resources is before["discovery"]
    assert facade._core.sync_connector_snapshot_batch is before["snapshot"]
    assert facade._core.reconcile_connector_remote_lifecycle is before["lifecycle"]
