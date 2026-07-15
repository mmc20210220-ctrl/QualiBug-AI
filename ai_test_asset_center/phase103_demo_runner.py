from __future__ import annotations

"""Phase103T: local demo runner and seed-data exporter.

This module turns the Phase103R/S command-center foundation into a one-command
local demo flow.  It deliberately stays framework-free so it can be used by:

* future Web/API endpoints as seed data,
* local CLI demos for sales/implementation walkthroughs,
* frontend development without a live customer system,
* regression tests that validate the PRD end-to-end story.

The runner creates a full demo project:

Project -> business model -> environment readiness -> test plan -> test run ->
risks -> evidence -> dashboard -> live map -> value metrics -> executive report.

All exported artifacts are customer-safe: the API facade and renderer apply the
same redaction path used by the command-center responses.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_command_center_api import EnterpriseCommandCenterAPI
from ai_test_asset_center.phase103_enterprise_command_center import redact_value

PHASE103T_VERSION = "phase103t-demo-runner-v1"


def _role_config(roles: Sequence[str], *, missing: Sequence[str] = ()) -> dict[str, dict[str, Any]]:
    missing_set = set(missing)
    return {
        role: {
            "configured": role not in missing_set,
            "auth_status": "not_configured" if role in missing_set else "passed",
        }
        for role in roles
    }


DEMO_SCENARIOS: dict[str, dict[str, Any]] = {
    "manufacturing": {
        "display_name": "制造 ERP 上线质量评估演示",
        "project": {
            "project_id": "demo_manufacturing_erp_v3",
            "customer_name": "某制造企业",
            "project_name": "ERP V3 上线质量评估",
            "system_name": "制造 ERP / MES / WMS 集成系统",
            "industry": "manufacturing",
            "system_type": "ERP + MES + WMS",
            "test_goal": "上线前核心业务链路风险评估",
            "planned_launch_date": "2026-07-15",
            "owner": "质量负责人",
        },
        "template_id": "industry_manufacturing",
        "critical_flow_ids": ["production_order", "inventory_sync", "finance_settlement"],
        "role_config": _role_config(
            ["planner_user", "operator_user", "qc_user", "warehouse_user", "finance_user", "admin_user"],
            missing=["finance_user"],
        ),
        "environment_config": {
            "base_url": "https://staging-erp.customer.example",
            "auth": {
                "type": "username_password_csrf",
                "username": "planner.demo",
                "password": "DemoPasswordShouldBeRedacted",
                "access_token": "raw-manufacturing-token",
                "cookie": "SESSION=raw-manufacturing-session",
            },
        },
        "preflight": {
            "base_url": "https://staging-erp.customer.example",
            "checks": {
                "url": {"valid": True, "scheme": "https", "host": "staging-erp.customer.example", "port": 443},
                "dns": {"status": "passed", "interpretation": "客户预生产域名可解析。", "latency_ms": 24},
                "http": {"status_code": 401, "reachable": True, "content_type": "application/json", "latency_ms": 118},
                "auth": {
                    "auth_type": "username_password_csrf",
                    "status": "passed",
                    "access_token_acquired": True,
                    "refresh_token_acquired": True,
                    "cookie_count": 2,
                    "csrf_token_acquired": True,
                    "access_token": "raw-manufacturing-token",
                },
                "session": {"path": "/api/me", "status_code": 200, "content_type": "application/json"},
                "api_smoke": {
                    "items": [
                        {"path": "/api/work-orders", "method": "GET", "status_code": 200, "content_type": "application/json", "affected_flow": "生产工单流转链路"},
                        {"path": "/api/inventory", "method": "GET", "status_code": 200, "content_type": "application/json", "affected_flow": "库存出入库链路"},
                        {"path": "/api/finance/reports", "method": "GET", "status_code": 403, "content_type": "application/json", "affected_flow": "财务结算链路"},
                    ]
                },
            },
            "required_customer_inputs": [
                {
                    "type": "test_account",
                    "title": "缺少 finance_user 测试账号",
                    "priority": "high",
                    "impact": "财务结算与报表链路无法完整验证。",
                    "suggested_input": "请提供具备只读财务报表权限的测试账号。",
                }
            ],
            "safe_execution_mode": "read_only",
        },
        "findings": [
            {
                "business_flow_id": "flow_inventory_sync",
                "business_title": "库存扣减与工单状态可能不一致",
                "technical_title": "work order QC pass but inventory snapshot mismatch",
                "message": "质检通过后成品入库数量与库存快照不一致",
                "risk_type": "state_consistency",
                "severity": "critical",
                "business_impact": "可能影响生产排程、库存准确性和后续财务结算，属于核心制造链路上线阻断风险。",
                "affected_modules": ["生产工单", "库存中心", "ERP 同步"],
                "affected_roles": ["warehouse_user", "qc_user"],
                "launch_blocking": True,
                "evidence_score": 0.91,
                "reproducibility_score": 0.9,
                "confidence_score": 0.93,
                "evidence": {
                    "summary": "AI 在质检通过后对比库存 before/after 快照，发现成品入库数量未与工单状态同步。",
                    "method": "POST",
                    "path": "/api/work-orders/WO-10086/qc-pass",
                    "auth_context": "qc_user",
                    "status_code": 200,
                    "content_type": "application/json",
                    "observed_issue": "工单已完成，但库存快照未增加对应成品数量。",
                    "before_snapshot": {"work_order_status": "IN_QC", "finished_goods_qty": 100},
                    "after_snapshot": {"work_order_status": "DONE", "finished_goods_qty": 100},
                    "reproduction_steps": [
                        "使用 qc_user 登录测试环境。",
                        "将测试工单流转到质检通过。",
                        "查询工单状态与成品库存快照。",
                        "观察工单已完成但库存数量未同步增加。",
                    ],
                },
            },
            {
                "business_flow_id": "flow_production_order",
                "business_title": "普通操作员可访问管理员生产配置",
                "technical_title": "operator_user GET /api/admin/production-config returned 200 Authorization: Bearer raw-token",
                "path": "/api/admin/production-config",
                "role": "operator_user",
                "severity": "critical",
                "launch_blocking": True,
                "evidence_score": 0.88,
                "reproducibility_score": 0.94,
                "evidence": {
                    "request_summary": {"method": "GET", "path": "/api/admin/production-config", "auth_context": "operator_user", "Authorization": "Bearer raw-token"},
                    "response_summary": {"status_code": 200, "content_type": "application/json", "observed_issue": "普通操作员可读取管理员生产配置。"},
                },
            },
            {
                "business_flow_id": "flow_finance_settlement",
                "business_title": "财务结算报表金额可能不准确",
                "technical_title": "finance report amount reconciliation mismatch",
                "message": "report amount mismatch in finance settlement",
                "risk_type": "report_accuracy",
                "severity": "high",
                "launch_blocking": True,
                "evidence_score": 0.84,
                "reproducibility_score": 0.76,
                "evidence": {
                    "method": "GET",
                    "path": "/api/finance/reports/settlement-summary",
                    "auth_context": "finance_user",
                    "status_code": 403,
                    "content_type": "application/json",
                    "observed_issue": "财务报表 API smoke 权限不足，且历史报表金额口径需要客户补充账号后验证。",
                },
            },
        ],
    },
    "ecommerce": {
        "display_name": "电商订单支付质量评估演示",
        "project": {
            "project_id": "demo_ecommerce_shop_v3",
            "customer_name": "某电商企业",
            "project_name": "商城 V3 上线质量评估",
            "system_name": "商城交易系统",
            "industry": "ecommerce",
            "system_type": "B2C 商城",
            "test_goal": "交易链路、支付链路和后台权限风险评估",
            "planned_launch_date": "2026-07-20",
            "owner": "交易系统负责人",
        },
        "template_id": "industry_ecommerce",
        "critical_flow_ids": ["order_payment", "admin_permission"],
        "role_config": _role_config(["normal_user", "merchant_user", "ops_user", "warehouse_user", "finance_user", "admin_user"]),
        "environment_config": {
            "base_url": "https://staging-shop.customer.example",
            "auth": {"type": "oauth2_client_credentials", "client_secret": "ecommerce-client-secret", "access_token": "raw-ecommerce-token"},
        },
        "preflight": {
            "base_url": "https://staging-shop.customer.example",
            "checks": {
                "url": {"valid": True, "host": "staging-shop.customer.example", "scheme": "https"},
                "dns": {"status": "passed"},
                "http": {"status_code": 401, "reachable": True, "content_type": "application/json"},
                "auth": {"auth_type": "oauth2_client_credentials", "status": "passed", "access_token_acquired": True, "cookie_count": 0},
                "session": {"path": "/api/users/me", "status_code": 200, "content_type": "application/json"},
                "api_smoke": {"items": [{"path": "/api/orders", "status_code": 200, "content_type": "application/json"}, {"path": "/api/payments", "status_code": 200, "content_type": "application/json"}]},
            },
            "safe_execution_mode": "read_only",
        },
        "findings": [
            {
                "business_flow_id": "flow_order_payment",
                "title": "duplicate payment submit caused inconsistent order status",
                "path": "/api/payment/submit",
                "role": "normal_user",
                "severity": "critical",
                "evidence_score": 0.93,
                "reproducibility_score": 0.92,
                "evidence": {"method": "POST", "path": "/api/payment/submit", "status_code": 200, "content_type": "application/json", "observed_issue": "重复提交支付请求后订单状态与支付流水不一致。"},
            },
            {
                "business_flow_id": "flow_admin_permission",
                "title": "normal_user GET /api/admin/orders returned 200",
                "path": "/api/admin/orders",
                "role": "normal_user",
                "severity": "critical",
                "evidence_score": 0.9,
                "reproducibility_score": 0.89,
                "evidence": {"method": "GET", "path": "/api/admin/orders", "status_code": 200, "content_type": "application/json", "observed_issue": "普通用户可访问后台订单管理接口。"},
            },
        ],
    },
    "saas": {
        "display_name": "SaaS 多租户权限隔离演示",
        "project": {
            "project_id": "demo_saas_tenant_v2",
            "customer_name": "某 SaaS 企业",
            "project_name": "SaaS V2 多租户质量评估",
            "system_name": "多租户企业协作平台",
            "industry": "saas",
            "system_type": "多租户 SaaS",
            "test_goal": "多租户隔离、订阅计费和 API 权限风险评估",
            "planned_launch_date": "2026-07-25",
            "owner": "平台负责人",
        },
        "template_id": "industry_saas",
        "critical_flow_ids": ["tenant_isolation", "role_permission", "subscription_billing"],
        "role_config": _role_config(["tenant_admin", "normal_user", "billing_admin", "auditor_user", "cross_tenant_user", "admin_user"]),
        "environment_config": {"base_url": "https://staging-saas.customer.example", "auth": {"type": "static_token", "access_token": "raw-saas-token"}},
        "preflight": {
            "base_url": "https://staging-saas.customer.example",
            "checks": {
                "url": {"valid": True, "host": "staging-saas.customer.example"},
                "dns": {"status": "passed"},
                "http": {"status_code": 401, "reachable": True},
                "auth": {"auth_type": "static_token", "status": "passed", "access_token_acquired": True},
                "session": {"path": "/api/me", "status_code": 200, "content_type": "application/json"},
                "api_smoke": {"items": [{"path": "/api/tenants/current", "status_code": 200, "content_type": "application/json"}]},
            },
            "safe_execution_mode": "read_only",
        },
        "findings": [
            {
                "business_flow_id": "flow_tenant_isolation",
                "business_title": "跨租户用户可能访问其他租户数据",
                "technical_title": "cross_tenant_user GET /api/tenants/B/projects returned 200",
                "path": "/api/tenants/B/projects",
                "role": "cross_tenant_user",
                "severity": "critical",
                "risk_type": "authorization_bypass",
                "business_impact": "可能导致跨租户数据泄露，影响客户信任、续费和合规要求。",
                "launch_blocking": True,
                "evidence_score": 0.95,
                "reproducibility_score": 0.93,
                "evidence": {"method": "GET", "path": "/api/tenants/B/projects", "auth_context": "cross_tenant_user", "status_code": 200, "content_type": "application/json", "observed_issue": "跨租户对照账号访问其他租户项目列表成功。"},
            }
        ],
    },
}


def list_demo_scenarios() -> list[dict[str, str]]:
    """Return available local demo scenarios for UI/CLI selection."""
    return [
        {"scenario": key, "display_name": str(value["display_name"]), "industry": str(value["project"]["industry"])}
        for key, value in sorted(DEMO_SCENARIOS.items())
    ]


def get_demo_scenario(scenario: str = "manufacturing") -> dict[str, Any]:
    """Return a redacted deep copy of a supported demo scenario."""
    key = (scenario or "manufacturing").strip().lower()
    if key not in DEMO_SCENARIOS:
        supported = ", ".join(sorted(DEMO_SCENARIOS))
        raise ValueError(f"unsupported Phase103 demo scenario: {scenario}. supported: {supported}")
    return redact_value(json.loads(json.dumps(DEMO_SCENARIOS[key], ensure_ascii=False)))


def _unwrap(response: Mapping[str, Any], label: str) -> Any:
    if not response.get("success"):
        raise RuntimeError(f"Phase103 demo step failed: {label}: {response.get('error')}")
    return response.get("data")


def seed_demo_project(
    api: EnterpriseCommandCenterAPI | None = None,
    *,
    scenario: str = "manufacturing",
    generate_report: bool = True,
) -> dict[str, Any]:
    """Create a full in-memory Phase103 demo project and return page-ready data."""
    api = api or EnterpriseCommandCenterAPI()
    raw = DEMO_SCENARIOS[(scenario or "manufacturing").strip().lower()]

    project = _unwrap(api.create_project(raw["project"]), "create_project")
    project_id = project["project_id"]
    business_model = _unwrap(
        api.apply_business_template(
            project_id,
            {
                "template_id": raw["template_id"],
                "critical_flow_ids": raw.get("critical_flow_ids", []),
                "role_config": raw.get("role_config", {}),
                "approved_by": "客户项目负责人",
            },
        ),
        "apply_business_template",
    )
    environment_config = _unwrap(api.patch_environment_config(project_id, raw.get("environment_config", {})), "patch_environment_config")
    environment_readiness = _unwrap(api.run_environment_preflight(project_id, raw.get("preflight", {})), "run_environment_preflight")
    test_plan = _unwrap(api.generate_test_plan(project_id, {"plan_name": f"{project['project_name']} AI 测试计划"}), "generate_test_plan")
    test_run_bundle = _unwrap(
        api.start_test_run(project_id, {"run_id": f"run_{scenario}_demo", "findings": raw.get("findings", [])}),
        "start_test_run",
    )
    onboarding = _unwrap(api.get_onboarding(project_id), "get_onboarding")
    command_center = _unwrap(api.get_command_center(project_id), "get_command_center")
    live_map = _unwrap(api.get_live_map(project_id), "get_live_map")
    risks = _unwrap(api.list_risks(project_id), "list_risks")
    risk_details = [
        _unwrap(api.get_risk_detail(project_id, str(risk["risk_id"])), f"get_risk_detail:{risk['risk_id']}")
        for risk in risks
    ]
    value_metrics = _unwrap(api.get_value_metrics(project_id), "get_value_metrics")
    report = _unwrap(api.generate_report(project_id), "generate_report") if generate_report else None

    bundle = {
        "version": PHASE103T_VERSION,
        "scenario": scenario,
        "scenario_display_name": raw["display_name"],
        "project_id": project_id,
        "project": project,
        "onboarding": onboarding,
        "business_model": business_model,
        "environment_config": environment_config,
        "environment_readiness": environment_readiness,
        "test_plan": test_plan,
        "test_run": test_run_bundle["test_run"],
        "command_center": command_center,
        "live_map": live_map,
        "risks": risks,
        "risk_details": risk_details,
        "value_metrics": value_metrics,
        "executive_report": report,
        "frontend_pages": {
            "dashboard": command_center,
            "environment": environment_readiness,
            "test_plan": test_plan,
            "live_map": live_map,
            "risks": risks,
            "value": value_metrics,
            "report": report,
        },
    }
    return redact_value(bundle)


def render_demo_markdown(bundle: Mapping[str, Any]) -> str:
    """Render a concise customer-safe demo summary for README/sales handoff."""
    project = dict(bundle.get("project") or {})
    dashboard = dict(bundle.get("command_center") or {})
    launch = dict(dashboard.get("launch_decision") or {})
    env = dict(bundle.get("environment_readiness") or {})
    value = dict(bundle.get("value_metrics") or {})
    risks = [risk for risk in bundle.get("risks", []) if isinstance(risk, Mapping)]
    report = dict(bundle.get("executive_report") or {})

    lines = [
        f"# {project.get('project_name', 'Phase103 Demo')} 演示数据包",
        "",
        f"- 场景：{bundle.get('scenario_display_name')}",
        f"- 客户：{project.get('customer_name')}",
        f"- 行业：{project.get('industry')}",
        f"- 环境状态：{env.get('status')} / 评分 {env.get('score')}",
        f"- 质量健康分：{dashboard.get('quality_health_score')}",
        f"- 上线建议：{launch.get('title')} / {launch.get('recommendation')}",
        f"- 高危风险：{dict(dashboard.get('risk_summary') or {}).get('critical', 0)}",
        f"- 上线阻断风险：{dict(dashboard.get('risk_summary') or {}).get('launch_blocking', 0)}",
        f"- AI 等价测试点：{value.get('ai_equivalent_test_points')}",
        f"- 预计节省工时：{value.get('estimated_hours_saved')} 小时",
        "",
        "## 当前最需关注风险",
    ]
    for index, risk in enumerate(risks[:5], start=1):
        flow = risk.get("affected_business_flow") if isinstance(risk.get("affected_business_flow"), Mapping) else {}
        lines.extend(
            [
                f"{index}. **{risk.get('title')}**",
                f"   - 等级：{risk.get('severity')}",
                f"   - 影响链路：{flow.get('name')}",
                f"   - 业务影响：{risk.get('business_impact')}",
                f"   - 建议动作：{risk.get('suggested_action')}",
            ]
        )
    lines.extend(["", "## 领导摘要", str(report.get("executive_summary") or dashboard.get("executive_summary") or "暂无摘要。")])
    lines.extend(["", "## 安全说明", "本演示数据已通过统一脱敏路径处理，不包含 token、cookie、password、session 原值。"])
    return str(redact_value("\n".join(lines)))


def export_demo_bundle(
    bundle: Mapping[str, Any],
    output_dir: str | Path,
    *,
    include_pretty_json: bool = True,
) -> dict[str, Any]:
    """Write page-ready JSON artifacts and a Markdown summary to output_dir."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    safe_bundle = redact_value(dict(bundle))
    files: dict[str, str] = {}

    artifacts = {
        "project.json": safe_bundle.get("project"),
        "onboarding.json": safe_bundle.get("onboarding"),
        "business_model.json": safe_bundle.get("business_model"),
        "environment_readiness.json": safe_bundle.get("environment_readiness"),
        "test_plan.json": safe_bundle.get("test_plan"),
        "test_run.json": safe_bundle.get("test_run"),
        "command_center.json": safe_bundle.get("command_center"),
        "live_map.json": safe_bundle.get("live_map"),
        "risks.json": safe_bundle.get("risks"),
        "risk_details.json": safe_bundle.get("risk_details"),
        "value_metrics.json": safe_bundle.get("value_metrics"),
        "executive_report.json": safe_bundle.get("executive_report"),
        "frontend_pages.json": safe_bundle.get("frontend_pages"),
    }
    for filename, content in artifacts.items():
        path = output / filename
        with path.open("w", encoding="utf-8") as handle:
            json.dump(content, handle, ensure_ascii=False, indent=2 if include_pretty_json else None, sort_keys=True)
        files[filename] = str(path)

    summary = render_demo_markdown(safe_bundle)
    summary_path = output / "README_demo_summary.md"
    summary_path.write_text(summary, encoding="utf-8")
    files["README_demo_summary.md"] = str(summary_path)

    manifest = {
        "version": PHASE103T_VERSION,
        "scenario": safe_bundle.get("scenario"),
        "project_id": safe_bundle.get("project_id"),
        "files": files,
        "redaction_status": "safe",
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    files["manifest.json"] = str(manifest_path)
    manifest["files"] = files
    return manifest


def build_and_export_demo(
    *,
    scenario: str = "manufacturing",
    output_dir: str | Path = "outputs/phase103_demo",
) -> dict[str, Any]:
    """Convenience helper used by CLI and future local demo scripts."""
    api = EnterpriseCommandCenterAPI()
    bundle = seed_demo_project(api, scenario=scenario)
    manifest = export_demo_bundle(bundle, output_dir)
    return {"bundle": bundle, "manifest": manifest}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build customer-safe Phase103 command-center demo seed data.")
    parser.add_argument("--scenario", default="manufacturing", choices=sorted(DEMO_SCENARIOS), help="Demo scenario to generate.")
    parser.add_argument("--output-dir", default="outputs/phase103_demo", help="Directory where JSON/Markdown artifacts will be written.")
    parser.add_argument("--list-scenarios", action="store_true", help="List supported scenarios and exit.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.list_scenarios:
        print(json.dumps(list_demo_scenarios(), ensure_ascii=False, indent=2))
        return 0

    result = build_and_export_demo(scenario=args.scenario, output_dir=args.output_dir)
    manifest = result["manifest"]
    print(f"Phase103 demo generated: {manifest['scenario']} -> {args.output_dir}")
    print(json.dumps({"project_id": manifest["project_id"], "files": sorted(manifest["files"].keys())}, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "DEMO_SCENARIOS",
    "PHASE103T_VERSION",
    "build_and_export_demo",
    "export_demo_bundle",
    "get_demo_scenario",
    "list_demo_scenarios",
    "main",
    "render_demo_markdown",
    "seed_demo_project",
]


if __name__ == "__main__":  # pragma: no cover - exercised through CLI smoke in tests when needed
    raise SystemExit(main())
