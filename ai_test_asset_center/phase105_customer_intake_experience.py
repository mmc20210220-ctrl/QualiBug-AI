from __future__ import annotations

"""Phase105C: customer intake experience for the QualiBug frontend layer.

Phase105A created the product shell and Phase105B strengthened the executive
quality dashboard. Phase105C focuses the true product entry point: a customer
intake page that turns enterprise materials into an explainable project draft,
business model preview, environment readiness checklist, and AI test plan input.

The generator is framework-neutral and dependency-free. It creates static
HTML/CSS/JS plus a redacted JSON data model so a future React/Vue frontend can
copy the information architecture and bind it to the Phase104 API contract.
"""
import os

import argparse
import html
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase105_frontend_product_shell import collect_product_shell_demo_data

PHASE105C_VERSION = "phase105c-customer-intake-experience-v1"

CUSTOMER_INTAKE_MANIFEST = "customer_intake_experience_manifest.json"
CUSTOMER_INTAKE_ACCEPTANCE_JSON = "customer_intake_experience_acceptance_report.json"
CUSTOMER_INTAKE_ACCEPTANCE_MD = "customer_intake_experience_acceptance_report.md"

REQUIRED_CUSTOMER_INTAKE_FILES: tuple[str, ...] = (
    "customer_intake.html",
    "README_CUSTOMER_INTAKE_EXPERIENCE.md",
    "data/customer_intake_experience_data.json",
    "assets/qualibug_customer_intake.css",
    "assets/qualibug_customer_intake.js",
    CUSTOMER_INTAKE_MANIFEST,
)

FORBIDDEN_CUSTOMER_INTAKE_PATTERNS: tuple[str, ...] = (
    "raw-token",
    "raw-cookie",
    "raw-session",
    "raw-password",
    "client_secret=",
    "clientSecret=raw",
    "SESSION=raw",
    "Bearer raw",
    "DemoPasswordShouldBeRedacted",
    "Traceback (most recent call last)",
)

CORE_CUSTOMER_INTAKE_LABELS: tuple[str, ...] = (
    "客户资料导入",
    "AI 分析入口",
    "资料完整度",
    "行业识别",
    "业务链路草案",
    "角色与账号清单",
    "环境配置草案",
    "安全边界",
    "客户补料清单",
    "生成测试计划",
)

INTAKE_STEPS: tuple[dict[str, str], ...] = (
    {"id": "project", "label": "项目基础信息", "intent": "确认客户、系统、行业、上线目标和负责人。"},
    {"id": "materials", "label": "企业资料导入", "intent": "接收 PRD、OpenAPI、流程图、角色说明、接口样例。"},
    {"id": "analysis", "label": "AI 分析入口", "intent": "把资料转成行业识别、业务链路、角色矩阵和风险关注点。"},
    {"id": "environment", "label": "环境配置草案", "intent": "整理 URL、认证方式、API smoke、安全边界和客户补料项。"},
    {"id": "handoff", "label": "生成测试计划", "intent": "形成 Phase104 项目创建、环境预检和 AI 测试计划输入。"},
)

SCENARIO_INTAKE_SOURCES: dict[str, tuple[dict[str, str], ...]] = {
    "manufacturing": (
        {"name": "ERP/MES/WMS 上线 PRD", "type": "PRD", "status": "已解析", "summary": "识别生产工单、库存出入库、质检、财务结算等核心链路。"},
        {"name": "OpenAPI 接口摘要", "type": "API", "status": "待客户补充", "summary": "已识别工单和库存接口，财务报表接口需补充权限样例。"},
        {"name": "角色权限说明", "type": "RBAC", "status": "部分完成", "summary": "已识别计划员、仓库、质检、财务、管理员角色。"},
        {"name": "上线安全边界", "type": "Boundary", "status": "已确认", "summary": "默认只读测试，不执行影响生产数据的写入探针。"},
    ),
    "ecommerce": (
        {"name": "订单与支付 PRD", "type": "PRD", "status": "已解析", "summary": "识别下单、支付、库存扣减、发货、退款与优惠券链路。"},
        {"name": "交易 API 摘要", "type": "API", "status": "已解析", "summary": "已提取订单、支付、退款、库存 API smoke 候选。"},
        {"name": "商家与用户角色说明", "type": "RBAC", "status": "部分完成", "summary": "已识别买家、商家、仓库、客服、运营管理员。"},
        {"name": "沙箱支付边界", "type": "Boundary", "status": "待确认", "summary": "需要客户确认支付回调、退款回放和测试金额限制。"},
    ),
    "saas": (
        {"name": "多租户 SaaS PRD", "type": "PRD", "status": "已解析", "summary": "识别租户创建、成员邀请、权限配置、订阅计费链路。"},
        {"name": "租户 API 摘要", "type": "API", "status": "已解析", "summary": "已提取工作区、邀请、RBAC、账单 API smoke 候选。"},
        {"name": "套餐与权限矩阵", "type": "RBAC", "status": "部分完成", "summary": "需要补充企业版、专业版和试用版功能边界。"},
        {"name": "数据隔离边界", "type": "Boundary", "status": "已确认", "summary": "重点验证跨租户访问、角色越权和账单状态一致性。"},
    ),
}

