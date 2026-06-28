from __future__ import annotations

"""Phase103U: static frontend bundle exporter for the Enterprise Command Center.

This module turns the Phase103T page-ready demo bundle into a self-contained
static UI package.  It is intentionally framework-free so product, sales,
implementation, and future frontend teams can open a complete command-center
prototype without waiting for a web stack.

Generated output includes:

* index.html and page-level HTML files for dashboard/environment/test plan/map/
  risks/report/value.
* a shared CSS file implementing the V1 enterprise command-center visual style.
* a redacted JavaScript data payload that frontend prototypes can consume.
* a JSON manifest describing the generated pages.

All exported content passes through the same redaction path used by the API
facade, and HTML text is escaped before rendering.
"""

import argparse
import html
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_demo_runner import seed_demo_project
from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase103_command_center_api import EnterpriseCommandCenterAPI

PHASE103U_VERSION = "phase103u-static-frontend-v1"


PAGE_ORDER: tuple[tuple[str, str, str], ...] = (
    ("dashboard", "质量驾驶舱", "dashboard.html"),
    ("environment", "环境适配中心", "environment.html"),
    ("test_plan", "AI 测试计划", "test_plan.html"),
    ("live_map", "实时测试地图", "live_map.html"),
    ("risks", "AI 风险发现", "risks.html"),
    ("report", "成果战报", "report.html"),
    ("value", "ROI 价值分析", "value.html"),
)


def _e(value: Any) -> str:
    """HTML-escape a value after redaction."""
    return html.escape(str(redact_value(value) if value is not None else ""), quote=True)


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _fmt_percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "-"


def _fmt_score(value: Any) -> str:
    try:
        return f"{float(value):.0f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_money_range(value: Mapping[str, Any] | None = None, *, min_value: Any = None, max_value: Any = None, currency: str = "CNY") -> str:
    if isinstance(value, Mapping):
        min_value = value.get("min", min_value)
        max_value = value.get("max", max_value)
        currency = str(value.get("currency") or currency)
    unit = "¥" if currency.upper() == "CNY" else f"{currency} "
    try:
        return f"{unit}{int(float(min_value)):,} - {unit}{int(float(max_value)):,}"
    except (TypeError, ValueError):
        return "-"


def _severity_label(severity: Any) -> str:
    mapping = {
        "critical": "高危",
        "high": "高风险",
        "medium": "中风险",
        "low": "低风险",
        "info": "提示",
    }
    return mapping.get(str(severity).lower(), str(severity or "未知"))


def _status_label(status: Any) -> str:
    mapping = {
        "ready": "已就绪",
        "partial_ready": "部分就绪",
        "blocked": "阻断",
        "needs_customer_input": "等待客户补充",
        "unsafe": "不适合写入测试",
        "passed": "通过",
        "failed": "失败",
        "warning": "隐患",
        "risk": "风险",
        "running": "执行中",
        "covered_with_risk": "已覆盖但存在风险",
        "covered_passed": "已覆盖且通过",
        "partial_covered": "部分覆盖",
        "not_covered": "未覆盖",
        "detected": "已发现",
        "confirmed": "已确认",
        "closed": "已关闭",
        "HOLD": "暂缓上线",
        "NO_GO": "不建议上线",
        "GO": "建议上线",
        "CONDITIONAL_GO": "可灰度上线",
    }
    return mapping.get(str(status), mapping.get(str(status).lower(), str(status or "未知")))


def _metric_card(title: str, value: Any, *, unit: str = "", subtitle: str = "", status: str = "") -> str:
    status_class = f" status-{html.escape(status.lower(), quote=True)}" if status else ""
    return (
        f'<section class="metric-card{status_class}">'
        f'<div class="metric-title">{_e(title)}</div>'
        f'<div class="metric-value">{_e(value)}<span>{_e(unit)}</span></div>'
        f'<div class="metric-subtitle">{_e(subtitle)}</div>'
        "</section>"
    )


