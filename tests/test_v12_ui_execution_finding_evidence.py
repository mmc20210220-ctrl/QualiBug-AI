from __future__ import annotations

from ai_test_asset_center.__main__ import _ui_verification_context
from ai_test_asset_center.v12_pipeline import _ui_bridge_finding, _ui_execution_status_finding


def test_ui_bridge_finding_normalizes_created_data_and_artifact_refs() -> None:
    finding = _ui_bridge_finding(
        {"title": "UI 订单状态异常"},
        request_result={
            "request_id": "ui_req_1",
            "provider": "page_agent",
            "bridge_provider": "page_agent_browser_plan",
            "status": "executed",
            "current_url": "http://127.0.0.1:8080/orders/ord_123",
            "artifacts": [
                {"artifact_type": "trace", "ref": "platform_workspace/demo/page_agent_runs/ui_req_1/trace.zip"},
                {"artifact_type": "screenshot", "ref": "platform_workspace/demo/page_agent_runs/ui_req_1/final.png"},
            ],
            "created_data": {
                "entity": "order",
                "id": "ord_123",
            },
        },
        campaign_id="camp_ui",
        discovery_round=1,
    )

    raw = finding["raw_evidence"]
    assert raw["created_data"]["object_type"] == "order"
    assert raw["created_data"]["object_id"] == "ord_123"
    assert raw["created_data"]["data_scope_ref"] == "order:ord_123"
    assert raw["created_data"]["object_url"] == "http://127.0.0.1:8080/orders/ord_123"
    assert raw["ui_execution_result"]["bridge_provider"] == "page_agent_browser_plan"
    assert raw["ui_execution_result"]["artifact_refs"] == [
        "platform_workspace/demo/page_agent_runs/ui_req_1/trace.zip",
        "platform_workspace/demo/page_agent_runs/ui_req_1/final.png",
    ]
    assert raw["ui_execution_result"]["artifact_types"] == ["trace", "screenshot"]

    context = _ui_verification_context(finding)
    assert context["object_type"] == "order"
    assert context["object_id"] == "ord_123"
    assert context["data_scope_ref"] == "order:ord_123"


def test_ui_execution_status_finding_preserves_page_agent_evidence_for_failed_result() -> None:
    finding = _ui_execution_status_finding(
        {
            "request_id": "ui_req_2",
            "title": "创建订单表单提交流程",
            "provider": "page_agent",
            "bridge_provider": "page_agent_browser_plan",
            "status": "failed",
            "reason": "assertion_failed",
            "current_url": "http://127.0.0.1:8080/orders/new",
            "artifacts": [{"artifact_type": "screenshot", "ref": "platform_workspace/demo/page_agent_runs/ui_req_2/final.png"}],
            "created_data": {"object_type": "order", "object_id": "ord_failed"},
        },
        campaign_id="camp_ui",
        discovery_round=2,
    )

    raw = finding["raw_evidence"]
    assert raw["has_real_evidence"] is True
    assert raw["ui_execution_result"]["status"] == "failed"
    assert raw["ui_execution_result"]["reason"] == "assertion_failed"
    assert raw["ui_execution_result"]["artifact_refs"] == [
        "platform_workspace/demo/page_agent_runs/ui_req_2/final.png"
    ]
    assert raw["created_data"]["object_type"] == "order"
    assert raw["created_data"]["object_id"] == "ord_failed"
