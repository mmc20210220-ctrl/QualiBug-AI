from __future__ import annotations

import json

from ai_test_asset_center.phase104_command_center_http_api import Phase104CommandCenterHttpApp


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _data(response):
    body = response.json_body()
    assert body["success"] is True, body
    return body["data"]


def test_phase104a_mutable_http_api_onboarding_flow() -> None:
    app = Phase104CommandCenterHttpApp()

    project = _data(
        app.handle(
            "POST",
            "/api/v1/projects",
            _json(
                {
                    "project_name": "ERP V3 上线质量评估",
                    "customer_name": "某制造企业",
                    "system_name": "制造 ERP",
                    "industry": "manufacturing",
                }
            ),
        )
    )
    project_id = project["project_id"]

    templates = _data(app.handle("GET", "/api/v1/industry-templates"))
    assert any(item["industry"] == "manufacturing" for item in templates)

    model = _data(
        app.handle(
            "POST",
            f"/api/v1/projects/{project_id}/business-model/apply-template",
            _json({"template_id": "industry_manufacturing", "approved_by": "客户项目负责人", "role_config": {"planner_user": True, "operator_user": True, "qc_user": True, "warehouse_user": True, "finance_user": True, "admin_user": True}}),
        )
    )
    assert model["confirmed_business_flows"]

    env_config = _data(
        app.handle(
            "PATCH",
            f"/api/v1/projects/{project_id}/environment/config",
            _json(
                {
                    "base_url": "https://staging.example.com",
                    "auth_type": "bearer",
                    "access_token": "raw-token-should-never-leak",
                    "password": "RawPasswordShouldNeverLeak",
                }
            ),
        )
    )
    assert "raw-token-should-never-leak" not in json.dumps(env_config, ensure_ascii=False)
    assert "RawPasswordShouldNeverLeak" not in json.dumps(env_config, ensure_ascii=False)

    readiness = _data(
        app.handle(
            "POST",
            f"/api/v1/projects/{project_id}/environment/preflight",
            _json(
                {
                    "safe_execution_mode": "read_only",
                    "checks": {
                        "dns": {"status": "passed"},
                        "http": {"status_code": 200, "reachable": True, "content_type": "application/json"},
                        "auth": {"status": "passed", "access_token_acquired": True, "cookie_count": 1},
                        "session": {"status": "passed", "status_code": 200, "content_type": "application/json"},
                        "api_smoke": {
                            "items": [
                                {"path": "/api/orders", "status_code": 200, "content_type": "application/json"},
                                {"path": "/api/users/me", "status_code": 200, "content_type": "application/json"}
                            ]
                        }
                    }
                }
            ),
        )
    )
    assert readiness["status"] in {"ready", "partial_ready", "needs_customer_input"}

    plan = _data(app.handle("POST", f"/api/v1/projects/{project_id}/test-plan/generate", _json({"plan_name": "V1 AI 测试计划"})))
    assert plan["estimated_value"]["equivalent_test_points"] > 0

    run = _data(
        app.handle(
            "POST",
            f"/api/v1/projects/{project_id}/test-runs",
            _json(
                {
                    "run_id": "run_phase104a_test",
                    "findings": [
                        {
                            "risk_id": "risk_admin_access",
                            "title": "normal_user GET /api/admin/users returned 200",
                            "severity": "critical",
                            "path": "/api/admin/users",
                            "role": "normal_user",
                            "evidence_score": 0.9,
                            "reproducibility_score": 0.9,
                            "evidence": {
                                "method": "GET",
                                "path": "/api/admin/users",
                                "status_code": 200,
                                "authorization": "Bearer raw-risk-token",
                                "cookie": "SESSION=raw-risk-session",
                            },
                        }
                    ],
                }
            ),
        )
    )
    assert run["test_run"]["risk_found"] == 1

    dashboard = _data(app.handle("GET", f"/api/v1/projects/{project_id}/command-center"))
    risks = _data(app.handle("GET", f"/api/v1/projects/{project_id}/risks?severity=critical&launch_blocking=true"))
    detail = _data(app.handle("GET", f"/api/v1/projects/{project_id}/risks/{risks[0]['risk_id']}"))
    value = _data(app.handle("GET", f"/api/v1/projects/{project_id}/value-metrics"))
    report = _data(app.handle("POST", f"/api/v1/projects/{project_id}/reports/generate"))

    assert dashboard["quality_health_score"] >= 0
    assert risks and risks[0]["launch_blocking"] is True
    assert "业务" in risks[0]["business_impact"] or "风险" in risks[0]["business_impact"]
    assert detail["evidence_bundle"]["redaction_status"] == "safe"
    assert value["estimated_hours_saved"] >= 0
    assert "上线" in report["executive_summary"]

    combined = json.dumps([dashboard, risks, detail, value, report], ensure_ascii=False)
    assert "raw-risk-token" not in combined
    assert "SESSION=raw-risk-session" not in combined
    assert "RawPasswordShouldNeverLeak" not in combined


def test_phase104a_seeded_http_api_exposes_full_demo_project() -> None:
    app = Phase104CommandCenterHttpApp(seed_scenario="saas")
    health = app.handle("GET", "/api/v1/health").json_body()
    projects = _data(app.handle("GET", "/api/v1/projects"))
    project_id = projects[0]["project_id"]

    dashboard = _data(app.handle("GET", f"/api/v1/projects/{project_id}/command-center"))
    live_map = _data(app.handle("GET", f"/api/v1/projects/{project_id}/live-map"))
    report = _data(app.handle("GET", f"/api/v1/projects/{project_id}/reports/executive"))

    assert health["data"]["status"] == "ok"
    assert dashboard["top_risks"]
    assert live_map["nodes"]
    assert "上线" in report["executive_summary"]


def test_phase104a_returns_customer_safe_errors_and_cors_preflight() -> None:
    app = Phase104CommandCenterHttpApp()

    invalid_json = app.handle("POST", "/api/v1/projects", "{not valid json")
    missing_project = app.handle("GET", "/api/v1/projects/missing-project")
    unsupported = app.handle("DELETE", "/api/v1/projects")
    options = app.handle("OPTIONS", "/api/v1/projects")

    assert invalid_json.status == 400
    assert invalid_json.json_body()["error"]["code"] == "INVALID_JSON"
    assert missing_project.status == 404
    assert missing_project.json_body()["error"]["code"] == "PROJECT_NOT_FOUND"
    assert unsupported.status == 405
    assert unsupported.json_body()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert options.status == 204
    assert options.headers["Access-Control-Allow-Origin"] == "*"