def _risk_card(risk: Mapping[str, Any], *, compact: bool = False) -> str:
    flow = _safe_dict(risk.get("affected_business_flow"))
    severity = str(risk.get("severity") or "unknown").lower()
    badges = [f'<span class="badge severity-{_e(severity)}">{_e(_severity_label(severity))}</span>']
    if risk.get("launch_blocking"):
        badges.append('<span class="badge danger">阻断上线</span>')
    if risk.get("status"):
        badges.append(f'<span class="badge muted">{_e(_status_label(risk.get("status")))}</span>')
    details = ""
    if not compact:
        details = (
            '<div class="risk-metrics">'
            f'<span>证据完整度 {_e(_fmt_percent(risk.get("evidence_score")))}</span>'
            f'<span>复现稳定性 {_e(_fmt_percent(risk.get("reproducibility_score")))}</span>'
            f'<span>置信度 {_e(_fmt_percent(risk.get("confidence_score")))}</span>'
            "</div>"
        )
    risk_id = _e(risk.get("risk_id"))
    return (
        '<article class="risk-card">'
        f'<div class="risk-badges">{"".join(badges)}</div>'
        f'<h3>{_e(risk.get("title"))}</h3>'
        f'<p class="risk-impact">{_e(risk.get("business_impact"))}</p>'
        f'<p class="muted-line">影响链路：{_e(flow.get("name") or risk.get("business_flow_name") or "未映射")}</p>'
        f'{details}'
        f'<p class="suggestion">建议动作：{_e(risk.get("suggested_action") or "进入证据链查看修复建议")}</p>'
        f'<a class="button ghost" href="risks.html#risk-{risk_id}">查看证据链</a>'
        "</article>"
    )


def _section(title: str, body: str, *, subtitle: str = "") -> str:
    return (
        '<section class="panel">'
        '<div class="section-head">'
        f'<h2>{_e(title)}</h2>'
        f'<p>{_e(subtitle)}</p>'
        '</div>'
        f'{body}'
        '</section>'
    )


def _base_layout(bundle: Mapping[str, Any], *, page_key: str, title: str, body: str) -> str:
    project = _safe_dict(bundle.get("project"))
    dashboard = _safe_dict(bundle.get("command_center"))
    env = _safe_dict(bundle.get("environment_readiness"))
    active_links = []
    for key, label, filename in PAGE_ORDER:
        active = " active" if key == page_key else ""
        active_links.append(f'<a class="nav-item{active}" href="{_e(filename)}">{_e(label)}</a>')
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_e(title)} - QualiBug AI 企业质量指挥中心</title>
  <link rel="stylesheet" href="assets/phase103_ui.css" />
  <script src="assets/phase103_demo_data.js"></script>
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">QualiBug AI</div>
      <div class="brand-subtitle">企业质量指挥中心</div>
      <nav>{''.join(active_links)}</nav>
      <div class="sidebar-footer">
        <span>环境：{_e(_status_label(env.get('status')))}</span>
        <span>健康分：{_e(dashboard.get('quality_health_score', '-'))}</span>
      </div>
    </aside>
    <main class="main">
      <header class="topbar">
        <div>
          <div class="project-name">{_e(project.get('project_name') or 'Phase103 Demo')}</div>
          <div class="project-meta">客户：{_e(project.get('customer_name'))} · 行业：{_e(project.get('industry'))} · 系统：{_e(project.get('system_name'))}</div>
        </div>
        <div class="topbar-actions">
          <a class="button" href="report.html">查看成果战报</a>
        </div>
      </header>
      <div class="page-title">
        <h1>{_e(title)}</h1>
        <p>V1 静态演示包 · 数据已脱敏 · 由 Phase103U 生成</p>
      </div>
      {body}
    </main>
  </div>
