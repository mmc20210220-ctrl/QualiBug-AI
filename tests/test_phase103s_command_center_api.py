from __future__ import annotations

from ai_test_asset_center.phase103_command_center_api import (
    CommandCenterAPIError,
    EnterpriseCommandCenterAPI,
    api_error_response,
    api_response,
)


def _ready_preflight() -> dict:
    return {
        "base_url": "https://staging.shop.example",
        "checks": {
            "url": {"valid": True, "host": "staging.shop.example"},
            "dns": {"status": "passed"},
            "http": {"status_code": 401, "reachable": True},
            "auth": {"status": "passed", "access_token_acquired": True, "cookie_count": 2, "access_token": "secret-token"},
            "session": {"path": "/api/me", "status_code": 200, "content_type": "application/json"},
            "api_smoke": {"items": [{"path": "/api/orders", "status_code": 200, "content_type": "application/json"}]},
        },
        "safe_execution_mode": "read_only",
    }


def test_phase103s_response_envelopes_redact_sensitive_values() -> None:
    ok = api_response({"Authorization": "Bearer abc.def", "nested": {"password": "123456"}})
    dumped = str(ok)

    assert ok["success"] is True
    assert ok["error"] is None
    assert "abc.def" not in dumped
    assert "123456" not in dumped
    assert ok["meta"]["version"].startswith("phase103s")

    err = api_error_response(CommandCenterAPIError("ENV_AUTH_FAILED", "认证失败", details={"access_token": "secret"}))
    assert err["success"] is False
    assert err["error"]["code"] == "ENV_AUTH_FAILED"
    assert "secret" not in str(err)


def test_phase103s_project_business_model_environment_and_plan_flow() -> None:
    api = EnterpriseCommandCenterAPI()
    project = api.create_project(
        {
            "customer_name": "某电商企业",
            "project_name": "商城 V3 上线评估",
            "system_name": "商城系统",
            "industry": "ecommerce",
        }
    )["data"]
    project_id = project["project_id"]

    model = api.apply_business_template(
        project_id,
        {
            "template_id": "industry_ecommerce",
            "role_config": {"normal_user": True, "admin_user": True, "finance_user": True},
            "critical_flow_ids": ["order_payment"],
            "approved_by": "客户项目负责人",
        },
    )["data"]
    env = api.run_environment_preflight(project_id, _ready_preflight())["data"]
    plan = api.generate_test_plan(project_id)["data"]
    onboarding = api.get_onboarding(project_id)["data"]

    assert model["industry"] == "ecommerce"
    assert any(flow["criticality"] == "critical" for flow in model["confirmed_business_flows"])
    assert env["status"] in {"ready", "partial_ready", "needs_customer_input"}
    assert plan["estimated_value"]["equivalent_test_points"] > 0
    assert onboarding["current_step"] == "run_test"
    assert onboarding["completion_rate"] >= 0.8


def test_phase103s_test_run_generates_risks_dashboard_map_report_and_value() -> None:
    api = EnterpriseCommandCenterAPI()
    project_id = api.create_project({"customer_name": "某 SaaS 企业", "project_name": "SaaS V2 质量评估", "industry": "saas"})["data"]["project_id"]
    api.apply_business_template(
        project_id,
        {
            "template_id": "industry_saas",
            "role_config": {"normal_user": True, "tenant_admin": True, "admin_user": True, "cross_tenant_user": True},
            "critical_flow_ids": ["tenant_isolation"],
        },
    )
    api.run_environment_preflight(project_id, _ready_preflight())
    api.generate_test_plan(project_id)

    run = api.start_test_run(
        project_id,
        {
            "findings": [
                {
                    "title": "normal_user GET /api/admin/users returned 200 Authorization: Bearer raw-token",
                    "path": "/api/admin/users",
                    "role": "normal_user",
                    "evidence_score": 0.92,
                    "reproducibility_score": 0.95,
                    "evidence": {
                        "request_summary": {"method": "GET", "path": "/api/admin/users", "headers": {"Authorization": "Bearer raw-token"}},
                        "response_summary": {"status_code": 200, "content_type": "application/json", "body": {"phone": "13912345678"}},
                    },
                }
            ]
        },
    )["data"]
    dashboard = api.get_command_center(project_id)["data"]
    live_map = api.get_live_map(project_id)["data"]
    risks = api.list_risks(project_id, {"severity": "critical", "launch_blocking": True})["data"]
    detail = api.get_risk_detail(project_id, risks[0]["risk_id"])["data"]
    report = api.generate_report(project_id)["data"]
    value = api.get_value_metrics(project_id)["data"]
    dumped = str(detail)

    assert run["test_run"]["risk_found"] == 1
    assert dashboard["launch_decision"]["recommendation"] == "NO_GO"
    assert dashboard["risk_summary"]["launch_blocking"] == 1
    assert live_map["risk_overlays"]
    assert len(risks) == 1
    assert detail["evidence_bundle"]["redaction_status"] == "safe"
    assert "raw-token" not in dumped
    assert "13912345678" not in dumped
    assert "AI 价值量化" in report["markdown"]
    assert value["estimated_hours_saved"] > 0


def test_phase103s_missing_preconditions_return_customer_safe_errors() -> None:
    api = EnterpriseCommandCenterAPI()
    project_id = api.create_project({"customer_name": "客户", "project_name": "ERP 项目", "industry": "manufacturing"})["data"]["project_id"]

    try:
        api.generate_test_plan(project_id)
    except CommandCenterAPIError as exc:
        response = api.fail(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("expected missing business model error")

    assert response["success"] is False
    assert response["error"]["code"] == "BUSINESS_MODEL_REQUIRED"
    assert "请先完成业务链路建模" in response["error"]["message"]


def test_phase103s_environment_config_does_not_expose_credentials() -> None:
    api = EnterpriseCommandCenterAPI()
    project_id = api.create_project({"project_name": "环境接入项目"})["data"]["project_id"]
    saved = api.patch_environment_config(
        project_id,
        {
            "base_url": "https://example.test",
            "auth": {
                "username": "tester",
                "password": "plain-password",
                "access_token": "plain-token",
                "client_secret": "plain-secret",
            },
        },
    )

    dumped = str(saved)
    assert "plain-password" not in dumped
    assert "plain-token" not in dumped
    assert "plain-secret" not in dumped
    assert saved["data"]["config"]["auth"]["password"] == "****"
