from __future__ import annotations

from ai_test_asset_center.connector_source_ingestion import ingest_connector_snapshot


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
    assert receipt["runtime_source_hash"] == receipt["source_hash"]
    assert receipt["source_origin"] == "connector_snapshot"
    assert receipt["connector_id"] == "docs-sync"
    assert "sync_cursor" not in receipt
