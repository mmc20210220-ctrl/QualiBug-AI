from __future__ import annotations

"""Phase105E: business flow map experience for the frontend layer.

Phase105A created the product shell, Phase105B strengthened the executive
quality dashboard, Phase105C created the customer-intake entry page, and
Phase105D visualised customer environment diagnosis. Phase105E focuses the
business flow map: a customer-facing screen that explains which business
chains QualiBug has understood, which nodes are covered, which nodes carry
risk, which chains are blocked by environment constraints, and where evidence
has returned.

The generator is dependency-free and framework-neutral. It emits static
HTML/CSS/JS plus a redacted JSON view model that a future React/Vue frontend
can reuse directly.
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

PHASE105E_VERSION = "phase105e-business-flow-map-experience-v1"

BUSINESS_FLOW_MAP_MANIFEST = "business_flow_map_experience_manifest.json"
BUSINESS_FLOW_MAP_ACCEPTANCE_JSON = "business_flow_map_experience_acceptance_report.json"
BUSINESS_FLOW_MAP_ACCEPTANCE_MD = "business_flow_map_experience_acceptance_report.md"

REQUIRED_BUSINESS_FLOW_MAP_FILES: tuple[str, ...] = (
    "business_flow_map.html",
    "README_BUSINESS_FLOW_MAP_EXPERIENCE.md",
    "data/business_flow_map_experience_data.json",
    "assets/qualibug_business_flow_map.css",
    "assets/qualibug_business_flow_map.js",
    BUSINESS_FLOW_MAP_MANIFEST,
)

CORE_BUSINESS_FLOW_MAP_LABELS: tuple[str, ...] = (
    "业务流程地图",
    "AI 已理解的业务链路",
    "节点覆盖状态",
    "风险爆点",
    "环境阻断链路",
    "证据回流",
    "链路详情",
    "业务影响",
    "聚焦高危风险",
    "重新布局",
)

FORBIDDEN_BUSINESS_FLOW_MAP_PATTERNS: tuple[str, ...] = (
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

STATUS_LABELS: dict[str, str] = {
    "passed": "已覆盖",
    "risk": "发现风险",
    "failed": "执行失败",
    "idle": "待覆盖",
    "running": "执行中",
    "blocked": "环境阻断",
    "partial_executable": "部分可测",
    "executable": "可测",
    "ready": "可测",
    "needs_customer_input": "待客户补料",
    "unknown": "待确认",
}

SEVERITY_WEIGHT: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}


@dataclass(frozen=True)
class BusinessFlowMapExperienceCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class BusinessFlowMapExperienceAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    checks: list[BusinessFlowMapExperienceCheck] = field(default_factory=list)
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


def _escape(value: Any) -> str:
    return html.escape(_safe_text(value), quote=True)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct(value: Any) -> int:
    number = _float_value(value)
    if 0 <= number <= 1:
        return int(round(number * 100))
    return int(round(number))


def _status_label(status: Any) -> str:
    return STATUS_LABELS.get(str(status or "unknown"), _safe_text(status, "待确认"))


def _severity_class(severity: Any) -> str:
    text = str(severity or "none").lower()
    if text in {"critical", "high"}:
        return "danger"
    if text in {"medium", "warning"}:
        return "warn"
    if text in {"low", "info"}:
        return "muted"
    return "good"


def _node_status_class(node: Mapping[str, Any], overlays: Sequence[Mapping[str, Any]]) -> str:
    if overlays:
        highest = max((SEVERITY_WEIGHT.get(str(item.get("severity", "")).lower(), 0) for item in overlays), default=0)
        return "danger" if highest >= 3 else "warn"
    status = str(node.get("status") or "idle")
    if status in {"risk", "failed"}:
        return "danger"
    if status in {"running", "partial_executable"}:
        return "warn"
    if status == "passed":
        return "good"
    return "idle"


def _sort_nodes(nodes: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(nodes, key=lambda node: (_as_mapping(node.get("position")).get("y", 0), _as_mapping(node.get("position")).get("x", 0), str(node.get("label"))))


def _flow_node_ids(nodes: Sequence[Mapping[str, Any]], flow_id: str) -> set[str]:
    return {str(node.get("node_id")) for node in nodes if flow_id in [str(item) for item in _as_list(node.get("business_flow_ids"))]}


def _risk_summary_for_flow(risks: Sequence[Mapping[str, Any]], flow_id: str, flow_name: str) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for risk in risks:
        flow = _as_mapping(risk.get("affected_business_flow"))
        if str(flow.get("business_flow_id") or "") == flow_id or str(flow.get("name") or "") == flow_name:
            matched.append(
                {
                    "risk_id": _safe_text(risk.get("risk_id")),
                    "title": _safe_text(risk.get("title")),
                    "severity": _safe_text(risk.get("severity"), "unknown"),
                    "launch_blocking": bool(risk.get("launch_blocking")),
                    "business_impact": _safe_text(risk.get("business_impact")),
                    "evidence_score": _pct(risk.get("evidence_score")),
                    "suggested_action": _safe_text(risk.get("suggested_action")),
                }
            )
    return matched


def _build_flow_lanes(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    live_map = _as_mapping(data.get("live_map"))
    test_plan = _as_mapping(data.get("test_plan"))
    environment = _as_mapping(data.get("environment"))
    nodes = [_as_mapping(node) for node in _as_list(live_map.get("nodes")) if isinstance(node, Mapping)]
    edges = [_as_mapping(edge) for edge in _as_list(live_map.get("edges")) if isinstance(edge, Mapping)]
    overlays = [_as_mapping(item) for item in _as_list(live_map.get("risk_overlays")) if isinstance(item, Mapping)]
    risks = [_as_mapping(item) for item in _as_list(data.get("risks")) if isinstance(item, Mapping)]
    probe_groups = [_as_mapping(item) for item in _as_list(test_plan.get("probe_groups")) if isinstance(item, Mapping)]
    env_blockers = [_safe_text(item) for item in _as_list(environment.get("current_blockers"))]

    lanes: list[dict[str, Any]] = []
    for index, group in enumerate(probe_groups, start=1):
        flow_id = _safe_text(group.get("business_flow_id"), f"flow_{index}")
        flow_name = _safe_text(group.get("business_flow_name"), f"业务链路 {index}")
        node_ids = _flow_node_ids(nodes, flow_id)
        flow_nodes = _sort_nodes([node for node in nodes if str(node.get("node_id")) in node_ids])
        flow_edges = [edge for edge in edges if str(edge.get("business_flow_id")) == flow_id]
        flow_risks = _risk_summary_for_flow(risks, flow_id, flow_name)
        blocked_reasons = [_safe_text(item) for item in _as_list(group.get("blocked_reasons"))]
        probe_total = _int_value(group.get("probe_total"), len(_as_list(group.get("probes"))))
        probe_executable = _int_value(group.get("probe_executable"))
        probe_blocked = _int_value(group.get("probe_blocked"))

        enriched_nodes: list[dict[str, Any]] = []
        for order, node in enumerate(flow_nodes, start=1):
            node_id = _safe_text(node.get("node_id"), f"node_{order}")
            node_overlays = [overlay for overlay in overlays if str(overlay.get("node_id")) == node_id]
            risk_titles = []
            for overlay in node_overlays:
                risk_id = str(overlay.get("risk_id") or "")
                risk_title = next((_safe_text(risk.get("title")) for risk in risks if str(risk.get("risk_id")) == risk_id), _safe_text(overlay.get("business_impact")))
                risk_titles.append(risk_title)
            metrics = _as_mapping(node.get("metrics"))
            enriched_nodes.append(
                {
                    "node_id": node_id,
                    "label": _safe_text(node.get("label")),
                    "order": order,
                    "status": _safe_text(node.get("status"), "idle"),
                    "status_label": _status_label(node.get("status")),
                    "risk_level": _safe_text(node.get("risk_level"), "none"),
                    "visual_class": _node_status_class(node, node_overlays),
                    "risk_count": len(node_overlays) or _int_value(metrics.get("risk_count")),
                    "risk_titles": risk_titles,
                    "probe_total": _int_value(metrics.get("probe_total")),
                    "probe_passed": _int_value(metrics.get("probe_passed")),
                }
            )

        critical_or_high = [risk for risk in flow_risks if str(risk.get("severity")).lower() in {"critical", "high"}]
        lane_status = _safe_text(group.get("status"), "unknown")
        if critical_or_high:
            display_status = "risk"
        elif probe_blocked:
            display_status = "partial_executable"
        elif probe_executable and probe_executable == probe_total:
            display_status = "executable"
        else:
            display_status = lane_status

        lanes.append(
            {
                "flow_id": flow_id,
                "flow_name": flow_name,
                "order": index,
                "status": lane_status,
                "display_status": display_status,
                "display_status_label": _status_label(display_status),
                "node_count": len(enriched_nodes),
                "edge_count": len(flow_edges),
                "risk_count": len(flow_risks),
                "critical_or_high_risk_count": len(critical_or_high),
                "probe_total": probe_total,
                "probe_executable": probe_executable,
                "probe_blocked": probe_blocked,
                "coverage_rate": _pct((probe_executable / probe_total) if probe_total else 0),
                "blocked_reasons": blocked_reasons,
                "environment_blockers": env_blockers if blocked_reasons else [],
                "nodes": enriched_nodes,
                "edges": [
                    {
                        "edge_id": _safe_text(edge.get("edge_id")),
                        "from_node_id": _safe_text(edge.get("from_node_id")),
                        "to_node_id": _safe_text(edge.get("to_node_id")),
                        "status": _safe_text(edge.get("status"), "unknown"),
                    }
                    for edge in flow_edges
                ],
                "risks": flow_risks,
                "business_explanation": _flow_business_explanation(flow_name, len(flow_risks), probe_blocked, critical_or_high),
                "next_action": _flow_next_action(len(flow_risks), blocked_reasons, critical_or_high),
            }
        )

    return lanes


def _flow_business_explanation(flow_name: str, risk_count: int, blocked_count: int, critical_or_high: Sequence[Mapping[str, Any]]) -> str:
    if critical_or_high:
        return f"{flow_name} 已发现高优先级业务风险，需要在上线前修复并复测。"
    if risk_count:
        return f"{flow_name} 已覆盖并发现中低风险，建议纳入灰度观察。"
    if blocked_count:
        return f"{flow_name} 存在环境或安全边界阻断，当前只能部分验证。"
    return f"{flow_name} 当前链路可测，未发现阻断上线风险。"


def _flow_next_action(risk_count: int, blocked_reasons: Sequence[str], critical_or_high: Sequence[Mapping[str, Any]]) -> str:
    if critical_or_high:
        return "优先打开证据链，确认复现步骤、业务影响和修复责任人。"
    if blocked_reasons:
        return "先补齐账号、权限或安全授权，再重新生成测试计划。"
    if risk_count:
        return "安排修复验证，并把风险状态纳入上线检查清单。"
    return "继续执行回归探针，并保持证据回流。"


def _build_map_summary(data: Mapping[str, Any], lanes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    dashboard = _as_mapping(data.get("dashboard"))
    business_summary = _as_mapping(dashboard.get("business_flow_summary"))
    risks = [_as_mapping(item) for item in _as_list(data.get("risks")) if isinstance(item, Mapping)]
    live_map = _as_mapping(data.get("live_map"))
    overlays = _as_list(live_map.get("risk_overlays"))
    blocked_lanes = [lane for lane in lanes if _int_value(lane.get("probe_blocked")) > 0]
    high_risks = [risk for risk in risks if str(risk.get("severity", "")).lower() in {"critical", "high"}]
    coverage_rate = business_summary.get("coverage_rate")
    return {
        "total_flows": _int_value(business_summary.get("total"), len(lanes)),
        "covered_flows": _int_value(business_summary.get("covered")),
        "coverage_rate": _pct(coverage_rate),
        "risk_nodes": len({_safe_text(item.get("node_id")) for item in overlays if isinstance(item, Mapping)}),
        "risk_overlays": len(overlays),
        "critical_or_high_risks": len(high_risks),
        "blocked_flows": len(blocked_lanes),
        "events": len(_as_list(live_map.get("events"))),
        "business_judgement": "存在高危业务风险，建议先修复再上线。" if high_risks else "核心链路暂无高危风险，可进入下一轮回归。",
    }


def _build_phase104_actions(data: Mapping[str, Any]) -> dict[str, str]:
    project = _as_mapping(data.get("project"))
    project_id = _safe_text(project.get("project_id"), "{project_id}")
    return {
        "read_live_map": f"GET /api/v1/projects/{project_id}/live-map",
        "read_risks": f"GET /api/v1/projects/{project_id}/risks",
        "read_test_plan": f"GET /api/v1/projects/{project_id}/test-plan",
        "read_environment": f"GET /api/v1/projects/{project_id}/environment/readiness",
        "generate_report": f"POST /api/v1/projects/{project_id}/reports/generate",
    }


def build_business_flow_map_view_model(scenario: str = "manufacturing", api_base_url: str = "http://127.0.0.1:8088") -> dict[str, Any]:
    source = collect_product_shell_demo_data(scenario=scenario, api_base_url=api_base_url)
    lanes = _build_flow_lanes(source)
    live_map = _as_mapping(source.get("live_map"))
    risks = [_as_mapping(item) for item in _as_list(source.get("risks")) if isinstance(item, Mapping)]
    risk_overlays = [_as_mapping(item) for item in _as_list(live_map.get("risk_overlays")) if isinstance(item, Mapping)]
    events = [_as_mapping(item) for item in _as_list(live_map.get("events")) if isinstance(item, Mapping)]

    overlay_cards = []
    for overlay in risk_overlays:
        risk_id = _safe_text(overlay.get("risk_id"))
        risk = next((item for item in risks if str(item.get("risk_id")) == risk_id), {})
        node_label = next(
            (
                _safe_text(node.get("label"))
                for lane in lanes
                for node in _as_list(lane.get("nodes"))
                if isinstance(node, Mapping) and str(node.get("node_id")) == str(overlay.get("node_id"))
            ),
            _safe_text(overlay.get("node_id")),
        )
        overlay_cards.append(
            {
                "risk_id": risk_id,
                "node_id": _safe_text(overlay.get("node_id")),
                "node_label": node_label,
                "title": _safe_text(risk.get("title"), "业务风险"),
                "severity": _safe_text(overlay.get("severity"), _safe_text(risk.get("severity"), "unknown")),
                "business_impact": _safe_text(overlay.get("business_impact"), _safe_text(risk.get("business_impact"))),
                "launch_blocking": bool(overlay.get("launch_blocking") or risk.get("launch_blocking")),
                "visual_type": _safe_text(overlay.get("visual_type"), "pulse"),
            }
        )

    return redact_value(
        {
            "version": PHASE105E_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "api_base_url": api_base_url.rstrip("/"),
            "project": source.get("project"),
            "map_summary": _build_map_summary(source, lanes),
            "flow_lanes": lanes,
            "risk_overlays": overlay_cards,
            "event_timeline": [
                {
                    "event_id": _safe_text(event.get("event_id")),
                    "event_type": _safe_text(event.get("event_type")),
                    "severity": _safe_text(event.get("severity"), "info"),
                    "message": _safe_text(event.get("message")),
                    "timestamp": _safe_text(event.get("timestamp")),
                }
                for event in events
            ],
            "phase104_actions": _build_phase104_actions(source),
            "display_principles": [
                "业务地图先展示客户业务语言，再展示技术探针细节。",
                "每个风险必须落到业务节点，并能跳转证据链。",
                "环境阻断必须落到具体业务链路，避免客户误解为系统不可用。",
                "前端默认只展示脱敏状态、证据评分和业务影响摘要。",
            ],
        }
    )


def _render_kpi_cards(summary: Mapping[str, Any]) -> str:
    cards = [
        ("AI 已理解的业务链路", summary.get("total_flows"), "条"),
        ("节点覆盖状态", f"{summary.get('coverage_rate')}%", "覆盖率"),
        ("风险爆点", summary.get("risk_overlays"), "个"),
        ("环境阻断链路", summary.get("blocked_flows"), "条"),
        ("证据回流", summary.get("events"), "条事件"),
    ]
    return "\n".join(
        f"""
        <article class=\"qb-map-kpi\">
          <span>{_escape(title)}</span>
          <strong>{_escape(value)}</strong>
          <small>{_escape(unit)}</small>
        </article>
        """
        for title, value, unit in cards
    )


def _render_flow_lanes(lanes: Sequence[Mapping[str, Any]]) -> str:
    rendered_lanes: list[str] = []
    for lane in lanes:
        node_html = "\n".join(_render_node(node, lane) for node in _as_list(lane.get("nodes")) if isinstance(node, Mapping))
        risk_badge = "" if not _int_value(lane.get("critical_or_high_risk_count")) else f"<b>{_int_value(lane.get('critical_or_high_risk_count'))} 个高危</b>"
        blocked_badge = "" if not _int_value(lane.get("probe_blocked")) else f"<b>{_int_value(lane.get('probe_blocked'))} 个阻断探针</b>"
        reasons = "".join(f"<li>{_escape(item)}</li>" for item in _as_list(lane.get("blocked_reasons"))) or "<li>暂无环境阻断。</li>"
        risks = "".join(
            f"<li><span class=\"risk-dot {_severity_class(risk.get('severity'))}\"></span>{_escape(risk.get('title'))}<em>{_escape(risk.get('severity'))}</em></li>"
            for risk in _as_list(lane.get("risks"))
            if isinstance(risk, Mapping)
        ) or "<li>该链路暂无风险。</li>"
        rendered_lanes.append(
            f"""
            <section class=\"qb-flow-lane\" data-flow=\"{_escape(lane.get('flow_id'))}\" data-status=\"{_escape(lane.get('display_status'))}\">
              <div class=\"qb-flow-head\">
                <div>
                  <span class=\"qb-flow-order\">#{_escape(lane.get('order'))}</span>
                  <h2>{_escape(lane.get('flow_name'))}</h2>
                  <p>{_escape(lane.get('business_explanation'))}</p>
                </div>
                <div class=\"qb-flow-meta\">
                  <span class=\"status-pill {_escape(lane.get('display_status'))}\">{_escape(lane.get('display_status_label'))}</span>
                  {risk_badge}
                  {blocked_badge}
                </div>
              </div>
              <div class=\"qb-node-track\" aria-label=\"业务节点\">
                {node_html}
              </div>
              <div class=\"qb-flow-detail-grid\">
                <div><strong>链路详情</strong><p>可执行探针 {_escape(lane.get('probe_executable'))} / {_escape(lane.get('probe_total'))}，覆盖率 {_escape(lane.get('coverage_rate'))}%。</p></div>
                <div><strong>环境阻断链路</strong><ul>{reasons}</ul></div>
                <div><strong>业务影响</strong><ul>{risks}</ul></div>
                <div><strong>下一步动作</strong><p>{_escape(lane.get('next_action'))}</p></div>
              </div>
            </section>
            """
        )
    return "\n".join(rendered_lanes)


def _render_node(node: Mapping[str, Any], lane: Mapping[str, Any]) -> str:
    risk_titles = _as_list(node.get("risk_titles"))
    risk_preview = "；".join(_safe_text(item) for item in risk_titles[:2]) if risk_titles else "暂无风险爆点"
    return f"""
    <button class=\"qb-map-node {_escape(node.get('visual_class'))}\"
            data-node=\"{_escape(node.get('node_id'))}\"
            data-flow=\"{_escape(lane.get('flow_id'))}\"
            data-title=\"{_escape(node.get('label'))}\"
            data-status=\"{_escape(node.get('status_label'))}\"
            data-risk=\"{_escape(risk_preview)}\">
      <span class=\"node-order\">{_escape(node.get('order'))}</span>
      <strong>{_escape(node.get('label'))}</strong>
      <small>{_escape(node.get('status_label'))} · 风险 {_escape(node.get('risk_count'))}</small>
    </button>
    """


def _render_risk_overlays(overlays: Sequence[Mapping[str, Any]]) -> str:
    if not overlays:
        return "<p class=\"qb-empty\">当前地图暂无风险爆点。</p>"
    return "\n".join(
        f"""
        <article class=\"qb-overlay-card {_severity_class(item.get('severity'))}\">
          <div>
            <span>{_escape(item.get('node_label'))}</span>
            <strong>{_escape(item.get('title'))}</strong>
          </div>
          <b>{_escape(item.get('severity'))}</b>
          <p>{_escape(item.get('business_impact'))}</p>
          <small>{'阻断上线' if item.get('launch_blocking') else '非阻断风险'} · {_escape(item.get('visual_type'))}</small>
        </article>
        """
        for item in overlays
    )


def _render_event_timeline(events: Sequence[Mapping[str, Any]]) -> str:
    if not events:
        return "<li>暂无事件回流。</li>"
    return "\n".join(
        f"<li><span>{_escape(event.get('timestamp'))}</span><strong>{_escape(event.get('message'))}</strong><em>{_escape(event.get('severity'))}</em></li>"
        for event in events
    )


def _render_action_list(actions: Mapping[str, str]) -> str:
    return "\n".join(f"<li><code>{_escape(name)}</code><span>{_escape(path)}</span></li>" for name, path in actions.items())


def render_business_flow_map_html(data: Mapping[str, Any]) -> str:
    project = _as_mapping(data.get("project"))
    summary = _as_mapping(data.get("map_summary"))
    lanes = [_as_mapping(item) for item in _as_list(data.get("flow_lanes")) if isinstance(item, Mapping)]
    overlays = [_as_mapping(item) for item in _as_list(data.get("risk_overlays")) if isinstance(item, Mapping)]
    events = [_as_mapping(item) for item in _as_list(data.get("event_timeline")) if isinstance(item, Mapping)]
    actions = _as_mapping(data.get("phase104_actions"))
    return f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>QualiBug AI · 业务流程地图</title>
  <link rel=\"stylesheet\" href=\"assets/qualibug_business_flow_map.css\" />
</head>
<body>
  <main class=\"qb-map-page\">
    <section class=\"qb-hero\">
      <div>
        <p class=\"eyebrow\">QualiBug AI 企业质量指挥中心 · Phase105E</p>
        <h1>业务流程地图</h1>
        <p>把 AI 已理解的业务链路、节点覆盖状态、风险爆点、环境阻断链路和证据回流放到一张客户看得懂的图上。</p>
      </div>
      <aside>
        <span>当前项目</span>
        <strong>{_escape(project.get('name') or project.get('project_name') or project.get('system_name'))}</strong>
        <small>{_escape(project.get('industry') or data.get('scenario'))}</small>
      </aside>
    </section>

    <section class=\"qb-judgement\">
      <div>
        <span>地图结论</span>
        <strong>{_escape(summary.get('business_judgement'))}</strong>
      </div>
      <div class=\"qb-map-actions\">
        <button data-filter=\"all\">全部链路</button>
        <button data-filter=\"risk\">聚焦高危风险</button>
        <button data-filter=\"blocked\">只看环境阻断</button>
        <button id=\"relayoutBtn\">重新布局</button>
      </div>
    </section>

    <section class=\"qb-map-kpis\">
      {_render_kpi_cards(summary)}
    </section>

    <section class=\"qb-map-grid\">
      <div class=\"qb-map-board\">
        <div class=\"qb-section-title\"><span>AI 已理解的业务链路</span><b>横向链路 / 纵向风险</b></div>
        {_render_flow_lanes(lanes)}
      </div>
      <aside class=\"qb-side-panel\">
        <div class=\"qb-node-detail\" id=\"nodeDetail\">
          <span>节点详情</span>
          <strong>点击业务节点查看状态</strong>
          <p>这里会展示节点覆盖状态、风险标题和业务影响。</p>
        </div>
        <div class=\"qb-panel-card\">
          <h2>风险爆点</h2>
          {_render_risk_overlays(overlays)}
        </div>
        <div class=\"qb-panel-card\">
          <h2>证据回流</h2>
          <ul class=\"qb-event-list\">{_render_event_timeline(events)}</ul>
        </div>
        <div class=\"qb-panel-card\">
          <h2>Phase104 API 动作</h2>
          <ul class=\"qb-action-list\">{_render_action_list(actions)}</ul>
        </div>
      </aside>
    </section>
  </main>
  <script src=\"assets/qualibug_business_flow_map.js\"></script>
</body>
</html>
"""


