from __future__ import annotations

import json
import urllib.parse

import pytest

from ai_test_asset_center.connector_sync_authority import register_connector_instance
from ai_test_asset_center.enterprise_knowledge_center import list_enterprise_knowledge_sources
from ai_test_asset_center.feishu_connector_adapter import (
    FeishuConnectorError,
    FeishuHttpResponse,
    discover_feishu_wiki_resources,
    materialize_feishu_resource,
    sync_feishu_connector,
    test_feishu_connector_connection as check_feishu_connector_connection,
)

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
ACTOR = {"name": "qa-owner", "role": "qa_lead"}
TOKEN = "tenant-access-token-value-123456"
PROFILE_REF = "vault-ref://connectors/feishu-prod"


def _json_response(payload: dict, status: int = 200) -> FeishuHttpResponse:
    return FeishuHttpResponse(
        status=status,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


def _register(tmp_path, scope: str = "wiki-space:space1") -> None:
    register_connector_instance(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        connector_type="feishu",
        resource_scope=scope,
        connection_profile_ref=PROFILE_REF,
        actor=ACTOR,
    )


def _resolver(_: str) -> dict[str, str]:
    return {"auth_mode": "tenant_access_token", "tenant_access_token": TOKEN}


def _single_doc_transport(*, export_error: bool = True):
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def transport(method, url, headers, body, timeout, max_bytes):
        calls.append((method, url, dict(headers), body))
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path
        if path.endswith("/wiki/v2/spaces/space1/nodes"):
            return _json_response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "items": [
                            {
                                "space_id": "space1",
                                "node_token": "node1",
                                "obj_token": "docx1token",
                                "obj_type": "docx",
                                "parent_node_token": "",
                                "title": "订单需求",
                                "has_child": False,
                                "obj_edit_time": "1720000000",
                            }
                        ],
                        "has_more": False,
                        "page_token": "",
                    },
                }
            )
        if path.endswith("/drive/v1/export_tasks") and method == "POST":
            if export_error:
                return _json_response({"code": 1069902, "msg": "no permission"}, 403)
            return _json_response(
                {"code": 0, "msg": "success", "data": {"ticket": "ticket1"}}
            )
        if path.endswith("/drive/v1/export_tasks/ticket1"):
            return _json_response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "result": {
                            "file_token": "filetoken1",
                            "file_name": "订单需求",
                            "file_extension": "docx",
                            "job_status": 0,
                            "job_error_msg": "success",
                        }
                    },
                }
            )
        if path.endswith("/drive/v1/export_tasks/file/filetoken1/download"):
            return FeishuHttpResponse(
                status=200,
                headers={
                    "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "Content-Disposition": "attachment; filename=order.docx",
                },
                body=b"official-export-bytes",
            )
        if path.endswith("/docx/v1/documents/docx1token/raw_content"):
            return _json_response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {"content": "# 订单需求\n订单只能由所属租户查看。"},
                }
            )
        raise AssertionError(f"unexpected request: {method} {url}")

    return transport, calls


def test_connection_resolves_internal_app_secret_only_in_memory(tmp_path):
    _register(tmp_path)
    calls = []

    def transport(method, url, headers, body, timeout, max_bytes):
        calls.append((method, url, dict(headers), body))
        path = urllib.parse.urlsplit(url).path
        if path.endswith("/auth/v3/tenant_access_token/internal"):
            request = json.loads(body.decode("utf-8"))
            assert request == {"app_id": "cli_app_123", "app_secret": "secret-value-123"}
            return _json_response(
                {"code": 0, "msg": "ok", "tenant_access_token": TOKEN, "expire": 7200}
            )
        if path.endswith("/wiki/v2/spaces/space1/nodes"):
            assert headers["Authorization"] == f"Bearer {TOKEN}"
            return _json_response(
                {"code": 0, "msg": "success", "data": {"items": [], "has_more": False}}
            )
        raise AssertionError(url)

    receipt = check_feishu_connector_connection(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=lambda ref: {
            "auth_mode": "internal_app",
            "app_id": "cli_app_123",
            "app_secret": "secret-value-123",
        },
        root=tmp_path,
        transport=transport,
        sleeper=lambda _: None,
    )

    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert receipt["status"] == "AVAILABLE"
    assert receipt["auth_mode"] == "internal_app"
    assert "secret-value-123" not in serialized
    assert TOKEN not in serialized
    assert receipt["credentials_persisted"] is False
    assert receipt["access_token_persisted"] is False


