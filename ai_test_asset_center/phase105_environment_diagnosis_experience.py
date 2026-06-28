from __future__ import annotations

"""Phase105D: customer environment diagnosis experience for the frontend layer.

Phase105A created the product shell, Phase105B strengthened the executive
quality dashboard, and Phase105C created the customer-intake entry page.
Phase105D focuses the environment diagnosis center, which is the key product
screen for explaining whether a customer system is reachable, authenticated,
executable, and safe to test.

The generator is framework-neutral and dependency-free. It creates static
HTML/CSS/JS plus a redacted JSON data model so a future React/Vue frontend can
bind the page to Phase104 API data without rethinking the UX.
"""

import argparse
import html
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase105_frontend_product_shell import collect_product_shell_demo_data

PHASE105D_VERSION = "phase105d-environment-diagnosis-experience-v1"

ENVIRONMENT_DIAGNOSIS_MANIFEST = "environment_diagnosis_experience_manifest.json"
ENVIRONMENT_DIAGNOSIS_ACCEPTANCE_JSON = "environment_diagnosis_experience_acceptance_report.json"
ENVIRONMENT_DIAGNOSIS_ACCEPTANCE_MD = "environment_diagnosis_experience_acceptance_report.md"

REQUIRED_ENVIRONMENT_DIAGNOSIS_FILES: tuple[str, ...] = (
    "environment_diagnosis.html",
    "README_ENVIRONMENT_DIAGNOSIS_EXPERIENCE.md",
    "data/environment_diagnosis_experience_data.json",
    "assets/qualibug_environment_diagnosis.css",
    "assets/qualibug_environment_diagnosis.js",
    ENVIRONMENT_DIAGNOSIS_MANIFEST,
)