def render_business_flow_map_css() -> str:
    return """
:root {
  --bg: #07111f;
  --panel: rgba(15, 25, 44, 0.88);
  --card: rgba(255, 255, 255, 0.07);
  --line: rgba(148, 163, 184, 0.24);
  --text: #e5eefb;
  --muted: #92a3b9;
  --good: #45d483;
  --warn: #f8c14a;
  --danger: #ff6b7c;
  --accent: #6dd3ff;
  font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; background: radial-gradient(circle at top left, #12345b 0, var(--bg) 42%, #040814 100%); color: var(--text); }
.qb-map-page { width: min(1480px, calc(100vw - 48px)); margin: 0 auto; padding: 32px 0 48px; }
.qb-hero { display: flex; justify-content: space-between; gap: 24px; align-items: stretch; padding: 28px; border: 1px solid var(--line); border-radius: 28px; background: linear-gradient(135deg, rgba(45, 109, 255, .24), rgba(14, 20, 35, .8)); box-shadow: 0 24px 70px rgba(0, 0, 0, .3); }
.qb-hero h1 { margin: 8px 0 12px; font-size: clamp(34px, 5vw, 58px); letter-spacing: -1px; }
.qb-hero p { max-width: 860px; margin: 0; color: var(--muted); line-height: 1.8; }
.eyebrow { color: var(--accent) !important; font-weight: 800; letter-spacing: .08em; }
.qb-hero aside { min-width: 300px; border-radius: 22px; padding: 22px; background: rgba(255,255,255,.08); border: 1px solid var(--line); }
.qb-hero aside span, .qb-hero aside small { display: block; color: var(--muted); }
.qb-hero aside strong { display: block; margin: 12px 0; font-size: 24px; }
.qb-judgement { margin: 22px 0; padding: 20px 24px; border: 1px solid var(--line); border-radius: 24px; display: flex; justify-content: space-between; gap: 24px; align-items: center; background: rgba(255,255,255,.06); }
.qb-judgement span { color: var(--muted); }
.qb-judgement strong { display: block; margin-top: 6px; font-size: 22px; }
.qb-map-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.qb-map-actions button { border: 1px solid var(--line); color: var(--text); background: rgba(255,255,255,.08); padding: 10px 14px; border-radius: 999px; cursor: pointer; }
.qb-map-actions button:hover, .qb-map-actions button.active { border-color: var(--accent); color: var(--accent); }
.qb-map-kpis { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 14px; margin-bottom: 20px; }
.qb-map-kpi { padding: 18px; border: 1px solid var(--line); border-radius: 22px; background: var(--card); }
.qb-map-kpi span, .qb-map-kpi small { color: var(--muted); }
.qb-map-kpi strong { display: block; margin: 10px 0 4px; font-size: 34px; }
.qb-map-grid { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 18px; align-items: start; }
.qb-map-board, .qb-side-panel { border: 1px solid var(--line); border-radius: 28px; background: var(--panel); padding: 20px; }
.qb-section-title { display: flex; justify-content: space-between; color: var(--muted); margin-bottom: 16px; }
.qb-section-title span { color: var(--text); font-weight: 900; }
.qb-flow-lane { border: 1px solid var(--line); border-radius: 24px; background: rgba(255,255,255,.045); padding: 18px; margin-bottom: 16px; transition: opacity .2s ease, transform .2s ease; }
.qb-flow-lane.hidden { display: none; }
.qb-flow-head { display: flex; justify-content: space-between; gap: 18px; }
.qb-flow-head h2 { margin: 6px 0; font-size: 22px; }
.qb-flow-head p { margin: 0; color: var(--muted); line-height: 1.7; }
.qb-flow-order { color: var(--accent); font-weight: 900; }
.qb-flow-meta { display: flex; align-items: flex-start; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.qb-flow-meta b, .status-pill { border-radius: 999px; padding: 6px 10px; border: 1px solid var(--line); font-size: 12px; }
.status-pill.risk, .status-pill.blocked { border-color: rgba(255,107,124,.6); color: var(--danger); }
.status-pill.partial_executable { border-color: rgba(248,193,74,.6); color: var(--warn); }
.status-pill.executable, .status-pill.ready { border-color: rgba(69,212,131,.6); color: var(--good); }
.qb-node-track { margin: 18px 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(126px, 1fr)); gap: 12px; position: relative; }
.qb-map-node { min-height: 112px; text-align: left; border: 1px solid var(--line); border-radius: 20px; padding: 14px; color: var(--text); background: rgba(255,255,255,.06); cursor: pointer; position: relative; overflow: hidden; }
.qb-map-node:before { content: ''; position: absolute; inset: 0 0 auto 0; height: 4px; background: var(--muted); }
.qb-map-node.good:before { background: var(--good); }
.qb-map-node.warn:before { background: var(--warn); }
.qb-map-node.danger:before { background: var(--danger); }
.qb-map-node.idle:before { background: #64748b; }
.qb-map-node:hover, .qb-map-node.active { border-color: var(--accent); transform: translateY(-2px); }
.node-order { display: inline-flex; width: 26px; height: 26px; align-items: center; justify-content: center; border-radius: 50%; background: rgba(255,255,255,.1); color: var(--accent); font-weight: 900; }
.qb-map-node strong { display: block; margin: 12px 0 8px; }
.qb-map-node small { color: var(--muted); line-height: 1.5; }
.qb-flow-detail-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.qb-flow-detail-grid > div { border: 1px solid var(--line); border-radius: 18px; padding: 14px; background: rgba(0,0,0,.16); }
.qb-flow-detail-grid strong { display: block; margin-bottom: 8px; }
.qb-flow-detail-grid p, .qb-flow-detail-grid ul { margin: 0; color: var(--muted); line-height: 1.6; padding-left: 16px; }
.qb-side-panel { position: sticky; top: 18px; }
.qb-node-detail, .qb-panel-card { border: 1px solid var(--line); border-radius: 22px; padding: 16px; background: rgba(255,255,255,.055); margin-bottom: 14px; }
.qb-node-detail span { color: var(--accent); font-weight: 800; }
.qb-node-detail strong { display: block; margin: 8px 0; font-size: 20px; }
.qb-node-detail p { margin: 0; color: var(--muted); line-height: 1.7; }
.qb-panel-card h2 { margin: 0 0 12px; font-size: 18px; }
.qb-overlay-card { border: 1px solid var(--line); border-radius: 18px; padding: 14px; margin-bottom: 10px; background: rgba(255,255,255,.05); }
.qb-overlay-card.danger { border-color: rgba(255,107,124,.55); }
.qb-overlay-card.warn { border-color: rgba(248,193,74,.55); }
.qb-overlay-card div { display: flex; justify-content: space-between; gap: 10px; color: var(--muted); }
.qb-overlay-card strong { display: block; color: var(--text); margin-top: 4px; }
.qb-overlay-card b { display: inline-block; margin: 10px 0; color: var(--danger); }
.qb-overlay-card p, .qb-overlay-card small { color: var(--muted); line-height: 1.6; }
.qb-event-list, .qb-action-list { padding: 0; margin: 0; list-style: none; }
.qb-event-list li, .qb-action-list li { border-top: 1px solid var(--line); padding: 12px 0; display: grid; gap: 6px; }
.qb-event-list span, .qb-event-list em, .qb-action-list code { color: var(--muted); font-size: 12px; }
.qb-action-list span { color: var(--text); word-break: break-all; }
.risk-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; background: var(--muted); }
.risk-dot.danger { background: var(--danger); }
.risk-dot.warn { background: var(--warn); }
.risk-dot.good { background: var(--good); }
.qb-empty { color: var(--muted); }
@media (max-width: 1100px) { .qb-map-grid { grid-template-columns: 1fr; } .qb-side-panel { position: static; } .qb-map-kpis, .qb-flow-detail-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .qb-hero, .qb-judgement { flex-direction: column; } }
@media (max-width: 680px) { .qb-map-page { width: min(100vw - 24px, 100%); } .qb-map-kpis, .qb-flow-detail-grid { grid-template-columns: 1fr; } }
"""


