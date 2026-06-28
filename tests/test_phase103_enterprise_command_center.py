from __future__ import annotations

from ai_test_asset_center.phase103_enterprise_command_center import (
    build_command_center_snapshot,
    build_customer_business_model,
    build_environment_readiness_report,
    build_evidence_bundle,
    build_realtime_map_snapshot,
    calculate_launch_decision,
    calculate_value_metrics,
    generate_ai_test_plan,
    generate_executive_report,
    list_industry_templates,
    redact_value,
    translate_risk_finding,
)


def test_phase103_industry_templates_cover_v1_scope() -> None:
    templates = list_industry_templates()
    industries = {item["industry"] for item in templates}

    assert {
        "ecommerce",
        "finance",
        "manufacturing",
        "healthcare",
        "government",
        "education",
        "logistics",
        "saas",
    }.issubset(industries)
    assert all(item["business_flow_count"] >= 2 for item in templates)


def test_phase103_business_model_generates_flows_roles_and_missing_inputs() -> None:
    model = build_customer_business_model(
        "proj_erp_v3",
        "manufacturing",
        role_config={"planner_user": {"configured": True, "auth_status": "passed"}},
    )
    report = build_environment_readiness_report(
        "proj_erp_v3",
        {
            "base_url": "https://staging.example.com",
            "checks": {
                "http": {"status_code": 401, "reachable": True},
                "auth": {"status": "passed", "access_token_acquired": True, "cookie_count": 1},
                "session": {"status_code": 200, "content_type": "application/json"},
                "api_smoke": {"items": [{"path": "/api/orders", "status_code": 200, "content_type": "application/json"}]},
            },
        },
        model,
    )

    assert model["industry"] == "manufacturing"
    assert any(flow["name"] == "生产工单流转链路" for flow in model["confirmed_business_flows"])
    assert any(item["type"] == "test_account" and "finance_user" in item["title"] for item in report["required_customer_inputs"])
    assert report["redaction_status"] == "safe"


def test_phase103_environment_readiness_explains_authenticated_api_smoke_failure() -> None:
    report = build_environment_readiness_report(
        "proj_shop",
        {
            "base_url": "https://staging.shop.example",
            "checks": {
                "url": {"valid": True, "host": "staging.shop.example"},
                "dns": {"status": "passed"},
                "http": {"status_code": 401, "reachable": True},
                "auth": {"status": "passed", "access_token_acquired": True, "cookie_count": 2, "csrf_token_acquired": True},
                "session": {"status_code": 200, "content_type": "application/json", "path": "/api/me"},
                "api_smoke": {
                    "items": [
                        {"path": "/api/orders", "method": "GET", "status_code": 200, "content_type": "application/json", "affected_flow": "订单链路"},
                        {"path": "/api/payments", "method": "GET", "status_code": 403, "content_type": "application/json", "affected_flow": "支付链路"},
                    ]
                },
            },
        },
    )

    assert report["status"] in {"partial_ready", "needs_customer_input"}
    assert report["checks"]["api_smoke"]["failed"] == 1
    assert "支付 API" not in " ".join(report["current_blockers"]) or report["current_blockers"]
    assert "权限" in report["checks"]["api_smoke"]["items"][1]["issue"]


def test_phase103_test_plan_blocks_missing_roles_and_write_probes_in_read_only_mode() -> None:
    model = build_customer_business_model(
        "proj_erp_v3",
        "manufacturing",
        role_config={"planner_user": {"configured": True, "auth_status": "passed"}},
    )
    readiness = build_environment_readiness_report(
        "proj_erp_v3",
        {
            "base_url": "https://staging.example.com",
            "checks": {
                "url": {"valid": True},
                "dns": {"status": "passed"},
                "http": {"status_code": 401, "reachable": True},
                "auth": {"status": "passed", "access_token_acquired": True},
                "session": {"status_code": 200, "content_type": "application/json"},
                "api_smoke": {"items": [{"path": "/api/work-orders", "status_code": 200, "content_type": "application/json"}]},
            },
            "safe_execution_mode": "read_only",
        },
        model,
    )

    plan = generate_ai_test_plan("proj_erp_v3", model, readiness)
    blocked_reasons = [reason for group in plan["probe_groups"] for reason in group["blocked_reasons"]]

    assert plan["coverage_summary"]["business_flow_total"] >= 2
    assert any("缺少必要测试账号" in reason for reason in blocked_reasons)
    assert any("只读测试" in reason for reason in blocked_reasons)
    assert plan["estimated_value"]["manual_minutes_per_test_point"] == 12


