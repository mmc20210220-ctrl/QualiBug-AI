from __future__ import annotations

import json
import urllib.parse

import pytest

from ai_test_asset_center.connector_materialization_capability import (
    ResourceDisposition,
)
from ai_test_asset_center.connector_sync_authority import (
    load_connector_sync_run,
    register_connector_instance,
)
from ai_test_asset_center.enterprise_knowledge_center import (
    list_enterprise_knowledge_sources,
)
from ai_test_asset_center.feishu_connector_adapter import (
    FeishuConnectorError,
    FeishuHttpResponse,
)
from ai_test_asset_center.feishu_connector_capability_sync import (
    classify_feishu_resource,
    sync_feishu_connector,
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


def _register(tmp_path) -> None:
    register_connector_instance(
        PROJECT,
        root=tmp_path,
        connector_instance_id=CONNECTOR,
        connector_type="feishu",
        resource_scope="wiki-space:space1",
        connection_profile_ref=PROFILE_REF,
        actor=ACTOR,
    )


def _resolver(_: str) -> dict[str, str]:
    return {"auth_mode": "tenant_access_token", "tenant_access_token": TOKEN}


def _mixed_transport():
    calls: list[tuple[str, str]] = []

    def transport(method, url, headers, body, timeout, max_bytes):
        calls.append((method, url))
        path = urllib.parse.urlsplit(url).path
        if path.endswith("/wiki/v2/spaces/space1/nodes"):
            return _json_response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "items": [
                            {
                                "space_id": "space1",
                                "node_token": "doc-node",
                                "obj_token": "doc-token",
                                "obj_type": "docx",
                                "title": "订单需求",
                                "has_child": False,
                                "obj_edit_time": "1720000000",
                            },
                            {
                                "space_id": "space1",
                                "node_token": "mind-node",
                                "obj_token": "mind-token",
                                "obj_type": "mindnote",
                                "title": "订单流程脑图",
                                "has_child": False,
                                "obj_edit_time": "1720000001",
                            },
                        ],
                        "has_more": False,
                    },
                }
            )
        if path.endswith("/drive/v1/export_tasks") and method == "POST":
            request = json.loads(body.decode("utf-8"))
            assert request["token"] == "doc-token"
            return _json_response({"code": 1069902, "msg": "no permission"}, 403)
        if path.endswith("/docx/v1/documents/doc-token/raw_content"):
            return _json_response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {"content": "# 订单需求\n订单只能由所属租户查看。"},
                }
            )
        raise AssertionError(f"unexpected request: {method} {url}")

    return transport, calls


def _single_node_transport(obj_type: str, revision: str):
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
                                "space_id": "space1",
                                "node_token": "shared-node",
                                "obj_token": "shared-token",
                                "obj_type": obj_type,
                                "title": "订单生命周期",
                                "has_child": False,
                                "obj_edit_time": revision,
                            }
                        ],
                        "has_more": False,
                    },
                }
            )
        if path.endswith("/drive/v1/export_tasks") and method == "POST":
            return _json_response({"code": 1069902, "msg": "no permission"}, 403)
        if path.endswith("/docx/v1/documents/shared-token/raw_content"):
            return _json_response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {"content": "# 订单生命周期\n创建、支付、取消。"},
                }
            )
        raise AssertionError(f"unexpected request: {method} {url}")

    return transport


def test_classifier_separates_supported_and_observable_unsupported() -> None:
    supported = classify_feishu_resource(
        {
            "remote_resource_id": "wiki:space1:doc-node",
            "resource_kind": "feishu-wiki-docx",
            "obj_type": "docx",
        }
    )
    unsupported = classify_feishu_resource(
        {
            "remote_resource_id": "wiki:space1:mind-node",
            "resource_kind": "feishu-wiki-mindnote",
            "obj_type": "mindnote",
        }
    )

    assert supported.disposition is ResourceDisposition.MATERIALIZABLE
    assert unsupported.disposition is ResourceDisposition.OBSERVABLE_UNSUPPORTED
    assert unsupported.reason_code == "FEISHU_OBJECT_TYPE_UNSUPPORTED"
    assert unsupported.retry_trigger == "ADAPTER_CAPABILITY_CHANGE"


