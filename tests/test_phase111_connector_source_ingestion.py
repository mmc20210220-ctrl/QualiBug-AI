from __future__ import annotations

import pytest

from ai_test_asset_center.connector_source_ingestion import ConnectorSnapshotError, ingest_connector_snapshot
from ai_test_asset_center.enterprise_source_registry import load_source_content


def test_connector_snapshot_registers_immutable_source_version(tmp_path):
    manifest = ingest_connector_snapshot(
        "enterprise-project",
        root=tmp_path,
        connector_id="docs-sync",
        source_id="api-contract",
        source_type="openapi",
        content='{"openapi":"3.0.0","paths":{}}',
        external_ref="document-ref-42",
        sync_cursor="cursor-7",
        actor={"name": "connector-service", "role": "connector"},
    )

    assert manifest["source_origin"] == "connector_snapshot"
    assert manifest["connector_id"] == "docs-sync"
    assert load_source_content("enterprise-project", manifest["source_hash"], root=tmp_path).startswith('{"openapi"')


def test_connector_snapshot_rejects_obvious_credentials(tmp_path):
    with pytest.raises(ConnectorSnapshotError, match="connector_snapshot_contains_credential"):
        ingest_connector_snapshot(
            "enterprise-project",
            root=tmp_path,
            connector_id="docs-sync",
            source_id="api-contract",
            source_type="openapi",
            content="Authorization: Bearer supersecretvalue12345",
            external_ref="document-ref-42",
        )
