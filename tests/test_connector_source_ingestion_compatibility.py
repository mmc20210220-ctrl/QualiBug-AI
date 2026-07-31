from __future__ import annotations

from ai_test_asset_center.connector_source_ingestion import ingest_connector_snapshot
from ai_test_asset_center.enterprise_source_registry import load_source_content


def test_connector_bridge_preserves_legacy_receipt_identity(tmp_path):
    receipt = ingest_connector_snapshot(
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

    assert receipt["source_id"] == "api-contract"
    assert receipt["requested_source_id"] == "api-contract"
    assert receipt["canonical_source_id"].startswith("src_")
    assert receipt["runtime_source_id"]
    assert receipt["source_origin"] == "connector_snapshot"
    assert receipt["connector_id"] == "docs-sync"
    assert "sync_cursor" not in receipt
    assert load_source_content(
        "enterprise-project",
        receipt["source_hash"],
        root=tmp_path,
    ).startswith('{"openapi"')
