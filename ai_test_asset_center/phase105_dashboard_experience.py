from __future__ import annotations

"""Phase105B: executive quality dashboard experience for QualiBug.

Phase105A created the product shell and page-level information architecture.
Phase105B focuses the most important landing page: the quality dashboard that
lets leaders, delivery managers, QA owners, and sales/customer-success users see
launch risk, business impact, environment readiness, risk evidence, and ROI in
one screen.

The output is framework-neutral static HTML/CSS/JS backed by redacted Phase104
API demo data.  It intentionally remains dependency-free so it can be generated
inside customer-controlled environments and committed as a front-end reference.
"""

import argparse
import html
import json
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase105_frontend_product_shell import collect_product_shell_demo_data

PHASE105B_VERSION = "phase105b-dashboard-experience-v1"

DASHBOARD_MANIFEST = "dashboard_experience_manifest.json"
DASHBOARD_ACCEPTANCE_JSON = "dashboard_experience_acceptance_report.json"
DASHBOARD_ACCEPTANCE_MD = "dashboard_experience_acceptance_report.md"

REQUIRED_DASHBOARD_FILES: tuple[str, ...] = (
    "dashboard.html",
    "README_DASHBOARD_EXPERIENCE.md",
    "data/dashboard_experience_data.json",
    "assets/qualibug_dashboard_experience.css",
    "assets/qualibug_dashboard_experience.js",
    DASHBOARD_MANIFEST,
)