def render_business_flow_map_js() -> str:
    return """
(function () {
  const nodes = Array.from(document.querySelectorAll('.qb-map-node'));
  const detail = document.getElementById('nodeDetail');
  const filterButtons = Array.from(document.querySelectorAll('[data-filter]'));
  const lanes = Array.from(document.querySelectorAll('.qb-flow-lane'));

  nodes.forEach((node) => {
    node.addEventListener('click', () => {
      nodes.forEach((item) => item.classList.remove('active'));
      node.classList.add('active');
      if (!detail) return;
      const title = node.getAttribute('data-title') || '业务节点';
      const status = node.getAttribute('data-status') || '待确认';
      const risk = node.getAttribute('data-risk') || '暂无风险爆点';
      detail.innerHTML = `<span>节点详情</span><strong>${title}</strong><p>节点覆盖状态：${status}</p><p>风险摘要：${risk}</p>`;
    });
  });

  filterButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const filter = button.getAttribute('data-filter') || 'all';
      filterButtons.forEach((item) => item.classList.toggle('active', item === button));
      lanes.forEach((lane) => {
        const status = lane.getAttribute('data-status') || '';
        const hasRisk = status === 'risk' || lane.querySelector('.qb-map-node.danger');
        const isBlocked = status === 'partial_executable' || status === 'blocked';
        const show = filter === 'all' || (filter === 'risk' && hasRisk) || (filter === 'blocked' && isBlocked);
        lane.classList.toggle('hidden', !show);
      });
    });
  });

  const relayout = document.getElementById('relayoutBtn');
  if (relayout) {
    relayout.addEventListener('click', () => {
      lanes.forEach((lane, index) => {
        lane.style.transform = 'translateY(-4px)';
        setTimeout(() => { lane.style.transform = 'translateY(0)'; }, 120 + index * 40);
      });
    });
  }
})();
"""