</body>
</html>"""


def render_dashboard_page(bundle: Mapping[str, Any]) -> str:
    dashboard = _safe_dict(bundle.get("command_center"))
    launch = _safe_dict(dashboard.get("launch_decision"))
    flow_summary = _safe_dict(dashboard.get("business_flow_summary"))
    risk_summary = _safe_dict(dashboard.get("risk_summary"))
    value = _safe_dict(bundle.get("value_metrics") or dashboard.get("value_metrics"))
    env = _safe_dict(bundle.get("environment_readiness"))
    top_risks = _safe_list(dashboard.get("top_risks") or bundle.get("risks"))[:3]
    events = _safe_list(dashboard.get("recent_events"))[:6]
    impact_range = _fmt_money_range(
        min_value=value.get("estimated_business_impact_min"),
        max_value=value.get("estimated_business_impact_max"),
        currency=str(value.get("currency") or "CNY"),
    )
    metrics = "".join(
        [
            _metric_card("质量健康分", dashboard.get("quality_health_score", "-"), unit="/100", subtitle="综合核心链路、风险、环境和证据可信度"),
            _metric_card("上线建议", launch.get("title") or _status_label(launch.get("recommendation")), subtitle=launch.get("summary", ""), status=str(launch.get("recommendation") or "").lower()),
            _metric_card("核心链路覆盖", _fmt_percent(flow_summary.get("coverage_rate")), subtitle=f"已覆盖 {flow_summary.get('covered', 0)} / {flow_summary.get('total', 0)} 条"),
            _metric_card("上线阻断风险", risk_summary.get("launch_blocking", 0), unit="个", subtitle=f"高危 {risk_summary.get('critical', 0)} · 高风险 {risk_summary.get('high', 0)}", status="critical"),
            _metric_card("预计节省工时", value.get("estimated_hours_saved", 0), unit="h", subtitle=f"等价测试点 {value.get('ai_equivalent_test_points', 0)}"),
            _metric_card("潜在业务影响", impact_range, subtitle="估算区间，不代表确定收益"),
        ]
    )
    risk_html = "".join(_risk_card(risk, compact=True) for risk in top_risks) or '<p class="empty">暂无风险发现。</p>'
    event_html = "".join(
        f'<li><span>{_e(event.get("timestamp", ""))}</span>{_e(event.get("message") or event.get("event_type"))}</li>'
        for event in events
        if isinstance(event, Mapping)
    ) or '<li><span>-</span>暂无事件。</li>'
    body = f"""
    <div class="metric-grid">{metrics}</div>
    <div class="dashboard-grid">
      {_section('实时 AI 测试地图摘要', _render_map_preview(bundle), subtitle='业务链路、风险节点和环境阻断的统一态势')}
      {_section('当前最需关注风险', risk_html, subtitle='默认按上线阻断、严重等级、核心链路和证据可信度排序')}
    </div>
    <div class="dashboard-grid bottom">
      {_section('最近测试事件', f'<ul class="timeline">{event_html}</ul>')}
      {_section('领导摘要', f'<p class="executive-text">{_e(dashboard.get("executive_summary"))}</p><p class="muted-line">环境状态：{_e(_status_label(env.get("status")))} · 评分 { _e(env.get("score", "-")) }</p>')}
    </div>
    """
    return _base_layout(bundle, page_key="dashboard", title="企业质量驾驶舱", body=body)


def _render_map_preview(bundle: Mapping[str, Any]) -> str:
    live_map = _safe_dict(bundle.get("live_map"))
    nodes = _safe_list(live_map.get("nodes"))[:9]
    if not nodes:
        return '<p class="empty">暂无地图节点。</p>'
    node_html = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        status = str(node.get("status") or "unknown").lower()
        node_html.append(
            f'<div class="map-node status-{_e(status)}"><span>{_e(node.get("label"))}</span><small>{_e(_status_label(status))}</small></div>'
        )
    return f'<div class="map-preview">{"".join(node_html)}</div>'


def render_environment_page(bundle: Mapping[str, Any]) -> str:
    env = _safe_dict(bundle.get("environment_readiness"))
    checks = _safe_dict(env.get("checks"))
    cards = []
    for key, label in (("url", "URL 配置"), ("dns", "DNS / Host 解析"), ("http", "HTTP 可达性"), ("auth", "认证握手"), ("session", "Session 健康"), ("api_smoke", "认证后 API Smoke")):
        check = _safe_dict(checks.get(key))
        status = check.get("status") or check.get("result") or ("passed" if check.get("valid") or check.get("reachable") else "unknown")
        interpretation = check.get("interpretation") or check.get("issue") or check.get("content_type") or "已生成安全摘要。"
        cards.append(
            '<article class="check-card">'
            f'<div><h3>{_e(label)}</h3><span class="badge status-{_e(str(status).lower())}">{_e(_status_label(status))}</span></div>'
            f'<p>{_e(interpretation)}</p>'
            '</article>'
        )
    required = _safe_list(env.get("required_customer_inputs"))
    req_html = "".join(
        f'<article class="required-card"><b>{_e(item.get("title"))}</b><p>{_e(item.get("impact") or item.get("why_needed"))}</p><small>建议：{_e(item.get("suggested_input"))}</small></article>'
        for item in required
        if isinstance(item, Mapping)
    ) or '<p class="empty">暂无客户补料项。</p>'
    progress = " → ".join(["URL", "DNS", "HTTP", "Auth", "Session", "API Smoke", "测试准入"])
    body = f"""
    <div class="metric-grid compact">
      {_metric_card('环境适配评分', env.get('score', '-'), unit='/100', subtitle='URL、认证、Session、API Smoke 综合评分')}
      {_metric_card('当前状态', _status_label(env.get('status')), subtitle='决定是否允许进入正式测试')}
      {_metric_card('安全执行模式', env.get('safe_execution_mode', '-'), subtitle='写入探针需客户授权和清理策略')}
    </div>
    {_section('接入流程', f'<div class="step-line">{_e(progress)}</div><p>{_e("；".join(env.get("current_blockers", []) or ["无阻断项"]))}</p>')}
    <div class="dashboard-grid">
      {_section('诊断项', f'<div class="check-grid">{"".join(cards)}</div>')}
      {_section('客户补料清单', req_html, subtitle='用于明确为什么测不了、缺什么、谁来补')}
    </div>
    """
    return _base_layout(bundle, page_key="environment", title="客户环境适配中心", body=body)


def render_test_plan_page(bundle: Mapping[str, Any]) -> str:
    plan = _safe_dict(bundle.get("test_plan"))
    coverage = _safe_dict(plan.get("coverage_summary"))
    value = _safe_dict(plan.get("estimated_value"))
    groups = _safe_list(plan.get("probe_groups"))
    rows = []
    blocked = []
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        rows.append(
            f'<tr><td>{_e(group.get("business_flow_name"))}</td><td>{_e(_status_label(group.get("status")))}</td><td>{_e(group.get("probe_executable", 0))}/{_e(group.get("probe_total", 0))}</td><td>{_e("；".join(group.get("blocked_reasons", []) or []))}</td></tr>'
        )
        for reason in group.get("blocked_reasons", []) or []:
            blocked.append(f'<li>{_e(group.get("business_flow_name"))}：{_e(reason)}</li>')
    table = f'<table><thead><tr><th>业务链路</th><th>状态</th><th>可执行探针</th><th>不可执行原因</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    body = f"""
    <div class="metric-grid compact">
      {_metric_card('覆盖业务链路', coverage.get('business_flow_total', 0), unit='条', subtitle=f"可执行 {coverage.get('business_flow_executable', 0)} · 阻断 {coverage.get('business_flow_blocked', 0)}")}
      {_metric_card('预计等价测试点', value.get('equivalent_test_points', 0), subtitle='由业务链路、探针和角色矩阵估算')}
      {_metric_card('预计节省工时', value.get('estimated_hours_saved', 0), unit='h', subtitle=f"按单点 {value.get('manual_minutes_per_test_point', 12)} 分钟估算")}
      {_metric_card('安全模式', plan.get('safe_execution_mode', '-'), subtitle='V1 默认只读优先')}
    </div>
    {_section('业务链路覆盖计划', table)}
    {_section('不可执行探针与补料建议', '<ul class="timeline">' + ''.join(blocked or ['<li><span>-</span>暂无阻断探针。</li>']) + '</ul>')}
    """
    return _base_layout(bundle, page_key="test_plan", title="AI 测试计划中心", body=body)


def render_live_map_page(bundle: Mapping[str, Any]) -> str:
    live_map = _safe_dict(bundle.get("live_map"))
    nodes = _safe_list(live_map.get("nodes"))
    overlays = _safe_list(live_map.get("risk_overlays"))
    events = _safe_list(live_map.get("events"))[:8]
    map_html = _render_map_preview(bundle)
    overlay_html = "".join(
        f'<li><b>{_e(_severity_label(item.get("severity")))}</b> { _e(item.get("business_impact")) }</li>'
        for item in overlays
        if isinstance(item, Mapping)
    ) or '<li>暂无风险爆点。</li>'
    event_html = "".join(f'<li><span>{_e(item.get("timestamp", ""))}</span>{_e(item.get("message") or item.get("event_type"))}</li>' for item in events if isinstance(item, Mapping)) or '<li><span>-</span>暂无事件。</li>'
    body = f"""
    <div class="metric-grid compact">
      {_metric_card('地图节点', len(nodes), unit='个', subtitle='由业务链路模型生成')}
      {_metric_card('风险爆点', len(overlays), unit='个', subtitle='由 RiskFinding 映射到节点')}
      {_metric_card('地图模式', live_map.get('layout_mode', 'business_flow'), subtitle='V1 2.5D 基础版')}
    </div>
    <div class="dashboard-grid">
      {_section('业务链路地图', map_html)}
      {_section('风险爆点', f'<ul class="timeline">{overlay_html}</ul>')}
    </div>
    {_section('实时事件流', f'<ul class="timeline">{event_html}</ul>')}
    """
    return _base_layout(bundle, page_key="live_map", title="实时 AI 测试地图", body=body)


def render_risks_page(bundle: Mapping[str, Any]) -> str:
    risks = [risk for risk in _safe_list(bundle.get("risks")) if isinstance(risk, Mapping)]
    details = [detail for detail in _safe_list(bundle.get("risk_details")) if isinstance(detail, Mapping)]
    risk_cards = "".join(f'<div id="risk-{_e(risk.get("risk_id"))}">{_risk_card(risk)}</div>' for risk in risks) or '<p class="empty">暂无风险发现。</p>'
    evidence_sections = []
    for detail in details[:3]:
        risk = _safe_dict(detail.get("risk"))
        evidence = _safe_dict(detail.get("evidence_bundle"))
        steps = "".join(f'<li>{_e(step)}</li>' for step in _safe_list(evidence.get("reproduction_steps"))[:5])
        evidence_sections.append(
            '<article class="evidence-card">'
            f'<h3>{_e(risk.get("title"))}</h3>'
            f'<p>{_e(evidence.get("summary"))}</p>'
            f'<ol>{steps}</ol>'
            f'<p class="muted-line">脱敏状态：{_e(evidence.get("redaction_status"))}</p>'
            '</article>'
        )
    body = f"""
    <div class="metric-grid compact">
      {_metric_card('风险总数', len(risks), unit='个')}
      {_metric_card('上线阻断', sum(1 for risk in risks if risk.get('launch_blocking')), unit='个', status='critical')}
      {_metric_card('平均证据完整度', _fmt_percent(sum(float(risk.get('evidence_score') or 0) for risk in risks) / len(risks) if risks else 0))}
    </div>
    {_section('业务风险卡片', risk_cards)}
    {_section('证据链摘要', ''.join(evidence_sections) or '<p class="empty">暂无证据链。</p>', subtitle='完整前端可跳转到风险详情页；V1 静态包展示前 3 条摘要')}
    """
    return _base_layout(bundle, page_key="risks", title="AI 风险发现中心", body=body)


def render_report_page(bundle: Mapping[str, Any]) -> str:
    report = _safe_dict(bundle.get("executive_report"))
    top_risks = _safe_list(report.get("top_risks") or bundle.get("risks"))[:5]
    risk_html = "".join(_risk_card(risk, compact=True) for risk in top_risks if isinstance(risk, Mapping)) or '<p class="empty">暂无高危风险。</p>'
    next_actions = _safe_list(report.get("next_actions"))
    actions_html = "".join(
        f'<li><b>{_e(item.get("priority", "P0"))}</b> {_e(item.get("title"))}<br><small>{_e(item.get("reason"))}</small></li>'
        for item in next_actions
        if isinstance(item, Mapping)
    ) or '<li>暂无下一步行动。</li>'
    body = f"""
    {_section(report.get('title') or '上线质量风险评估报告', f'<p class="executive-text">{_e(report.get("executive_summary"))}</p>')}
    {_section('上线决策建议', f'<p class="executive-text">{_e(_status_label(report.get("launch_recommendation")))}</p><p>质量健康分：{_e(report.get("quality_health_score"))} / 100</p>')}
    {_section('高危风险摘要', risk_html)}
    {_section('下一步行动建议', f'<ul class="timeline">{actions_html}</ul>')}
    {_section('报告安全说明', '<p>本报告默认脱敏，未展示 token、cookie、password、session 原值和客户敏感业务数据。</p>')}
    """
    return _base_layout(bundle, page_key="report", title="领导层成果战报", body=body)


def render_value_page(bundle: Mapping[str, Any]) -> str:
    value = _safe_dict(bundle.get("value_metrics"))
    impact = _fmt_money_range(
        min_value=value.get("estimated_business_impact_min"),
        max_value=value.get("estimated_business_impact_max"),
        currency=str(value.get("currency") or "CNY"),
    )
    notes = "".join(f'<li>{_e(note)}</li>' for note in _safe_list(value.get("calculation_notes")))
    body = f"""
    <div class="metric-grid">
      {_metric_card('AI 等价测试点', value.get('ai_equivalent_test_points', 0), subtitle='由探针、链路验证、证据快照折算')}
      {_metric_card('预计节省工时', value.get('estimated_hours_saved', 0), unit='h', subtitle=f"按单点 {value.get('manual_minutes_per_test_point', 12)} 分钟估算")}
      {_metric_card('核心链路覆盖率', _fmt_percent(value.get('business_flow_coverage_rate')), subtitle='按客户确认核心链路计算')}
      {_metric_card('上线阻断风险', value.get('launch_blocking_risks', 0), unit='个', subtitle='优先进入修复闭环', status='critical')}
      {_metric_card('证据可信度', _fmt_percent(value.get('evidence_trust_score')), subtitle='证据完整度、复现稳定性、业务上下文')}
      {_metric_card('潜在业务影响', impact, subtitle='估算区间，不代表确定收益')}
    </div>
    {_section('计算口径', f'<ul class="timeline">{notes}</ul><p>ROI 表达统一使用“预计、估算、潜在影响区间”，避免绝对收益承诺。</p>')}
    """
    return _base_layout(bundle, page_key="value", title="AI 质量价值分析", body=body)


def render_index_page(bundle: Mapping[str, Any]) -> str:
    return render_dashboard_page(bundle).replace("企业质量驾驶舱", "QualiBug AI 企业质量指挥中心", 1)


def render_static_pages(bundle: Mapping[str, Any]) -> dict[str, str]:
    """Render all static HTML pages for a redacted Phase103 bundle."""
    safe = redact_value(dict(bundle))
    return {
        "index.html": render_index_page(safe),
        "dashboard.html": render_dashboard_page(safe),
        "environment.html": render_environment_page(safe),
        "test_plan.html": render_test_plan_page(safe),
        "live_map.html": render_live_map_page(safe),
        "risks.html": render_risks_page(safe),
        "report.html": render_report_page(safe),
        "value.html": render_value_page(safe),
    }


def render_css() -> str:
    """Return the V1 static design-system CSS."""
    return """