def test_discovery_honors_empty_permission_page_and_recurses():
    calls: list[str] = []

    def transport(method, url, headers, body, timeout, max_bytes):
        calls.append(url)
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        parent = query.get("parent_node_token", [""])[0]
        page = query.get("page_token", [""])[0]
        if parent == "" and page == "":
            return _json_response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {"items": [], "has_more": True, "page_token": "page2"},
                }
            )
        if parent == "" and page == "page2":
            return _json_response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "items": [
                            {
                                "node_token": "parent1",
                                "obj_token": "docx-parent",
                                "obj_type": "docx",
                                "title": "父文档",
                                "has_child": True,
                                "obj_edit_time": "1",
                            }
                        ],
                        "has_more": False,
                    },
                }
            )
        if parent == "parent1":
            return _json_response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "items": [
                            {
                                "node_token": "child1",
                                "obj_token": "sheet-child",
                                "obj_type": "sheet",
                                "title": "子表格",
                                "parent_node_token": "parent1",
                                "has_child": False,
                                "obj_edit_time": "2",
                            }
                        ],
                        "has_more": False,
                    },
                }
            )
        raise AssertionError(url)

    rows = discover_feishu_wiki_resources(
        TOKEN,
        "wiki-space:space1",
        transport=transport,
        sleeper=lambda _: None,
    )

    assert [row["node_token"] for row in rows] == ["parent1", "child1"]
    assert rows[1]["parent_node_token"] == "parent1"
    assert len(calls) == 3


def test_official_export_chain_returns_original_bytes():
    transport, calls = _single_doc_transport(export_error=False)
    descriptor = {
        "remote_resource_id": "wiki:space1:node1",
        "resource_kind": "feishu-wiki-docx",
        "obj_token": "docx1token",
        "obj_type": "docx",
        "title": "订单需求",
        "remote_revision": "17",
        "remote_updated_at": "",
        "parent_node_token": "",
    }

    item = materialize_feishu_resource(
        descriptor,
        TOKEN,
        transport=transport,
        sleeper=lambda _: None,
    )

    assert item["content"] == b"official-export-bytes"
    assert item["filename"] == "order.docx"
    assert item["source_type"] == "feishu_document"
    assert item["export_format"] == "docx"
    assert item["adapter_degraded"] is False
    assert any("/export_tasks/file/filetoken1/download" in url for _, url, _, _ in calls)


def test_raw_text_fallback_is_explicit_and_receipted():
    descriptor = {
        "remote_resource_id": "wiki:space1:node1",
        "resource_kind": "feishu-wiki-docx",
        "obj_token": "docx1token",
        "obj_type": "docx",
        "title": "订单需求",
        "remote_revision": "17",
    }
    transport, _ = _single_doc_transport(export_error=True)

    with pytest.raises(FeishuConnectorError, match="feishu_api_failed"):
        materialize_feishu_resource(
            descriptor,
            TOKEN,
            transport=transport,
            sleeper=lambda _: None,
        )

    transport, _ = _single_doc_transport(export_error=True)
    item = materialize_feishu_resource(
        descriptor,
        TOKEN,
        transport=transport,
        allow_raw_text_fallback=True,
        sleeper=lambda _: None,
    )
    assert isinstance(item["content"], str)
    assert item["filename"].endswith(".txt")
    assert item["adapter_degraded"] is True
    assert item["degradation_reason"] == "OFFICIAL_EXPORT_FAILED_RAW_TEXT_FALLBACK"


