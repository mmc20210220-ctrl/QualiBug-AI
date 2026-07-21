from __future__ import annotations

"""Phase105F: risk list and evidence-detail frontend experience.

Phase105A-E created the frontend product shell, executive dashboard,
customer-intake page, environment diagnosis page, and business-flow map.
Phase105F focuses the conversion layer that makes AI findings believable:
turn technical bugs into business-risk cards, then provide an evidence detail
screen that explains reproduction, request/response summaries, snapshot diffs,
fix suggestions, closure criteria, and safe redaction status.

The module is intentionally dependency-free. It exports static HTML/CSS/JS and
a redacted JSON view model that can be reused by a future React/Vue frontend.
"""

import argparse
import html
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase104_command_center_http_api import Phase104CommandCenterHttpApp
from ai_test_asset_center.phase105_frontend_product_shell import collect_product_shell_demo_data
from ai_test_asset_center.version import default_api_base_url

PHASE105F_VERSION = "phase105f-risk-evidence-experience-v1"

RISK_EVIDENCE_MANIFEST = "risk_evidence_experience_manifest.json"
RISK_EVIDENCE_ACCEPTANCE_JSON = "risk_evidence_experience_acceptance_report.json"
RISK_EVIDENCE_ACCEPTANCE_MD = "risk_evidence_experience_acceptance_report.md"

REQUIRED_RISK_EVIDENCE_FILES: tuple[str, ...] = (
    "risk_evidence.html",
    "README_RISK_EVIDENCE_EXPERIENCE.md",
    "data/risk_evidence_experience_data.json",
    "assets/qualibug_risk_evidence.css",
    "assets/qualibug_risk_evidence.js",
    RISK_EVIDENCE_MANIFEST,
)

CORE_RISK_EVIDENCE_LABELS: tuple[str, ...] = (
    "风险与 Bug 列表",
    "证据链详情",
    "业务影响",
    "阻断上线",
    "复现步骤",
    "请求响应摘要",
    "快照对比",
    "修复建议",
    "关闭条件",
    "证据可信度",
    "默认脱敏",
)