def render_business_flow_map_readme(data: Mapping[str, Any]) -> str:
    summary = _as_mapping(data.get("map_summary"))
    return f"""# Phase105E 业务流程地图体验

本目录由 `ai_test_asset_center.phase105_business_flow_map_experience` 生成。

## 页面目标

把 AI 已理解的业务链路、节点覆盖状态、风险爆点、环境阻断链路和证据回流做成客户能看懂的业务地图。

## 核心指标

- 业务链路：{summary.get('total_flows')} 条
- 覆盖率：{summary.get('coverage_rate')}%
- 风险爆点：{summary.get('risk_overlays')} 个
- 环境阻断链路：{summary.get('blocked_flows')} 条
- 地图结论：{summary.get('business_judgement')}

## 主要文件

- `business_flow_map.html`：业务流程地图入口。
- `data/business_flow_map_experience_data.json`：前端可绑定的脱敏数据模型。
- `assets/qualibug_business_flow_map.css`：页面样式。
- `assets/qualibug_business_flow_map.js`：节点点击、过滤和重新布局交互。
- `business_flow_map_experience_acceptance_report.md`：页面验收报告。
"""


def scan_business_flow_map_for_secret_leaks(output_dir: str | Path) -> list[str]:
    root = Path(output_dir)
    leaks: list[str] = []
    if not root.exists():
        return [f"missing output dir: {root}"]
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".zip"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in FORBIDDEN_BUSINESS_FLOW_MAP_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(root)} contains forbidden pattern: {pattern}")
    return leaks