def test_full_sync_uses_common_source_occurrence_authority(tmp_path):
    _register(tmp_path)
    transport, calls = _single_doc_transport(export_error=True)

    receipt = sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=_resolver,
        root=tmp_path,
        actor=ACTOR,
        allow_raw_text_fallback=True,
        transport=transport,
        sleeper=lambda _: None,
    )

    assert receipt["status"] == "COMPLETE"
    assert receipt["discovered_resource_count"] == 1
    assert receipt["materialized_resource_count"] == 1
    assert receipt["degraded_resource_count"] == 1
    assert receipt["next_cursor"].startswith("feishu-snapshot-v1:")
    assert receipt["next_cursor_persisted_by_adapter"] is False
    assert receipt["connector_parser_implemented"] is False

    inventory = list_enterprise_knowledge_sources(PROJECT, root=tmp_path)
    assert inventory["summary"]["active_source_count"] == 1
    source = inventory["sources"][0]
    assert source["source_ref"].startswith("connector://feishu-prod/feishu-wiki-docx/")
    assert source["source_type"] == "feishu_document"

    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in tmp_path.rglob("*.json")
    )
    assert TOKEN not in persisted
    assert "source_content_persisted_in_adapter_receipt" not in persisted
    assert any(headers.get("Authorization") == f"Bearer {TOKEN}" for _, _, headers, _ in calls)


def test_unsupported_feishu_type_blocks_before_ingestion(tmp_path):
    _register(tmp_path)

    def transport(method, url, headers, body, timeout, max_bytes):
        path = urllib.parse.urlsplit(url).path
        if path.endswith("/wiki/v2/spaces/space1/nodes"):
            return _json_response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "items": [
                            {
                                "node_token": "mind1",
                                "obj_token": "mind-token",
                                "obj_type": "mindnote",
                                "title": "脑图",
                                "has_child": False,
                            }
                        ],
                        "has_more": False,
                    },
                }
            )
        raise AssertionError(url)

    with pytest.raises(FeishuConnectorError, match="object_type_unsupported:mindnote"):
        sync_feishu_connector(
            PROJECT,
            connector_instance_id=CONNECTOR,
            resolve_connection_profile=_resolver,
            root=tmp_path,
            actor=ACTOR,
            transport=transport,
            sleeper=lambda _: None,
        )

    inventory = list_enterprise_knowledge_sources(PROJECT, root=tmp_path)
    assert inventory["summary"]["active_source_count"] == 0


def test_committed_snapshot_requires_previous_cursor_before_network(tmp_path):
    _register(tmp_path)
    transport, _ = _single_doc_transport(export_error=True)
    first = sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=_resolver,
        root=tmp_path,
        actor=ACTOR,
        allow_raw_text_fallback=True,
        transport=transport,
        sleeper=lambda _: None,
    )
    assert first["next_cursor"]

    called = False

    def resolver(_: str):
        nonlocal called
        called = True
        return _resolver("")

    with pytest.raises(FeishuConnectorError, match="previous_cursor_required"):
        sync_feishu_connector(
            PROJECT,
            connector_instance_id=CONNECTOR,
            resolve_connection_profile=resolver,
            root=tmp_path,
            actor=ACTOR,
            transport=lambda *args: (_ for _ in ()).throw(AssertionError("network called")),
        )
    assert called is False


def test_rate_limit_response_retries_without_leaking_token():
    attempts = 0
    sleeps = []

    def transport(method, url, headers, body, timeout, max_bytes):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _json_response({"code": 99991400, "msg": "rate limited"}, 400)
        return _json_response(
            {"code": 0, "msg": "success", "data": {"items": [], "has_more": False}}
        )

    rows = discover_feishu_wiki_resources(
        TOKEN,
        "wiki-space:space1",
        transport=transport,
        sleeper=sleeps.append,
    )
    assert rows == []
    assert attempts == 2
    assert sleeps


def _descriptor(index: int, revision: str = "1") -> dict:
    return {
        "space_id": "space1",
        "node_token": f"node{index}",
        "obj_token": f"docx{index}",
        "obj_type": "docx",
        "title": f"Document {index}",
        "parent_node_token": "",
        "has_child": False,
        "remote_revision": revision,
        "remote_updated_at": revision,
        "remote_resource_id": f"wiki:space1:node{index}",
        "resource_kind": "feishu-wiki-docx",
    }