FORBIDDEN_RISK_EVIDENCE_PATTERNS: tuple[str, ...] = (
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

SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

SEVERITY_LABELS: dict[str, str] = {
    "critical": "严重",
    "high": "高危",
    "medium": "中危",
    "low": "低危",
    "info": "提示",
}

STATUS_LABELS: dict[str, str] = {
    "confirmed": "已确认",
    "suspected": "待确认",
    "fixed": "已修复",
    "verified": "已复验",
    "accepted": "已接受",
    "false_positive": "误报关闭",
}


@dataclass(frozen=True)
class RiskEvidenceExperienceCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class RiskEvidenceExperienceAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    checks: list[RiskEvidenceExperienceCheck] = field(default_factory=list)
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


def _severity_label(severity: Any) -> str:
    text = str(severity or "info").lower()
    return SEVERITY_LABELS.get(text, _safe_text(severity, "待确认"))


def _severity_class(severity: Any) -> str:
    text = str(severity or "info").lower()
    if text == "critical":
        return "critical"
    if text == "high":
        return "high"
    if text == "medium":
        return "medium"
    return "low"


def _status_label(status: Any) -> str:
    return STATUS_LABELS.get(str(status or "suspected"), _safe_text(status, "待确认"))


def _api_data(app: Phase104CommandCenterHttpApp, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
    payload = json.dumps(body or {}, ensure_ascii=False) if body is not None else None
    response = app.handle(method, path, payload)
    envelope = response.json_body()
    if not envelope.get("success"):
        raise RuntimeError(f"API call failed for {method} {path}: {envelope.get('error')}")
    return envelope.get("data")


def _collect_all_risk_details(scenario: str) -> list[dict[str, Any]]:
    """Read all risk evidence details from the Phase104 API facade."""
    app = Phase104CommandCenterHttpApp(seed_scenario=scenario)
    projects = _api_data(app, "GET", "/api/v1/projects")
    if not projects:
        return []
    project_id = str(projects[0]["project_id"])
    risks = _api_data(app, "GET", f"/api/v1/projects/{project_id}/risks")
    details: list[dict[str, Any]] = []
    for risk in _as_list(risks):
        risk_id = _as_mapping(risk).get("risk_id")
        if not risk_id:
            continue
        details.append(_api_data(app, "GET", f"/api/v1/projects/{project_id}/risks/{risk_id}"))
    return details


def _risk_sort_key(risk: Mapping[str, Any]) -> tuple[int, int, str]:
    severity = str(risk.get("severity") or "info").lower()
    blocking = 0 if risk.get("launch_blocking") else 1
    return (SEVERITY_ORDER.get(severity, 9), blocking, str(risk.get("title") or ""))


def _normalize_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    risk = _as_mapping(detail.get("risk"))
    evidence = _as_mapping(detail.get("evidence_bundle"))
    request_summary = _as_mapping(evidence.get("request_summary"))
    response_summary = _as_mapping(evidence.get("response_summary"))
    snapshots = _as_mapping(evidence.get("snapshots"))
    before = _as_mapping(snapshots.get("before"))
    after = _as_mapping(snapshots.get("after"))
    if not before and not after:
        before = {"expected": "业务规则、权限边界或数据口径应保持一致"}
        after = {"observed": response_summary.get("observed_issue") or risk.get("technical_title") or "AI 探针观察到异常差异"}

    affected_flow = _as_mapping(risk.get("affected_business_flow"))
    modules = [_safe_text(item) for item in _as_list(risk.get("affected_modules"))]
    roles = [_safe_text(item) for item in _as_list(risk.get("affected_roles"))]

    return redact_value(
        {
            "risk_id": risk.get("risk_id"),
            "title": risk.get("title"),
            "technical_title": risk.get("technical_title"),
            "severity": risk.get("severity"),
            "severity_label": _severity_label(risk.get("severity")),
            "status": risk.get("status"),
            "status_label": _status_label(risk.get("status")),
            "launch_blocking": bool(risk.get("launch_blocking")),
            "business_flow_id": affected_flow.get("business_flow_id"),
            "business_flow_name": affected_flow.get("name"),
            "business_impact": risk.get("business_impact"),
            "suggested_action": risk.get("suggested_action"),
            "affected_modules": modules,
            "affected_roles": roles,
            "risk_type": risk.get("risk_type"),
            "owner": risk.get("owner") or "待分配",
            "confidence_score": _pct(risk.get("confidence_score")),
            "evidence_score": _pct(risk.get("evidence_score")),
            "reproducibility_score": _pct(risk.get("reproducibility_score")),
            "first_seen_at": risk.get("first_seen_at"),
            "last_verified_at": risk.get("last_verified_at"),
            "redaction_status": risk.get("redaction_status") or evidence.get("redaction_status") or "safe",
            "evidence": {
                "evidence_id": evidence.get("evidence_id"),
                "summary": evidence.get("summary"),
                "discovery_path": _as_list(evidence.get("discovery_path")),
                "reproduction_steps": _as_list(evidence.get("reproduction_steps")),
                "request_summary": request_summary,
                "response_summary": response_summary,
                "snapshot_before": before,
                "snapshot_after": after,
                "suggested_fix": _as_list(evidence.get("suggested_fix")),
                "closure_criteria": _as_list(evidence.get("closure_criteria")),
                "redaction_status": evidence.get("redaction_status") or "safe",
            },
        }
    )


def _build_filter_summary(risks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    severity_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    flow_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    launch_blocking = 0
    for risk in risks:
        severity = str(risk.get("severity") or "info").lower()
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        if risk.get("launch_blocking"):
            launch_blocking += 1
        flow_name = _safe_text(risk.get("business_flow_name") or _as_mapping(risk.get("affected_business_flow")).get("name"), "未关联链路")
        flow_counts[flow_name] = flow_counts.get(flow_name, 0) + 1
        status = str(risk.get("status") or "suspected")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "total": len(risks),
        "launch_blocking": launch_blocking,
        "non_blocking": len(risks) - launch_blocking,
        "severity_counts": severity_counts,
        "flow_counts": flow_counts,
        "status_counts": status_counts,
    }


def build_risk_evidence_view_model(scenario: str = "manufacturing", api_base_url: str | None = None) -> dict[str, Any]:
    api_base_url = api_base_url or default_api_base_url()
    shell_data = collect_product_shell_demo_data(scenario=scenario, api_base_url=api_base_url)
    details = [_normalize_detail(item) for item in _collect_all_risk_details(scenario)]
    if not details and isinstance(shell_data.get("risk_detail"), Mapping):
        details = [_normalize_detail(shell_data["risk_detail"])]

    details = sorted(details, key=_risk_sort_key)
    selected = details[0] if details else {}
    dashboard = _as_mapping(shell_data.get("dashboard"))
    launch_decision = _as_mapping(dashboard.get("launch_decision"))
    value_metrics = _as_mapping(shell_data.get("value_metrics"))

    phase104_actions = {
        "list_risks": f"GET /api/v1/projects/{shell_data['project']['project_id']}/risks",
        "read_risk_detail": f"GET /api/v1/projects/{shell_data['project']['project_id']}/risks/{{risk_id}}",
        "read_command_center": f"GET /api/v1/projects/{shell_data['project']['project_id']}/command-center",
        "read_live_map": f"GET /api/v1/projects/{shell_data['project']['project_id']}/live-map",
        "generate_report": f"POST /api/v1/projects/{shell_data['project']['project_id']}/reports/generate",
    }

    return redact_value(
        {
            "version": PHASE105F_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "api_base_url": api_base_url.rstrip("/"),
            "project": shell_data.get("project"),
            "launch_decision": launch_decision,
            "risk_summary": _build_filter_summary(details),
            "risks": details,
            "selected_risk": selected,
            "value_context": {
                "estimated_hours_saved": value_metrics.get("estimated_hours_saved"),
                "estimated_business_impact_min": value_metrics.get("estimated_business_impact_min"),
                "estimated_business_impact_max": value_metrics.get("estimated_business_impact_max"),
                "evidence_trust_score": _pct(value_metrics.get("evidence_trust_score")),
                "currency": value_metrics.get("currency", "CNY"),
            },
            "display_principles": [
                "先展示业务影响和上线阻断，再展开技术细节。",
                "证据链必须展示复现步骤、请求响应摘要、快照对比和关闭条件。",
                "默认只展示脱敏后的摘要、状态码、字段变化和复验结论。",
                "风险列表必须支持按严重级别、上线阻断、业务链路和状态聚焦。",
            ],
            "phase104_actions": phase104_actions,
        }
    )


def _render_risk_cards(risks: Sequence[Mapping[str, Any]]) -> str:
    if not risks:
        return '<article class="qb-empty">暂无风险</article>'
    cards: list[str] = []
    for index, risk in enumerate(risks):
        severity_class = _severity_class(risk.get("severity"))
        cards.append(
            f"""
            <article class="qb-risk-card {severity_class}" data-risk-id="{_escape(risk.get('risk_id'))}" data-index="{index}" data-severity="{_escape(risk.get('severity'))}" data-blocking="{str(bool(risk.get('launch_blocking'))).lower()}" data-flow="{_escape(risk.get('business_flow_name'))}" data-status="{_escape(risk.get('status'))}">
              <div class="qb-risk-card-head">
                <span class="qb-severity {severity_class}">{_escape(risk.get('severity_label'))}</span>
                <span class="qb-pill {'danger' if risk.get('launch_blocking') else 'safe'}">{'阻断上线' if risk.get('launch_blocking') else '非阻断'}</span>
                <span class="qb-pill">{_escape(risk.get('status_label'))}</span>
              </div>
              <h3>{_escape(risk.get('title'))}</h3>
              <p>{_escape(risk.get('business_impact'))}</p>
              <div class="qb-mini-grid">
                <span>链路<br><strong>{_escape(risk.get('business_flow_name'))}</strong></span>
                <span>证据可信度<br><strong>{_escape(risk.get('evidence_score'))}%</strong></span>
                <span>复现稳定性<br><strong>{_escape(risk.get('reproducibility_score'))}%</strong></span>
              </div>
              <button class="qb-detail-btn" data-risk-index="{index}">查看证据链详情</button>
            </article>
            """
        )
    return "\n".join(cards)


def _render_metric(label: str, value: Any, desc: str, tone: str = "") -> str:
    return f'<article class="qb-metric {tone}"><small>{_escape(label)}</small><strong>{_escape(value)}</strong><span>{_escape(desc)}</span></article>'


def _html_template(data: Mapping[str, Any]) -> str:
    summary = _as_mapping(data.get("risk_summary"))
    selected = _as_mapping(data.get("selected_risk"))
    launch = _as_mapping(data.get("launch_decision"))
    risk_cards = _render_risk_cards(_as_list(data.get("risks")))
    metrics = "\n".join(
        [
            _render_metric("风险总数", summary.get("total", 0), "本轮 AI 已转成业务语言的风险卡片。"),
            _render_metric("阻断上线", summary.get("launch_blocking", 0), "会直接影响上线建议的风险。", "danger"),
            _render_metric("当前建议", launch.get("title", "待确认"), launch.get("summary", "等待 AI 生成上线判断。"), "decision"),
            _render_metric("证据可信度", f"{_as_mapping(data.get('value_context')).get('evidence_trust_score', 0)}%", "综合证据链完整度与复现稳定性。"),
        ]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>QualiBug · 风险与 Bug 列表 / 证据链详情</title>
  <link rel="stylesheet" href="assets/qualibug_risk_evidence.css" />
</head>
<body>
  <div class="qb-layout">
    <aside class="qb-sidebar">
      <div class="qb-logo"><span>QB</span><strong>QualiBug AI</strong><small>企业质量指挥中心</small></div>
      <nav>
        <a>质量驾驶舱</a>
        <a>客户资料导入</a>
        <a>环境诊断中心</a>
        <a>业务流程地图</a>
        <a class="active">风险与 Bug 列表</a>
        <a>证据链详情</a>
        <a>领导层报告</a>
      </nav>
    </aside>
    <main class="qb-main">
      <header class="qb-hero">
        <div>
          <p class="qb-eyebrow">Phase105F · Risk & Evidence Experience</p>
          <h1>风险与 Bug 列表</h1>
          <p>把技术 Bug 翻译成客户看得懂的业务风险，并用证据链详情证明“真实、可复现、可修复、可复验”。</p>
        </div>
        <div class="qb-launch-card">
          <small>上线建议</small>
          <strong>{_escape(launch.get('title', '待确认'))}</strong>
          <span>{_escape(launch.get('risk_level', 'unknown'))}</span>
        </div>
      </header>

      <section class="qb-metrics">{metrics}</section>

      <section class="qb-toolbar">
        <div>
          <label>严重级别</label>
          <select id="severityFilter">
            <option value="all">全部</option>
            <option value="critical">严重</option>
            <option value="high">高危</option>
            <option value="medium">中危</option>
            <option value="low">低危</option>
          </select>
        </div>
        <div>
          <label>上线阻断</label>
          <select id="blockingFilter">
            <option value="all">全部</option>
            <option value="true">只看阻断上线</option>
            <option value="false">非阻断</option>
          </select>
        </div>
        <div>
          <label>业务链路</label>
          <select id="flowFilter"><option value="all">全部链路</option></select>
        </div>
        <button id="resetFilters">重置筛选</button>
      </section>

      <section class="qb-content-grid">
        <section>
          <div class="qb-section-title"><h2>AI 风险发现中心</h2><span>风险卡片 · 业务影响 · 修复动作</span></div>
          <div id="riskList" class="qb-risk-list">{risk_cards}</div>
        </section>
        <section class="qb-evidence-panel">
          <div class="qb-section-title"><h2>证据链详情</h2><span>默认脱敏 · 可复现 · 可关闭</span></div>
          <div id="evidenceDetail" class="qb-evidence-detail">
            <h3>{_escape(selected.get('title'))}</h3>
            <p>{_escape(selected.get('business_impact'))}</p>
          </div>
        </section>
      </section>

      <section class="qb-detail-grid">
        <article>
          <h2>业务影响</h2>
          <p id="businessImpact">{_escape(selected.get('business_impact'))}</p>
        </article>
        <article>
          <h2>修复建议</h2>
          <ul id="fixList"><li>{_escape(selected.get('suggested_action'))}</li></ul>
        </article>
        <article>
          <h2>关闭条件</h2>
          <ul id="closureList"><li>风险对应探针复测通过。</li><li>证据链生成 verified 记录。</li></ul>
        </article>
      </section>

      <section class="qb-api-box">
        <h2>Phase104 API 动作交接</h2>
        <div id="apiActions"></div>
      </section>
    </main>
  </div>
  <script id="riskEvidenceData" type="application/json">{json.dumps(redact_value(data), ensure_ascii=False)}</script>
  <script src="assets/qualibug_risk_evidence.js"></script>
</body>
</html>
"""


def _css_text() -> str:
    return """
:root { --bg:#f5f7fb; --panel:#fff; --ink:#172033; --muted:#667085; --line:#dfe5ef; --blue:#2264ff; --green:#138a55; --orange:#b65b00; --red:#c72b35; --purple:#6f42c1; }
* { box-sizing: border-box; }
body { margin:0; font-family: Inter, "Microsoft YaHei", "PingFang SC", Arial, sans-serif; background:var(--bg); color:var(--ink); }
.qb-layout { display:flex; min-height:100vh; }
.qb-sidebar { width:268px; background:#111827; color:#fff; padding:24px 18px; position:sticky; top:0; height:100vh; }
.qb-logo { display:grid; gap:4px; margin-bottom:26px; }
.qb-logo span { width:44px; height:44px; border-radius:14px; display:grid; place-items:center; background:linear-gradient(135deg,#3b82f6,#7c3aed); font-weight:800; }
.qb-logo strong { font-size:20px; }
.qb-logo small { color:#b9c2d0; }
.qb-sidebar nav { display:grid; gap:8px; }
.qb-sidebar a { color:#cbd5e1; text-decoration:none; padding:12px 13px; border-radius:14px; }
.qb-sidebar a.active, .qb-sidebar a:hover { background:#243044; color:#fff; }
.qb-main { flex:1; padding:28px; max-width:1500px; margin:0 auto; }
.qb-hero { display:flex; justify-content:space-between; gap:20px; align-items:stretch; margin-bottom:20px; }
.qb-hero > div:first-child { background:linear-gradient(135deg,#ffffff,#eef4ff); border:1px solid var(--line); border-radius:28px; padding:26px; flex:1; box-shadow:0 18px 40px rgba(15,23,42,.06); }
.qb-eyebrow { color:var(--blue); font-weight:800; letter-spacing:.04em; text-transform:uppercase; margin:0 0 8px; }
h1,h2,h3,p { margin-top:0; }
h1 { font-size:34px; margin-bottom:10px; }
.qb-launch-card { min-width:230px; border-radius:28px; padding:24px; color:#fff; background:linear-gradient(135deg,#c72b35,#7f1d1d); display:grid; align-content:center; box-shadow:0 18px 40px rgba(127,29,29,.22); }
.qb-launch-card small { opacity:.85; }
.qb-launch-card strong { font-size:28px; margin:6px 0; }
.qb-metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:18px; }
.qb-metric { background:var(--panel); border:1px solid var(--line); border-radius:22px; padding:18px; display:grid; gap:6px; }
.qb-metric strong { font-size:25px; }
.qb-metric small, .qb-metric span { color:var(--muted); }
.qb-metric.danger { border-color:rgba(199,43,53,.35); background:#fff7f7; }
.qb-metric.decision { border-color:rgba(34,100,255,.25); background:#f7faff; }
.qb-toolbar { background:var(--panel); border:1px solid var(--line); border-radius:22px; padding:14px; display:flex; align-items:end; gap:12px; margin-bottom:18px; flex-wrap:wrap; }
.qb-toolbar div { display:grid; gap:6px; }
.qb-toolbar label { font-size:12px; color:var(--muted); font-weight:700; }
select, button { border:1px solid var(--line); border-radius:12px; padding:10px 12px; background:#fff; color:var(--ink); }
button { cursor:pointer; font-weight:800; }
.qb-content-grid { display:grid; grid-template-columns:minmax(0,1fr) 520px; gap:18px; align-items:start; }
.qb-section-title { display:flex; align-items:end; justify-content:space-between; gap:12px; margin:4px 0 12px; }
.qb-section-title h2 { margin:0; }
.qb-section-title span { color:var(--muted); font-size:13px; }
.qb-risk-list { display:grid; gap:14px; }
.qb-risk-card { background:var(--panel); border:1px solid var(--line); border-left:6px solid var(--blue); border-radius:24px; padding:18px; box-shadow:0 12px 28px rgba(15,23,42,.05); }
.qb-risk-card.critical { border-left-color:var(--red); }
.qb-risk-card.high { border-left-color:var(--orange); }
.qb-risk-card.medium { border-left-color:#d69e2e; }
.qb-risk-card.low { border-left-color:var(--green); }
.qb-risk-card-head { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:10px; }
.qb-severity, .qb-pill { display:inline-flex; align-items:center; min-height:26px; border-radius:999px; padding:4px 10px; font-size:12px; font-weight:800; background:#eef2ff; color:#364152; }
.qb-severity.critical { background:#ffe7e8; color:var(--red); }
.qb-severity.high { background:#fff1db; color:var(--orange); }
.qb-severity.medium { background:#fff8db; color:#9a6700; }
.qb-severity.low { background:#e9f8ef; color:var(--green); }
.qb-pill.danger { background:#ffe7e8; color:var(--red); }
.qb-pill.safe { background:#e9f8ef; color:var(--green); }
.qb-mini-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin:14px 0; }
.qb-mini-grid span { border:1px solid var(--line); background:#f9fbff; border-radius:14px; padding:10px; color:var(--muted); font-size:12px; }
.qb-mini-grid strong { color:var(--ink); }
.qb-detail-btn { background:#172033; color:#fff; border-color:#172033; }
.qb-evidence-panel { position:sticky; top:20px; }
.qb-evidence-detail, .qb-detail-grid article, .qb-api-box { background:var(--panel); border:1px solid var(--line); border-radius:24px; padding:18px; box-shadow:0 12px 28px rgba(15,23,42,.05); }
.qb-evidence-detail h3 { font-size:22px; }
.qb-evidence-block { border-top:1px solid var(--line); padding-top:14px; margin-top:14px; }
.qb-evidence-block h4 { margin:0 0 8px; }
.qb-timeline { display:grid; gap:8px; }
.qb-timeline li { background:#f9fbff; border:1px solid var(--line); border-radius:14px; padding:10px; }
.qb-kv { display:grid; grid-template-columns:140px 1fr; gap:8px; font-size:14px; margin:6px 0; }
.qb-kv span:first-child { color:var(--muted); }
.qb-diff { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.qb-diff pre { background:#101828; color:#d1e7ff; border-radius:14px; padding:12px; overflow:auto; white-space:pre-wrap; }
.qb-detail-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-top:18px; }
.qb-api-box { margin-top:18px; }
.qb-api-actions { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
.qb-api-actions code { display:block; background:#f3f6fb; border:1px solid var(--line); border-radius:14px; padding:12px; overflow:auto; }
.qb-empty { background:#fff; border:1px dashed var(--line); border-radius:20px; padding:18px; color:var(--muted); }
@media (max-width: 1100px) { .qb-sidebar { display:none; } .qb-content-grid, .qb-detail-grid, .qb-metrics { grid-template-columns:1fr; } .qb-evidence-panel { position:static; } .qb-hero { flex-direction:column; } }
"""


def _js_text() -> str:
    return """
const data = JSON.parse(document.getElementById('riskEvidenceData').textContent || '{}');
const risks = data.risks || [];
const esc = (v) => String(v ?? '—').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = (v) => Number.isFinite(Number(v)) ? `${Math.round(Number(v))}%` : '—';

function severityClass(severity) {
  const s = String(severity || '').toLowerCase();
  if (s === 'critical') return 'critical';
  if (s === 'high') return 'high';
  if (s === 'medium') return 'medium';
  return 'low';
}

function list(items) {
  if (!items || !items.length) return '<li>暂无</li>';
  return items.map((item) => `<li>${esc(typeof item === 'object' ? (item.name || item.title || JSON.stringify(item)) : item)}</li>`).join('');
}

function kv(label, value) {
  return `<div class="qb-kv"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
}

function renderEvidence(index) {
  const risk = risks[index] || risks[0] || {};
  const ev = risk.evidence || {};
  const request = ev.request_summary || {};
  const response = ev.response_summary || {};
  const before = ev.snapshot_before || {};
  const after = ev.snapshot_after || {};
  document.querySelectorAll('.qb-risk-card').forEach((card) => card.style.outline = 'none');
  const active = document.querySelector(`.qb-risk-card[data-index="${index}"]`);
  if (active) active.style.outline = '3px solid rgba(34,100,255,.25)';

  document.getElementById('evidenceDetail').innerHTML = `
    <div class="qb-risk-card-head"><span class="qb-severity ${severityClass(risk.severity)}">${esc(risk.severity_label)}</span><span class="qb-pill ${risk.launch_blocking ? 'danger' : 'safe'}">${risk.launch_blocking ? '阻断上线' : '非阻断'}</span><span class="qb-pill">默认脱敏：${esc(risk.redaction_status)}</span></div>
    <h3>${esc(risk.title)}</h3>
    <p>${esc(risk.business_impact)}</p>
    <div class="qb-mini-grid"><span>证据可信度<br><strong>${pct(risk.evidence_score)}</strong></span><span>复现稳定性<br><strong>${pct(risk.reproducibility_score)}</strong></span><span>置信度<br><strong>${pct(risk.confidence_score)}</strong></span></div>
    <div class="qb-evidence-block"><h4>发现路径</h4><ol class="qb-timeline">${list(ev.discovery_path)}</ol></div>
    <div class="qb-evidence-block"><h4>复现步骤</h4><ol class="qb-timeline">${list(ev.reproduction_steps)}</ol></div>
    <div class="qb-evidence-block"><h4>请求响应摘要</h4>${kv('请求方法', request.method)}${kv('请求路径', request.path)}${kv('认证上下文', request.auth_context)}${kv('响应状态码', response.status_code)}${kv('响应类型', response.content_type)}${kv('观察到的问题', response.observed_issue)}</div>
    <div class="qb-evidence-block"><h4>快照对比</h4><div class="qb-diff"><pre>${esc(JSON.stringify(before, null, 2))}</pre><pre>${esc(JSON.stringify(after, null, 2))}</pre></div></div>
  `;
  document.getElementById('businessImpact').textContent = risk.business_impact || '—';
  document.getElementById('fixList').innerHTML = list(ev.suggested_fix && ev.suggested_fix.length ? ev.suggested_fix : [risk.suggested_action]);
  document.getElementById('closureList').innerHTML = list(ev.closure_criteria);
}

function applyFilters() {
  const severity = document.getElementById('severityFilter').value;
  const blocking = document.getElementById('blockingFilter').value;
  const flow = document.getElementById('flowFilter').value;
  document.querySelectorAll('.qb-risk-card').forEach((card) => {
    const showSeverity = severity === 'all' || card.dataset.severity === severity;
    const showBlocking = blocking === 'all' || card.dataset.blocking === blocking;
    const showFlow = flow === 'all' || card.dataset.flow === flow;
    card.style.display = showSeverity && showBlocking && showFlow ? '' : 'none';
  });
}

function initFilters() {
  const flows = [...new Set(risks.map((risk) => risk.business_flow_name).filter(Boolean))];
  const flowSelect = document.getElementById('flowFilter');
  flows.forEach((flow) => {
    const option = document.createElement('option');
    option.value = flow;
    option.textContent = flow;
    flowSelect.appendChild(option);
  });
  ['severityFilter', 'blockingFilter', 'flowFilter'].forEach((id) => document.getElementById(id).addEventListener('change', applyFilters));
  document.getElementById('resetFilters').addEventListener('click', () => {
    document.getElementById('severityFilter').value = 'all';
    document.getElementById('blockingFilter').value = 'all';
    document.getElementById('flowFilter').value = 'all';
    applyFilters();
  });
  document.querySelectorAll('.qb-detail-btn').forEach((button) => button.addEventListener('click', () => renderEvidence(Number(button.dataset.riskIndex || 0))));
}

function initApiActions() {
  const actions = data.phase104_actions || {};
  document.getElementById('apiActions').innerHTML = `<div class="qb-api-actions">${Object.entries(actions).map(([key, value]) => `<code><strong>${esc(key)}</strong><br>${esc(value)}</code>`).join('')}</div>`;
}

initFilters();
initApiActions();
renderEvidence(0);
"""


def _readme_text(manifest: Mapping[str, Any]) -> str:
    return f"""# Phase105F 风险与证据链详情体验

Phase105F 把前端显示层推进到风险证明环节：风险列表负责把技术 Bug 转成业务语言，证据链详情负责证明风险真实、可复现、可修复、可关闭。

## 输出

- `risk_evidence.html`
- `assets/qualibug_risk_evidence.css`
- `assets/qualibug_risk_evidence.js`
- `data/risk_evidence_experience_data.json`
- `risk_evidence_experience_manifest.json`
- `risk_evidence_experience_acceptance_report.json`
- `risk_evidence_experience_acceptance_report.md`

## 运行

```powershell
python -m ai_test_asset_center.phase105_risk_evidence_experience --scenario {manifest.get('scenario', 'manufacturing')} --output-dir .\\outputs\\phase105_risk_evidence_experience
Start-Process .\\outputs\\phase105_risk_evidence_experience\\risk_evidence.html
```

## 页面重点

- 风险与 Bug 列表
- 业务影响说明
- 阻断上线标识
- 证据链详情
- 复现步骤
- 请求响应摘要
- 快照对比
- 修复建议
- 关闭条件
- 默认脱敏状态
"""


def _manifest(output_dir: Path, scenario: str, data: Mapping[str, Any]) -> dict[str, Any]:
    return redact_value(
        {
            "version": PHASE105F_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "output_dir": str(output_dir),
            "entrypoint": "risk_evidence.html",
            "required_files": list(REQUIRED_RISK_EVIDENCE_FILES),
            "core_labels": list(CORE_RISK_EVIDENCE_LABELS),
            "risk_count": _as_mapping(data.get("risk_summary")).get("total", 0),
            "launch_blocking_count": _as_mapping(data.get("risk_summary")).get("launch_blocking", 0),
            "redaction_status": "safe",
            "phase104_actions": data.get("phase104_actions"),
        }
    )


def build_risk_evidence_experience(output_dir: str | Path, *, scenario: str = "manufacturing", api_base_url: str | None = None) -> dict[str, Any]:
    api_base_url = api_base_url or default_api_base_url()
    output = Path(output_dir)
    data = build_risk_evidence_view_model(scenario=scenario, api_base_url=api_base_url)
    manifest = _manifest(output, scenario, data)

    _write_text(output / "risk_evidence.html", _html_template(data))
    _write_text(output / "assets" / "qualibug_risk_evidence.css", _css_text())
    _write_text(output / "assets" / "qualibug_risk_evidence.js", _js_text())
    _write_text(output / "data" / "risk_evidence_experience_data.json", _json_dump(data))
    _write_text(output / "README_RISK_EVIDENCE_EXPERIENCE.md", _readme_text(manifest))
    _write_text(output / RISK_EVIDENCE_MANIFEST, _json_dump(manifest))
    return manifest


def _check_file_contains(output_dir: Path, relative_path: str, required_text: Sequence[str]) -> tuple[bool, str]:
    path = output_dir / relative_path
    if not path.exists():
        return False, f"missing {relative_path}"
    text = path.read_text(encoding="utf-8", errors="ignore")
    missing = [label for label in required_text if label not in text]
    if missing:
        return False, f"{relative_path} missing labels: {', '.join(missing)}"
    return True, f"{relative_path} contains required labels"


def scan_risk_evidence_for_secret_leaks(output_dir: str | Path) -> list[str]:
    output = Path(output_dir)
    leaks: list[str] = []
    for path in output.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js", ".json", ".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_RISK_EVIDENCE_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(output)} contains forbidden pattern {pattern}")
    return leaks


def validate_risk_evidence_experience(output_dir: str | Path) -> RiskEvidenceExperienceAcceptanceReport:
    output = Path(output_dir)
    scenario = "unknown"
    checks: list[RiskEvidenceExperienceCheck] = []

    manifest_path = output / RISK_EVIDENCE_MANIFEST
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        scenario = str(manifest.get("scenario") or scenario)
        checks.append(RiskEvidenceExperienceCheck("manifest", True, "risk evidence manifest exists"))
    else:
        checks.append(RiskEvidenceExperienceCheck("manifest", False, "risk evidence manifest missing"))

    for relative_path in REQUIRED_RISK_EVIDENCE_FILES:
        exists = (output / relative_path).exists()
        checks.append(RiskEvidenceExperienceCheck(f"file:{relative_path}", exists, f"{relative_path} {'exists' if exists else 'missing'}"))

    html_ok, html_detail = _check_file_contains(output, "risk_evidence.html", CORE_RISK_EVIDENCE_LABELS)
    checks.append(RiskEvidenceExperienceCheck("html_labels", html_ok, html_detail))

    data_path = output / "data" / "risk_evidence_experience_data.json"
    if data_path.exists():
        data = json.loads(data_path.read_text(encoding="utf-8"))
        risk_summary = _as_mapping(data.get("risk_summary"))
        risks = _as_list(data.get("risks"))
        selected = _as_mapping(data.get("selected_risk"))
        evidence = _as_mapping(selected.get("evidence"))
        checks.extend(
            [
                RiskEvidenceExperienceCheck("data:risk_list", bool(risks), "risk list is present for frontend rendering"),
                RiskEvidenceExperienceCheck("data:business_impact", all(_as_mapping(r).get("business_impact") for r in risks), "every risk carries business impact text"),
                RiskEvidenceExperienceCheck("data:launch_blocking", _int_value(risk_summary.get("launch_blocking")) >= 1, "launch blocking risk count is visible"),
                RiskEvidenceExperienceCheck("data:evidence_detail", bool(evidence.get("reproduction_steps")) and bool(evidence.get("request_summary")) and bool(evidence.get("response_summary")), "selected risk includes reproduction steps and request/response summary"),
                RiskEvidenceExperienceCheck("data:snapshot_diff", bool(evidence.get("snapshot_before")) and bool(evidence.get("snapshot_after")), "selected risk includes before/after snapshot diff"),
                RiskEvidenceExperienceCheck("data:closure_criteria", bool(evidence.get("closure_criteria")), "selected risk includes closure criteria"),
                RiskEvidenceExperienceCheck("data:phase104_actions", bool(data.get("phase104_actions")), "Phase104 API handoff actions are available"),
            ]
        )
    else:
        checks.append(RiskEvidenceExperienceCheck("data", False, "risk evidence data JSON missing"))

    css_ok, css_detail = _check_file_contains(output, "assets/qualibug_risk_evidence.css", ["qb-risk-card", "qb-evidence-detail", "qb-diff"])
    checks.append(RiskEvidenceExperienceCheck("css", css_ok, css_detail))

    js_ok, js_detail = _check_file_contains(output, "assets/qualibug_risk_evidence.js", ["renderEvidence", "applyFilters", "riskEvidenceData"])
    checks.append(RiskEvidenceExperienceCheck("js", js_ok, js_detail))

    leaks = scan_risk_evidence_for_secret_leaks(output)
    checks.append(RiskEvidenceExperienceCheck("secret_scan", not leaks, "no raw secret or traceback leak detected" if not leaks else "; ".join(leaks)))

    passed = all(check.passed for check in checks)
    score = int(round(100 * sum(1 for check in checks if check.passed) / max(len(checks), 1)))
    return RiskEvidenceExperienceAcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE105F_VERSION,
        scenario=scenario,
        output_dir=str(output),
        checks=checks,
        artifacts={
            "entrypoint": "risk_evidence.html",
            "manifest": RISK_EVIDENCE_MANIFEST,
            "acceptance_json": RISK_EVIDENCE_ACCEPTANCE_JSON,
            "acceptance_md": RISK_EVIDENCE_ACCEPTANCE_MD,
        },
    )


def _acceptance_md(report: RiskEvidenceExperienceAcceptanceReport) -> str:
    rows = "\n".join(
        f"| {check.key} | {'PASS' if check.passed else 'FAIL'} | {check.detail} |"
        for check in report.checks
    )
    return f"""# Phase105F 风险与证据链详情体验验收报告

- 版本：`{report.version}`
- 场景：`{report.scenario}`
- 结论：{'通过' if report.passed else '未通过'}
- 得分：{report.score}

| 检查项 | 结果 | 说明 |
|---|---:|---|
{rows}
"""


def run_risk_evidence_experience_export(
    *,
    output_dir: str | Path = "outputs/phase105_risk_evidence_experience",
    scenario: str = "manufacturing",
    api_base_url: str | None = None,
    validate_only: bool = False,
) -> dict[str, Any]:
    api_base_url = api_base_url or default_api_base_url()
    output = Path(output_dir)
    if not validate_only:
        build_risk_evidence_experience(output, scenario=scenario, api_base_url=api_base_url)
    report = validate_risk_evidence_experience(output)
    _write_text(output / RISK_EVIDENCE_ACCEPTANCE_JSON, _json_dump(report.to_dict()))
    _write_text(output / RISK_EVIDENCE_ACCEPTANCE_MD, _acceptance_md(report))
    return {"manifest": json.loads((output / RISK_EVIDENCE_MANIFEST).read_text(encoding="utf-8")) if (output / RISK_EVIDENCE_MANIFEST).exists() else {}, "acceptance": report.to_dict()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Phase105F risk and evidence frontend experience.")
    parser.add_argument("--scenario", default="manufacturing", choices=["manufacturing", "ecommerce", "saas"], help="Demo scenario used to collect Phase104 API data.")
    parser.add_argument("--api-base-url", default=None, help="Backend API base URL (default: from QUALIBUG_API_BASE_URL or QUALIBUG_PORT)")
    parser.add_argument("--output-dir", default="outputs/phase105_risk_evidence_experience", help="Output directory.")
    parser.add_argument("--validate-only", action="store_true", help="Validate an existing output directory without rebuilding files.")
    args = parser.parse_args(argv)
    result = run_risk_evidence_experience_export(
        output_dir=args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(json.dumps(result["acceptance"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["acceptance"].get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())

