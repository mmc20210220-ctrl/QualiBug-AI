from __future__ import annotations

import json
import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "test-private-pilot-secret")

from ai_test_asset_center import enterprise_pilot_runtime as runtime
from ai_test_asset_center import private_pilot_service as service
from ai_test_asset_center.__main__ import scan
from ai_test_asset_center.enterprise_source_registry import register_source_asset
from ai_test_asset_center.execution_approvals import verify_execution_approval
from ai_test_asset_center.ssrf_guard import SsrfBlockedError


API_SPEC = '{"openapi":"3.0.0","paths":{"/api/orders":{"get":{"responses":{"200":{"description":"ok"}}}}}}'


def test_prepare_v12_scan_body_loads_registered_source_and_runtime_defaults(tmp_path, monkeypatch):
    manifest = register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    monkeypatch.setenv("QUALIBUG_SCOPE_ID", "checkout-scope")
    monkeypatch.setenv("QUALIBUG_ENVIRONMENT_REF", "staging-env")

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {"base_url": "http://127.0.0.1:8000"},
        local_dev_mode=False,
    )

    assert prepared["api_doc"] == API_SPEC
    assert prepared["source_manifest"]["source_id"] == manifest["source_id"]
    assert prepared["source_manifest"]["source_hash"] == manifest["source_hash"]
    assert prepared["scope_id"] == "checkout-scope"
    assert prepared["environment_ref"] == "staging-env"
    assert prepared["execution_mode"] == "approved_sandbox_write"
    assert prepared["test_data_contract"] == {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "checkout-scope",
    }
    assert "execution_approval_id" not in prepared


def test_prepare_v12_scan_body_auto_issues_local_runtime_approval_for_non_production(tmp_path):
    manifest = register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "api_doc": API_SPEC,
            "base_url": "http://127.0.0.1:8000",
            "scope_id": "checkout-scope",
            "environment_ref": "staging-env",
            "source_manifest": manifest,
        },
        local_dev_mode=True,
    )

    approval_id = prepared.get("execution_approval_id", "")
    binding = service._predicted_campaign_binding("demo", tmp_path, prepared)

    assert approval_id.startswith("eap_")
    verdict = verify_execution_approval(
        "demo",
        approval_id,
        root=tmp_path,
        campaign_id=binding["campaign_id"],
        scope_id=binding["scope_id"],
        environment_ref=binding["environment_ref"],
        source_hash=binding["source_hash"],
        target_base_url=prepared["base_url"],
        execution_mode=prepared["execution_mode"],
    )
    assert verdict["valid"] is True
    assert prepared["execution_mode"] == "approved_sandbox_write"
    assert prepared["test_data_contract"] == {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "checkout-scope",
    }


def test_prepare_v12_scan_body_promotes_runtime_defaults_after_base_url_is_backfilled(tmp_path):
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    registry_path = tmp_path / "platform_workspace" / "demo" / "enterprise_pilot_runtime" / "connector_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "test_profile": {
                    "scope_id": "checkout-scope",
                    "environment_ref": "local-benchmark",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    first_pass = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {},
        local_dev_mode=False,
    )
    assert "execution_mode" not in first_pass
    assert "test_data_contract" not in first_pass

    first_pass["base_url"] = "http://127.0.0.1:8080"
    second_pass = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        first_pass,
        local_dev_mode=False,
    )

    assert second_pass["execution_mode"] == "approved_sandbox_write"
    assert second_pass["test_data_contract"] == {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "checkout-scope",
    }


