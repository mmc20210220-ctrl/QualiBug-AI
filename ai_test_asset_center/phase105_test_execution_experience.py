from __future__ import annotations

"""Phase105I: AI test plan and realtime execution frontend experience.

Phase105A-H focused on the product shell, dashboard, customer intake,
environment diagnosis, business-flow map, risk/evidence view, report/ROI view,
and unified hub.  Phase105I fills the execution-control gap: a customer-facing
page that explains what AI plans to test, which probes are executable or
blocked, what is currently running, which realtime events were produced, and how
evidence flows back to risks.

The exporter is framework-neutral and dependency-free.  It writes static
HTML/CSS/JS plus a redacted JSON view model that can later be reused by a real
React/Vue page connected to the Phase104 API.
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

PHASE105I_VERSION = "phase105i-test-execution-experience-v1"

TEST_EXECUTION_MANIFEST = "test_execution_experience_manifest.json"
TEST_EXECUTION_ACCEPTANCE_JSON = "test_execution_experience_acceptance_report.json"
TEST_EXECUTION_ACCEPTANCE_MD = "test_execution_experience_acceptance_report.md"

REQUIRED_TEST_EXECUTION_FILES: tuple[str, ...] = (
    "test_execution.html",
    "README_TEST_EXECUTION_EXPERIENCE.md",
    "data/test_execution_experience_data.json",
    "assets/qualibug_test_execution.css",
    "assets/qualibug_test_execution.js",
    TEST_EXECUTION_MANIFEST,
)

CORE_TEST_EXECUTION_LABELS: tuple[str, ...] = (
    "AI 测试计划",
    "实时测试执行",
    "可执行探针",
    "阻断探针",
    "安全执行模式",
    "执行时间线",
    "风险事件",
    "证据回流",
    "客户补料",
    "Phase104 API 动作交接",
    "默认脱敏",
)

FORBIDDEN_TEST_EXECUTION_PATTERNS: tuple[str, ...] = (
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

PROBE_TYPE_LABELS: dict[str, str] = {
    "auth_probe": "认证与会话",
    "role_permission_probe": "角色权限边界",
    "business_flow_probe": "业务链路验证",
    "state_consistency_probe": "状态一致性",
    "api_smoke_probe": "API Smoke",
    "evidence_probe": "证据回流",
}

EVENT_TYPE_LABELS: dict[str, str] = {
    "test_run_status": "运行状态",
    "risk_detected": "风险事件",
    "probe_started": "探针开始",
    "probe_finished": "探针完成",
    "evidence_generated": "证据回流",
}

SEVERITY_LABELS: dict[str, str] = {
    "critical": "严重",
    "high": "高危",
    "medium": "中危",
    "low": "低危",
    "info": "提示",
}


@dataclass(frozen=True)
class TestExecutionExperienceCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class TestExecutionExperienceAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    checks: list[TestExecutionExperienceCheck] = field(default_factory=list)
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, Mapping) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _safe_text(value: Any, default: str = "—") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _escape(value: Any) -> str:
    return html.escape(_safe_text(value), quote=True)


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


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


def _probe_type_label(value: Any) -> str:
    return PROBE_TYPE_LABELS.get(_safe_text(value, "").lower(), _safe_text(value, "通用探针"))


def _event_type_label(value: Any) -> str:
    return EVENT_TYPE_LABELS.get(_safe_text(value, "").lower(), _safe_text(value, "执行事件"))


def _severity_label(value: Any) -> str:
    return SEVERITY_LABELS.get(_safe_text(value, "info").lower(), _safe_text(value, "提示"))


def _normalize_probe(probe: Mapping[str, Any]) -> dict[str, Any]:
    executable = bool(probe.get("executable"))
    return {
        "probe_id": _safe_text(probe.get("probe_id")),
        "name": _safe_text(probe.get("name")),
        "probe_type": _safe_text(probe.get("probe_type"), "probe"),
        "probe_type_label": _probe_type_label(probe.get("probe_type")),
        "execution_mode": _safe_text(probe.get("execution_mode"), "read_only"),
        "estimated_test_points": _int_value(probe.get("estimated_test_points")),
        "required_roles": [_safe_text(role) for role in _as_list(probe.get("required_roles"))],
        "executable": executable,
        "status_label": "可执行" if executable else "已阻断",
        "blocked_reason": _safe_text(probe.get("blocked_reason"), "—"),
    }


def _normalize_probe_group(group: Mapping[str, Any]) -> dict[str, Any]:
    probes = [_normalize_probe(_as_mapping(probe)) for probe in _as_list(group.get("probes"))]
    executable = sum(1 for probe in probes if probe["executable"])
    blocked = len(probes) - executable
    estimated_points = sum(_int_value(probe.get("estimated_test_points")) for probe in probes)
    return {
        "group_id": _safe_text(group.get("group_id")),
        "business_flow_id": _safe_text(group.get("business_flow_id")),
        "business_flow_name": _safe_text(group.get("business_flow_name")),
        "status": _safe_text(group.get("status"), "partial_executable"),
        "probe_total": _int_value(group.get("probe_total"), len(probes)),
        "probe_executable": _int_value(group.get("probe_executable"), executable),
        "probe_blocked": _int_value(group.get("probe_blocked"), blocked),
        "estimated_test_points": estimated_points,
        "blocked_reasons": [_safe_text(reason) for reason in _as_list(group.get("blocked_reasons"))],
        "probes": probes,
    }


def _normalize_event(event: Mapping[str, Any], index: int) -> dict[str, Any]:
    severity = _safe_text(event.get("severity"), "info")
    return {
        "step": index,
        "event_id": _safe_text(event.get("event_id")),
        "event_type": _safe_text(event.get("event_type"), "test_run_status"),
        "event_type_label": _event_type_label(event.get("event_type")),
        "severity": severity,
        "severity_label": _severity_label(severity),
        "timestamp": _safe_text(event.get("timestamp")),
        "message": _safe_text(event.get("message")),
        "risk_id": _safe_text(event.get("risk_id"), "—"),
    }


def _normalize_node(node: Mapping[str, Any]) -> dict[str, Any]:
    metrics = _as_mapping(node.get("metrics"))
    return {
        "node_id": _safe_text(node.get("node_id")),
        "label": _safe_text(node.get("label")),
        "status": _safe_text(node.get("status"), "idle"),
        "risk_level": _safe_text(node.get("risk_level"), "none"),
        "probe_total": _int_value(metrics.get("probe_total")),
        "probe_passed": _int_value(metrics.get("probe_passed")),
        "probe_failed": _int_value(metrics.get("probe_failed")),
        "risk_count": _int_value(metrics.get("risk_count")),
    }


def _normalize_risk_overlay(overlay: Mapping[str, Any]) -> dict[str, Any]:
    severity = _safe_text(overlay.get("severity"), "info")
    return {
        "risk_id": _safe_text(overlay.get("risk_id")),
        "node_id": _safe_text(overlay.get("node_id")),
        "severity": severity,
        "severity_label": _severity_label(severity),
        "launch_blocking": bool(overlay.get("launch_blocking")),
        "business_impact": _safe_text(overlay.get("business_impact")),
        "visual_type": _safe_text(overlay.get("visual_type"), "pulse"),
    }


def _extract_test_execution_view_model(scenario: str, api_base_url: str) -> dict[str, Any]:
    raw = collect_product_shell_demo_data(scenario=scenario, api_base_url=api_base_url)
    project = _as_mapping(raw.get("project"))
    test_plan = _as_mapping(raw.get("test_plan"))
    coverage = _as_mapping(test_plan.get("coverage_summary"))
    estimated_value = _as_mapping(test_plan.get("estimated_value"))
    live_map = _as_mapping(raw.get("live_map"))
    environment = _as_mapping(raw.get("environment"))
    risks = _as_list(raw.get("risks"))

    probe_groups = [_normalize_probe_group(_as_mapping(group)) for group in _as_list(test_plan.get("probe_groups"))]
    all_probes = [probe for group in probe_groups for probe in _as_list(group.get("probes"))]
    executable_probe_count = sum(1 for probe in all_probes if bool(_as_mapping(probe).get("executable")))
    blocked_probe_count = len(all_probes) - executable_probe_count
    timeline_events = [_normalize_event(_as_mapping(event), index) for index, event in enumerate(_as_list(live_map.get("events")), start=1)]
    nodes = [_normalize_node(_as_mapping(node)) for node in _as_list(live_map.get("nodes"))[:12]]
    risk_overlays = [_normalize_risk_overlay(_as_mapping(overlay)) for overlay in _as_list(live_map.get("risk_overlays"))]
    evidence_events = [event for event in timeline_events if event["event_type"] == "evidence_generated"]
    risk_events = [event for event in timeline_events if event["event_type"] == "risk_detected"]

    # Demo live-map data may not emit a separate evidence_generated event.  Keep the
    # customer experience explicit by deriving evidence回流 from confirmed risks.
    if not evidence_events:
        for index, risk in enumerate(risks[:3], start=1):
            risk_map = _as_mapping(risk)
            evidence_events.append(
                {
                    "step": index,
                    "event_id": f"derived_evidence_{index}",
                    "event_type": "evidence_generated",
                    "event_type_label": "证据回流",
                    "severity": _safe_text(risk_map.get("severity"), "info"),
                    "severity_label": _severity_label(risk_map.get("severity")),
                    "timestamp": _safe_text(risk_map.get("last_verified_at") or risk_map.get("first_seen_at")),
                    "message": f"证据链已回流：{_safe_text(risk_map.get('title'))}",
                    "risk_id": _safe_text(risk_map.get("risk_id")),
                }
            )

    execution_summary = {
        "run_id": _safe_text(live_map.get("run_id")),
        "map_id": _safe_text(live_map.get("map_id")),
        "status": "completed" if timeline_events else _safe_text(test_plan.get("status"), "ready"),
        "status_label": "执行完成" if timeline_events else "待执行",
        "safe_execution_mode": _safe_text(test_plan.get("safe_execution_mode") or environment.get("safe_execution_mode"), "read_only"),
        "probe_total": len(all_probes),
        "probe_executable": executable_probe_count,
        "probe_blocked": blocked_probe_count,
        "risk_event_count": len(risk_events),
        "evidence_event_count": len(evidence_events),
        "node_count": len(_as_list(live_map.get("nodes"))),
        "updated_at": _safe_text(live_map.get("updated_at") or test_plan.get("generated_at")),
    }

    view_model = {
        "version": PHASE105I_VERSION,
        "generated_at": _now(),
        "scenario": scenario,
        "api_base_url": api_base_url,
        "project": project,
        "test_plan": {
            "plan_id": _safe_text(test_plan.get("plan_id")),
            "name": _safe_text(test_plan.get("name"), "AI 测试计划"),
            "status": _safe_text(test_plan.get("status"), "ready"),
            "generated_at": _safe_text(test_plan.get("generated_at")),
            "safe_execution_mode": execution_summary["safe_execution_mode"],
            "coverage_summary": {
                "business_flow_total": _int_value(coverage.get("business_flow_total")),
                "business_flow_executable": _int_value(coverage.get("business_flow_executable")),
                "business_flow_blocked": _int_value(coverage.get("business_flow_blocked")),
                "core_flow_executable": _int_value(coverage.get("core_flow_executable")),
            },
            "estimated_value": {
                "equivalent_test_points": _int_value(estimated_value.get("equivalent_test_points")),
                "estimated_hours_saved": _float_value(estimated_value.get("estimated_hours_saved")),
                "manual_minutes_per_test_point": _int_value(estimated_value.get("manual_minutes_per_test_point"), 12),
            },
            "probe_groups": probe_groups,
            "required_customer_inputs": _as_list(test_plan.get("required_customer_inputs") or environment.get("required_customer_inputs")),
        },
        "realtime_execution": {
            "summary": execution_summary,
            "timeline_events": timeline_events,
            "risk_events": risk_events,
            "evidence_events": evidence_events,
            "nodes": nodes,
            "risk_overlays": risk_overlays,
        },
        "operator_panels": [
            {
                "title": "执行前确认",
                "items": ["确认安全执行模式", "确认账号角色", "确认补料清单", "确认可执行探针范围"],
            },
            {
                "title": "执行中观察",
                "items": ["观察当前业务节点", "观察风险事件", "观察证据回流", "观察环境阻断"],
            },
            {
                "title": "执行后动作",
                "items": ["进入风险证据链", "生成领导报告", "输出修复与复验队列", "同步上线决策"],
            },
        ],
        "phase104_actions": {
            "read_test_plan": "/api/v1/projects/{project_id}/test-plan",
            "start_test_run": "/api/v1/projects/{project_id}/runs",
            "read_live_map": "/api/v1/projects/{project_id}/live-map",
            "read_risks": "/api/v1/projects/{project_id}/risks",
            "read_risk_detail": "/api/v1/projects/{project_id}/risks/{risk_id}",
        },
        "display_principles": [
            "先解释准备测什么，再展示正在测什么。",
            "阻断探针必须显示客户可执行的阻断原因。",
            "实时事件要把风险和证据回流映射到业务节点。",
            "默认脱敏，不展示 token、cookie、password、session、client_secret 原值。",
        ],
    }
    return redact_value(view_model)


def _render_kpi(label: str, value: Any, detail: str) -> str:
    return f"""
      <article class=\"kpi-card\">
        <span>{_escape(label)}</span>
        <strong>{_escape(value)}</strong>
        <p>{_escape(detail)}</p>
      </article>
    """


def _render_probe(probe: Mapping[str, Any]) -> str:
    executable_class = "probe-ok" if bool(probe.get("executable")) else "probe-blocked"
    roles = "、".join(_safe_text(role) for role in _as_list(probe.get("required_roles"))) or "—"
    return f"""
      <li class=\"probe-item {executable_class}\">
        <div>
          <strong>{_escape(probe.get('name'))}</strong>
          <p>{_escape(probe.get('probe_type_label'))} · 模式 {_escape(probe.get('execution_mode'))} · 角色 {html.escape(roles)}</p>
          <small>阻断原因：{_escape(probe.get('blocked_reason'))}</small>
        </div>
        <span>{_escape(probe.get('status_label'))}</span>
      </li>
    """


def _render_probe_group(group: Mapping[str, Any]) -> str:
    probes = "\n".join(_render_probe(_as_mapping(probe)) for probe in _as_list(group.get("probes")))
    reasons = _as_list(group.get("blocked_reasons"))
    reason_html = "".join(f"<li>{_escape(reason)}</li>" for reason in reasons) or "<li>暂无阻断原因</li>"
    return f"""
      <article class=\"probe-group\">
        <header>
          <div>
            <span>业务链路</span>
            <h3>{_escape(group.get('business_flow_name'))}</h3>
          </div>
          <p>可执行 {_escape(group.get('probe_executable'))} / 总计 {_escape(group.get('probe_total'))} · 阻断 {_escape(group.get('probe_blocked'))}</p>
        </header>
        <ul class=\"probe-list\">{probes}</ul>
        <details>
          <summary>阻断探针原因</summary>
          <ul>{reason_html}</ul>
        </details>
      </article>
    """


def _render_event(event: Mapping[str, Any]) -> str:
    return f"""
      <li class=\"event-item severity-{_escape(event.get('severity'))}\">
        <span class=\"event-step\">{_escape(event.get('step'))}</span>
        <div>
          <strong>{_escape(event.get('event_type_label'))} · {_escape(event.get('severity_label'))}</strong>
          <p>{_escape(event.get('message'))}</p>
          <small>{_escape(event.get('timestamp'))} · risk: {_escape(event.get('risk_id'))}</small>
        </div>
      </li>
    """


def _render_node(node: Mapping[str, Any]) -> str:
    return f"""
      <button class=\"node-card risk-{_escape(node.get('risk_level'))}\" data-node=\"{_escape(node.get('node_id'))}\">
        <strong>{_escape(node.get('label'))}</strong>
        <span>{_escape(node.get('status'))} · 风险 {_escape(node.get('risk_count'))}</span>
      </button>
    """


def _render_customer_input(item: Any) -> str:
    data = _as_mapping(item)
    if not data:
        return f"<li>{_escape(item)}</li>"
    title = _safe_text(data.get("title") or data.get("name") or data.get("field"), "客户补料")
    reason = _safe_text(data.get("reason") or data.get("description") or data.get("detail"), "用于解除阻断探针或提升覆盖率。")
    priority = _safe_text(data.get("priority"), "P1")
    return f"<li><strong>{_escape(priority)} · {_escape(title)}</strong><p>{_escape(reason)}</p></li>"


def render_test_execution_html(data: Mapping[str, Any]) -> str:
    project = _as_mapping(data.get("project"))
    test_plan = _as_mapping(data.get("test_plan"))
    coverage = _as_mapping(test_plan.get("coverage_summary"))
    value = _as_mapping(test_plan.get("estimated_value"))
    realtime = _as_mapping(data.get("realtime_execution"))
    summary = _as_mapping(realtime.get("summary"))
    probe_groups = _as_list(test_plan.get("probe_groups"))
    events = _as_list(realtime.get("timeline_events"))
    evidence_events = _as_list(realtime.get("evidence_events"))
    nodes = _as_list(realtime.get("nodes"))
    inputs = _as_list(test_plan.get("required_customer_inputs"))

    kpis = "\n".join(
        [
            _render_kpi("可执行探针", summary.get("probe_executable"), "当前环境、认证和安全模式允许执行的探针。"),
            _render_kpi("阻断探针", summary.get("probe_blocked"), "因权限、写入限制或客户补料不足而暂缓。"),
            _render_kpi("核心链路", f"{coverage.get('core_flow_executable')} / {coverage.get('business_flow_total')}", "本轮可执行的核心业务链路。"),
            _render_kpi("节省工时", f"{value.get('estimated_hours_saved')}h", "按 AI 等价测试点折算的人工节省。"),
        ]
    )
    groups_html = "\n".join(_render_probe_group(_as_mapping(group)) for group in probe_groups)
    events_html = "\n".join(_render_event(_as_mapping(event)) for event in events)
    evidence_html = "\n".join(_render_event(_as_mapping(event)) for event in evidence_events)
    nodes_html = "\n".join(_render_node(_as_mapping(node)) for node in nodes)
    inputs_html = "\n".join(_render_customer_input(item) for item in inputs) or "<li>当前无必须客户补料。</li>"

    return f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>QualiBug AI · AI 测试计划与实时测试执行</title>
  <link rel=\"stylesheet\" href=\"assets/qualibug_test_execution.css\" />
</head>
<body>
  <main class=\"page\">
    <header class=\"hero\">
      <nav>
        <a href=\"index.html\">产品壳</a>
        <a href=\"dashboard.html\">质量驾驶舱</a>
        <a href=\"business_flow_map.html\">业务流程地图</a>
        <a href=\"risk_evidence.html\">风险证据</a>
      </nav>
      <div>
        <p class=\"eyebrow\">QualiBug AI 企业质量指挥中心 · Phase105I</p>
        <h1>AI 测试计划 + 实时测试执行</h1>
        <p>把“准备测什么、能测什么、为什么不能测、AI 正在跑到哪里、产生了哪些风险事件和证据回流”放到一个执行控制台里。</p>
      </div>
      <aside class=\"run-card\">
        <span>实时测试执行</span>
        <strong>{_escape(summary.get('status_label'))}</strong>
        <p>安全执行模式：{_escape(summary.get('safe_execution_mode'))}</p>
      </aside>
    </header>

    <section class=\"project-strip\">
      <div><span>客户</span><strong>{_escape(project.get('customer_name'))}</strong></div>
      <div><span>系统</span><strong>{_escape(project.get('system_name'))}</strong></div>
      <div><span>计划</span><strong>{_escape(test_plan.get('name'))}</strong></div>
      <div><span>运行 ID</span><strong>{_escape(summary.get('run_id'))}</strong></div>
    </section>

    <section class=\"kpi-grid\" aria-label=\"AI 测试计划核心指标\">{kpis}</section>

    <section class=\"layout two-col\">
      <div>
        <div class=\"section-title\"><span>AI 测试计划</span><h2>可执行探针 / 阻断探针</h2></div>
        <div class=\"probe-groups\">{groups_html}</div>
      </div>
      <aside class=\"customer-inputs\">
        <div class=\"section-title\"><span>客户补料</span><h2>解除阻断的下一步</h2></div>
        <ul>{inputs_html}</ul>
        <p class=\"safe-note\">默认脱敏：前端不展示 token / cookie / password / session / client_secret 原值。</p>
      </aside>
    </section>

    <section class=\"layout two-col\">
      <div>
        <div class=\"section-title\"><span>实时测试执行</span><h2>执行时间线与风险事件</h2></div>
        <ol class=\"event-list\">{events_html}</ol>
      </div>
      <aside>
        <div class=\"section-title\"><span>证据回流</span><h2>已生成的证据链</h2></div>
        <ol class=\"event-list compact\">{evidence_html}</ol>
      </aside>
    </section>

    <section>
      <div class=\"section-title\"><span>业务节点运行态</span><h2>AI 当前覆盖的业务节点</h2></div>
      <div class=\"node-grid\">{nodes_html}</div>
    </section>

    <section class=\"api-panel\">
      <div class=\"section-title\"><span>Phase104 API 动作交接</span><h2>后续前端真实接入</h2></div>
      <code>GET /api/v1/projects/{{project_id}}/test-plan</code>
      <code>POST /api/v1/projects/{{project_id}}/runs</code>
      <code>GET /api/v1/projects/{{project_id}}/live-map</code>
      <code>GET /api/v1/projects/{{project_id}}/risks</code>
    </section>
  </main>
  <script src=\"assets/qualibug_test_execution.js\"></script>
</body>
</html>
"""