FORBIDDEN_ENVIRONMENT_DIAGNOSIS_PATTERNS: tuple[str, ...] = (
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

CORE_ENVIRONMENT_DIAGNOSIS_LABELS: tuple[str, ...] = (
    "环境诊断中心",
    "可测性评分",
    "阻断原因",
    "URL / DNS / HTTP",
    "认证与会话",
    "API Smoke",
    "安全执行模式",
    "客户补料清单",
    "下一步动作",
    "重新预检",
)

DIAGNOSIS_STAGES: tuple[dict[str, str], ...] = (
    {"id": "url", "label": "URL 解析", "intent": "确认目标地址、协议、Host 与端口是否有效。"},
    {"id": "dns", "label": "DNS / Host", "intent": "确认客户预生产或测试域名是否能解析。"},
    {"id": "http", "label": "HTTP / HTTPS", "intent": "确认服务可达、状态码和响应类型是否可解释。"},
    {"id": "auth", "label": "认证与会话", "intent": "确认账号、CSRF、Token、Cookie、Session 是否可建立。"},
    {"id": "api_smoke", "label": "API Smoke", "intent": "确认核心业务 API 是否可用且账号权限足够。"},
    {"id": "safety", "label": "安全执行模式", "intent": "确认只读测试、沙箱边界和不可执行探针。"},
    {"id": "handoff", "label": "客户补料", "intent": "把环境阻断转成客户能执行的资料补充清单。"},
)

STATUS_LABELS: dict[str, str] = {
    "ready": "可正式测试",
    "passed": "通过",
    "partial_passed": "部分通过",
    "needs_customer_input": "待客户补料",
    "blocked": "阻断",
    "failed": "失败",
    "warning": "需关注",
    "unknown": "待确认",
}

STATUS_CLASS: dict[str, str] = {
    "ready": "good",
    "passed": "good",
    "partial_passed": "warn",
    "needs_customer_input": "warn",
    "blocked": "bad",
    "failed": "bad",
    "warning": "warn",
    "unknown": "muted",
}


@dataclass(frozen=True)
class EnvironmentDiagnosisExperienceCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class EnvironmentDiagnosisExperienceAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    checks: list[EnvironmentDiagnosisExperienceCheck] = field(default_factory=list)
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


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _status_label(status: Any) -> str:
    return STATUS_LABELS.get(str(status or "unknown"), _safe_text(status, "待确认"))


def _status_class(status: Any) -> str:
    return STATUS_CLASS.get(str(status or "unknown"), "muted")


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _escape(value: Any) -> str:
    return html.escape(_safe_text(value), quote=True)


def _extract_api_smoke(environment: Mapping[str, Any]) -> dict[str, Any]:
    checks = _as_mapping(environment.get("checks"))
    api_smoke = _as_mapping(checks.get("api_smoke"))
    items = [dict(item) for item in _as_list(api_smoke.get("items")) if isinstance(item, Mapping)]
    passed = _int_value(api_smoke.get("passed"))
    failed = _int_value(api_smoke.get("failed"))
    total = _int_value(api_smoke.get("total"), len(items))
    return {
        "status": _safe_text(api_smoke.get("status"), "unknown"),
        "passed": passed,
        "failed": failed,
        "total": total or len(items),
        "items": items,
        "health_note": "核心 API 可用于正式测试" if failed == 0 and items else "仍存在 API 权限、路径或认证上下文问题",
    }


def _extract_check_cards(environment: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks = _as_mapping(environment.get("checks"))
    url = _as_mapping(checks.get("url"))
    dns = _as_mapping(checks.get("dns"))
    http_check = _as_mapping(checks.get("http"))
    auth = _as_mapping(checks.get("auth"))
    api_smoke = _extract_api_smoke(environment)
    blockers = _as_list(environment.get("current_blockers"))

    url_host = _safe_text(url.get("host"), "待客户补充目标 Host")
    return [
        {
            "id": "url",
            "title": "URL 解析",
            "status": _safe_text(url.get("status"), "unknown"),
            "headline": url_host,
            "detail": f"{_safe_text(url.get('scheme'), 'https')}://{url_host}:{_safe_text(url.get('port'), '443')}",
            "business_explanation": "目标地址格式可用于后续探针执行。" if url.get("valid") else "目标地址格式或 Host 需要客户确认。",
            "next_action": _safe_text(url.get("issue"), "无需补充。"),
        },
        {
            "id": "dns",
            "title": "DNS / Host",
            "status": _safe_text(dns.get("status") or dns.get("result"), "unknown"),
            "headline": f"{_safe_text(dns.get('latency_ms'), '—')} ms",
            "detail": _safe_text(dns.get("interpretation"), "等待 DNS 探测结果。"),
            "business_explanation": "客户测试环境可被访问到。" if dns.get("status") == "passed" or dns.get("result") == "passed" else "当前网络路径可能阻断后续测试。",
            "next_action": "如客户网络白名单限制，请开放测试机出口或提供 VPN / 代理说明。",
        },
        {
            "id": "http",
            "title": "HTTP / HTTPS",
            "status": _safe_text(http_check.get("status"), "unknown"),
            "headline": f"HTTP { _safe_text(http_check.get('status_code'), '—') }",
            "detail": _safe_text(http_check.get("interpretation"), "等待 HTTP 探测结果。"),
            "business_explanation": "服务可达，后续关键在认证和业务权限。" if http_check.get("reachable") else "服务不可达，无法进入正式测试。",
            "next_action": "确认环境地址、证书、网关、IP 白名单和预生产访问策略。",
        },
        {
            "id": "auth",
            "title": "认证与会话",
            "status": _safe_text(auth.get("status"), "unknown"),
            "headline": _safe_text(auth.get("auth_type"), "待确认认证方式"),
            "detail": "CSRF / Token / Cookie / Session 均只展示安全状态，不展示原值。",
            "business_explanation": "已具备登录态，可进入业务接口 smoke。" if auth.get("status") == "passed" else "认证未完成，业务链路无法验证。",
            "next_action": _safe_text(auth.get("issue"), "如存在 MFA/SSO，请提供测试绕过方式或服务账号。"),
        },
        {
            "id": "api_smoke",
            "title": "API Smoke",
            "status": _safe_text(api_smoke.get("status"), "unknown"),
            "headline": f"{api_smoke['passed']} / {api_smoke['total']} 通过",
            "detail": api_smoke["health_note"],
            "business_explanation": "用最小业务 API 验证账号权限、路由和认证上下文。",
            "next_action": "修复失败 API 的权限、路径或租户绑定。" if api_smoke["failed"] else "可进入更深测试计划。",
        },
        {
            "id": "blockers",
            "title": "阻断原因",
            "status": "passed" if not blockers else "needs_customer_input",
            "headline": f"{len(blockers)} 项待处理",
            "detail": "；".join(str(item) for item in blockers[:3]) if blockers else "暂无阻断项。",
            "business_explanation": "把技术阻断转成客户能处理的补料清单。",
            "next_action": "按优先级补充测试账号、权限、网关、API 样例或安全边界说明。",
        },
    ]


def _extract_required_inputs(environment: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_inputs = [dict(item) for item in _as_list(environment.get("required_customer_inputs")) if isinstance(item, Mapping)]
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_inputs, start=1):
        normalized.append(
            {
                "rank": index,
                "title": _safe_text(item.get("title"), "待补充资料"),
                "type": _safe_text(item.get("type"), "input"),
                "priority": _safe_text(item.get("priority"), "medium"),
                "status": _safe_text(item.get("status"), "pending"),
                "why_needed": _safe_text(item.get("why_needed"), "用于完成客户环境接入和测试准入。"),
                "impact": _safe_text(item.get("impact"), "相关业务链路无法完整验证。"),
                "suggested_input": _safe_text(item.get("suggested_input"), "请客户提供对应资料。"),
                "affected_flows": [str(flow) for flow in _as_list(item.get("affected_flows"))],
            }
        )
    return normalized


def _extract_business_impact(demo: Mapping[str, Any], environment: Mapping[str, Any]) -> list[dict[str, Any]]:
    impacts: list[dict[str, Any]] = []
    api_smoke = _extract_api_smoke(environment)
    for item in api_smoke["items"]:
        affected_flow = _safe_text(item.get("affected_flow"), "核心业务链路")
        result = _safe_text(item.get("result"), "unknown")
        impacts.append(
            {
                "flow": affected_flow,
                "status": "passed" if result == "passed" else "blocked",
                "api": f"{_safe_text(item.get('method'), 'GET')} {_safe_text(item.get('path'), '/')}",
                "business_effect": "该链路可进入后续 AI 测试计划。" if result == "passed" else _safe_text(item.get("issue"), "该链路暂不能完整验证。"),
                "evidence_summary": f"状态码 {_safe_text(item.get('status_code'), '—')} · {_safe_text(item.get('content_type'), 'unknown')}",
            }
        )

    if not impacts:
        test_plan = _as_mapping(demo.get("test_plan"))
        for group in _as_list(test_plan.get("probe_groups"))[:4]:
            if isinstance(group, Mapping):
                blocked = _int_value(group.get("probe_blocked"))
                impacts.append(
                    {
                        "flow": _safe_text(group.get("business_flow_name"), "核心业务链路"),
                        "status": "blocked" if blocked else "ready",
                        "api": "待 API smoke",
                        "business_effect": "存在阻断探针，需要客户补充环境资料。" if blocked else "可进入测试计划。",
                        "evidence_summary": f"可执行探针 {_safe_text(group.get('probe_executable'), '0')} · 阻断探针 {blocked}",
                    }
                )
    return impacts


def _build_environment_diagnosis_data(scenario: str = "manufacturing", api_base_url: str = "http://127.0.0.1:8790") -> dict[str, Any]:
    demo = collect_product_shell_demo_data(scenario=scenario, api_base_url=api_base_url)
    environment = _as_mapping(demo.get("environment"))
    project = _as_mapping(demo.get("project"))
    dashboard = _as_mapping(demo.get("dashboard"))
    api_smoke = _extract_api_smoke(environment)
    blockers = [str(item) for item in _as_list(environment.get("current_blockers"))]
    required_inputs = _extract_required_inputs(environment)
    score = _int_value(environment.get("score"), 0)
    allow_formal_test = bool(environment.get("allow_formal_test"))
    status = _safe_text(environment.get("status"), "unknown")

    if allow_formal_test:
        readiness_verdict = "环境可进入正式 AI 测试"
        readiness_reason = "URL、认证、关键 API smoke 与安全边界均满足当前测试准入。"
    elif blockers:
        readiness_verdict = "暂缓正式测试，需客户补料"
        readiness_reason = "系统已识别阻断原因，可按补料清单处理后重新预检。"
    else:
        readiness_verdict = "可进入受限测试"
        readiness_reason = "环境基本可达，但仍建议先在只读或沙箱模式执行。"

    suggested_actions = [str(item) for item in _as_list(environment.get("suggested_actions"))]
    if not suggested_actions and required_inputs:
        suggested_actions = [item["suggested_input"] for item in required_inputs]
    if not suggested_actions:
        suggested_actions = ["确认安全边界后进入 AI 测试计划。"]

    data = {
        "version": PHASE105D_VERSION,
        "generated_at": _now(),
        "scenario": scenario,
        "api_base_url": api_base_url.rstrip("/"),
        "project": project,
        "readiness_summary": {
            "score": score,
            "status": status,
            "status_label": _status_label(status),
            "status_class": _status_class(status),
            "allow_formal_test": allow_formal_test,
            "safe_execution_mode": _safe_text(environment.get("safe_execution_mode"), "read_only"),
            "readiness_verdict": readiness_verdict,
            "readiness_reason": readiness_reason,
            "last_checked_at": _safe_text(environment.get("last_checked_at"), _now()),
            "redaction_status": _safe_text(environment.get("redaction_status"), "safe"),
        },
        "diagnosis_stages": list(DIAGNOSIS_STAGES),
        "check_cards": _extract_check_cards(environment),
        "api_smoke": api_smoke,
        "current_blockers": blockers,
        "required_customer_inputs": required_inputs,
        "business_impact": _extract_business_impact(demo, environment),
        "suggested_actions": suggested_actions,
        "safety_model": {
            "mode": _safe_text(environment.get("safe_execution_mode"), "read_only"),
            "principles": [
                "默认只读测试，不修改客户生产数据。",
                "前端只展示认证状态和脱敏摘要，不展示 Token、Cookie、Session 原值。",
                "API smoke 失败时先解释环境与权限，不误判为业务 Bug。",
                "客户补料完成后先重新预检，再进入正式测试计划。",
            ],
        },
        "phase104_actions": {
            "save_environment_config": f"PATCH /api/v1/projects/{project.get('project_id', '{project_id}')}/environment/config",
            "run_preflight": f"POST /api/v1/projects/{project.get('project_id', '{project_id}')}/environment/preflight",
            "generate_test_plan": f"POST /api/v1/projects/{project.get('project_id', '{project_id}')}/test-plan/generate",
        },
        "dashboard_context": {
            "quality_score": dashboard.get("quality_score"),
            "launch_recommendation": dashboard.get("launch_decision", {}).get("recommendation") if isinstance(dashboard.get("launch_decision"), Mapping) else None,
            "top_risk_count": len(_as_list(dashboard.get("top_risks"))),
        },
        "core_labels": list(CORE_ENVIRONMENT_DIAGNOSIS_LABELS),
    }
    return redact_value(data)


def _render_status_pill(status: Any) -> str:
    return f'<span class="status-pill {html.escape(_status_class(status), quote=True)}">{_escape(_status_label(status))}</span>'


def _render_score_ring(score: int) -> str:
    score = max(0, min(100, score))
    return f"""
      <div class="score-ring" style="--score:{score}">
        <div class="score-ring__inner">
          <strong>{score}</strong>
          <span>可测性评分</span>
        </div>
      </div>
    """


def _render_diagnosis_html(data: Mapping[str, Any]) -> str:
    project = _as_mapping(data.get("project"))
    summary = _as_mapping(data.get("readiness_summary"))
    check_cards = [dict(item) for item in _as_list(data.get("check_cards")) if isinstance(item, Mapping)]
    api_smoke = _as_mapping(data.get("api_smoke"))
    blockers = [str(item) for item in _as_list(data.get("current_blockers"))]
    required_inputs = [dict(item) for item in _as_list(data.get("required_customer_inputs")) if isinstance(item, Mapping)]
    impacts = [dict(item) for item in _as_list(data.get("business_impact")) if isinstance(item, Mapping)]
    actions = [str(item) for item in _as_list(data.get("suggested_actions"))]
    safety = _as_mapping(data.get("safety_model"))

    check_cards_html = "\n".join(
        f"""
        <article class="check-card check-card--{_escape(_status_class(card.get('status')))}">
          <div class="check-card__head">
            <div>
              <span class="eyebrow">{_escape(card.get('id'))}</span>
              <h3>{_escape(card.get('title'))}</h3>
            </div>
            {_render_status_pill(card.get('status'))}
          </div>
          <strong class="check-card__headline">{_escape(card.get('headline'))}</strong>
          <p>{_escape(card.get('detail'))}</p>
          <div class="check-card__explain">业务解释：{_escape(card.get('business_explanation'))}</div>
          <div class="next-action">下一步：{_escape(card.get('next_action'))}</div>
        </article>
        """
        for card in check_cards
    )

    smoke_rows = "\n".join(
        f"""
        <tr>
          <td>{_escape(item.get('method'))}</td>
          <td><code>{_escape(item.get('path'))}</code></td>
          <td>{_escape(item.get('affected_flow'))}</td>
          <td>{_render_status_pill(item.get('result'))}</td>
          <td>{_escape(item.get('status_code'))}</td>
          <td>{_escape(item.get('issue'))}</td>
        </tr>
        """
        for item in _as_list(api_smoke.get("items"))
        if isinstance(item, Mapping)
    ) or '<tr><td colspan="6">暂无 API smoke 数据，等待环境预检。</td></tr>'

    blockers_html = "\n".join(f"<li>{_escape(item)}</li>" for item in blockers) or "<li>暂无阻断项，可以进入下一步。</li>"

    inputs_html = "\n".join(
        f"""
        <article class="input-card priority-{_escape(item.get('priority'))}">
          <div class="input-card__rank">#{_escape(item.get('rank'))}</div>
          <div>
            <div class="input-card__meta">{_escape(item.get('type'))} · { _escape(item.get('priority')) } · { _escape(item.get('status')) }</div>
            <h3>{_escape(item.get('title'))}</h3>
            <p>{_escape(item.get('why_needed'))}</p>
            <div class="input-card__impact">影响：{_escape(item.get('impact'))}</div>
            <div class="next-action">建议提供：{_escape(item.get('suggested_input'))}</div>
          </div>
        </article>
        """
        for item in required_inputs
    ) or '<article class="input-card"><div><h3>暂无客户补料项</h3><p>当前环境资料满足下一步测试准入。</p></div></article>'

    impacts_html = "\n".join(
        f"""
        <div class="impact-row impact-row--{_escape(item.get('status'))}">
          <div>
            <strong>{_escape(item.get('flow'))}</strong>
            <span>{_escape(item.get('api'))}</span>
          </div>
          <p>{_escape(item.get('business_effect'))}</p>
          <small>{_escape(item.get('evidence_summary'))}</small>
        </div>
        """
        for item in impacts
    )

    action_html = "\n".join(
        f"""
        <li>
          <span class="action-index">{idx}</span>
          <span>{_escape(action)}</span>
        </li>
        """
        for idx, action in enumerate(actions, start=1)
    )

    safety_html = "\n".join(f"<li>{_escape(item)}</li>" for item in _as_list(safety.get("principles")))

    score = _int_value(summary.get("score"))
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>QualiBug AI · 环境诊断中心</title>
  <link rel="stylesheet" href="assets/qualibug_environment_diagnosis.css" />
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">QB</div>
        <div><strong>QualiBug AI</strong><span>企业质量指挥中心</span></div>
      </div>
      <nav>
        <a href="#summary" class="active">环境诊断中心</a>
        <a href="#checks">URL / DNS / HTTP</a>
        <a href="#auth">认证与会话</a>
        <a href="#smoke">API Smoke</a>
        <a href="#inputs">客户补料清单</a>
        <a href="#actions">下一步动作</a>
      </nav>
    </aside>

    <main class="content">
      <header class="topbar">
        <div>
          <span class="eyebrow">Phase105D · Frontend Experience</span>
          <h1>环境诊断中心</h1>
          <p>把客户环境是否可测、哪里阻断、为什么阻断、下一步补什么讲清楚。</p>
        </div>
        <div class="project-chip">
          <span>当前项目</span>
          <strong>{_escape(project.get('project_name') or project.get('name') or project.get('project_id'))}</strong>
        </div>
      </header>

      <section id="summary" class="hero-grid">
        <div class="hero-card">
          <div class="hero-card__copy">
            <span class="eyebrow">客户环境可测性</span>
            <h2>{_escape(summary.get('readiness_verdict'))}</h2>
            <p>{_escape(summary.get('readiness_reason'))}</p>
            <div class="hero-actions">
              <button data-action="rerun">重新预检</button>
              <button class="secondary" data-action="handoff">复制补料清单</button>
            </div>
          </div>
          {_render_score_ring(score)}
        </div>
        <div class="summary-stack">
          <div class="metric-card">
            <span>状态</span>
            <strong>{_escape(summary.get('status_label'))}</strong>
            {_render_status_pill(summary.get('status'))}
          </div>
          <div class="metric-card">
            <span>安全执行模式</span>
            <strong>{_escape(summary.get('safe_execution_mode'))}</strong>
            <small>默认只读 / 沙箱优先</small>
          </div>
          <div class="metric-card">
            <span>脱敏状态</span>
            <strong>{_escape(summary.get('redaction_status'))}</strong>
            <small>不展示 Token / Cookie / Session 原值</small>
          </div>
        </div>
      </section>

      <section id="checks" class="section-card">
        <div class="section-title">
          <div>
            <span class="eyebrow">Readiness Checks</span>
            <h2>URL / DNS / HTTP / 认证 / API smoke 分层诊断</h2>
          </div>
          <span class="timestamp">最后预检：{_escape(summary.get('last_checked_at'))}</span>
        </div>
        <div class="check-grid">{check_cards_html}</div>
      </section>

      <section id="auth" class="two-column">
        <article class="section-card">
          <span class="eyebrow">Blocking Reasons</span>
          <h2>阻断原因</h2>
          <ul class="blocker-list">{blockers_html}</ul>
        </article>
        <article class="section-card">
          <span class="eyebrow">Safety Model</span>
          <h2>安全执行模式</h2>
          <p>当前模式：<strong>{_escape(safety.get('mode'))}</strong></p>
          <ul class="safety-list">{safety_html}</ul>
        </article>
      </section>

      <section id="smoke" class="section-card">
        <div class="section-title">
          <div>
            <span class="eyebrow">API Smoke</span>
            <h2>核心业务 API 可测性</h2>
          </div>
          <div class="smoke-summary">
            <strong>{_escape(api_smoke.get('passed'))}/{_escape(api_smoke.get('total'))}</strong>
            <span>通过</span>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>方法</th><th>路径</th><th>业务链路</th><th>结果</th><th>状态码</th><th>解释</th></tr></thead>
            <tbody>{smoke_rows}</tbody>
          </table>
        </div>
      </section>

      <section class="two-column">
        <article class="section-card" id="inputs">
          <span class="eyebrow">Customer Handoff</span>
          <h2>客户补料清单</h2>
          <div class="input-list">{inputs_html}</div>
        </article>
        <article class="section-card">
          <span class="eyebrow">Business Impact</span>
          <h2>环境问题影响哪些业务链路</h2>
          <div class="impact-list">{impacts_html}</div>
        </article>
      </section>

      <section id="actions" class="section-card action-card">
        <div>
          <span class="eyebrow">Next Actions</span>
          <h2>下一步动作</h2>
          <p>让客户知道现在该补什么，而不是只看到技术错误。</p>
        </div>
        <ol class="action-list">{action_html}</ol>
      </section>
    </main>
  </div>
  <script id="environment-diagnosis-data" type="application/json">{html.escape(json.dumps(redact_value(data), ensure_ascii=False), quote=False)}</script>
  <script src="assets/qualibug_environment_diagnosis.js"></script>
</body>
</html>
"""
    return html_doc


def _render_css() -> str:
    return """:root {
  color-scheme: dark;
  --bg: #08111f;
  --panel: #101b2f;
  --panel-2: #14233a;
  --line: rgba(148, 163, 184, 0.18);
  --text: #e5eefb;
  --muted: #91a4bd;
  --brand: #68e1fd;
  --good: #52e6a8;
  --warn: #ffd166;
  --bad: #ff6b7a;
  --shadow: 0 24px 80px rgba(0,0,0,.35);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; background: radial-gradient(circle at 15% 0%, rgba(104,225,253,.18), transparent 36%), var(--bg); color: var(--text); }
a { color: inherit; text-decoration: none; }
.app-shell { min-height: 100vh; display: grid; grid-template-columns: 280px minmax(0, 1fr); }
.sidebar { position: sticky; top: 0; height: 100vh; padding: 28px 20px; background: rgba(7, 13, 25, .86); border-right: 1px solid var(--line); backdrop-filter: blur(18px); }
.brand { display: flex; gap: 12px; align-items: center; margin-bottom: 30px; }
.brand-mark { width: 44px; height: 44px; border-radius: 16px; display: grid; place-items: center; background: linear-gradient(135deg, var(--brand), #8b5cf6); color: #04111f; font-weight: 900; }
.brand span, .metric-card span, .eyebrow, small { color: var(--muted); }
nav { display: grid; gap: 8px; }
nav a { padding: 12px 14px; border-radius: 14px; color: var(--muted); }
nav a.active, nav a:hover { color: var(--text); background: rgba(104, 225, 253, .12); }
.content { padding: 30px; display: grid; gap: 24px; }
.topbar { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; }
h1, h2, h3, p { margin-top: 0; }
h1 { margin-bottom: 8px; font-size: clamp(28px, 4vw, 44px); }
h2 { margin-bottom: 10px; }
p { color: var(--muted); line-height: 1.7; }
.eyebrow { display: inline-block; margin-bottom: 8px; text-transform: uppercase; letter-spacing: .12em; font-size: 12px; font-weight: 800; }
.project-chip, .metric-card, .section-card, .hero-card { border: 1px solid var(--line); background: linear-gradient(180deg, rgba(16, 27, 47, .94), rgba(11, 20, 37, .92)); box-shadow: var(--shadow); }
.project-chip { min-width: 260px; border-radius: 18px; padding: 16px; display: grid; gap: 6px; }
.hero-grid { display: grid; grid-template-columns: minmax(0, 1fr) 290px; gap: 18px; }
.hero-card { border-radius: 28px; padding: 28px; display: flex; justify-content: space-between; gap: 24px; align-items: center; overflow: hidden; position: relative; }
.hero-card:before { content: ""; position: absolute; inset: -40% -10% auto auto; width: 320px; height: 320px; border-radius: 999px; background: rgba(104,225,253,.16); filter: blur(6px); }
.hero-card__copy { position: relative; max-width: 760px; }
.hero-actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 18px; }
button { border: 0; border-radius: 14px; padding: 12px 16px; color: #04111f; background: var(--brand); font-weight: 800; cursor: pointer; }
button.secondary { color: var(--text); background: rgba(148, 163, 184, .14); border: 1px solid var(--line); }
.score-ring { --score: 0; width: 170px; height: 170px; border-radius: 50%; display: grid; place-items: center; background: conic-gradient(var(--brand) calc(var(--score) * 1%), rgba(148,163,184,.18) 0); position: relative; flex: 0 0 auto; }
.score-ring__inner { width: 126px; height: 126px; border-radius: 50%; background: #08111f; display: grid; place-items: center; text-align: center; padding: 16px; }
.score-ring strong { font-size: 42px; line-height: 1; }
.score-ring span { color: var(--muted); font-size: 13px; }
.summary-stack { display: grid; gap: 14px; }
.metric-card { border-radius: 22px; padding: 18px; }
.metric-card strong { display: block; margin: 8px 0; font-size: 22px; }
.section-card { border-radius: 24px; padding: 22px; }
.section-title { display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; margin-bottom: 16px; }
.timestamp { color: var(--muted); font-size: 13px; }
.check-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.check-card { border-radius: 20px; padding: 18px; border: 1px solid var(--line); background: rgba(20,35,58,.75); }
.check-card--good { border-color: rgba(82,230,168,.32); }
.check-card--warn { border-color: rgba(255,209,102,.35); }
.check-card--bad { border-color: rgba(255,107,122,.35); }
.check-card__head { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
.check-card__headline { display: block; margin: 10px 0; font-size: 20px; }
.check-card__explain, .next-action, .input-card__impact { border-radius: 14px; padding: 10px; background: rgba(148,163,184,.08); color: #c7d2e2; line-height: 1.55; margin-top: 10px; }
.status-pill { display: inline-flex; align-items: center; border-radius: 999px; padding: 6px 10px; font-size: 12px; font-weight: 800; white-space: nowrap; }
.status-pill.good { color: #06351f; background: var(--good); }
.status-pill.warn { color: #352500; background: var(--warn); }
.status-pill.bad { color: #3f0710; background: var(--bad); }
.status-pill.muted { color: var(--text); background: rgba(148,163,184,.18); }
.two-column { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 18px; }
.blocker-list, .safety-list { display: grid; gap: 10px; padding-left: 20px; color: #dbe7f5; line-height: 1.7; }
.table-wrap { overflow-x: auto; border-radius: 18px; border: 1px solid var(--line); }
table { width: 100%; border-collapse: collapse; min-width: 860px; }
th, td { padding: 14px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; background: rgba(148,163,184,.08); }
code { color: var(--brand); }
.smoke-summary { text-align: right; }
.smoke-summary strong { display: block; font-size: 28px; }
.input-list, .impact-list { display: grid; gap: 14px; }
.input-card { display: grid; grid-template-columns: 52px 1fr; gap: 14px; border: 1px solid var(--line); background: rgba(148,163,184,.06); border-radius: 18px; padding: 16px; }
.input-card__rank { width: 42px; height: 42px; border-radius: 14px; display: grid; place-items: center; background: rgba(104,225,253,.14); color: var(--brand); font-weight: 900; }
.input-card__meta { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
.impact-row { border-left: 4px solid var(--brand); background: rgba(148,163,184,.06); border-radius: 16px; padding: 14px; }
.impact-row--blocked, .impact-row--failed { border-left-color: var(--bad); }
.impact-row--passed, .impact-row--ready { border-left-color: var(--good); }
.impact-row span { display: block; margin-top: 4px; color: var(--muted); }
.impact-row small { display: block; margin-top: 8px; }
.action-card { display: grid; grid-template-columns: 320px 1fr; gap: 22px; }
.action-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
.action-list li { display: flex; gap: 12px; align-items: flex-start; padding: 14px; border-radius: 16px; background: rgba(104,225,253,.08); }
.action-index { width: 28px; height: 28px; border-radius: 999px; display: grid; place-items: center; flex: 0 0 auto; color: #04111f; background: var(--brand); font-weight: 900; }
@media (max-width: 1080px) { .app-shell { grid-template-columns: 1fr; } .sidebar { position: relative; height: auto; } .hero-grid, .two-column, .action-card { grid-template-columns: 1fr; } .check-grid { grid-template-columns: repeat(2, minmax(0,1fr)); } }
@media (max-width: 720px) { .content { padding: 18px; } .topbar, .hero-card { flex-direction: column; } .check-grid { grid-template-columns: 1fr; } .score-ring { width: 140px; height: 140px; } }
"""


def _render_js() -> str:
    return """(() => {
  const payload = document.getElementById('environment-diagnosis-data');
  const data = payload ? JSON.parse(payload.textContent || '{}') : {};
  const buttons = document.querySelectorAll('[data-action]');
  buttons.forEach((button) => {
    button.addEventListener('click', async () => {
      const action = button.getAttribute('data-action');
      if (action === 'handoff') {
        const inputs = (data.required_customer_inputs || [])
          .map((item, index) => `${index + 1}. ${item.title}：${item.suggested_input}`)
          .join('\n') || '暂无客户补料项。';
        try { await navigator.clipboard.writeText(inputs); button.textContent = '已复制补料清单'; }
        catch (_) { button.textContent = '补料清单已生成'; }
        setTimeout(() => { button.textContent = '复制补料清单'; }, 1800);
        return;
      }
      if (action === 'rerun') {
        button.textContent = '预检命令已准备';
        const route = data.phase104_actions?.run_preflight || 'POST /api/v1/projects/{project_id}/environment/preflight';
        console.info('[QualiBug] rerun preflight route:', route);
        setTimeout(() => { button.textContent = '重新预检'; }, 1800);
      }
    });
  });
})();
"""


def _render_readme(data: Mapping[str, Any]) -> str:
    summary = _as_mapping(data.get("readiness_summary"))
    return f"""# Phase105D 环境诊断中心真实 UI

Phase105D 聚焦前端显示层中的客户环境诊断中心。这个页面用于回答客户现场最常见的问题：为什么现在不能直接测、环境哪里被阻断、需要客户补什么、补完以后走哪个动作。

## 页面入口

- `environment_diagnosis.html`

## 核心能力

- 可测性评分：{_safe_text(summary.get('score'))}
- 环境状态：{_safe_text(summary.get('status_label'))}
- 安全执行模式：{_safe_text(summary.get('safe_execution_mode'))}
- URL / DNS / HTTP 分层诊断
- 认证、Token、Cookie、Session 只展示安全状态，不展示原值
- API Smoke 结果转成业务链路影响
- 阻断原因和客户补料清单
- 下一步动作队列：重新预检、生成测试计划、补充账号权限

## 输出文件

```text
environment_diagnosis.html
assets/qualibug_environment_diagnosis.css
assets/qualibug_environment_diagnosis.js
data/environment_diagnosis_experience_data.json
README_ENVIRONMENT_DIAGNOSIS_EXPERIENCE.md
environment_diagnosis_experience_manifest.json
environment_diagnosis_experience_acceptance_report.json
environment_diagnosis_experience_acceptance_report.md
```

## 运行

```powershell
python -m ai_test_asset_center.phase105_environment_diagnosis_experience --scenario manufacturing --output-dir .\\outputs\\phase105_environment_diagnosis_experience
Start-Process .\\outputs\\phase105_environment_diagnosis_experience\\environment_diagnosis.html
```

## 验收重点

1. 页面必须包含环境诊断中心、可测性评分、阻断原因、URL / DNS / HTTP、认证与会话、API Smoke、安全执行模式、客户补料清单、下一步动作。
2. 必须生成 JSON 数据和验收报告。
3. 禁止泄露 token / cookie / password / session / client_secret / traceback。
"""


def _scan_text_files(root: Path) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    if not root.exists():
        return findings
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js", ".json", ".md", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in FORBIDDEN_ENVIRONMENT_DIAGNOSIS_PATTERNS:
            if pattern in text:
                findings.append((str(path.relative_to(root)), pattern))
    return findings


def scan_environment_diagnosis_for_secret_leaks(output_dir: str | Path) -> list[tuple[str, str]]:
    return _scan_text_files(Path(output_dir))


def build_environment_diagnosis_experience(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
) -> dict[str, Any]:
    root = Path(output_dir)
    data = _build_environment_diagnosis_data(scenario=scenario, api_base_url=api_base_url)

    _write_text(root / "environment_diagnosis.html", _render_diagnosis_html(data))
    _write_text(root / "assets" / "qualibug_environment_diagnosis.css", _render_css())
    _write_text(root / "assets" / "qualibug_environment_diagnosis.js", _render_js())
    _write_text(root / "data" / "environment_diagnosis_experience_data.json", _json_dump(data))
    _write_text(root / "README_ENVIRONMENT_DIAGNOSIS_EXPERIENCE.md", _render_readme(data))

    leaks = scan_environment_diagnosis_for_secret_leaks(root)
    manifest = redact_value(
        {
            "version": PHASE105D_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "entrypoint": "environment_diagnosis.html",
            "output_dir": str(root),
            "core_labels": list(CORE_ENVIRONMENT_DIAGNOSIS_LABELS),
            "files": list(REQUIRED_ENVIRONMENT_DIAGNOSIS_FILES),
            "redaction_status": "safe" if not leaks else "unsafe",
            "secret_leaks": leaks,
            "page_goal": "让客户一眼看懂环境是否可测、哪里阻断、下一步补什么。",
        }
    )
    _write_text(root / ENVIRONMENT_DIAGNOSIS_MANIFEST, _json_dump(manifest))
    return manifest


def validate_environment_diagnosis_experience(output_dir: str | Path) -> EnvironmentDiagnosisExperienceAcceptanceReport:
    root = Path(output_dir)
    checks: list[EnvironmentDiagnosisExperienceCheck] = []

    def add(key: str, passed: bool, detail: str, severity: str = "critical") -> None:
        checks.append(EnvironmentDiagnosisExperienceCheck(key=key, passed=passed, detail=detail, severity=severity))

    missing = [rel for rel in REQUIRED_ENVIRONMENT_DIAGNOSIS_FILES if not (root / rel).exists()]
    add("required_files", not missing, "所有环境诊断 UI 文件已生成。" if not missing else f"缺少文件：{', '.join(missing)}")

    html_path = root / "environment_diagnosis.html"
    html_text = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
    missing_labels = [label for label in CORE_ENVIRONMENT_DIAGNOSIS_LABELS if label not in html_text]
    add("core_labels", not missing_labels, "环境诊断核心文案完整。" if not missing_labels else f"缺少核心文案：{', '.join(missing_labels)}")

    data_path = root / "data" / "environment_diagnosis_experience_data.json"
    data: dict[str, Any] = {}
    if data_path.exists():
        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
            required_keys = {"readiness_summary", "check_cards", "api_smoke", "current_blockers", "required_customer_inputs", "business_impact", "suggested_actions", "safety_model", "phase104_actions"}
            missing_keys = sorted(required_keys - set(data.keys()))
            add("data_contract", not missing_keys, "环境诊断数据合同完整。" if not missing_keys else f"缺少数据字段：{', '.join(missing_keys)}")
        except json.JSONDecodeError as exc:
            add("data_contract", False, f"环境诊断 JSON 不可解析：{exc}")
    else:
        add("data_contract", False, "缺少 environment_diagnosis_experience_data.json")

    readiness = _as_mapping(data.get("readiness_summary"))
    add("readiness_summary", bool(readiness.get("score") is not None and readiness.get("readiness_verdict")), "可测性评分和环境结论已生成。" if readiness else "缺少可测性评分或环境结论。")

    check_cards = _as_list(data.get("check_cards"))
    card_titles = {str(card.get("title")) for card in check_cards if isinstance(card, Mapping)}
    required_cards = {"URL 解析", "DNS / Host", "HTTP / HTTPS", "认证与会话", "API Smoke", "阻断原因"}
    missing_cards = sorted(required_cards - card_titles)
    add("diagnosis_cards", not missing_cards, "诊断卡片覆盖 URL/DNS/HTTP/认证/API/阻断。" if not missing_cards else f"缺少诊断卡片：{', '.join(missing_cards)}")

    api_smoke = _as_mapping(data.get("api_smoke"))
    add("api_smoke", bool(_as_list(api_smoke.get("items")) or api_smoke.get("total") is not None), "API Smoke 结果已展示。" if api_smoke else "缺少 API Smoke 结果。")

    inputs = _as_list(data.get("required_customer_inputs"))
    actions = _as_list(data.get("suggested_actions"))
    add("customer_handoff", bool(inputs or actions), "客户补料清单或下一步动作已生成。" if (inputs or actions) else "缺少客户补料与动作队列。")

    phase104_actions = _as_mapping(data.get("phase104_actions"))
    add("phase104_handoff", bool(phase104_actions.get("run_preflight") and phase104_actions.get("generate_test_plan")), "Phase104 预检与测试计划动作已声明。" if phase104_actions else "缺少 Phase104 动作交接。")

    leaks = scan_environment_diagnosis_for_secret_leaks(root)
    add("secret_scan", not leaks, "未发现原始密钥或 traceback 泄露。" if not leaks else f"发现疑似泄露：{leaks}")

    total = len(checks)
    passed_count = sum(1 for check in checks if check.passed)
    score = int(round((passed_count / total) * 100)) if total else 0
    report = EnvironmentDiagnosisExperienceAcceptanceReport(
        passed=all(check.passed for check in checks),
        score=score,
        version=PHASE105D_VERSION,
        scenario=str(data.get("scenario") or "unknown"),
        output_dir=str(root),
        checks=checks,
        artifacts={
            "entrypoint": "environment_diagnosis.html",
            "data": "data/environment_diagnosis_experience_data.json",
            "manifest": ENVIRONMENT_DIAGNOSIS_MANIFEST,
        },
    )
    return report


def _write_acceptance_report(output_dir: Path, report: EnvironmentDiagnosisExperienceAcceptanceReport) -> dict[str, Any]:
    payload = report.to_dict()
    _write_text(output_dir / ENVIRONMENT_DIAGNOSIS_ACCEPTANCE_JSON, _json_dump(payload))
    lines = [
        "# Phase105D 环境诊断中心体验验收报告",
        "",
        f"- 通过：{'是' if report.passed else '否'}",
        f"- 分数：{report.score}",
        f"- 版本：{report.version}",
        f"- 场景：{report.scenario}",
        "",
        "## 检查项",
        "",
    ]
    for check in report.checks:
        lines.append(f"- [{'x' if check.passed else ' '}] `{check.key}`：{check.detail}")
    lines.append("")
    _write_text(output_dir / ENVIRONMENT_DIAGNOSIS_ACCEPTANCE_MD, "\n".join(lines))
    return payload


def run_environment_diagnosis_experience_export(
    *,
    output_dir: str | Path,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    validate_only: bool = False,
) -> dict[str, Any]:
    root = Path(output_dir)
    manifest: dict[str, Any] | None = None
    if not validate_only:
        manifest = build_environment_diagnosis_experience(root, scenario=scenario, api_base_url=api_base_url)
    report = validate_environment_diagnosis_experience(root)
    acceptance = _write_acceptance_report(root, report)
    return redact_value({"manifest": manifest, "acceptance": acceptance})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or validate Phase105D environment diagnosis frontend experience.")
    parser.add_argument("--output-dir", default="outputs/phase105_environment_diagnosis_experience")
    parser.add_argument("--scenario", default="manufacturing", choices=("manufacturing", "ecommerce", "saas"))
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8790")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    result = run_environment_diagnosis_experience_export(
        output_dir=args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("acceptance", {}).get("passed") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