def test_predicted_campaign_binding_matches_scan_campaign_for_markdown_api(tmp_path):
    api_markdown = """# API

### GET /api/orders

### GET /api/orders/:id
"""
    manifest = register_source_asset("demo", "api-contract", api_markdown, source_type="openapi", root=tmp_path)
    input_dir = tmp_path / "platform_workspace" / "demo" / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "PRD.md").write_text("订单必须处于 `PENDING_PAYMENT` 状态。", encoding="utf-8")
    (input_dir / "schema.sql").write_text(
        "CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT CHECK (status IN ('PENDING_PAYMENT','PAID')));",
        encoding="utf-8",
    )

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "api_doc": api_markdown,
            "base_url": "http://127.0.0.1:8000",
            "scope_id": "checkout-scope",
            "environment_ref": "staging-env",
            "source_manifest": manifest,
        },
        local_dev_mode=True,
    )

    binding = service._predicted_campaign_binding("demo", tmp_path, prepared)
    result = scan(
        "demo",
        root=tmp_path,
        prd_text=str(prepared.get("prd") or ""),
        api_doc_text=str(prepared.get("api_doc") or ""),
        base_url=str(prepared.get("base_url") or ""),
        save_report=False,
        campaign_context={
            "scope_id": prepared["scope_id"],
            "environment_ref": prepared["environment_ref"],
            "execution_approval_id": prepared["execution_approval_id"],
            "execution_mode": prepared["execution_mode"],
            "source_manifest": prepared["source_manifest"],
        },
    )

    assert binding["campaign_id"] == result["campaign"]["campaign_id"]


def test_prepare_v12_scan_body_backfills_prd_from_project_input(tmp_path):
    input_dir = tmp_path / "platform_workspace" / "demo" / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "PRD.md").write_text("Orders must not be payable after cancellation.", encoding="utf-8")
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {"base_url": "http://127.0.0.1:8000"},
        local_dev_mode=False,
    )

    assert prepared["prd"] == "Orders must not be payable after cancellation."


def test_load_connector_registry_preserves_test_profile(tmp_path):
    registry_path = tmp_path / "platform_workspace" / "demo" / "enterprise_pilot_runtime" / "connector_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "connectors": [{"connector_id": "gateway", "enabled": True, "endpoint_ref": "http://localhost:8080"}],
                "test_profile": {
                    "api_base_url": "http://localhost:8080",
                    "test_credentials": {"buyer": {"email": "buyer01@example.com", "password": "Test@123456"}},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    loaded = runtime.load_connector_registry("demo", tmp_path)

    assert loaded["connectors"][0]["connector_id"] == "gateway"
    assert loaded["test_profile"]["api_base_url"] == "http://localhost:8080"
    assert loaded["test_profile"]["test_credentials"]["buyer"]["email"] == "buyer01@example.com"


def test_prepare_v12_scan_body_uses_connector_registry_runtime_defaults(tmp_path):
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    registry_path = tmp_path / "platform_workspace" / "demo" / "enterprise_pilot_runtime" / "connector_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "connectors": [{"connector_id": "gateway", "enabled": True, "endpoint_ref": "http://127.0.0.1:8080"}],
                "test_profile": {
                    "api_base_url": "http://127.0.0.1:8080",
                    "scope_id": "benchmark-mall-checkout",
                    "environment_ref": "local-benchmark",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {"base_url": "http://127.0.0.1:8080"},
        local_dev_mode=False,
    )

    assert prepared["scope_id"] == "benchmark-mall-checkout"
    assert prepared["environment_ref"] == "local-benchmark"
    assert prepared["execution_mode"] == "approved_sandbox_write"
    assert prepared["test_data_contract"] == {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "benchmark-mall-checkout",
    }


def test_prepare_v12_scan_body_keeps_production_like_targets_read_only(tmp_path):
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "base_url": "http://127.0.0.1:8080",
            "scope_id": "checkout-scope",
            "environment_ref": "prod",
        },
        local_dev_mode=False,
    )

    assert prepared["execution_mode"] == "safe_read_only"
    assert "test_data_contract" not in prepared