def _materialized(descriptor: dict) -> dict:
    token = descriptor["node_token"]
    return {
        "remote_resource_id": descriptor["remote_resource_id"],
        "resource_kind": descriptor["resource_kind"],
        "source_type": "feishu_document",
        "content": f"# {token}\ncontent for {token}",
        "filename": f"{token}.txt",
        "remote_revision": descriptor["remote_revision"],
        "remote_updated_at": descriptor["remote_updated_at"],
        "parent_remote_id": descriptor["parent_node_token"],
        "export_format": "txt",
        "declared_mime": "text/plain",
        "adapter_degraded": False,
        "degradation_reason": "",
    }


def test_unchanged_snapshot_skips_all_exports_and_batch_touches_occurrences(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    _register(tmp_path)
    descriptors = [_descriptor(index) for index in range(3)]
    exports: list[str] = []

    monkeypatch.setattr(
        "ai_test_asset_center.feishu_connector_adapter.discover_feishu_wiki_resources",
        lambda *args, **kwargs: [dict(row) for row in descriptors],
    )

    def materialize(row, *args, **kwargs):
        exports.append(row["remote_resource_id"])
        return _materialized(row)

    monkeypatch.setattr(
        "ai_test_asset_center.feishu_connector_adapter.materialize_feishu_resource",
        materialize,
    )

    first = sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=_resolver,
        root=tmp_path,
        actor=ACTOR,
        sleeper=lambda _: None,
    )
    second = sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=_resolver,
        root=tmp_path,
        actor=ACTOR,
        previous_cursor=first["next_cursor"],
        sleeper=lambda _: None,
    )

    assert len(exports) == 3
    assert second["status"] == "COMPLETE"
    assert second["materialized_resource_count"] == 0
    assert second["unchanged_resource_count"] == 3
    assert second["export_avoided_count"] == 3
    assert second["success_count"] == 3
    assert second["materialized_success_count"] == 0
    assert second["unchanged_success_count"] == 3

    from ai_test_asset_center.enterprise_knowledge_center.source_occurrence_observation import (
        list_source_occurrence_observations,
    )

    observations = list_source_occurrence_observations(
        PROJECT,
        source_ref_prefix="connector://feishu-prod/",
        root=tmp_path,
    )
    assert observations["source_occurrence_count"] == 3
    # Each sync records a source-occurrence observation (materialized or unchanged)
    # plus an ACL-visibility observation, so each of the 3 occurrences is observed 4
    # times across the two syncs.
    assert {row["observation_count"] for row in observations["source_occurrences"]} == {4}


def test_single_revision_change_exports_only_changed_and_retires_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    _register(tmp_path)
    descriptors = [_descriptor(index) for index in range(3)]
    exports: list[str] = []

    monkeypatch.setattr(
        "ai_test_asset_center.feishu_connector_adapter.discover_feishu_wiki_resources",
        lambda *args, **kwargs: [dict(row) for row in descriptors],
    )

    def materialize(row, *args, **kwargs):
        exports.append(row["remote_resource_id"])
        return _materialized(row)

    monkeypatch.setattr(
        "ai_test_asset_center.feishu_connector_adapter.materialize_feishu_resource",
        materialize,
    )

    first = sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=_resolver,
        root=tmp_path,
        actor=ACTOR,
        sleeper=lambda _: None,
    )
    descriptors[1] = _descriptor(1, revision="2")
    descriptors.pop(2)

    second = sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=_resolver,
        root=tmp_path,
        actor=ACTOR,
        previous_cursor=first["next_cursor"],
        deletion_policy="RETIRE_MISSING",
        max_retire_count=10,
        max_retire_ratio=1.0,
        sleeper=lambda _: None,
    )

    assert exports == [
        "wiki:space1:node0",
        "wiki:space1:node1",
        "wiki:space1:node2",
        "wiki:space1:node1",
    ]
    assert second["status"] == "COMPLETE"
    assert second["materialized_resource_count"] == 1
    assert second["unchanged_resource_count"] == 1
    assert second["success_count"] == 2
    assert second["retired_count"] == 1
    assert second["deletion_reconciliation"]["status"] == "COMPLETE"

    inventory = list_enterprise_knowledge_sources(PROJECT, root=tmp_path)
    active_refs = {
        row["source_ref"]
        for row in inventory["sources"]
        if row.get("status") == "active"
    }
    assert len(active_refs) == 2
    assert all("node2" not in source_ref for source_ref in active_refs)