def render_test_execution_css() -> str:
    return """
:root { color-scheme: dark; font-family: Inter, 'Microsoft YaHei', system-ui, sans-serif; background: #07111f; color: #e8f0ff; }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; background: radial-gradient(circle at top left, rgba(54, 211, 153, .18), transparent 34rem), #07111f; }
a { color: inherit; text-decoration: none; }
.page { width: min(1440px, calc(100% - 48px)); margin: 0 auto; padding: 28px 0 54px; }
.hero, .project-strip, .kpi-card, .probe-group, .customer-inputs, .event-list, .node-card, .api-panel { border: 1px solid rgba(148, 163, 184, .2); background: rgba(15, 23, 42, .76); box-shadow: 0 22px 80px rgba(0,0,0,.28); }
.hero { border-radius: 30px; padding: 26px; display: grid; grid-template-columns: 1fr 320px; gap: 24px; align-items: end; }
nav { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 28px; }
nav a { padding: 8px 12px; border: 1px solid rgba(148, 163, 184, .22); border-radius: 999px; color: #b6c5dc; }
.eyebrow, .section-title span, .kpi-card span, .project-strip span, .run-card span { color: #7dd3fc; font-size: 12px; letter-spacing: .12em; text-transform: uppercase; }
h1 { margin: 0; font-size: clamp(32px, 5vw, 64px); line-height: 1.02; }
h2, h3, p { margin-top: 0; }
.hero p { color: #b6c5dc; max-width: 860px; }
.run-card { border-radius: 24px; padding: 22px; background: linear-gradient(135deg, rgba(34,197,94,.22), rgba(59,130,246,.18)); border: 1px solid rgba(125, 211, 252, .28); }
.run-card strong { display: block; font-size: 34px; margin: 8px 0; }
.project-strip { margin: 18px 0; border-radius: 24px; padding: 16px; display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; }
.project-strip div { padding: 12px; border-radius: 18px; background: rgba(15, 23, 42, .72); }
.project-strip strong { display: block; margin-top: 6px; color: #fff; }
.kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 16px; margin-bottom: 20px; }
.kpi-card { border-radius: 22px; padding: 20px; }
.kpi-card strong { display: block; margin: 10px 0; font-size: 34px; color: #fff; }
.kpi-card p, .probe-item p, .probe-item small, .event-item small, .customer-inputs p, .safe-note { color: #94a3b8; }
.layout { display: grid; gap: 20px; margin-top: 20px; }
.two-col { grid-template-columns: minmax(0, 1.35fr) minmax(320px, .65fr); }
.section-title { margin: 0 0 14px; }
.section-title h2 { margin: 4px 0 0; font-size: 26px; }
.probe-groups { display: grid; gap: 16px; }
.probe-group, .customer-inputs, .api-panel { border-radius: 24px; padding: 18px; }
.probe-group header { display: flex; justify-content: space-between; gap: 18px; border-bottom: 1px solid rgba(148,163,184,.16); margin-bottom: 12px; }
.probe-list, .customer-inputs ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }
.probe-item { display: grid; grid-template-columns: 1fr auto; gap: 14px; padding: 14px; border-radius: 18px; background: rgba(2, 6, 23, .38); border: 1px solid rgba(148,163,184,.14); }
.probe-item span { align-self: start; padding: 7px 10px; border-radius: 999px; font-size: 12px; }
.probe-ok span { background: rgba(34,197,94,.18); color: #86efac; }
.probe-blocked span { background: rgba(248,113,113,.18); color: #fecaca; }
details { margin-top: 12px; color: #cbd5e1; }
details ul { margin-bottom: 0; }
.event-list { list-style: none; padding: 16px; margin: 0; border-radius: 24px; display: grid; gap: 12px; }
.event-list.compact { max-height: 440px; overflow: auto; }
.event-item { display: grid; grid-template-columns: 38px 1fr; gap: 12px; padding: 12px; border-radius: 18px; background: rgba(2, 6, 23, .36); border: 1px solid rgba(148,163,184,.14); }
.event-step { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 50%; background: rgba(59,130,246,.22); color: #bfdbfe; }
.severity-critical .event-step, .severity-high .event-step { background: rgba(239,68,68,.22); color: #fecaca; }
.node-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 14px; }
.node-card { text-align: left; color: inherit; border-radius: 20px; padding: 16px; cursor: pointer; }
.node-card strong { display: block; margin-bottom: 8px; }
.risk-critical, .risk-high { border-color: rgba(248,113,113,.42); background: rgba(127,29,29,.24); }
.api-panel { margin-top: 20px; display: grid; gap: 10px; }
.api-panel code { display: block; padding: 12px; border-radius: 14px; background: rgba(2,6,23,.54); color: #a7f3d0; overflow: auto; }
@media (max-width: 980px) { .hero, .two-col, .project-strip, .kpi-grid, .node-grid { grid-template-columns: 1fr; } }
"""