def test_prepare_v12_scan_body_auto_generates_page_agent_ui_observation_request_when_bridge_is_configured(tmp_path, monkeypatch):
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    monkeypatch.setenv("QUALIBUG_PAGE_AGENT_BRIDGE_URL", "http://127.0.0.1:8797/execute")

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "base_url": "http://127.0.0.1:8080",
            "scope_id": "checkout-scope",
            "environment_ref": "local-benchmark",
        },
        local_dev_mode=False,
    )

    request = prepared["ui_execution_requests"][0]
    assert request["provider"] == "page_agent"
    assert request["start_url"] == "http://127.0.0.1:8080"
    assert request["execution_mode"] == "safe_read_only"
    assert request["metadata"]["bridge_mode"] == "page_agent_browser_plan"
    assert request["metadata"]["auto_generated"] is True
    assert request["browser_plan"]["steps"][0]["action"] == "goto"
    assert request["browser_plan"]["steps"][0]["url"] == "http://127.0.0.1:8080"
    assert "checkout-scope" in request["page_hints"][0]


def test_prepare_v12_scan_body_prefers_followup_ui_execution_requests_asset_when_present(tmp_path, monkeypatch):
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    monkeypatch.setenv("QUALIBUG_PAGE_AGENT_BRIDGE_URL", "http://127.0.0.1:8797/execute")
    asset_path = tmp_path / "platform_workspace" / "demo" / "defect_discovery" / "ui_followup_execution_requests.json"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text(
        json.dumps(
            {
                "version": "ui_followup_execution_requests_v1",
                "project_id": "demo",
                "items": [
                    {
                        "request_template_id": "UIFOLLOW_1",
                        "title": "复现场景：订单详情页异常",
                        "severity": "P1",
                        "path": "/orders/123",
                        "task": "Re-open order details and collect deterministic UI evidence.",
                        "page_hints": ["候选路径：/orders/123"],
                        "browser_plan": {
                            "execution_mode": "safe_read_only",
                            "steps": [{"action": "goto", "url": "/orders/123", "wait_until": "networkidle"}],
                        },
                        "metadata": {
                            "bridge_mode": "page_agent_browser_plan",
                            "verification": {
                                "kind": "http_get",
                                "path": "/orders/{object_id}",
                                "expected_statuses": [200],
                                "body_contains": "{object_id}",
                            },
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "base_url": "http://127.0.0.1:8080",
            "scope_id": "checkout-scope",
            "environment_ref": "local-benchmark",
        },
        local_dev_mode=False,
    )

    assert len(prepared["ui_execution_requests"]) == 1
    request = prepared["ui_execution_requests"][0]
    assert request["request_id"] == "UIFOLLOW_1"
    assert request["start_url"] == "http://127.0.0.1:8080/orders/123"
    assert request["metadata"]["request_origin"] == "private_pilot_service_followup_asset"
    assert request["metadata"]["verification"]["kind"] == "http_get"
    assert request["metadata"]["verification"]["path"] == "/orders/{object_id}"
    assert request["metadata"]["verification"]["body_contains"] == "{object_id}"
    assert request["browser_plan"]["steps"][0]["url"] == "/orders/123"


def test_prepare_v12_scan_body_loads_only_executable_followup_ui_test_data_requests(tmp_path, monkeypatch):
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    monkeypatch.setenv("QUALIBUG_PAGE_AGENT_BRIDGE_URL", "http://127.0.0.1:8797/execute")
    asset_path = tmp_path / "platform_workspace" / "demo" / "defect_discovery" / "ui_followup_test_data_requests.json"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text(
        json.dumps(
            {
                "version": "ui_followup_test_data_requests_v1",
                "project_id": "demo",
                "items": [
                    {
                        "request_template_id": "UITESTDATA_EXEC",
                        "title": "可执行 UI 造数补位",
                        "path": "/orders/new",
                        "task": "Create disposable order data via UI",
                        "browser_plan": {
                            "steps": [
                                {"action": "goto", "url": "/orders/new", "wait_until": "networkidle"},
                                {"action": "screenshot", "full_page": True},
                            ]
                        },
                        "metadata": {"bridge_mode": "page_agent_browser_plan", "executable": True},
                    },
                    {
                        "request_template_id": "UITESTDATA_PLAN_ONLY",
                        "title": "仅计划 UI 造数补位",
                        "path": "/orders/new",
                        "task": "Create disposable order data via UI",
                        "browser_plan": {},
                        "metadata": {"bridge_mode": "page_agent_browser_plan", "executable": False},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "base_url": "http://127.0.0.1:8080",
            "scope_id": "checkout-scope",
            "environment_ref": "local-benchmark",
        },
        local_dev_mode=False,
    )

    assert len(prepared["ui_test_data_requests"]) == 1
    request = prepared["ui_test_data_requests"][0]
    assert request["request_id"] == "UITESTDATA_EXEC"
    assert request["execution_mode"] == "approved_sandbox_write"
    assert request["start_url"] == "http://127.0.0.1:8080/orders/new"
    assert request["browser_plan"]["write_approved"] is True
    assert request["metadata"]["request_origin"] == "private_pilot_service_followup_test_data_asset"


def test_prepare_v12_scan_body_promotes_approved_followup_ui_test_data_request(tmp_path, monkeypatch):
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    monkeypatch.setenv("QUALIBUG_PAGE_AGENT_BRIDGE_URL", "http://127.0.0.1:8797/execute")
    asset_path = tmp_path / "platform_workspace" / "demo" / "defect_discovery" / "ui_followup_test_data_requests.json"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text(
        json.dumps(
            {
                "version": "ui_followup_test_data_requests_v1",
                "project_id": "demo",
                "items": [
                    {
                        "request_template_id": "UITESTDATA_PROMOTED",
                        "title": "已确认 selector 的 UI 造数补位",
                        "path": "/orders/new",
                        "task": "Create disposable order data via UI",
                        "browser_plan": {},
                        "metadata": {"bridge_mode": "page_agent_browser_plan", "executable": False},
                        "promotion": {
                            "status": "approved",
                            "approved_by": "qa_reviewer",
                            "confirmed_field_bindings": [
                                {
                                    "field": "customerId",
                                    "selector": "[name='customerId']",
                                    "action": "fill",
                                }
                            ],
                            "approved_browser_plan": {
                                "steps": [
                                    {"action": "goto", "url": "/orders/new", "wait_until": "networkidle"},
                                    {"action": "fill", "selector": "[name='customerId']", "value": "cust_001"},
                                    {"action": "click", "selector": "[data-testid='submit-order']"},
                                    {"action": "screenshot", "full_page": True},
                                ]
                            },
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "base_url": "http://127.0.0.1:8080",
            "scope_id": "checkout-scope",
            "environment_ref": "local-benchmark",
        },
        local_dev_mode=False,
    )

    assert len(prepared["ui_test_data_requests"]) == 1
    request = prepared["ui_test_data_requests"][0]
    assert request["request_id"] == "UITESTDATA_PROMOTED"
    assert request["browser_plan"]["write_approved"] is True
    assert request["browser_plan"]["steps"][1]["action"] == "fill"
    assert request["metadata"]["request_origin"] == "private_pilot_service_promoted_followup_test_data_asset"
    assert request["metadata"]["promotion_status"] == "approved"
    assert request["metadata"]["approved_by"] == "qa_reviewer"


def test_validate_scan_base_url_allows_loopback_for_local_private_service():
    service._validate_scan_base_url("http://127.0.0.1:8080", local_dev_mode=True)


def test_validate_scan_base_url_blocks_loopback_outside_local_private_service():
    try:
        service._validate_scan_base_url("http://127.0.0.1:8080", local_dev_mode=False)
    except SsrfBlockedError:
        return
    raise AssertionError("expected loopback base_url to be blocked outside local private service")