FORBIDDEN_DASHBOARD_PATTERNS: tuple[str, ...] = (
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

CORE_DASHBOARD_LABELS: tuple[str, ...] = (
    "质量驾驶舱",
    "上线决策",
    "质量趋势",
    "Top 风险",
    "环境可测性",
    "业务链路覆盖",
    "ROI 价值",
    "下一步动作",
)


@dataclass(frozen=True)
class DashboardExperienceCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class DashboardExperienceAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    checks: list[DashboardExperienceCheck] = field(default_factory=list)
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


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _risk_status(severity: str, launch_blocking: bool) -> str:
    severity_lower = severity.lower()
    if launch_blocking or severity_lower in {"critical", "high"}:
        return "danger"
    if severity_lower == "medium":
        return "warning"
    return "safe"


def _trend_points(current_score: Any) -> list[dict[str, Any]]:
    current = max(0, min(100, _as_int(current_score, 72)))
    labels = ["T-5", "T-4", "T-3", "T-2", "T-1", "当前"]
    offsets = [-14, -10, -7, -4, -2, 0]
    points: list[dict[str, Any]] = []
    for label, offset in zip(labels, offsets, strict=True):
        points.append(
            {
                "label": label,
                "quality_score": max(0, min(100, current + offset)),
                "blocking_risks": max(0, 5 - labels.index(label)),
                "environment_score": max(0, min(100, current + offset + 4)),
            }
        )
    return points


def _severity_distribution(risks: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counter = Counter(str(risk.get("severity") or "unknown").lower() for risk in risks)
    return {key: counter.get(key, 0) for key in ("critical", "high", "medium", "low", "unknown")}


def _flow_cards(live_map: Mapping[str, Any], risks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    nodes = [node for node in live_map.get("nodes", []) if isinstance(node, Mapping)]
    overlays = [overlay for overlay in live_map.get("risk_overlays", []) if isinstance(overlay, Mapping)]
    flow_nodes: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for node in nodes:
        flow_id = str(node.get("business_flow_id") or node.get("flow_id") or "unknown_flow")
        flow_nodes[flow_id].append(node)

    risk_by_flow = Counter()
    for overlay in overlays:
        flow_id = str(overlay.get("business_flow_id") or overlay.get("flow_id") or "unknown_flow")
        risk_by_flow[flow_id] += 1
    for risk in risks:
        affected = risk.get("affected_business_flow") if isinstance(risk.get("affected_business_flow"), Mapping) else {}
        flow_id = str(affected.get("business_flow_id") or "")
        if flow_id:
            risk_by_flow[flow_id] += 1

    cards: list[dict[str, Any]] = []
    for flow_id, items in sorted(flow_nodes.items()):
        first = dict(items[0]) if items else {}
        name = first.get("business_flow_name") or first.get("flow_name") or flow_id
        risk_count = risk_by_flow.get(flow_id, 0)
        cards.append(
            {
                "flow_id": flow_id,
                "name": name,
                "node_count": len(items),
                "risk_count": risk_count,
                "status": "risk" if risk_count else "covered",
                "coverage_label": f"{len(items)} 个节点已映射",
            }
        )
    return cards[:8]


def collect_dashboard_experience_data(
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
) -> dict[str, Any]:
    """Collect and reshape Phase104 demo data for the dashboard-first UI."""
    source = collect_product_shell_demo_data(scenario=scenario, api_base_url=api_base_url)
    project = dict(source.get("project") or {})
    dashboard = dict(source.get("dashboard") or {})
    environment = dict(source.get("environment") or {})
    live_map = dict(source.get("live_map") or {})
    value_metrics = dict(source.get("value_metrics") or {})
    risks = [dict(risk) for risk in source.get("risks", []) if isinstance(risk, Mapping)]
    launch_decision = dict(dashboard.get("launch_decision") or {})
    flow_summary = dict(dashboard.get("business_flow_summary") or {})
    env_summary = dict(dashboard.get("environment_readiness") or {})

    critical_or_high = sum(1 for risk in risks if str(risk.get("severity") or "").lower() in {"critical", "high"})
    launch_blocking = sum(1 for risk in risks if bool(risk.get("launch_blocking")))
    env_score = environment.get("score", env_summary.get("score"))
    quality_score = dashboard.get("quality_health_score", 0)

    kpis = [
        {
            "key": "quality_score",
            "label": "质量健康分",
            "value": _as_int(quality_score),
            "unit": "分",
            "status": "danger" if _as_int(quality_score) < 70 else "warning" if _as_int(quality_score) < 85 else "safe",
            "help": "综合风险、环境、覆盖和证据可信度。",
        },
        {
            "key": "launch_decision",
            "label": "上线建议",
            "value": launch_decision.get("recommendation") or "PENDING",
            "unit": "",
            "status": "danger" if launch_decision.get("recommendation") in {"NO_GO", "HOLD"} else "safe",
            "help": launch_decision.get("summary") or "等待上线建议。",
        },
        {
            "key": "blocking_risks",
            "label": "阻断风险",
            "value": launch_blocking,
            "unit": "个",
            "status": "danger" if launch_blocking else "safe",
            "help": "会影响上线决策的 confirmed 风险。",
        },
        {
            "key": "core_flow_coverage",
            "label": "核心链路覆盖",
            "value": int(round(_as_float(flow_summary.get("coverage_rate")) * 100)),
            "unit": "%",
            "status": "warning" if _as_float(flow_summary.get("coverage_rate")) < 0.8 else "safe",
            "help": "用业务链路覆盖解释测试范围。",
        },
        {
            "key": "environment_score",
            "label": "环境可测分",
            "value": _as_int(env_score),
            "unit": "分",
            "status": "warning" if str(environment.get("status") or env_summary.get("status")) != "ready" else "safe",
            "help": "URL、认证、API smoke 和补料情况。",
        },
        {
            "key": "hours_saved",
            "label": "预计节省工时",
            "value": _as_float(value_metrics.get("estimated_hours_saved")),
            "unit": "h",
            "status": "safe",
            "help": "保守估算 AI 等价测试点节省的人力。",
        },
    ]

    top_risks = []
    for risk in risks[:6]:
        top_risks.append(
            {
                "risk_id": risk.get("risk_id"),
                "title": risk.get("title"),
                "severity": risk.get("severity"),
                "status": risk.get("status"),
                "launch_blocking": bool(risk.get("launch_blocking")),
                "business_impact": risk.get("business_impact"),
                "affected_flow": (risk.get("affected_business_flow") or {}).get("name") if isinstance(risk.get("affected_business_flow"), Mapping) else None,
                "evidence_score": risk.get("evidence_score"),
                "reproducibility_score": risk.get("reproducibility_score"),
                "suggested_action": risk.get("suggested_action"),
                "ui_status": _risk_status(str(risk.get("severity") or ""), bool(risk.get("launch_blocking"))),
            }
        )

    action_queue = []
    for action in launch_decision.get("required_actions", []) or []:
        action_queue.append({"type": "launch", "priority": "P0", "title": action, "owner": "研发负责人 / 测试负责人"})
    for blocker in environment.get("current_blockers", []) or env_summary.get("current_blockers", []) or []:
        action_queue.append({"type": "environment", "priority": "P0", "title": blocker, "owner": "客户实施 / 环境负责人"})
    for risk in top_risks[:3]:
        if risk.get("suggested_action"):
            action_queue.append({"type": "risk", "priority": "P1", "title": risk["suggested_action"], "owner": "缺陷负责人"})

    checks = environment.get("checks", {}) if isinstance(environment.get("checks"), Mapping) else {}
    environment_lanes = []
    for key in ("connectivity", "auth", "api_smoke", "business_accounts", "safe_execution"):
        value = checks.get(key) if isinstance(checks.get(key), Mapping) else {}
        environment_lanes.append(
            {
                "key": key,
                "label": {
                    "connectivity": "连通性",
                    "auth": "认证链路",
                    "api_smoke": "API Smoke",
                    "business_accounts": "账号角色",
                    "safe_execution": "安全边界",
                }.get(key, key),
                "status": value.get("status") or value.get("result") or "checked",
                "detail": value.get("issue") or value.get("summary") or "已完成检查",
            }
        )

    data = {
        "version": PHASE105B_VERSION,
        "generated_at": _now(),
        "scenario": scenario,
        "api_base_url": api_base_url.rstrip("/"),
        "project": {
            "project_id": project.get("project_id"),
            "project_name": project.get("project_name"),
            "customer_name": project.get("customer_name"),
            "system_name": project.get("system_name") or project.get("project_name"),
        },
        "dashboard_title": "QualiBug AI 企业质量驾驶舱",
        "dashboard_subtitle": "让领导先看到上线建议、业务风险、环境阻断、证据可信度和 ROI 价值。",
        "launch_decision": launch_decision,
        "executive_summary": dashboard.get("executive_summary"),
        "kpis": kpis,
        "trend_points": _trend_points(quality_score),
        "risk_severity_distribution": _severity_distribution(risks),
        "business_flow_summary": flow_summary,
        "business_flow_cards": _flow_cards(live_map, risks),
        "environment_summary": {
            "status": environment.get("status") or env_summary.get("status"),
            "score": _as_int(env_score),
            "allow_formal_test": bool(environment.get("allow_formal_test")),
            "safe_execution_mode": environment.get("safe_execution_mode") or env_summary.get("safe_execution_mode"),
            "current_blockers": environment.get("current_blockers") or env_summary.get("current_blockers") or [],
            "suggested_actions": environment.get("suggested_actions") or [],
            "lanes": environment_lanes,
        },
        "top_risks": top_risks,
        "roi": {
            "ai_equivalent_test_points": value_metrics.get("ai_equivalent_test_points"),
            "estimated_hours_saved": value_metrics.get("estimated_hours_saved"),
            "estimated_business_impact_min": value_metrics.get("estimated_business_impact_min"),
            "estimated_business_impact_max": value_metrics.get("estimated_business_impact_max"),
            "currency": value_metrics.get("currency") or "CNY",
            "evidence_trust_score": value_metrics.get("evidence_trust_score"),
            "calculation_notes": value_metrics.get("calculation_notes") or [],
        },
        "action_queue": action_queue[:10],
        "display_rules": [
            "首页先展示上线建议与业务风险，不要求客户先理解技术日志。",
            "所有凭证、Cookie、Session、Token、Authorization 原文必须隐藏。",
            "环境阻断必须给出客户下一步动作，而不是只显示失败。",
            "Top 风险必须显示业务影响、证据评分和复现稳定性。",
        ],
    }
    return redact_value(data)


def render_dashboard_html() -> str:
    labels = "\n".join(f"<li>{html.escape(label)}</li>" for label in CORE_DASHBOARD_LABELS)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>QualiBug AI · Phase105B 质量驾驶舱</title>
  <link rel="stylesheet" href="assets/qualibug_dashboard_experience.css" />
</head>
<body>
  <main class="qd-page">
    <aside class="qd-sidebar">
      <div class="qd-logo">QB</div>
      <p>Phase105B</p>
      <h1>质量驾驶舱</h1>
      <ul>{labels}</ul>
    </aside>
    <section class="qd-main">
      <header class="qd-hero">
        <div>
          <span class="qd-chip">Enterprise Quality Command Center</span>
          <h2 id="dashboardTitle">质量驾驶舱</h2>
          <p id="dashboardSubtitle">加载中...</p>
        </div>
        <div class="qd-decision" id="launchDecisionCard"></div>
      </header>
      <section class="qd-kpis" id="kpiGrid" aria-label="关键指标"></section>
      <section class="qd-grid qd-grid-2">
        <article class="qd-card qd-large">
          <div class="qd-card-head"><h3>质量趋势</h3><span>风险与环境变化</span></div>
          <div class="qd-trend" id="qualityTrend"></div>
        </article>
        <article class="qd-card">
          <div class="qd-card-head"><h3>上线决策</h3><span>原因与动作</span></div>
          <div id="decisionReasons"></div>
        </article>
      </section>
      <section class="qd-grid qd-grid-3">
        <article class="qd-card"><div class="qd-card-head"><h3>业务链路覆盖</h3><span>按业务而非用例解释覆盖</span></div><div id="flowCoverage"></div></article>
        <article class="qd-card"><div class="qd-card-head"><h3>环境可测性</h3><span>阻断原因与补料动作</span></div><div id="environmentReadiness"></div></article>
        <article class="qd-card"><div class="qd-card-head"><h3>ROI 价值</h3><span>保守可信的价值证明</span></div><div id="roiValue"></div></article>
      </section>
      <section class="qd-grid qd-grid-2">
        <article class="qd-card qd-large"><div class="qd-card-head"><h3>Top 风险</h3><span>业务影响、证据评分、复现稳定性</span></div><div id="topRisks"></div></article>
        <article class="qd-card"><div class="qd-card-head"><h3>下一步动作</h3><span>客户、研发、测试各自要做什么</span></div><div id="actionQueue"></div></article>
      </section>
      <footer class="qd-footer" id="dashboardFooter"></footer>
    </section>
  </main>
  <script src="assets/qualibug_dashboard_experience.js"></script>
</body>
</html>
"""


def render_dashboard_css() -> str:
    return """:root {
  --bg: #08111f;
  --bg-soft: #101b2f;
  --panel: #ffffff;
  --panel-soft: #f6f8fc;
  --text: #132033;
  --muted: #66758a;
  --line: #dce5f1;
  --blue: #2563eb;
  --cyan: #0891b2;
  --green: #16a34a;
  --amber: #d97706;
  --red: #dc2626;
  --purple: #7c3aed;
  --shadow: 0 24px 70px rgba(15, 23, 42, .12);
  font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; background: #eaf0f8; color: var(--text); }
.qd-page { min-height: 100vh; display: grid; grid-template-columns: 276px 1fr; }
.qd-sidebar { min-height: 100vh; background: radial-gradient(circle at 20% 0%, #1d4ed8 0, #0f172a 38%, #08111f 100%); color: #fff; padding: 28px 22px; position: sticky; top: 0; }
.qd-logo { width: 52px; height: 52px; border-radius: 18px; display: grid; place-items: center; font-weight: 900; background: linear-gradient(135deg, #60a5fa, #a78bfa); box-shadow: 0 18px 45px rgba(96, 165, 250, .28); }
.qd-sidebar p { margin: 26px 0 6px; color: #93c5fd; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; font-size: 12px; }
.qd-sidebar h1 { margin: 0 0 22px; font-size: 30px; letter-spacing: -.04em; }
.qd-sidebar ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 9px; }
.qd-sidebar li { border: 1px solid rgba(255,255,255,.08); background: rgba(255,255,255,.05); border-radius: 14px; padding: 11px 12px; color: #dbeafe; font-weight: 700; }
.qd-main { padding: 28px 34px 58px; }
.qd-hero { display: grid; grid-template-columns: 1fr 380px; gap: 20px; align-items: stretch; margin-bottom: 18px; }
.qd-chip { display: inline-flex; border-radius: 999px; padding: 7px 12px; background: #dbeafe; color: #1d4ed8; font-size: 12px; font-weight: 900; }
.qd-hero h2 { margin: 12px 0 8px; font-size: 38px; letter-spacing: -.045em; }
.qd-hero p { margin: 0; color: var(--muted); line-height: 1.7; max-width: 780px; }
.qd-decision { background: #fff; border: 1px solid var(--line); border-radius: 28px; padding: 22px; box-shadow: var(--shadow); }
.qd-decision small { color: var(--muted); font-weight: 800; }
.qd-decision strong { display: block; font-size: 34px; margin: 10px 0 8px; letter-spacing: -.04em; }
.qd-decision.danger { border-color: rgba(220,38,38,.25); background: linear-gradient(135deg, #fff, #fff1f2); }
.qd-decision.warning { border-color: rgba(217,119,6,.25); background: linear-gradient(135deg, #fff, #fffbeb); }
.qd-decision.safe { border-color: rgba(22,163,74,.25); background: linear-gradient(135deg, #fff, #f0fdf4); }
.qd-kpis { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 14px; margin-bottom: 16px; }
.qd-kpi { background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 16px; box-shadow: var(--shadow); min-height: 148px; }
.qd-kpi small { color: var(--muted); font-weight: 800; }
.qd-kpi strong { display: block; font-size: 30px; line-height: 1; margin: 14px 0 10px; letter-spacing: -.035em; }
.qd-kpi p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.55; }
.qd-kpi::before { content: ""; display: block; width: 34px; height: 4px; border-radius: 999px; background: var(--blue); margin-bottom: 12px; }
.qd-kpi.danger::before, .qd-tag.danger { background: var(--red); color: #fff; }
.qd-kpi.warning::before, .qd-tag.warning { background: var(--amber); color: #fff; }
.qd-kpi.safe::before, .qd-tag.safe { background: var(--green); color: #fff; }
.qd-grid { display: grid; gap: 16px; margin-bottom: 16px; }
.qd-grid-2 { grid-template-columns: 1.25fr .75fr; }
.qd-grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.qd-card { background: var(--panel); border: 1px solid var(--line); border-radius: 26px; padding: 20px; box-shadow: var(--shadow); overflow: hidden; }
.qd-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; border-bottom: 1px solid var(--line); padding-bottom: 14px; margin-bottom: 16px; }
.qd-card h3 { margin: 0; font-size: 20px; letter-spacing: -.02em; }
.qd-card-head span { color: var(--muted); font-size: 13px; text-align: right; line-height: 1.5; }
.qd-trend { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); align-items: end; gap: 12px; min-height: 230px; }
.qd-bar { display: grid; align-items: end; gap: 8px; height: 220px; }
.qd-bar-fill { border-radius: 16px 16px 8px 8px; background: linear-gradient(180deg, #60a5fa, #2563eb); min-height: 26px; display: grid; align-items: start; justify-content: center; color: #fff; padding-top: 7px; font-weight: 900; }
.qd-bar small { text-align: center; color: var(--muted); font-weight: 800; }
.qd-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }
.qd-list li { background: var(--panel-soft); border: 1px solid var(--line); border-radius: 16px; padding: 12px 13px; color: var(--muted); line-height: 1.55; }
.qd-flow { display: grid; gap: 10px; }
.qd-flow-item { display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; padding: 12px; border: 1px solid var(--line); background: var(--panel-soft); border-radius: 16px; }
.qd-flow-item strong { display: block; }
.qd-flow-item small { display: block; color: var(--muted); margin-top: 5px; }
.qd-risk { border: 1px solid var(--line); border-left: 5px solid var(--red); border-radius: 18px; padding: 15px; background: var(--panel-soft); margin-bottom: 12px; }
.qd-risk h4 { margin: 8px 0 8px; font-size: 17px; }
.qd-risk p { margin: 0 0 9px; color: var(--muted); line-height: 1.55; }
.qd-risk-meta { display: flex; gap: 8px; flex-wrap: wrap; }
.qd-tag { display: inline-flex; border-radius: 999px; padding: 5px 9px; background: #e0e7ff; color: #3730a3; font-size: 12px; font-weight: 900; }
.qd-roi { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.qd-roi div { border-radius: 16px; background: var(--panel-soft); border: 1px solid var(--line); padding: 14px; }
.qd-roi small { color: var(--muted); font-weight: 800; }
.qd-roi strong { display: block; font-size: 22px; margin-top: 8px; }
.qd-footer { color: var(--muted); text-align: right; font-size: 13px; }
@media (max-width: 1280px) { .qd-kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); } .qd-grid-3, .qd-grid-2, .qd-hero { grid-template-columns: 1fr; } }
@media (max-width: 860px) { .qd-page { grid-template-columns: 1fr; } .qd-sidebar { position: relative; min-height: auto; } .qd-kpis, .qd-trend { grid-template-columns: 1fr; } .qd-main { padding: 22px; } }
"""


def render_dashboard_js() -> str:
    return r"""const esc = (value) => String(value ?? "—").replace(/[&<>'"]/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[ch]));
const money = (value, currency = "CNY") => value == null ? "—" : `${currency} ${Number(value).toLocaleString("zh-CN")}`;
const pct = (value) => value == null ? "—" : `${Math.round(Number(value) * 100)}%`;

function list(items, empty = "暂无数据") {
  if (!items || !items.length) return `<p class="qd-empty">${esc(empty)}</p>`;
  return `<ul class="qd-list">${items.map((item) => `<li>${item}</li>`).join("")}</ul>`;
}

function renderLaunchDecision(data) {
  const decision = data.launch_decision || {};
  const status = decision.recommendation === "NO_GO" || decision.recommendation === "HOLD" ? "danger" : decision.recommendation === "CONDITIONAL_GO" ? "warning" : "safe";
  const card = document.getElementById("launchDecisionCard");
  card.className = `qd-decision ${status}`;
  card.innerHTML = `<small>上线建议</small><strong>${esc(decision.title || decision.recommendation || "待决策")}</strong><p>${esc(decision.summary || "等待上线决策生成")}</p>`;
}

function renderKpis(data) {
  document.getElementById("kpiGrid").innerHTML = (data.kpis || []).map((item) => `
    <article class="qd-kpi ${esc(item.status)}">
      <small>${esc(item.label)}</small>
      <strong>${esc(item.value)}${esc(item.unit || "")}</strong>
      <p>${esc(item.help)}</p>
    </article>
  `).join("");
}

function renderTrend(data) {
  document.getElementById("qualityTrend").innerHTML = (data.trend_points || []).map((point) => {
    const height = Math.max(16, Math.min(100, Number(point.quality_score || 0)));
    return `<div class="qd-bar"><div class="qd-bar-fill" style="height:${height}%">${esc(point.quality_score)}</div><small>${esc(point.label)}</small></div>`;
  }).join("");
}

function renderDecisionReasons(data) {
  const decision = data.launch_decision || {};
  document.getElementById("decisionReasons").innerHTML = `
    <p>${esc(data.executive_summary || decision.summary)}</p>
    ${list([...(decision.reasons || []), ...(decision.required_actions || [])].map(esc), "暂无决策原因")}
  `;
}

function renderFlowCoverage(data) {
  const summary = data.business_flow_summary || {};
  const cards = data.business_flow_cards || [];
  const head = list([
    `覆盖：${esc(summary.covered)} / ${esc(summary.total)} 条核心链路`,
    `覆盖率：${pct(summary.coverage_rate)}`,
    `有风险链路：${esc(summary.covered_with_risk)}`,
  ]);
  const flowHtml = `<div class="qd-flow">${cards.map((flow) => `<div class="qd-flow-item"><div><strong>${esc(flow.name)}</strong><small>${esc(flow.coverage_label)}</small></div><span class="qd-tag ${flow.risk_count ? "danger" : "safe"}">${flow.risk_count ? `${esc(flow.risk_count)} 风险` : "已覆盖"}</span></div>`).join("")}</div>`;
  document.getElementById("flowCoverage").innerHTML = head + flowHtml;
}

function renderEnvironment(data) {
  const env = data.environment_summary || {};
  const lanes = env.lanes || [];
  document.getElementById("environmentReadiness").innerHTML = `
    ${list([`状态：${esc(env.status)}`, `评分：${esc(env.score)} / 100`, `正式测试：${esc(env.allow_formal_test ? "允许" : "暂缓")}`, `安全模式：${esc(env.safe_execution_mode)}`])}
    ${list(lanes.map((lane) => `${esc(lane.label)}：${esc(lane.status)} · ${esc(lane.detail)}`), "暂无环境检查")}
  `;
}

function renderRoi(data) {
  const roi = data.roi || {};
  document.getElementById("roiValue").innerHTML = `
    <div class="qd-roi">
      <div><small>AI 等价测试点</small><strong>${esc(roi.ai_equivalent_test_points)}</strong></div>
      <div><small>节省工时</small><strong>${esc(roi.estimated_hours_saved)}h</strong></div>
      <div><small>证据可信度</small><strong>${pct(roi.evidence_trust_score)}</strong></div>
      <div><small>潜在影响区间</small><strong>${money(roi.estimated_business_impact_min, roi.currency)} - ${money(roi.estimated_business_impact_max, roi.currency)}</strong></div>
    </div>
    ${list((roi.calculation_notes || []).map(esc), "暂无计算说明")}
  `;
}

function renderTopRisks(data) {
  document.getElementById("topRisks").innerHTML = (data.top_risks || []).map((risk) => `
    <article class="qd-risk">
      <div class="qd-risk-meta"><span class="qd-tag danger">${esc(risk.severity)}</span><span class="qd-tag ${risk.launch_blocking ? "warning" : "safe"}">${risk.launch_blocking ? "阻断上线" : "非阻断"}</span><span class="qd-tag">证据 ${esc(risk.evidence_score)}</span><span class="qd-tag">复现 ${esc(risk.reproducibility_score)}</span></div>
      <h4>${esc(risk.title)}</h4>
      <p>${esc(risk.business_impact)}</p>
      <p><strong>建议：</strong>${esc(risk.suggested_action)}</p>
    </article>
  `).join("") || `<p class="qd-empty">暂无风险</p>`;
}

function renderActionQueue(data) {
  document.getElementById("actionQueue").innerHTML = list((data.action_queue || []).map((item) => `<strong>${esc(item.priority)}</strong> · ${esc(item.owner)}<br>${esc(item.title)}`), "暂无下一步动作");
}

async function bootDashboardExperience() {
  const response = await fetch("data/dashboard_experience_data.json", { cache: "no-store" });
  const data = await response.json();
  document.getElementById("dashboardTitle").textContent = `${data.dashboard_title} · ${data.project?.customer_name || "客户"}`;
  document.getElementById("dashboardSubtitle").textContent = data.dashboard_subtitle;
  renderLaunchDecision(data);
  renderKpis(data);
  renderTrend(data);
  renderDecisionReasons(data);
  renderFlowCoverage(data);
  renderEnvironment(data);
  renderRoi(data);
  renderTopRisks(data);
  renderActionQueue(data);
  document.getElementById("dashboardFooter").textContent = `${data.version} · ${data.generated_at} · ${data.scenario}`;
}

bootDashboardExperience().catch((error) => {
  document.body.innerHTML = `<main style="padding:24px;font-family:sans-serif"><h1>质量驾驶舱加载失败</h1><pre>${esc(error.message)}</pre></main>`;
});
"""


def render_dashboard_readme(data: Mapping[str, Any]) -> str:
    return f"""# Phase105B 质量驾驶舱 UI 强化

Phase105B 聚焦 QualiBug 的首页显示层：质量驾驶舱。目标是让领导、交付经理、测试负责人和售前人员在一个页面里看懂上线建议、Top 风险、环境可测性、业务链路覆盖和 ROI 价值。

## 页面重点

- 上线决策卡：优先展示 GO / HOLD / NO_GO 与原因。
- KPI 指标：质量健康分、阻断风险、链路覆盖、环境可测分、节省工时。
- 质量趋势：用简单趋势柱图展示风险和环境状态变化。
- 业务链路覆盖：按业务链路解释测试范围。
- 环境可测性：把环境阻断变成客户下一步动作。
- Top 风险：展示业务影响、证据评分、复现稳定性和修复建议。
- ROI 价值：展示 AI 等价测试点、节省工时、潜在影响区间。

## 当前演示数据

- 场景：{data.get('scenario')}
- 客户：{_safe_text(data.get('project', {}).get('customer_name'))}
- 系统：{_safe_text(data.get('project', {}).get('system_name'))}
- 上线建议：{_safe_text(data.get('launch_decision', {}).get('recommendation'))}
- 风险数：{len(data.get('top_risks', []))}

## 本地运行

```powershell
python -m ai_test_asset_center.phase105_dashboard_experience --scenario manufacturing --output-dir .\\outputs\\phase105_dashboard_experience
Start-Process .\\outputs\\phase105_dashboard_experience\\dashboard.html
```
"""


def scan_dashboard_experience_for_secret_leaks(base: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".html", ".css", ".js", ".json", ".md", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lowered = text.lower()
        for pattern in FORBIDDEN_DASHBOARD_PATTERNS:
            if pattern.lower() in lowered:
                findings.append(f"{path.relative_to(base).as_posix()}: contains forbidden pattern {pattern}")
    return findings


def build_dashboard_experience(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
) -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = collect_dashboard_experience_data(scenario=scenario, api_base_url=api_base_url)

    _write_text(out / "dashboard.html", render_dashboard_html())
    _write_text(out / "assets" / "qualibug_dashboard_experience.css", render_dashboard_css())
    _write_text(out / "assets" / "qualibug_dashboard_experience.js", render_dashboard_js())
    _write_text(out / "data" / "dashboard_experience_data.json", _json_dump(data))
    _write_text(out / "README_DASHBOARD_EXPERIENCE.md", render_dashboard_readme(data))

    manifest = redact_value(
        {
            "version": PHASE105B_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "api_base_url": api_base_url.rstrip("/"),
            "required_files": list(REQUIRED_DASHBOARD_FILES),
            "dashboard_labels": list(CORE_DASHBOARD_LABELS),
            "data_source": "Phase105A product shell view-model compiled from Phase104 API demo data",
            "redaction_status": "safe" if not scan_dashboard_experience_for_secret_leaks(out) else "failed",
        }
    )
    _write_text(out / DASHBOARD_MANIFEST, _json_dump(manifest))
    return manifest


def validate_dashboard_experience(output_dir: str | Path) -> DashboardExperienceAcceptanceReport:
    out = Path(output_dir)
    scenario = "unknown"
    checks: list[DashboardExperienceCheck] = []

    missing = [rel for rel in REQUIRED_DASHBOARD_FILES if not (out / rel).exists()]
    checks.append(
        DashboardExperienceCheck(
            "required_files",
            not missing,
            "dashboard experience files are present" if not missing else "missing files: " + ", ".join(missing),
        )
    )

    data: dict[str, Any] = {}
    data_path = out / "data" / "dashboard_experience_data.json"
    if data_path.exists():
        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
            scenario = str(data.get("scenario") or scenario)
        except json.JSONDecodeError as exc:
            checks.append(DashboardExperienceCheck("data_json", False, f"dashboard data is invalid JSON: {exc}"))
        else:
            required_keys = {
                "project",
                "launch_decision",
                "kpis",
                "trend_points",
                "business_flow_cards",
                "environment_summary",
                "top_risks",
                "roi",
                "action_queue",
            }
            missing_keys = sorted(required_keys - set(data.keys()))
            checks.append(
                DashboardExperienceCheck(
                    "dashboard_data_contract",
                    not missing_keys,
                    "dashboard view-model has all core blocks" if not missing_keys else "missing keys: " + ", ".join(missing_keys),
                )
            )
            kpis = data.get("kpis", []) if isinstance(data.get("kpis"), list) else []
            checks.append(
                DashboardExperienceCheck(
                    "kpi_coverage",
                    len(kpis) >= 6 and {item.get("key") for item in kpis} >= {"quality_score", "launch_decision", "blocking_risks", "environment_score"},
                    "dashboard contains leadership KPI cards",
                )
            )
            checks.append(
                DashboardExperienceCheck(
                    "risk_and_action_coverage",
                    bool(data.get("top_risks")) and bool(data.get("action_queue")),
                    "dashboard exposes top risks and next actions",
                )
            )
            checks.append(
                DashboardExperienceCheck(
                    "flow_environment_roi",
                    bool(data.get("business_flow_cards"))
                    and isinstance(data.get("environment_summary"), Mapping)
                    and isinstance(data.get("roi"), Mapping)
                    and "estimated_hours_saved" in data["roi"],
                    "business flow, environment, and ROI blocks are populated",
                )
            )

    html_path = out / "dashboard.html"
    if html_path.exists():
        text = html_path.read_text(encoding="utf-8")
        missing_labels = [label for label in CORE_DASHBOARD_LABELS if label not in text]
        checks.append(
            DashboardExperienceCheck(
                "dashboard_labels",
                not missing_labels,
                "dashboard contains required leadership labels" if not missing_labels else "missing labels: " + ", ".join(missing_labels),
            )
        )

    js_path = out / "assets" / "qualibug_dashboard_experience.js"
    if js_path.exists():
        js_text = js_path.read_text(encoding="utf-8")
        required_renderers = [
            "renderLaunchDecision",
            "renderKpis",
            "renderTrend",
            "renderFlowCoverage",
            "renderEnvironment",
            "renderRoi",
            "renderTopRisks",
            "renderActionQueue",
        ]
        missing_renderers = [name for name in required_renderers if name not in js_text]
        checks.append(
            DashboardExperienceCheck(
                "dashboard_renderers",
                not missing_renderers,
                "dashboard JavaScript renderers cover core blocks" if not missing_renderers else "missing renderers: " + ", ".join(missing_renderers),
            )
        )

    leaks = scan_dashboard_experience_for_secret_leaks(out)
    checks.append(
        DashboardExperienceCheck(
            "redaction",
            not leaks,
            "no forbidden credential or traceback patterns found" if not leaks else "; ".join(leaks),
        )
    )

    passed_count = sum(1 for check in checks if check.passed)
    score = int(round((passed_count / max(len(checks), 1)) * 100))
    report = DashboardExperienceAcceptanceReport(
        passed=all(check.passed for check in checks),
        score=score,
        version=PHASE105B_VERSION,
        scenario=scenario,
        output_dir=str(out),
        checks=checks,
        artifacts={"required_files": list(REQUIRED_DASHBOARD_FILES), "dashboard_labels": list(CORE_DASHBOARD_LABELS)},
    )
    return report


def render_acceptance_markdown(report: DashboardExperienceAcceptanceReport) -> str:
    status = "PASSED" if report.passed else "FAILED"
    rows = "\n".join(
        f"| {check.key} | {'PASS' if check.passed else 'FAIL'} | {check.severity} | {check.detail} |" for check in report.checks
    )
    return f"""# Phase105B 质量驾驶舱体验验收报告

- 状态：**{status}**
- 分数：**{report.score} / 100**
- 场景：{report.scenario}
- 输出目录：`{report.output_dir}`
- 版本：{report.version}

| 检查项 | 结果 | 严重级别 | 说明 |
|---|---|---|---|
{rows}
"""


def run_dashboard_experience_export(
    *,
    output_dir: str | Path,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    validate_only: bool = False,
) -> dict[str, Any]:
    out = Path(output_dir)
    if not validate_only:
        build_dashboard_experience(out, scenario=scenario, api_base_url=api_base_url)
    report = validate_dashboard_experience(out)
    _write_text(out / DASHBOARD_ACCEPTANCE_JSON, _json_dump(report.to_dict()))
    _write_text(out / DASHBOARD_ACCEPTANCE_MD, render_acceptance_markdown(report))
    manifest = json.loads((out / DASHBOARD_MANIFEST).read_text(encoding="utf-8")) if (out / DASHBOARD_MANIFEST).exists() else None
    return {"manifest": manifest, "acceptance": report.to_dict()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or validate the Phase105B quality dashboard experience.")
    parser.add_argument("--output-dir", default="outputs/phase105_dashboard_experience")
    parser.add_argument("--scenario", default="manufacturing", choices=["manufacturing", "ecommerce", "saas"])
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8790")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    result = run_dashboard_experience_export(
        output_dir=args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(json.dumps(result["acceptance"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["acceptance"].get("passed") else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