def build_business_flow_map_experience(
    output_dir: str | Path,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
) -> dict[str, Any]:
    output = Path(output_dir)
    data = build_business_flow_map_view_model(scenario=scenario, api_base_url=api_base_url)

    _write_text(output / "business_flow_map.html", render_business_flow_map_html(data))
    _write_text(output / "assets" / "qualibug_business_flow_map.css", render_business_flow_map_css())
    _write_text(output / "assets" / "qualibug_business_flow_map.js", render_business_flow_map_js())
    _write_text(output / "data" / "business_flow_map_experience_data.json", _json_dump(data))
    _write_text(output / "README_BUSINESS_FLOW_MAP_EXPERIENCE.md", render_business_flow_map_readme(data))

    leaks = scan_business_flow_map_for_secret_leaks(output)
    manifest = redact_value(
        {
            "version": PHASE105E_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "entrypoint": "business_flow_map.html",
            "output_dir": str(output),
            "required_files": list(REQUIRED_BUSINESS_FLOW_MAP_FILES),
            "core_labels": list(CORE_BUSINESS_FLOW_MAP_LABELS),
            "map_summary": data.get("map_summary"),
            "redaction_status": "safe" if not leaks else "unsafe",
            "secret_leaks": leaks,
        }
    )
    _write_text(output / BUSINESS_FLOW_MAP_MANIFEST, _json_dump(manifest))
    report = validate_business_flow_map_experience(output)
    write_business_flow_map_acceptance_report(output, report)
    return manifest