:root{--bg:#07111f;--panel:#0d1b2e;--panel2:#10243b;--line:#223754;--text:#f4f8ff;--muted:#9fb0c6;--blue:#4aa3ff;--green:#3ddc97;--yellow:#f3c969;--orange:#ff9f45;--red:#ff5a6a;--purple:#a78bfa;--gray:#6b7280;--shadow:0 20px 60px rgba(0,0,0,.28)}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0%,rgba(74,163,255,.18),transparent 28%),linear-gradient(135deg,#07111f 0%,#0a1526 56%,#050a12 100%);color:var(--text);font-family:Inter,Segoe UI,Microsoft YaHei,Arial,sans-serif}.app-shell{display:flex;min-height:100vh}.sidebar{width:252px;background:rgba(7,17,31,.88);border-right:1px solid var(--line);padding:24px 18px;position:sticky;top:0;height:100vh}.brand{font-size:24px;font-weight:800;letter-spacing:.2px}.brand-subtitle{color:var(--muted);font-size:13px;margin:6px 0 26px}.nav-item{display:block;color:#c9d7ea;text-decoration:none;padding:12px 14px;border-radius:12px;margin:6px 0;border:1px solid transparent}.nav-item:hover,.nav-item.active{background:rgba(74,163,255,.12);border-color:rgba(74,163,255,.32);color:#fff}.sidebar-footer{position:absolute;bottom:20px;left:18px;right:18px;color:var(--muted);font-size:12px;display:grid;gap:6px}.main{flex:1;padding:22px 28px 60px}.topbar{display:flex;justify-content:space-between;align-items:center;background:rgba(13,27,46,.72);border:1px solid var(--line);border-radius:18px;padding:16px 18px;box-shadow:var(--shadow)}.project-name{font-size:18px;font-weight:700}.project-meta{color:var(--muted);font-size:13px;margin-top:4px}.button{display:inline-block;border:0;border-radius:12px;padding:10px 14px;background:linear-gradient(135deg,#2b79ff,#33c7ff);color:#fff;text-decoration:none;font-weight:700}.button.ghost{background:transparent;border:1px solid var(--line);color:#dbeafe;margin-top:8px}.page-title{margin:26px 0 18px}.page-title h1{margin:0;font-size:28px}.page-title p,.muted-line{color:var(--muted)}.metric-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:14px;margin-bottom:16px}.metric-grid.compact{grid-template-columns:repeat(4,minmax(0,1fr))}.metric-card,.panel,.risk-card,.check-card,.required-card,.evidence-card{background:rgba(13,27,46,.82);border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:var(--shadow)}.metric-title{color:var(--muted);font-size:13px}.metric-value{font-size:28px;font-weight:850;margin:8px 0}.metric-value span{font-size:15px;color:var(--muted);margin-left:3px}.metric-subtitle{font-size:12px;color:var(--muted);line-height:1.45}.status-critical,.status-no_go{border-color:rgba(255,90,106,.7)}.status-hold,.status-high{border-color:rgba(255,159,69,.65)}.dashboard-grid{display:grid;grid-template-columns:1.25fr .9fr;gap:16px;margin-top:16px}.dashboard-grid.bottom{grid-template-columns:1fr 1fr}.section-head h2{margin:0 0 5px}.section-head p{margin:0 0 14px;color:var(--muted)}.badge{display:inline-flex;align-items:center;padding:4px 8px;border-radius:999px;background:rgba(159,176,198,.14);color:#e7eefc;font-size:12px;margin-right:6px}.danger,.severity-critical{background:rgba(255,90,106,.16);color:#ffb3bc;border:1px solid rgba(255,90,106,.42)}.severity-high{background:rgba(255,159,69,.16);color:#ffd0a3;border:1px solid rgba(255,159,69,.42)}.severity-medium{background:rgba(243,201,105,.16);color:#ffe4a3;border:1px solid rgba(243,201,105,.42)}.severity-low{background:rgba(74,163,255,.16);color:#b8d9ff;border:1px solid rgba(74,163,255,.42)}.muted{color:var(--muted)}.risk-card{margin-bottom:12px;border-left:4px solid var(--red)}.risk-card h3{margin:10px 0 8px}.risk-impact{color:#d9e7fb;line-height:1.65}.risk-metrics{display:flex;gap:10px;flex-wrap:wrap;color:#bfd0e5;font-size:13px}.suggestion{color:#dbeafe}.map-preview{min-height:250px;display:flex;align-content:center;align-items:center;justify-content:center;gap:12px;flex-wrap:wrap;background:linear-gradient(135deg,rgba(74,163,255,.08),rgba(61,220,151,.04));border:1px dashed rgba(159,176,198,.28);border-radius:18px;padding:22px}.map-node{min-width:130px;text-align:center;border-radius:16px;padding:15px;border:1px solid var(--line);background:rgba(16,36,59,.9)}.map-node span{display:block;font-weight:800}.map-node small{color:var(--muted)}.status-risk{border-color:rgba(255,90,106,.78);box-shadow:0 0 0 4px rgba(255,90,106,.09)}.status-passed,.status-ready{border-color:rgba(61,220,151,.65)}.status-running{border-color:rgba(74,163,255,.65)}.status-warning,.status-partial_ready{border-color:rgba(243,201,105,.65)}.status-blocked{border-color:rgba(107,114,128,.65);opacity:.85}.check-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.check-card h3{margin:0 0 8px}.required-card{margin-bottom:10px}.step-line{font-weight:800;color:#dbeafe;letter-spacing:.4px}.timeline{list-style:none;margin:0;padding:0}.timeline li{padding:10px 0;border-bottom:1px solid rgba(34,55,84,.7);line-height:1.55}.timeline span{display:inline-block;min-width:92px;color:var(--muted)}table{width:100%;border-collapse:collapse;background:rgba(16,36,59,.5);border-radius:14px;overflow:hidden}th,td{text-align:left;padding:12px;border-bottom:1px solid var(--line);vertical-align:top}th{color:#c7d7ee;background:rgba(74,163,255,.08)}.executive-text{font-size:18px;line-height:1.75;color:#f8fbff}.empty{color:var(--muted);padding:16px;border:1px dashed var(--line);border-radius:14px}.evidence-card{margin:12px 0;background:rgba(16,36,59,.72)}@media(max-width:1100px){.sidebar{position:relative;height:auto;width:220px}.metric-grid,.metric-grid.compact{grid-template-columns:repeat(2,minmax(0,1fr))}.dashboard-grid,.dashboard-grid.bottom{grid-template-columns:1fr}.check-grid{grid-template-columns:1fr}}
""".strip() + "\n"


def _data_js(bundle: Mapping[str, Any]) -> str:
    payload = json.dumps(redact_value(dict(bundle)), ensure_ascii=False, sort_keys=True)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f"window.PHASE103_DEMO_DATA = {payload};\nwindow.PHASE103_STATIC_FRONTEND_VERSION = {json.dumps(PHASE103U_VERSION)};\n"


def export_static_frontend_bundle(
    bundle: Mapping[str, Any],
    output_dir: str | Path,
    *,
    include_data_payload: bool = True,
) -> dict[str, Any]:
    """Export a complete static UI bundle and return a manifest."""
    output = Path(output_dir)
    assets = output / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    safe = redact_value(dict(bundle))
    pages = render_static_pages(safe)
    files: dict[str, str] = {}

    for filename, content in pages.items():
        path = output / filename
        path.write_text(content, encoding="utf-8")
        files[filename] = str(path)

    css_path = assets / "phase103_ui.css"
    css_path.write_text(render_css(), encoding="utf-8")
    files["assets/phase103_ui.css"] = str(css_path)

    if include_data_payload:
        data_js_path = assets / "phase103_demo_data.js"
        data_js_path.write_text(_data_js(safe), encoding="utf-8")
        files["assets/phase103_demo_data.js"] = str(data_js_path)

    readme = render_static_frontend_readme(safe)
    readme_path = output / "README_static_frontend.md"
    readme_path.write_text(readme, encoding="utf-8")
    files["README_static_frontend.md"] = str(readme_path)

    manifest = {
        "version": PHASE103U_VERSION,
        "project_id": safe.get("project_id"),
        "scenario": safe.get("scenario"),
        "entrypoint": "index.html",
        "pages": {key: filename for key, _label, filename in PAGE_ORDER},
        "files": files,
        "redaction_status": "safe",
    }
    manifest_path = output / "static_frontend_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    files["static_frontend_manifest.json"] = str(manifest_path)
    manifest["files"] = files
    return manifest


def render_static_frontend_readme(bundle: Mapping[str, Any]) -> str:
    project = _safe_dict(bundle.get("project"))
    return "\n".join(
        [
            f"# {project.get('project_name', 'Phase103 Demo')} 静态前端演示包",
            "",
            "打开 `index.html` 即可查看 QualiBug AI 企业质量指挥中心 V1 静态原型。",
            "",
            "## 页面",
            "",
            "- `dashboard.html`：企业质量驾驶舱",
            "- `environment.html`：客户环境适配中心",
            "- `test_plan.html`：AI 测试计划中心",
            "- `live_map.html`：实时 AI 测试地图",
            "- `risks.html`：AI 风险发现与证据链摘要",
            "- `report.html`：领导层成果战报",
            "- `value.html`：AI 质量价值分析",
            "",
            "## 安全说明",
            "",
            "本静态演示包由统一脱敏路径生成，不包含 token、cookie、password、session 原值和客户敏感业务数据原文。",
        ]
    ) + "\n"


def build_and_export_static_frontend(
    *,
    scenario: str = "manufacturing",
    output_dir: str | Path = "outputs/phase103_static_frontend",
) -> dict[str, Any]:
    api = EnterpriseCommandCenterAPI()
    bundle = seed_demo_project(api, scenario=scenario)
    manifest = export_static_frontend_bundle(bundle, output_dir)
    return {"bundle": bundle, "manifest": manifest}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a customer-safe Phase103 static frontend demo bundle.")
    parser.add_argument("--scenario", default="manufacturing", choices=["manufacturing", "ecommerce", "saas"], help="Demo scenario to render.")
    parser.add_argument("--output-dir", default="outputs/phase103_static_frontend", help="Directory where static files will be written.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    result = build_and_export_static_frontend(scenario=args.scenario, output_dir=args.output_dir)
    manifest = result["manifest"]
    print(f"Phase103 static frontend generated: {manifest['scenario']} -> {args.output_dir}")
    print(json.dumps({"entrypoint": manifest["entrypoint"], "files": sorted(manifest["files"].keys())}, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "PAGE_ORDER",
    "PHASE103U_VERSION",
    "build_and_export_static_frontend",
    "export_static_frontend_bundle",
    "main",
    "render_css",
    "render_dashboard_page",
    "render_environment_page",
    "render_index_page",
    "render_live_map_page",
    "render_report_page",
    "render_risks_page",
    "render_static_frontend_readme",
    "render_static_pages",
    "render_test_plan_page",
    "render_value_page",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
