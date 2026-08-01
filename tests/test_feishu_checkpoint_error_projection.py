from __future__ import annotations

import pytest

import ai_test_asset_center.feishu_connector_capability_sync as facade
from ai_test_asset_center.connector_checkpoint_commit_authority import (
    ConnectorCheckpointCommitError,
)
from ai_test_asset_center.feishu_connector_adapter import FeishuConnectorError


def test_checkpoint_commit_failure_stays_inside_feishu_error_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args, **kwargs):
        raise ConnectorCheckpointCommitError(
            "checkpoint_finalization_pending_recovery"
        )

    monkeypatch.setattr(facade._core, "sync_feishu_connector", fail)

    with pytest.raises(
        FeishuConnectorError,
        match=(
            "feishu_lifecycle_checkpoint_commit_failed:"
            "checkpoint_finalization_pending_recovery"
        ),
    ):
        facade.sync_feishu_connector(
            "enterprise-project",
            connector_instance_id="feishu-prod",
        )