def test_mixed_supported_and_unsupported_resources_complete_truthfully(tmp_path) -> None:
    _register(tmp_path)
    transport, calls = _mixed_transport()

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
    assert receipt["remote_discovery_complete"] is True
    assert receipt["supported_materialization_complete"] is True
    assert receipt["knowledge_coverage_complete"] is False
    assert receipt["knowledge_coverage_status"] == "PARTIAL_UNSUPPORTED"
    assert receipt["discovered_resource_count"] == 2
    assert receipt["materialized_resource_count"] == 1
    assert receipt["unchanged_resource_count"] == 0
    assert receipt["unsupported_resource_count"] == 1
    assert receipt["coverage_observation_count"] == 1
    assert receipt["known_resource_count"] == 2
    assert receipt["unknown_gap_count"] == 0
    assert receipt["knowledge_coverage_ratio"] == 0.5
    assert receipt["customer_material_mutation_executed"] is False

    unsupported = receipt["unsupported_resources"][0]
    assert unsupported["remote_resource_id"] == "wiki:space1:mind-node"
    assert unsupported["remote_object_type"] == "mindnote"
    assert unsupported["reason_code"] == "FEISHU_OBJECT_TYPE_UNSUPPORTED"
    assert unsupported["content_materialized"] is False
    assert unsupported["source_occurrence_created"] is False
    assert "content" not in unsupported

    persisted = load_connector_sync_run(
        PROJECT,
        connector_instance_id=CONNECTOR,
        sync_epoch_id=receipt["sync_epoch_id"],
        root=tmp_path,
    )
    assert persisted["coverage_observation_count"] == 1
    assert persisted["knowledge_coverage_status"] == "PARTIAL_UNSUPPORTED"
    assert persisted["coverage_observations_create_source_occurrences"] is False
    assert persisted["customer_material_mutation_executed"] is False
    coverage = persisted["coverage_observations"][0]
    assert coverage["reason_code"] == "FEISHU_OBJECT_TYPE_UNSUPPORTED"
    assert coverage["content_materialized"] is False
    assert coverage["source_occurrence_created"] is False
    assert "content" not in coverage

    inventory = list_enterprise_knowledge_sources(PROJECT, root=tmp_path)
    assert inventory["summary"]["active_source_count"] == 1
    assert all("mind-token" not in url for _, url in calls)


def test_all_unsupported_resources_create_no_fake_source_occurrence(tmp_path) -> None:
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
                                "space_id": "space1",
                                "node_token": "mind-node",
                                "obj_token": "mind-token",
                                "obj_type": "mindnote",
                                "title": "订单流程脑图",
                                "has_child": False,
                            }
                        ],
                        "has_more": False,
                    },
                }
            )
        raise AssertionError(f"unsupported resource must not materialize: {method} {url}")

    receipt = sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=_resolver,
        root=tmp_path,
        actor=ACTOR,
        transport=transport,
        sleeper=lambda _: None,
    )

    assert receipt["status"] == "COMPLETE"
    assert receipt["discovered_resource_count"] == 1
    assert receipt["covered_resource_count"] == 0
    assert receipt["unsupported_resource_count"] == 1
    assert receipt["coverage_observation_count"] == 1
    assert receipt["knowledge_coverage_ratio"] == 0.0
    inventory = list_enterprise_knowledge_sources(PROJECT, root=tmp_path)
    assert inventory["summary"]["active_source_count"] == 0


def test_existing_source_becoming_unsupported_is_preserved_from_retirement(tmp_path) -> None:
    _register(tmp_path)

    first = sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=_resolver,
        root=tmp_path,
        actor=ACTOR,
        allow_raw_text_fallback=True,
        transport=_single_node_transport("docx", "17"),
        sleeper=lambda _: None,
    )
    before = list_enterprise_knowledge_sources(PROJECT, root=tmp_path)
    assert before["summary"]["active_source_count"] == 1
    original_source_ref = before["sources"][0]["source_ref"]

    second = sync_feishu_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        resolve_connection_profile=_resolver,
        root=tmp_path,
        actor=ACTOR,
        previous_cursor=first["next_cursor"],
        deletion_policy="RETIRE_MISSING",
        allow_raw_text_fallback=True,
        transport=_single_node_transport("mindnote", "18"),
        sleeper=lambda _: None,
    )

    assert second["status"] == "COMPLETE"
    assert second["unsupported_resource_count"] == 1
    assert second["preserved_unsupported_occurrence_count"] == 1
    assert second["coverage_existing_occurrence_recorded_count"] == 1
    assert second["deletion_reconciliation"]["missing_count"] == 0
    assert second["retired_count"] == 0
    assert second["unsupported_resources"][0]["freshness"] == "STALE_UNSUPPORTED"

    after = list_enterprise_knowledge_sources(PROJECT, root=tmp_path)
    assert after["summary"]["active_source_count"] == 1
    assert after["sources"][0]["source_ref"] == original_source_ref


def test_supported_export_failure_remains_fatal_and_is_not_isolated(tmp_path) -> None:
    _register(tmp_path)
    transport, _ = _mixed_transport()

    with pytest.raises(FeishuConnectorError, match="feishu_api_failed"):
        sync_feishu_connector(
            PROJECT,
            connector_instance_id=CONNECTOR,
            resolve_connection_profile=_resolver,
            root=tmp_path,
            actor=ACTOR,
            allow_raw_text_fallback=False,
            transport=transport,
            sleeper=lambda _: None,
        )

    inventory = list_enterprise_knowledge_sources(PROJECT, root=tmp_path)
    assert inventory["summary"]["active_source_count"] == 0