def render_test_execution_js() -> str:
    return """
document.querySelectorAll('[data-node]').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('[data-node]').forEach((item) => item.classList.remove('selected'));
    button.classList.add('selected');
  });
});
"""


def _render_readme(manifest: Mapping[str, Any]) -> str:
    return f"""# Phase105I AI 测试计划 + 实时测试执行页

本目录由 `ai_test_asset_center.phase105_test_execution_experience` 生成。

## 页面目标

把 QualiBug 的执行层从后端能力翻译成客户能看懂的前端页面：

- AI 测试计划：准备测什么、覆盖哪些业务链路。
- 可执行探针：当前环境与安全模式允许执行的探针。
- 阻断探针：因权限、写入限制、补料不足暂缓的探针。
- 实时测试执行：AI 当前运行状态、执行事件、风险事件。
- 证据回流：将风险与证据链关联到业务节点。
- 默认脱敏：不展示 token、cookie、password、session、client_secret 原值。

## 入口

- `test_execution.html`
- `data/test_execution_experience_data.json`
- `assets/qualibug_test_execution.css`
- `assets/qualibug_test_execution.js`

## Manifest

```json
{json.dumps(redact_value(dict(manifest)), ensure_ascii=False, indent=2, sort_keys=True)}
```
"""


def build_test_execution_experience(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    data = _extract_test_execution_view_model(scenario, api_base_url)

    _write_text(output / "test_execution.html", render_test_execution_html(data))
    _write_text(output / "assets" / "qualibug_test_execution.css", render_test_execution_css())
    _write_text(output / "assets" / "qualibug_test_execution.js", render_test_execution_js())
    _write_text(output / "data" / "test_execution_experience_data.json", _json_dump(data))

    manifest = redact_value(
        {
            "version": PHASE105I_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "entrypoint": "test_execution.html",
            "page_title": "AI 测试计划 + 实时测试执行",
            "required_files": list(REQUIRED_TEST_EXECUTION_FILES),
            "core_labels": list(CORE_TEST_EXECUTION_LABELS),
            "probe_group_count": len(_as_list(_as_mapping(data.get("test_plan")).get("probe_groups"))),
            "event_count": len(_as_list(_as_mapping(data.get("realtime_execution")).get("timeline_events"))),
            "redaction_status": "safe" if not scan_test_execution_for_secret_leaks(output) else "failed",
        }
    )
    _write_text(output / TEST_EXECUTION_MANIFEST, _json_dump(manifest))
    _write_text(output / "README_TEST_EXECUTION_EXPERIENCE.md", _render_readme(manifest))
    return manifest


def scan_test_execution_for_secret_leaks(output_dir: str | Path) -> list[str]:
    output = Path(output_dir)
    leaks: list[str] = []
    if not output.exists():
        return leaks
    for path in output.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js", ".json", ".md", ".txt"}:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in FORBIDDEN_TEST_EXECUTION_PATTERNS:
            if pattern in content:
                leaks.append(f"{path.relative_to(output)} contains {pattern}")
    return leaks


def validate_test_execution_experience(output_dir: str | Path) -> TestExecutionExperienceAcceptanceReport:
    output = Path(output_dir)
    checks: list[TestExecutionExperienceCheck] = []

    for rel in REQUIRED_TEST_EXECUTION_FILES:
        checks.append(
            TestExecutionExperienceCheck(
                key=f"file:{rel}",
                passed=(output / rel).exists(),
                detail=f"required artifact {rel} {'exists' if (output / rel).exists() else 'is missing'}",
            )
        )

    html_path = output / "test_execution.html"
    html_text = html_path.read_text(encoding="utf-8", errors="ignore") if html_path.exists() else ""
    for label in CORE_TEST_EXECUTION_LABELS:
        checks.append(
            TestExecutionExperienceCheck(
                key=f"label:{label}",
                passed=label in html_text,
                detail=f"核心前端文案 {label} {'已展示' if label in html_text else '缺失'}",
            )
        )

    data = _read_json(output / "data" / "test_execution_experience_data.json")
    plan = _as_mapping(data.get("test_plan"))
    realtime = _as_mapping(data.get("realtime_execution"))
    summary = _as_mapping(realtime.get("summary"))
    checks.extend(
        [
            TestExecutionExperienceCheck(
                key="data:probe_groups",
                passed=len(_as_list(plan.get("probe_groups"))) > 0,
                detail="AI 测试计划包含业务链路探针组" if _as_list(plan.get("probe_groups")) else "AI 测试计划缺少探针组",
            ),
            TestExecutionExperienceCheck(
                key="data:executable_and_blocked",
                passed=_int_value(summary.get("probe_executable")) > 0 and _int_value(summary.get("probe_blocked")) >= 0,
                detail=f"可执行探针 {summary.get('probe_executable')}，阻断探针 {summary.get('probe_blocked')}",
            ),
            TestExecutionExperienceCheck(
                key="data:timeline",
                passed=len(_as_list(realtime.get("timeline_events"))) > 0,
                detail="实时执行时间线已生成" if _as_list(realtime.get("timeline_events")) else "实时执行时间线缺失",
            ),
            TestExecutionExperienceCheck(
                key="data:evidence_events",
                passed=len(_as_list(realtime.get("evidence_events"))) > 0,
                detail="证据回流事件已展示" if _as_list(realtime.get("evidence_events")) else "证据回流事件缺失",
            ),
            TestExecutionExperienceCheck(
                key="data:phase104_actions",
                passed={"read_test_plan", "start_test_run", "read_live_map"}.issubset(set(_as_mapping(data.get("phase104_actions")).keys())),
                detail="Phase104 API 动作交接完整" if _as_mapping(data.get("phase104_actions")) else "Phase104 API 动作交接缺失",
            ),
        ]
    )

    leaks = scan_test_execution_for_secret_leaks(output)
    checks.append(
        TestExecutionExperienceCheck(
            key="redaction:secret_scan",
            passed=not leaks,
            detail="未发现原始凭证或 traceback 泄露" if not leaks else "; ".join(leaks[:8]),
        )
    )

    passed_count = sum(1 for check in checks if check.passed)
    score = int(round((passed_count / len(checks)) * 100)) if checks else 0
    report = TestExecutionExperienceAcceptanceReport(
        passed=all(check.passed for check in checks),
        score=score,
        version=PHASE105I_VERSION,
        scenario=_safe_text(data.get("scenario"), "unknown"),
        output_dir=str(output),
        checks=checks,
        artifacts={
            "entrypoint": "test_execution.html",
            "manifest": TEST_EXECUTION_MANIFEST,
            "data": "data/test_execution_experience_data.json",
        },
    )
    _write_text(output / TEST_EXECUTION_ACCEPTANCE_JSON, _json_dump(report.to_dict()))
    _write_text(output / TEST_EXECUTION_ACCEPTANCE_MD, render_test_execution_acceptance_markdown(report))
    return report


def render_test_execution_acceptance_markdown(report: TestExecutionExperienceAcceptanceReport) -> str:
    rows = []
    for check in report.checks:
        status = "PASS" if check.passed else "FAIL"
        rows.append(f"| {check.key} | {status} | {check.detail} |")
    return f"""# Phase105I AI 测试计划与实时执行页验收报告

- Version: `{report.version}`
- Scenario: `{report.scenario}`
- Passed: `{report.passed}`
- Score: `{report.score}`
- Output: `{report.output_dir}`

| Check | Status | Detail |
|---|---|---|
{chr(10).join(rows)}
"""


def run_test_execution_experience_export(
    *,
    output_dir: str | Path,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
    validate_only: bool = False,
) -> dict[str, Any]:
    manifest: dict[str, Any]
    if validate_only:
        manifest = _read_json(Path(output_dir) / TEST_EXECUTION_MANIFEST)
    else:
        manifest = build_test_execution_experience(output_dir, scenario=scenario, api_base_url=api_base_url)
    acceptance = validate_test_execution_experience(output_dir)
    return redact_value({"manifest": manifest, "acceptance": acceptance.to_dict()})


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the Phase105I AI test execution frontend page.")
    parser.add_argument("--output-dir", default="outputs/phase105_test_execution_experience")
    parser.add_argument("--scenario", default="manufacturing")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = run_test_execution_experience_export(
        output_dir=args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(_json_dump(result))
    return 0 if _as_mapping(result.get("acceptance")).get("passed") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