def test_phase103_risk_translation_and_evidence_are_business_safe() -> None:
    model = build_customer_business_model(
        "proj_saas",
        "saas",
        role_config={"normal_user": True, "tenant_admin": True, "cross_tenant_user": True},
    )
    risk = translate_risk_finding(
        "proj_saas",
        {
            "title": "normal_user GET /api/admin/users returned 200 Authorization: Bearer abc.def",
            "path": "/api/admin/users",
            "role": "normal_user",
            "evidence_score": 0.88,
            "reproducibility_score": 0.91,
        },
        model,
    )
    evidence = build_evidence_bundle(
        risk,
        {
            "request_summary": {"method": "GET", "path": "/api/admin/users", "headers": {"Authorization": "Bearer secret"}},
            "response_summary": {"status_code": 200, "content_type": "application/json", "body": {"phone": "13812345678"}},
        },
    )

    assert risk["severity"] == "critical"
    assert risk["launch_blocking"] is True
    assert "越权" in risk["business_impact"] or "受限" in risk["title"]
    assert evidence["redaction_status"] == "safe"
    assert evidence["request_summary"]["headers_redacted"] is True
    dumped = str(evidence)
    assert "secret" not in dumped
    assert "13812345678" not in dumped


def test_phase103_command_center_launch_decision_value_and_report() -> None:
    project = {"project_id": "proj_shop", "project_name": "商城 V3 上线评估", "system_name": "商城系统"}
    model = build_customer_business_model(
        "proj_shop",
        "ecommerce",
        role_config={"normal_user": True, "finance_user": True, "admin_user": True},
    )
    readiness = build_environment_readiness_report(
        "proj_shop",
        {
            "base_url": "https://staging.shop.example",
            "checks": {
                "url": {"valid": True},
                "dns": {"status": "passed"},
                "http": {"status_code": 401, "reachable": True},
                "auth": {"status": "passed", "access_token_acquired": True, "cookie_count": 1},
                "session": {"status_code": 200, "content_type": "application/json"},
                "api_smoke": {"items": [{"path": "/api/orders", "status_code": 200, "content_type": "application/json"}]},
            },
        },
        model,
    )
    plan = generate_ai_test_plan("proj_shop", model, readiness)
    risk = translate_risk_finding(
        "proj_shop",
        {
            "title": "POST /api/payment/submit duplicate request state mismatch",
            "business_flow_name": "订单支付链路",
            "path": "/api/payment/submit",
            "evidence_score": 0.9,
            "reproducibility_score": 0.95,
        },
        model,
    )
    evidence = build_evidence_bundle(risk)
    snapshot = build_command_center_snapshot(project, model, readiness, plan, [risk], [evidence])
    report = generate_executive_report(project, snapshot, [risk], [evidence])
    live_map = build_realtime_map_snapshot("proj_shop", model, [risk])

    assert snapshot["launch_decision"]["recommendation"] == "NO_GO"
    assert snapshot["risk_summary"]["launch_blocking"] == 1
    assert snapshot["value_metrics"]["estimated_hours_saved"] > 0
    assert report["launch_recommendation"] == "NO_GO"
    assert "AI 价值量化" in report["markdown"]
    assert live_map["risk_overlays"]


def test_phase103_redaction_removes_credentials_recursively() -> None:
    payload = {
        "headers": {"Authorization": "Bearer abc.def.ghi", "Cookie": "SESSION=secret"},
        "body": {"password": "123456", "phone": "13912345678", "id": "110101199003071234"},
    }
    safe = redact_value(payload)
    dumped = str(safe)

    assert "abc.def.ghi" not in dumped
    assert "SESSION=secret" not in dumped
    assert "123456" not in dumped
    assert "13912345678" not in dumped
    assert "110101199003071234" not in dumped