SCENARIO_SAFETY_BOUNDARIES: dict[str, tuple[str, ...]] = {
    "manufacturing": ("默认只读测试", "不创建真实生产工单", "不修改真实库存", "财务报表仅验证字段摘要"),
    "ecommerce": ("默认沙箱订单", "不触发真实支付扣款", "退款链路需客户授权", "优惠券规则仅验证测试券"),
    "saas": ("默认测试租户", "不读取真实客户数据", "跨租户验证只使用脱敏样例", "计费链路仅使用沙箱套餐"),
}


@dataclass(frozen=True)
class CustomerIntakeExperienceCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class CustomerIntakeExperienceAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    checks: list[CustomerIntakeExperienceCheck] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return redact_value(
            {
                "passed": self.passed,
                "score": self.score,
                "version": self.version,
                "scenario": self.scenario,
                "output_dir": self.output_dir,
                "checks": [asdict(check) for check in self.checks],
                "artifacts": self.artifacts,
            }
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json_dump(data: Any) -> str:
    return json.dumps(redact_value(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _safe_text(value: Any, default: str = "—") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _business_flows_from_demo(demo: Mapping[str, Any]) -> list[dict[str, Any]]:
    flow_by_id: dict[str, dict[str, Any]] = {}
    test_plan = demo.get("test_plan") if isinstance(demo.get("test_plan"), Mapping) else {}
    for group in _as_list(test_plan.get("probe_groups")):
        if not isinstance(group, Mapping):
            continue
        flow_id = str(group.get("business_flow_id") or group.get("group_id") or f"flow_{len(flow_by_id)+1}")
        flow_by_id[flow_id] = {
            "business_flow_id": flow_id,
            "name": _safe_text(group.get("business_flow_name"), "核心业务链路"),
            "probe_total": int(group.get("probe_total") or 0),
            "probe_executable": int(group.get("probe_executable") or 0),
            "probe_blocked": int(group.get("probe_blocked") or 0),
            "status": "blocked" if int(group.get("probe_blocked") or 0) else "ready",
            "risk_focus": [],
        }

    for risk in _as_list(demo.get("risks")):
        if not isinstance(risk, Mapping):
            continue
        affected = risk.get("affected_business_flow") if isinstance(risk.get("affected_business_flow"), Mapping) else {}
        flow_id = str(affected.get("business_flow_id") or f"risk_flow_{len(flow_by_id)+1}")
        flow = flow_by_id.setdefault(
            flow_id,
            {
                "business_flow_id": flow_id,
                "name": _safe_text(affected.get("name"), "风险关联业务链路"),
                "probe_total": 0,
                "probe_executable": 0,
                "probe_blocked": 0,
                "status": "needs_review",
                "risk_focus": [],
            },
        )
        focus = _safe_text(risk.get("title"), "风险待确认")
        if focus not in flow["risk_focus"]:
            flow["risk_focus"].append(focus)

    flows = list(flow_by_id.values())
    if not flows:
        flows = [
            {
                "business_flow_id": "flow_default",
                "name": "核心业务链路",
                "probe_total": 0,
                "probe_executable": 0,
                "probe_blocked": 0,
                "status": "needs_review",
                "risk_focus": ["等待客户资料导入后生成风险重点"],
            }
        ]
    return flows


def _role_requirements_from_demo(demo: Mapping[str, Any], scenario: str) -> list[dict[str, Any]]:
    role_names: list[str] = []
    for risk in _as_list(demo.get("risks")):
        if isinstance(risk, Mapping):
            for role in _as_list(risk.get("affected_roles")):
                role_text = str(role)
                if role_text and role_text not in role_names:
                    role_names.append(role_text)

    required_inputs = []
    environment = demo.get("environment") if isinstance(demo.get("environment"), Mapping) else {}
    for item in _as_list(environment.get("required_customer_inputs")):
        if isinstance(item, Mapping) and item.get("type") == "test_account":
            required_inputs.append(str(item.get("title") or "缺少测试账号"))

    fallback_roles = {
        "manufacturing": ["planner_user", "warehouse_user", "qc_user", "finance_user", "admin_user"],
        "ecommerce": ["buyer_user", "merchant_user", "warehouse_user", "support_user", "admin_user"],
        "saas": ["tenant_owner", "workspace_admin", "member_user", "billing_user", "readonly_auditor"],
    }
    if len(role_names) < 3:
        for role in fallback_roles.get(scenario) or []:
            if role not in role_names:
                role_names.append(role)

    rows: list[dict[str, Any]] = []
    for role in role_names[:8]:
        missing = any(role in text for text in required_inputs)
        rows.append(
            {
                "role": role,
                "purpose": _role_purpose(role),
                "account_status": "待客户补充" if missing else "已有或可模拟",
                "needed_for": _role_needed_for(role),
                "safety_note": "仅用于授权范围内验证，页面和报告不展示凭证原文。",
            }
        )
    return rows


def _role_purpose(role: str) -> str:
    lower = role.lower()
    if "finance" in lower or "billing" in lower:
        return "验证结算、账单、财务报表或计费一致性。"
    if "warehouse" in lower or "inventory" in lower:
        return "验证库存、出入库、扣减和履约状态。"
    if "admin" in lower or "owner" in lower:
        return "验证管理配置、权限边界和高危操作保护。"
    if "qc" in lower or "quality" in lower:
        return "验证质检、状态流转和异常回退。"
    if "buyer" in lower or "member" in lower:
        return "验证普通用户路径、权限边界和核心操作体验。"
    return "验证对应业务角色在核心链路中的权限和状态。"


def _role_needed_for(role: str) -> str:
    lower = role.lower()
    if "finance" in lower or "billing" in lower:
        return "财务/计费链路、报表一致性、权限边界"
    if "warehouse" in lower or "inventory" in lower:
        return "库存出入库、履约、状态一致性"
    if "admin" in lower or "owner" in lower:
        return "RBAC、配置变更、越权检测"
    if "qc" in lower or "quality" in lower:
        return "质检状态、异常流转、证据复现"
    return "核心业务流执行与回归验证"


def collect_customer_intake_experience_data(
    scenario: str = "manufacturing", api_base_url: str = os.environ.get("QUALIBUG_API_BASE_URL", "http://127.0.0.1:8088")
) -> dict[str, Any]:
    """Collect redacted demo data for the customer intake display layer."""
    demo = collect_product_shell_demo_data(scenario=scenario, api_base_url=api_base_url)
    project = dict(demo.get("project") or {})
    environment = demo.get("environment") if isinstance(demo.get("environment"), Mapping) else {}
    dashboard = demo.get("dashboard") if isinstance(demo.get("dashboard"), Mapping) else {}
    test_plan = demo.get("test_plan") if isinstance(demo.get("test_plan"), Mapping) else {}

    required_customer_inputs = _as_list(environment.get("required_customer_inputs"))
    source_templates = list(SCENARIO_INTAKE_SOURCES.get(scenario) or [])
    completion_total = len(source_templates) + 4
    completed = sum(1 for item in source_templates if item.get("status") == "已解析") + 3
    if not required_customer_inputs:
        completed += 1
    completion_rate = round(completed / max(completion_total, 1), 2)

    business_flows = _business_flows_from_demo(demo)
    role_requirements = _role_requirements_from_demo(demo, scenario)
    blockers = _as_list(environment.get("current_blockers"))
    launch = dashboard.get("launch_decision") if isinstance(dashboard.get("launch_decision"), Mapping) else {}
    coverage = test_plan.get("coverage_summary") if isinstance(test_plan.get("coverage_summary"), Mapping) else {}

    phase104_payload = {
        "project_payload": {
            "customer_name": project.get("customer_name"),
            "project_name": project.get("project_name"),
            "system_name": project.get("system_name"),
            "industry": project.get("industry"),
            "system_type": project.get("system_type"),
            "test_goal": project.get("test_goal"),
            "planned_launch_date": project.get("planned_launch_date"),
            "owner": project.get("owner"),
        },
        "business_model_request": {
            "industry_template": project.get("industry"),
            "core_flows": [flow["name"] for flow in business_flows],
            "roles": [row["role"] for row in role_requirements],
            "risk_focus": sorted({focus for flow in business_flows for focus in flow.get("risk_focus", [])}),
        },
        "environment_config_draft": {
            "safe_execution_mode": environment.get("safe_execution_mode") or "read_only",
            "api_base_url": api_base_url.rstrip("/"),
            "auth_status": "待客户确认认证方式与测试账号",
            "api_smoke_status": (environment.get("checks") or {}).get("api_smoke", {}).get("status") if isinstance(environment.get("checks"), Mapping) else "unknown",
        },
    }

    ai_analysis_preview = {
        "industry_identification": {
            "detected_industry": project.get("industry") or scenario,
            "confidence": 0.84,
            "reason": "根据系统名称、业务链路、接口摘要和角色矩阵生成行业初判。",
        },
        "coverage_hint": {
            "business_flow_total": coverage.get("business_flow_total") or len(business_flows),
            "business_flow_executable": coverage.get("business_flow_executable") or sum(1 for flow in business_flows if flow.get("status") == "ready"),
            "business_flow_blocked": coverage.get("business_flow_blocked") or len(blockers),
        },
        "launch_context": {
            "recommendation": launch.get("recommendation") or "待测试后生成",
            "summary": launch.get("summary") or "完成资料导入和环境预检后生成上线建议。",
        },
    }

    next_actions = [
        "确认行业识别和核心业务链路是否覆盖本次上线范围。",
        "补充缺失测试账号、认证说明和 API 权限样例。",
        "确认安全边界：只读、沙箱、灰度或授权写入。",
        "生成 AI 测试计划并进入环境诊断中心。",
    ]
    for item in required_customer_inputs[:3]:
        if isinstance(item, Mapping):
            next_actions.insert(1, _safe_text(item.get("suggested_input"), "补充客户所需资料。"))

    return redact_value(
        {
            "version": PHASE105C_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "api_base_url": api_base_url.rstrip("/"),
            "project_draft": phase104_payload["project_payload"],
            "intake_steps": list(INTAKE_STEPS),
            "intake_sources": source_templates,
            "material_completion": {
                "completion_rate": completion_rate,
                "completed_items": completed,
                "total_items": completion_total,
                "status": "needs_customer_input" if required_customer_inputs else "ready_for_plan",
            },
            "ai_analysis_preview": ai_analysis_preview,
            "business_flow_candidates": business_flows,
            "role_requirements": role_requirements,
            "environment_readiness_draft": {
                "score": environment.get("score", 0),
                "status": environment.get("status", "unknown"),
                "safe_execution_mode": environment.get("safe_execution_mode", "read_only"),
                "current_blockers": blockers,
                "suggested_actions": _as_list(environment.get("suggested_actions")),
            },
            "required_customer_inputs": required_customer_inputs,
            "safety_boundaries": list(SCENARIO_SAFETY_BOUNDARIES.get(scenario) or []),
            "phase104_handoff_payload": phase104_payload,
            "next_actions": next_actions,
            "display_principles": [
                "先让客户知道需要提供什么，再说明系统能自动生成什么。",
                "行业识别、业务链路和角色矩阵必须可修改、可确认、可追责。",
                "环境凭证只展示状态，不展示原文。",
                "阻断项必须给出客户下一步动作，而不是只报错。",
            ],
        }
    )


def render_customer_intake_html(data: Mapping[str, Any]) -> str:
    project = data.get("project_draft") if isinstance(data.get("project_draft"), Mapping) else {}
    completion = data.get("material_completion") if isinstance(data.get("material_completion"), Mapping) else {}
    analysis = data.get("ai_analysis_preview") if isinstance(data.get("ai_analysis_preview"), Mapping) else {}
    industry = analysis.get("industry_identification") if isinstance(analysis.get("industry_identification"), Mapping) else {}
    env = data.get("environment_readiness_draft") if isinstance(data.get("environment_readiness_draft"), Mapping) else {}
    launch = analysis.get("launch_context") if isinstance(analysis.get("launch_context"), Mapping) else {}

    steps_html = "\n".join(
        f"""
        <article class=\"step-card\" data-step=\"{html.escape(str(step.get('id')))}\">
          <span class=\"step-index\">{index:02d}</span>
          <div><h3>{html.escape(_safe_text(step.get('label')))}</h3><p>{html.escape(_safe_text(step.get('intent')))}</p></div>
        </article>
        """
        for index, step in enumerate(_as_list(data.get("intake_steps")), start=1)
        if isinstance(step, Mapping)
    )

    sources_html = "\n".join(
        f"""
        <article class=\"source-card\">
          <div class=\"source-type\">{html.escape(_safe_text(source.get('type')))}</div>
          <h3>{html.escape(_safe_text(source.get('name')))}</h3>
          <p>{html.escape(_safe_text(source.get('summary')))}</p>
          <span class=\"pill\">{html.escape(_safe_text(source.get('status')))}</span>
        </article>
        """
        for source in _as_list(data.get("intake_sources"))
        if isinstance(source, Mapping)
    )

    flows_html = "\n".join(
        f"""
        <article class=\"flow-card\">
          <div class=\"flow-head\"><h3>{html.escape(_safe_text(flow.get('name')))}</h3><span>{html.escape(_safe_text(flow.get('status')))}</span></div>
          <div class=\"flow-metrics\">
            <b>{int(flow.get('probe_executable') or 0)}</b><small>可执行探针</small>
            <b>{int(flow.get('probe_blocked') or 0)}</b><small>阻断探针</small>
          </div>
          <p>{html.escape(' / '.join(str(item) for item in _as_list(flow.get('risk_focus'))[:3]) or '等待 AI 分析补充风险重点')}</p>
        </article>
        """
        for flow in _as_list(data.get("business_flow_candidates"))
        if isinstance(flow, Mapping)
    )

    roles_html = "\n".join(
        f"""
        <tr>
          <td>{html.escape(_safe_text(row.get('role')))}</td>
          <td>{html.escape(_safe_text(row.get('purpose')))}</td>
          <td><span class=\"pill subtle\">{html.escape(_safe_text(row.get('account_status')))}</span></td>
          <td>{html.escape(_safe_text(row.get('needed_for')))}</td>
        </tr>
        """
        for row in _as_list(data.get("role_requirements"))
        if isinstance(row, Mapping)
    )

    required_inputs_html = "\n".join(
        f"""
        <li>
          <strong>{html.escape(_safe_text(item.get('title')))}</strong>
          <span>{html.escape(_safe_text(item.get('priority')))}</span>
          <p>{html.escape(_safe_text(item.get('suggested_input')))}</p>
        </li>
        """
        for item in _as_list(data.get("required_customer_inputs"))
        if isinstance(item, Mapping)
    ) or "<li><strong>暂无阻断补料项</strong><span>ready</span><p>当前资料足以生成 AI 测试计划。</p></li>"

    safety_html = "\n".join(f"<span>{html.escape(str(item))}</span>" for item in _as_list(data.get("safety_boundaries")))
    next_actions_html = "\n".join(f"<li>{html.escape(str(item))}</li>" for item in _as_list(data.get("next_actions"))[:6])

    return f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>QualiBug AI · 客户资料导入</title>
  <link rel=\"stylesheet\" href=\"assets/qualibug_customer_intake.css\" />
</head>
<body>
  <aside class=\"sidebar\">
    <div class=\"brand\"><span>QB</span><strong>QualiBug AI</strong><small>企业质量指挥中心</small></div>
    <nav>
      <a class=\"active\" href=\"#intake\">客户资料导入</a>
      <a href=\"#analysis\">AI 分析入口</a>
      <a href=\"#flows\">业务链路草案</a>
      <a href=\"#environment\">环境配置草案</a>
      <a href=\"#handoff\">生成测试计划</a>
    </nav>
  </aside>

  <main class=\"app\">
    <header class=\"topbar\">
      <div>
        <p class=\"eyebrow\">Phase105C · Customer Intake Experience</p>
        <h1>客户资料导入</h1>
        <p>把企业资料变成业务模型、环境清单和 AI 测试计划输入。</p>
      </div>
      <div class=\"project-chip\">
        <strong>{html.escape(_safe_text(project.get('project_name')))}</strong>
        <span>{html.escape(_safe_text(project.get('customer_name')))} · {html.escape(_safe_text(project.get('industry')))}</span>
      </div>
    </header>

    <section class=\"hero\" id=\"intake\">
      <div class=\"hero-copy\">
        <span class=\"pill\">AI 分析入口</span>
        <h2>上传企业资料，生成可确认的测试输入</h2>
        <p>支持 PRD、OpenAPI、流程图、角色矩阵、环境说明和上线边界。页面默认只展示摘要和状态，不展示敏感凭证原文。</p>
        <div class=\"hero-actions\">
          <button>上传企业资料</button>
          <button class=\"secondary\">粘贴接口摘要</button>
          <button class=\"ghost\">选择行业模板</button>
        </div>
      </div>
      <div class=\"score-card\">
        <small>资料完整度</small>
        <strong>{int(float(completion.get('completion_rate') or 0) * 100)}%</strong>
        <span>{int(completion.get('completed_items') or 0)} / {int(completion.get('total_items') or 0)} 项已完成</span>
      </div>
    </section>

    <section class=\"steps-grid\">{steps_html}</section>

    <section class=\"panel\" id=\"analysis\">
      <div class=\"section-head\"><p class=\"eyebrow\">AI Analysis</p><h2>行业识别与项目草案</h2></div>
      <div class=\"analysis-grid\">
        <article><small>行业识别</small><strong>{html.escape(_safe_text(industry.get('detected_industry')))}</strong><p>{html.escape(_safe_text(industry.get('reason')))}</p></article>
        <article><small>系统名称</small><strong>{html.escape(_safe_text(project.get('system_name')))}</strong><p>{html.escape(_safe_text(project.get('system_type')))}</p></article>
        <article><small>上线建议上下文</small><strong>{html.escape(_safe_text(launch.get('recommendation')))}</strong><p>{html.escape(_safe_text(launch.get('summary')))}</p></article>
      </div>
    </section>

    <section class=\"panel\">
      <div class=\"section-head\"><p class=\"eyebrow\">Materials</p><h2>资料来源与解析状态</h2></div>
      <div class=\"source-grid\">{sources_html}</div>
    </section>

    <section class=\"panel\" id=\"flows\">
      <div class=\"section-head\"><p class=\"eyebrow\">Business Model</p><h2>业务链路草案</h2></div>
      <div class=\"flow-grid\">{flows_html}</div>
    </section>

    <section class=\"panel\">
      <div class=\"section-head\"><p class=\"eyebrow\">Roles</p><h2>角色与账号清单</h2></div>
      <div class=\"table-wrap\"><table><thead><tr><th>角色</th><th>用途</th><th>账号状态</th><th>验证范围</th></tr></thead><tbody>{roles_html}</tbody></table></div>
    </section>

    <section class=\"panel split\" id=\"environment\">
      <div>
        <div class=\"section-head\"><p class=\"eyebrow\">Environment Draft</p><h2>环境配置草案</h2></div>
        <div class=\"env-card\"><strong>{int(env.get('score') or 0)}</strong><span>环境可测分</span><p>状态：{html.escape(_safe_text(env.get('status')))} · 模式：{html.escape(_safe_text(env.get('safe_execution_mode')))}</p></div>
        <div class=\"safety-list\"><h3>安全边界</h3><div>{safety_html}</div></div>
      </div>
      <div>
        <div class=\"section-head\"><p class=\"eyebrow\">Required Inputs</p><h2>客户补料清单</h2></div>
        <ul class=\"required-list\">{required_inputs_html}</ul>
      </div>
    </section>

    <section class=\"panel\" id=\"handoff\">
      <div class=\"section-head\"><p class=\"eyebrow\">Next</p><h2>生成测试计划前的下一步动作</h2></div>
      <ol class=\"next-actions\">{next_actions_html}</ol>
      <div class=\"handoff-box\">
        <h3>Phase104 API 交接输入已准备</h3>
        <p>已生成 project_payload、business_model_request、environment_config_draft，可继续进入环境诊断中心和 AI 测试计划。</p>
        <button>生成测试计划</button>
      </div>
    </section>
  </main>
  <script src=\"assets/qualibug_customer_intake.js\"></script>
</body>
</html>
"""


def render_customer_intake_css() -> str:
    return """:root{--bg:#08111f;--panel:#111c2f;--panel2:#15243a;--line:#273852;--text:#eef4ff;--muted:#96a8c4;--accent:#6ee7f9;--ok:#8ef6c3;--warn:#ffd166;--bad:#ff7b8a}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#12345c 0,#08111f 36%,#050912 100%);color:var(--text);font-family:Inter,"Microsoft YaHei",Arial,sans-serif}.sidebar{position:fixed;left:0;top:0;bottom:0;width:260px;padding:24px;border-right:1px solid var(--line);background:rgba(7,14,27,.88);backdrop-filter:blur(18px)}.brand{display:grid;gap:5px;margin-bottom:32px}.brand span{width:42px;height:42px;display:grid;place-items:center;border-radius:14px;background:linear-gradient(135deg,#5eead4,#60a5fa);color:#07111f;font-weight:900}.brand strong{font-size:20px}.brand small,.eyebrow{color:var(--muted)}nav{display:grid;gap:10px}nav a{color:var(--muted);text-decoration:none;padding:12px 14px;border-radius:14px;border:1px solid transparent}nav a.active,nav a:hover{color:var(--text);background:rgba(110,231,249,.1);border-color:rgba(110,231,249,.25)}.app{margin-left:260px;padding:28px 36px 56px}.topbar{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:26px}.topbar h1{font-size:36px;margin:4px 0}.project-chip{background:rgba(255,255,255,.06);border:1px solid var(--line);border-radius:18px;padding:14px 18px;min-width:300px}.project-chip span{display:block;color:var(--muted);margin-top:6px}.hero{display:grid;grid-template-columns:1fr 260px;gap:22px;background:linear-gradient(135deg,rgba(96,165,250,.18),rgba(20,184,166,.14));border:1px solid rgba(110,231,249,.25);border-radius:28px;padding:28px;margin-bottom:22px}.hero h2{font-size:34px;margin:12px 0}.hero p{color:#c8d7ef;max-width:820px}.hero-actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}button{border:0;border-radius:14px;padding:12px 18px;font-weight:800;color:#08111f;background:var(--accent);cursor:pointer}.secondary{background:var(--ok)}.ghost{background:transparent;color:var(--text);border:1px solid var(--line)}.pill{display:inline-flex;align-items:center;border:1px solid rgba(110,231,249,.28);background:rgba(110,231,249,.1);color:#bff7ff;border-radius:999px;padding:6px 10px;font-size:12px;font-weight:800}.pill.subtle{color:#dbeafe;background:rgba(148,163,184,.12);border-color:var(--line)}.score-card{border-radius:24px;background:rgba(8,17,31,.7);border:1px solid var(--line);display:grid;place-items:center;text-align:center;padding:20px}.score-card strong{font-size:56px;color:var(--ok)}.score-card span{color:var(--muted)}.steps-grid,.source-grid,.flow-grid,.analysis-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-bottom:22px}.step-card,.source-card,.flow-card,.analysis-grid article,.panel,.env-card,.handoff-box{background:rgba(17,28,47,.86);border:1px solid var(--line);border-radius:22px;padding:20px}.step-card{display:flex;gap:14px}.step-index{color:var(--accent);font-weight:900}.step-card h3,.source-card h3,.flow-card h3{margin:0 0 8px}.step-card p,.source-card p,.flow-card p,.analysis-grid p,.handoff-box p{color:var(--muted);line-height:1.65}.panel{margin-bottom:22px}.section-head{display:flex;align-items:end;justify-content:space-between;gap:18px;margin-bottom:18px}.section-head h2{margin:0;font-size:24px}.source-type{color:var(--accent);font-weight:900;margin-bottom:10px}.flow-head{display:flex;justify-content:space-between;gap:10px}.flow-head span{color:var(--warn);font-weight:800}.flow-metrics{display:grid;grid-template-columns:auto 1fr auto 1fr;gap:8px;align-items:end;margin:14px 0}.flow-metrics b{font-size:30px;color:var(--ok)}.flow-metrics small{color:var(--muted)}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid var(--line);padding:14px;color:#d9e5f7}th{color:var(--muted);font-size:13px}.split{display:grid;grid-template-columns:1fr 1fr;gap:22px}.env-card{display:grid;gap:8px}.env-card strong{font-size:54px;color:var(--warn)}.env-card span{font-weight:900}.env-card p{color:var(--muted)}.safety-list div{display:flex;flex-wrap:wrap;gap:10px}.safety-list span{border-radius:999px;background:rgba(142,246,195,.1);border:1px solid rgba(142,246,195,.25);padding:8px 12px;color:#c7ffdf}.required-list{list-style:none;padding:0;margin:0;display:grid;gap:12px}.required-list li{border:1px solid var(--line);border-radius:16px;padding:14px;background:rgba(255,255,255,.04)}.required-list li span{float:right;color:var(--warn);font-weight:900}.required-list p{clear:both;color:var(--muted);line-height:1.6}.next-actions{display:grid;gap:12px;color:#dbeafe}.next-actions li{background:rgba(255,255,255,.05);border:1px solid var(--line);border-radius:14px;padding:12px}.handoff-box{margin-top:18px;border-color:rgba(110,231,249,.28)}@media(max-width:980px){.sidebar{position:static;width:auto}.app{margin:0;padding:20px}.hero,.split{grid-template-columns:1fr}.steps-grid,.source-grid,.flow-grid,.analysis-grid{grid-template-columns:1fr}.topbar{display:block}.project-chip{min-width:0}}\n"""


def render_customer_intake_js() -> str:
    return """async function loadCustomerIntakeData(){
  const response = await fetch('data/customer_intake_experience_data.json');
  const data = await response.json();
  document.querySelectorAll('[data-step]').forEach((node, index) => {
    node.addEventListener('click', () => {
      document.querySelectorAll('[data-step]').forEach(item => item.classList.remove('selected'));
      node.classList.add('selected');
      console.log('QualiBug intake step selected', data.intake_steps[index]?.id);
    });
  });
}
loadCustomerIntakeData().catch(error => console.warn('QualiBug customer intake demo data failed to load', error));
"""


def render_customer_intake_readme(manifest: Mapping[str, Any]) -> str:
    return f"""# Phase105C 客户资料导入页体验

本目录是 QualiBug Phase105C 的前端显示层输出，重点是产品入口页：客户资料导入。

## 目标

- 让客户知道应该上传哪些企业资料。
- 让销售和实施人员解释 AI 会如何生成行业识别、业务链路、角色矩阵、环境配置草案和测试计划输入。
- 让前端团队获得可复用的信息架构和静态交互原型。
- 所有凭证、会话、客户敏感原文默认只展示状态和摘要，不展示原值。

## 文件

- `customer_intake.html`：客户资料导入页。
- `assets/qualibug_customer_intake.css`：页面样式。
- `assets/qualibug_customer_intake.js`：轻量交互脚本。
- `data/customer_intake_experience_data.json`：前端数据模型。
- `{CUSTOMER_INTAKE_MANIFEST}`：产物清单。
- `{CUSTOMER_INTAKE_ACCEPTANCE_MD}`：验收报告。

## 当前产物

- 场景：{manifest.get('scenario')}
- 版本：{manifest.get('version')}
- 页面：{manifest.get('entrypoint')}
- 脱敏状态：{manifest.get('redaction_status')}
"""


def build_customer_intake_experience(
    output_dir: str | Path, scenario: str = "manufacturing", api_base_url: str = os.environ.get("QUALIBUG_API_BASE_URL", "http://127.0.0.1:8088")
) -> dict[str, Any]:
    out = Path(output_dir)
    data = collect_customer_intake_experience_data(scenario=scenario, api_base_url=api_base_url)

    _write_text(out / "data" / "customer_intake_experience_data.json", _json_dump(data))
    _write_text(out / "assets" / "qualibug_customer_intake.css", render_customer_intake_css())
    _write_text(out / "assets" / "qualibug_customer_intake.js", render_customer_intake_js())
    _write_text(out / "customer_intake.html", render_customer_intake_html(data))

    manifest = redact_value(
        {
            "version": PHASE105C_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "output_dir": str(out),
            "entrypoint": "customer_intake.html",
            "required_files": list(REQUIRED_CUSTOMER_INTAKE_FILES),
            "core_labels": list(CORE_CUSTOMER_INTAKE_LABELS),
            "data_file": "data/customer_intake_experience_data.json",
            "phase104_handoff_ready": True,
            "redaction_status": "safe",
        }
    )
    _write_text(out / CUSTOMER_INTAKE_MANIFEST, _json_dump(manifest))
    _write_text(out / "README_CUSTOMER_INTAKE_EXPERIENCE.md", render_customer_intake_readme(manifest))
    return manifest


def scan_customer_intake_for_secret_leaks(output_dir: str | Path) -> list[str]:
    out = Path(output_dir)
    leaks: list[str] = []
    for path in out.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js", ".json", ".md"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_CUSTOMER_INTAKE_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(out)} contains forbidden pattern {pattern}")
    return leaks


def validate_customer_intake_experience(output_dir: str | Path) -> CustomerIntakeExperienceAcceptanceReport:
    out = Path(output_dir)
    checks: list[CustomerIntakeExperienceCheck] = []

    missing = [name for name in REQUIRED_CUSTOMER_INTAKE_FILES if not (out / name).exists()]
    checks.append(
        CustomerIntakeExperienceCheck(
            "required_files",
            not missing,
            "all required customer intake files exist" if not missing else "missing: " + ", ".join(missing),
        )
    )

    html_text = (out / "customer_intake.html").read_text(encoding="utf-8", errors="ignore") if (out / "customer_intake.html").exists() else ""
    missing_labels = [label for label in CORE_CUSTOMER_INTAKE_LABELS if label not in html_text]
    checks.append(
        CustomerIntakeExperienceCheck(
            "core_labels",
            not missing_labels,
            "customer intake page includes all core labels" if not missing_labels else "missing labels: " + ", ".join(missing_labels),
        )
    )

    data_path = out / "data" / "customer_intake_experience_data.json"
    data: dict[str, Any] = {}
    if data_path.exists():
        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            checks.append(CustomerIntakeExperienceCheck("data_json", False, f"invalid json: {exc}"))
    checks.append(
        CustomerIntakeExperienceCheck(
            "data_model",
            bool(data.get("project_draft") and data.get("business_flow_candidates") and data.get("phase104_handoff_payload")),
            "data model includes project draft, business flows, and Phase104 handoff payload"
            if data.get("project_draft") and data.get("business_flow_candidates") and data.get("phase104_handoff_payload")
            else "data model is incomplete",
        )
    )

    role_requirements = _as_list(data.get("role_requirements"))
    required_inputs = _as_list(data.get("required_customer_inputs"))
    safety_boundaries = _as_list(data.get("safety_boundaries"))
    checks.append(
        CustomerIntakeExperienceCheck(
            "intake_readiness",
            len(role_requirements) >= 3 and len(safety_boundaries) >= 3,
            "role/account matrix and safety boundaries are visible"
            if len(role_requirements) >= 3 and len(safety_boundaries) >= 3
            else "role/account matrix or safety boundaries are insufficient",
        )
    )
    checks.append(
        CustomerIntakeExperienceCheck(
            "customer_next_actions",
            bool(data.get("next_actions")) and isinstance(required_inputs, list),
            "customer next actions and required input list are generated"
            if data.get("next_actions") and isinstance(required_inputs, list)
            else "customer next actions are missing",
        )
    )

    manifest_path = out / CUSTOMER_INTAKE_MANIFEST
    manifest_ok = False
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_ok = manifest.get("version") == PHASE105C_VERSION and manifest.get("entrypoint") == "customer_intake.html"
        except json.JSONDecodeError:
            manifest_ok = False
    checks.append(
        CustomerIntakeExperienceCheck(
            "manifest",
            manifest_ok,
            "manifest references Phase105C customer intake entrypoint" if manifest_ok else "manifest missing or invalid",
        )
    )

    leaks = scan_customer_intake_for_secret_leaks(out)
    checks.append(
        CustomerIntakeExperienceCheck(
            "redaction",
            not leaks,
            "no forbidden credential or traceback patterns found" if not leaks else "; ".join(leaks),
        )
    )

    passed_count = sum(1 for check in checks if check.passed)
    score = int(round((passed_count / max(len(checks), 1)) * 100))
    return CustomerIntakeExperienceAcceptanceReport(
        passed=all(check.passed for check in checks),
        score=score,
        version=PHASE105C_VERSION,
        scenario=str(data.get("scenario") or "unknown"),
        output_dir=str(out),
        checks=checks,
        artifacts={"required_files": list(REQUIRED_CUSTOMER_INTAKE_FILES), "core_labels": list(CORE_CUSTOMER_INTAKE_LABELS)},
    )


def render_acceptance_markdown(report: CustomerIntakeExperienceAcceptanceReport) -> str:
    status = "PASSED" if report.passed else "FAILED"
    rows = "\n".join(
        f"| {check.key} | {'PASS' if check.passed else 'FAIL'} | {check.severity} | {check.detail} |" for check in report.checks
    )
    return f"""# Phase105C 客户资料导入页体验验收报告

- 状态：**{status}**
- 分数：**{report.score} / 100**
- 场景：{report.scenario}
- 输出目录：`{report.output_dir}`
- 版本：{report.version}

| 检查项 | 结果 | 严重级别 | 说明 |
|---|---|---|---|
{rows}
"""


def run_customer_intake_experience_export(
    *,
    output_dir: str | Path,
    scenario: str = "manufacturing",
    api_base_url: str = os.environ.get("QUALIBUG_API_BASE_URL", "http://127.0.0.1:8088"),
    validate_only: bool = False,
) -> dict[str, Any]:
    out = Path(output_dir)
    if not validate_only:
        build_customer_intake_experience(out, scenario=scenario, api_base_url=api_base_url)
    report = validate_customer_intake_experience(out)
    _write_text(out / CUSTOMER_INTAKE_ACCEPTANCE_JSON, _json_dump(report.to_dict()))
    _write_text(out / CUSTOMER_INTAKE_ACCEPTANCE_MD, render_acceptance_markdown(report))
    manifest = json.loads((out / CUSTOMER_INTAKE_MANIFEST).read_text(encoding="utf-8")) if (out / CUSTOMER_INTAKE_MANIFEST).exists() else None
    return {"manifest": manifest, "acceptance": report.to_dict()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or validate the Phase105C customer intake experience.")
    parser.add_argument("--output-dir", default="outputs/phase105_customer_intake_experience")
    parser.add_argument("--scenario", default="manufacturing", choices=["manufacturing", "ecommerce", "saas"])
    parser.add_argument("--api-base-url", default=os.environ.get("QUALIBUG_API_BASE_URL", "http://127.0.0.1:8088"))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    result = run_customer_intake_experience_export(
        output_dir=args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(json.dumps(result["acceptance"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["acceptance"].get("passed") else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