def validate_business_flow_map_experience(output_dir: str | Path) -> BusinessFlowMapExperienceAcceptanceReport:
    output = Path(output_dir)
    checks: list[BusinessFlowMapExperienceCheck] = []

    missing = [file_name for file_name in REQUIRED_BUSINESS_FLOW_MAP_FILES if not (output / file_name).exists()]
    checks.append(
        BusinessFlowMapExperienceCheck(
            key="required_files",
            passed=not missing,
            detail="all required files exist" if not missing else "missing files: " + ", ".join(missing),
        )
    )

    html_text = (output / "business_flow_map.html").read_text(encoding="utf-8") if (output / "business_flow_map.html").exists() else ""
    missing_labels = [label for label in CORE_BUSINESS_FLOW_MAP_LABELS if label not in html_text]
    checks.append(
        BusinessFlowMapExperienceCheck(
            key="core_labels",
            passed=not missing_labels,
            detail="core map labels are present" if not missing_labels else "missing labels: " + ", ".join(missing_labels),
        )
    )

    data_path = output / "data" / "business_flow_map_experience_data.json"
    data: dict[str, Any] = {}
    if data_path.exists():
        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            checks.append(BusinessFlowMapExperienceCheck(key="json_parse", passed=False, detail=f"invalid data json: {exc}"))
    lanes = _as_list(data.get("flow_lanes"))
    overlays = _as_list(data.get("risk_overlays"))
    summary = _as_mapping(data.get("map_summary"))
    checks.append(
        BusinessFlowMapExperienceCheck(
            key="flow_lanes",
            passed=bool(lanes) and all(_as_list(_as_mapping(lane).get("nodes")) for lane in lanes if isinstance(lane, Mapping)),
            detail=f"flow lanes={len(lanes)} with node tracks",
        )
    )
    checks.append(
        BusinessFlowMapExperienceCheck(
            key="risk_overlays",
            passed=bool(overlays),
            detail=f"risk overlays={len(overlays)}",
        )
    )
    checks.append(
        BusinessFlowMapExperienceCheck(
            key="map_summary",
            passed=_int_value(summary.get("total_flows")) > 0 and "business_judgement" in summary,
            detail=f"summary={summary}",
        )
    )
    phase104_actions = _as_mapping(data.get("phase104_actions"))
    checks.append(
        BusinessFlowMapExperienceCheck(
            key="phase104_actions",
            passed="read_live_map" in phase104_actions and str(phase104_actions.get("read_live_map", "")).startswith("GET /api/v1/projects/"),
            detail="Phase104 live-map/risk/test-plan actions are present",
        )
    )

    leaks = scan_business_flow_map_for_secret_leaks(output)
    checks.append(
        BusinessFlowMapExperienceCheck(
            key="secret_leak_scan",
            passed=not leaks,
            detail="no forbidden secret patterns found" if not leaks else "; ".join(leaks),
        )
    )

    passed = all(check.passed for check in checks)
    score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    scenario = _safe_text(data.get("scenario"), "unknown") if data else "unknown"
    return BusinessFlowMapExperienceAcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE105E_VERSION,
        scenario=scenario,
        output_dir=str(output),
        checks=checks,
        artifacts={
            "entrypoint": "business_flow_map.html",
            "manifest": BUSINESS_FLOW_MAP_MANIFEST,
            "data": "data/business_flow_map_experience_data.json",
        },
    )


