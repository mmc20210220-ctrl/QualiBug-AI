from __future__ import annotations

import hashlib

import pytest

from ai_test_asset_center.connector_source_ingestion import (
    ConnectorSnapshotError,
    build_connector_source_ref,
    ingest_connector_snapshot,
)
from ai_test_asset_center.enterprise_knowledge_center import (
    ingest_enterprise_knowledge_documents,
    list_enterprise_knowledge_sources,
)


def _connector_actor() -> dict[str, str]:
    return {"name": "enterprise-doc-sync", "role": "connector_service"}


def test_connector_snapshot_uses_source_occurrence_authority(tmp_path):
    receipt = ingest_connector_snapshot(
        "enterprise-project",
        root=tmp_path,
        connector_id="feishu-prod",
        source_id="legacy-prd-id",
        source_type="prd",
        content="# 订单需求\n订单只能由所属租户查看。",
        external_ref="doccnA123",
        remote_revision="17",
        sync_epoch_id="sync-20260731-001",
        sync_cursor="opaque-cursor-value",
        canonical_url="https://docs.example.com/doccnA123?tenant_token=secret#section",
        actor=_connector_actor(),
        filename="订单需求.md",
    )

    assert receipt["canonical_ingestion_authority"] == "SOURCE_OCCURRENCE_REGISTRY"
    assert receipt["connector_parser_implemented"] is False
    assert receipt["source_ref"] == "connector://feishu-prod/document/doccnA123"
    assert receipt["source_occurrence_id"].startswith("occurrence:")
    assert receipt["canonical_source_id"].startswith("src_")
    assert receipt["canonical_url"] == "https://docs.example.com/doccnA123"
    assert receipt["sync_cursor_fingerprint"] == hashlib.sha256(
        b"opaque-cursor-value"
    ).hexdigest()
    assert "sync_cursor" not in receipt
    assert receipt["raw_sync_cursor_persisted"] is False

    inventory = list_enterprise_knowledge_sources(
        "enterprise-project",
        root=tmp_path,
    )
    assert inventory["summary"]["active_source_count"] == 1
    assert inventory["sources"][0]["source_ref"] == receipt["source_ref"]
    assert inventory["sources"][0]["inventory_role"] == "SOURCE_OCCURRENCE"


def test_connector_binary_export_reuses_existing_document_pipeline(tmp_path):
    receipt = ingest_connector_snapshot(
        "enterprise-project",
        root=tmp_path,
        connector_id="api-platform",
        source_id="order-api",
        source_type="openapi",
        content=(
            b'{"openapi":"3.0.0","info":{"title":"Order API",'
            b'"version":"1"},"paths":{}}'
        ),
        remote_resource_id="spec-42",
        resource_kind="api-spec",
        export_format="json",
        declared_mime="application/json",
        actor=_connector_actor(),
        filename="order-openapi.json",
    )

    assert receipt["source_ref"] == "connector://api-platform/api-spec/spec-42"
    assert receipt["content_hash"]
    assert receipt["source_occurrence"]["format_identity"] == "json"
    assert receipt["created"][0]["parse"]["parse_status"] != "failed"


def test_same_online_resource_creates_version_lineage_across_rename(tmp_path):
    first = ingest_connector_snapshot(
        "enterprise-project",
        root=tmp_path,
        connector_id="feishu-prod",
        source_id="legacy-prd-id",
        source_type="prd",
        content="# 订单需求\n订单创建后为待支付。",
        remote_resource_id="doccnA123",
        actor=_connector_actor(),
        filename="旧标题.md",
    )
    second = ingest_connector_snapshot(
        "enterprise-project",
        root=tmp_path,
        connector_id="feishu-prod",
        source_id="legacy-prd-id",
        source_type="prd",
        content="# 订单需求\n订单创建后为待支付；支付成功后为已支付。",
        remote_resource_id="doccnA123",
        remote_revision="18",
        actor=_connector_actor(),
        filename="新标题.md",
    )

    assert first["source_ref"] == second["source_ref"]
    assert first["source_occurrence_id"] != second["source_occurrence_id"]

    inventory = list_enterprise_knowledge_sources(
        "enterprise-project",
        root=tmp_path,
        include_deleted=True,
    )
    lineage = [
        row for row in inventory["sources"] if row["source_ref"] == first["source_ref"]
    ]
    assert sorted(row["status"] for row in lineage) == ["active", "superseded"]
    assert sorted(row["occurrence_version"] for row in lineage) == [1, 2]


def test_connector_and_uploaded_copy_share_content_but_keep_occurrences(tmp_path):
    content = "# 退款规则\n已支付订单允许申请退款。"
    online = ingest_connector_snapshot(
        "enterprise-project",
        root=tmp_path,
        connector_id="feishu-prod",
        source_id="refund-prd",
        source_type="prd",
        content=content,
        remote_resource_id="doc-refund",
        actor=_connector_actor(),
        filename="退款规则.md",
    )
    uploaded = ingest_enterprise_knowledge_documents(
        "enterprise-project",
        [
            {
                "text": content,
                "filename": "退款规则-人工导出.md",
                "source_type": "prd",
                "external_ref": "upload://manual/refund-prd",
            }
        ],
        root=tmp_path,
        actor={"name": "qa-owner", "role": "qa_lead"},
    )

    assert uploaded["errors"] == []
    assert len(uploaded["source_occurrences"]) == 1
    assert uploaded["duplicate_source_occurrences"] == []
    assert (
        uploaded["source_occurrences"][0]["content_asset_id"]
        == online["source_occurrence"]["content_asset_id"]
    )

    inventory = list_enterprise_knowledge_sources(
        "enterprise-project",
        root=tmp_path,
    )
    assert inventory["summary"]["active_source_count"] == 2
    assert inventory["summary"]["content_asset_count"] == 1
    assert {row["source_ref"] for row in inventory["sources"]} == {
        online["source_ref"],
        "upload://manual/refund-prd",
    }


def test_connector_snapshot_rejects_credentials_and_untyped_binary(tmp_path):
    with pytest.raises(
        ConnectorSnapshotError,
        match="connector_snapshot_contains_credential",
    ):
        ingest_connector_snapshot(
            "enterprise-project",
            root=tmp_path,
            connector_id="docs-sync",
            source_id="api-contract",
            source_type="openapi",
            content="Authorization: Bearer supersecretvalue12345",
            external_ref="document-ref-42",
            actor=_connector_actor(),
        )

    with pytest.raises(
        ConnectorSnapshotError,
        match="connector_snapshot_filename_required_for_binary",
    ):
        ingest_connector_snapshot(
            "enterprise-project",
            root=tmp_path,
            connector_id="docs-sync",
            source_id="binary-document",
            source_type="other_document",
            content=b"binary-content",
            remote_resource_id="binary-42",
            actor=_connector_actor(),
        )


def test_connector_source_ref_is_connector_scoped_and_path_safe():
    assert build_connector_source_ref(
        "feishu-prod",
        "folder/doc 42",
        resource_kind="wiki-page",
    ) == "connector://feishu-prod/wiki-page/folder%2Fdoc%2042"