def write_business_flow_map_acceptance_report(output_dir: str | Path, report: BusinessFlowMapExperienceAcceptanceReport) -> None:
    output = Path(output_dir)
    payload = report.to_dict()
    _write_text(output / BUSINESS_FLOW_MAP_ACCEPTANCE_JSON, _json_dump(payload))
    rows = "\n".join(f"| {check.key} | {'PASS' if check.passed else 'FAIL'} | {check.detail} |" for check in report.checks)
    md = f"""# Phase105E 业务流程地图体验验收报告

- 结果：{'通过' if report.passed else '未通过'}
- 分数：{report.score}
- 场景：{report.scenario}
- 版本：{report.version}

| 检查项 | 状态 | 详情 |
|---|---|---|
{rows}
"""
    _write_text(output / BUSINESS_FLOW_MAP_ACCEPTANCE_MD, md)


def run_business_flow_map_experience_export(
    output_dir: str | Path,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
    validate_only: bool = False,
) -> dict[str, Any]:
    output = Path(output_dir)
    manifest: dict[str, Any] = {}
    if validate_only:
        manifest_path = output / BUSINESS_FLOW_MAP_MANIFEST
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = build_business_flow_map_experience(output, scenario=scenario, api_base_url=api_base_url)
    report = validate_business_flow_map_experience(output)
    write_business_flow_map_acceptance_report(output, report)
    return {"manifest": manifest, "acceptance": report.to_dict()}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate or validate the Phase105E business flow map experience.")
    parser.add_argument("--output-dir", default="outputs/phase105_business_flow_map_experience")
    parser.add_argument("--scenario", default="manufacturing", choices=("manufacturing", "ecommerce", "saas"))
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_business_flow_map_experience_export(
        output_dir=args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(_json_dump(result))
    return 0 if result["acceptance"].get("passed") else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

