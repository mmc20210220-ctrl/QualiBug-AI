from __future__ import annotations

"""Phase105G: executive report and ROI-value frontend experience.

Phase105A-F produced the product shell, dashboard, customer intake,
environment diagnosis, business-flow map, and risk/evidence screens. This
module focuses the management layer: an executive-ready report page plus a ROI
value center that answers whether to launch, why, what value AI testing created,
which risks block the release, and what actions must happen next.

The exporter is dependency-free and writes static HTML/CSS/JS plus a redacted
JSON view model. It is suitable for local product demos now and can be reused by
a future React/Vue frontend later.
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

PHASE105G_VERSION = "phase105g-report-roi-experience-v1"

REPORT_ROI_MANIFEST = "report_roi_experience_manifest.json"
REPORT_ROI_ACCEPTANCE_JSON = "report_roi_experience_acceptance_report.json"
REPORT_ROI_ACCEPTANCE_MD = "report_roi_experience_acceptance_report.md"

REQUIRED_REPORT_ROI_FILES: tuple[str, ...] = (
    "report_roi.html",
    "README_REPORT_ROI_EXPERIENCE.md",
    "data/report_roi_experience_data.json",
    "assets/qualibug_report_roi.css",
    "assets/qualibug_report_roi.js",
    REPORT_ROI_MANIFEST,
)

CORE_REPORT_ROI_LABELS: tuple[str, ...] = (
    "领导层报告",
    "ROI 价值中心",
    "上线建议",
    "执行摘要",
    "风险价值",
    "节省工时",
    "业务影响区间",
    "下一步动作",
    "证据可信度",
    "可复制摘要",
    "默认脱敏",
)

FORBIDDEN_REPORT_ROI_PATTERNS: tuple[str, ...] = (
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

DECISION_LABELS: dict[str, str] = {
    "GO": "建议上线",
    "CONDITIONAL_GO": "可灰度上线",
    "HOLD": "暂缓上线",
    "NO_GO": "不建议上线",
}

SEVERITY_LABELS: dict[str, str] = {
    "critical": "严重",
    "high": "高危",
    "medium": "中危",
    "low": "低危",
    "info": "提示",
}


@dataclass(frozen=True)
class ReportRoiExperienceCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class ReportRoiExperienceAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    checks: list[ReportRoiExperienceCheck] = field(default_factory=list)
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


def _currency(value: Any, currency: str = "CNY") -> str:
    amount = _int_value(value)
    return f"{amount:,} {currency}"


def _decision_label(recommendation: Any) -> str:
    return DECISION_LABELS.get(str(recommendation or "").upper(), _safe_text(recommendation, "待确认"))


def _severity_label(severity: Any) -> str:
    return SEVERITY_LABELS.get(str(severity or "info").lower(), _safe_text(severity, "待确认"))


def _extract_report_roi_view_model(scenario: str, api_base_url: str) -> dict[str, Any]:
    raw = collect_product_shell_demo_data(scenario=scenario, api_base_url=api_base_url)
    project = _as_mapping(raw.get("project"))
    dashboard = _as_mapping(raw.get("dashboard"))
    launch_decision = _as_mapping(dashboard.get("launch_decision"))
    executive_report = _as_mapping(raw.get("executive_report"))
    value_metrics = _as_mapping(raw.get("value_metrics") or executive_report.get("value_summary"))
    risk_summary = _as_mapping(executive_report.get("risk_summary"))
    coverage = _as_mapping(executive_report.get("coverage_summary") or dashboard.get("business_flow_summary"))
    evidence_trust = _as_mapping(executive_report.get("evidence_trust_summary"))
    top_risks = _as_list(executive_report.get("top_risks") or raw.get("risks"))
    business_impact_summary = _as_list(executive_report.get("business_impact_summary"))
    next_actions = _as_list(executive_report.get("next_actions") or launch_decision.get("required_actions"))

    currency = _safe_text(value_metrics.get("currency"), "CNY")
    recommendation = _safe_text(executive_report.get("launch_recommendation") or launch_decision.get("recommendation"), "HOLD")
    decision_card = {
        "recommendation": recommendation,
        "label": _decision_label(recommendation),
        "title": _safe_text(launch_decision.get("title"), _decision_label(recommendation)),
        "risk_level": _safe_text(launch_decision.get("risk_level"), "medium"),
        "summary": _safe_text(launch_decision.get("summary") or executive_report.get("executive_summary")),
        "reasons": _as_list(launch_decision.get("reasons")),
        "required_actions": _as_list(launch_decision.get("required_actions")),
    }

    roi_cards = [
        {
            "label": "AI 等价测试点",
            "value": _int_value(value_metrics.get("ai_equivalent_test_points")),
            "unit": "points",
            "explain": "将业务链路、权限、环境、证据探针折算为人工测试点。",
        },
        {
            "label": "预计节省工时",
            "value": _float_value(value_metrics.get("estimated_hours_saved")),
            "unit": "hours",
            "explain": f"按每个测试点 {_int_value(value_metrics.get('manual_minutes_per_test_point'), 12)} 分钟人工执行估算。",
        },
        {
            "label": "业务影响区间",
            "value": f"{_currency(value_metrics.get('estimated_business_impact_min'), currency)} - {_currency(value_metrics.get('estimated_business_impact_max'), currency)}",
            "unit": "range",
            "explain": "保守展示风险暴露区间，不承诺确定收益。",
        },
        {
            "label": "证据可信度",
            "value": f"{_pct(value_metrics.get('evidence_trust_score'))}%",
            "unit": "trust",
            "explain": "综合证据完整度、复现稳定性和脱敏状态。",
        },
    ]

    report_sections = [
        {
            "title": "执行摘要",
            "content": _safe_text(executive_report.get("executive_summary") or dashboard.get("executive_summary")),
        },
        {
            "title": "上线建议",
            "content": _safe_text(launch_decision.get("summary"), decision_card["summary"]),
        },
        {
            "title": "AI 价值量化",
            "content": f"AI 等价执行 {_int_value(value_metrics.get('ai_equivalent_test_points'))} 个测试点，预计节省 {_float_value(value_metrics.get('estimated_hours_saved'))} 小时，潜在业务影响区间为 {_currency(value_metrics.get('estimated_business_impact_min'), currency)} - {_currency(value_metrics.get('estimated_business_impact_max'), currency)}。",
        },
        {
            "title": "证据可信度",
            "content": _safe_text(evidence_trust.get("statement"), "报告证据默认脱敏，前端不展示 token、cookie、password、session 原值。"),
        },
    ]

    copy_blocks = {
        "executive_summary": report_sections[0]["content"],
        "launch_recommendation": f"{decision_card['label']}：{decision_card['summary']}",
        "value_statement": report_sections[2]["content"],
        "meeting_note": f"{_safe_text(project.get('project_name'))} 本轮质量评估结论为 {decision_card['label']}。{report_sections[0]['content']}",
    }

    normalized_top_risks: list[dict[str, Any]] = []
    for item in top_risks[:6]:
        risk = _as_mapping(item)
        flow = _as_mapping(risk.get("affected_business_flow"))
        normalized_top_risks.append(
            {
                "risk_id": _safe_text(risk.get("risk_id")),
                "title": _safe_text(risk.get("title")),
                "severity": _safe_text(risk.get("severity"), "info"),
                "severity_label": _severity_label(risk.get("severity")),
                "business_flow": _safe_text(flow.get("name")),
                "business_impact": _safe_text(risk.get("business_impact")),
                "suggested_action": _safe_text(risk.get("suggested_action")),
                "evidence_score": _pct(risk.get("evidence_score")),
                "reproducibility_score": _pct(risk.get("reproducibility_score")),
            }
        )

    normalized_actions: list[dict[str, Any]] = []
    for index, item in enumerate(next_actions[:6], start=1):
        if isinstance(item, Mapping):
            action = _as_mapping(item)
            normalized_actions.append(
                {
                    "step": index,
                    "priority": _safe_text(action.get("priority"), "P1"),
                    "title": _safe_text(action.get("title")),
                    "owner_suggestion": _safe_text(action.get("owner_suggestion"), "对应业务系统负责人"),
                    "verification_probe": _safe_text(action.get("verification_probe")),
                    "reason": _safe_text(action.get("reason")),
                }
            )
        else:
            normalized_actions.append(
                {
                    "step": index,
                    "priority": "P1",
                    "title": _safe_text(item),
                    "owner_suggestion": "对应业务系统负责人",
                    "verification_probe": "关联回归探针",
                    "reason": "上线前需要关闭或接受该动作。",
                }
            )

    view_model = {
        "version": PHASE105G_VERSION,
        "generated_at": _now(),
        "scenario": scenario,
        "api_base_url": api_base_url,
        "project": project,
        "decision_card": decision_card,
        "executive_report": {
            "title": _safe_text(executive_report.get("title"), f"{_safe_text(project.get('system_name'))} 上线质量风险评估报告"),
            "quality_health_score": _int_value(executive_report.get("quality_health_score")),
            "risk_summary": {
                "critical": _int_value(risk_summary.get("critical")),
                "high": _int_value(risk_summary.get("high")),
                "launch_blocking": _int_value(risk_summary.get("launch_blocking")),
            },
            "coverage_summary": {
                "total": _int_value(coverage.get("total")),
                "covered": _int_value(coverage.get("covered")),
                "blocked": _int_value(coverage.get("blocked")),
                "coverage_rate": _pct(coverage.get("coverage_rate")),
            },
            "business_impact_summary": business_impact_summary,
            "evidence_trust_summary": evidence_trust,
            "report_sections": report_sections,
            "markdown_preview": _safe_text(executive_report.get("markdown"))[:1800],
        },
        "roi_value_center": {
            "currency": currency,
            "roi_cards": roi_cards,
            "calculation_notes": _as_list(value_metrics.get("calculation_notes")),
            "estimated_hours_saved": _float_value(value_metrics.get("estimated_hours_saved")),
            "estimated_business_impact_min": _int_value(value_metrics.get("estimated_business_impact_min")),
            "estimated_business_impact_max": _int_value(value_metrics.get("estimated_business_impact_max")),
            "business_flow_coverage_rate": _pct(value_metrics.get("business_flow_coverage_rate")),
            "evidence_trust_score": _pct(value_metrics.get("evidence_trust_score")),
        },
        "top_risks": normalized_top_risks,
        "next_actions": normalized_actions,
        "copy_blocks": copy_blocks,
        "phase104_actions": {
            "read_executive_report": "/api/v1/projects/{project_id}/report",
            "read_value_metrics": "/api/v1/projects/{project_id}/value",
            "read_dashboard": "/api/v1/projects/{project_id}/command-center",
            "read_risk_detail": "/api/v1/projects/{project_id}/risks/{risk_id}",
        },
        "display_principles": [
            "领导层页面优先展示上线结论、业务影响和下一步动作。",
            "ROI 只展示保守估算与计算说明，不夸大收益。",
            "报告默认脱敏，不展示 token、cookie、password、session、client_secret 原值。",
        ],
    }
    return redact_value(view_model)


def _render_roi_card(card: Mapping[str, Any]) -> str:
    return f"""
      <article class=\"roi-card\">
        <span>{_escape(card.get('label'))}</span>
        <strong>{_escape(card.get('value'))}</strong>
        <p>{_escape(card.get('explain'))}</p>
      </article>
    """


def _render_report_section(section: Mapping[str, Any]) -> str:
    return f"""
      <article class=\"report-section\">
        <h3>{_escape(section.get('title'))}</h3>
        <p>{_escape(section.get('content'))}</p>
      </article>
    """


def _render_risk_card(risk: Mapping[str, Any]) -> str:
    return f"""
      <article class=\"risk-card severity-{_escape(risk.get('severity'))}\">
        <div class=\"risk-card__head\">
          <span class=\"pill danger\">{_escape(risk.get('severity_label'))}</span>
          <span>{_escape(risk.get('business_flow'))}</span>
        </div>
        <h3>{_escape(risk.get('title'))}</h3>
        <p>{_escape(risk.get('business_impact'))}</p>
        <footer>
          <span>证据 {_escape(risk.get('evidence_score'))}%</span>
          <span>复现 {_escape(risk.get('reproducibility_score'))}%</span>
        </footer>
      </article>
    """


def _render_action(action: Mapping[str, Any]) -> str:
    return f"""
      <li class=\"action-item\">
        <span class=\"step\">{_escape(action.get('step'))}</span>
        <div>
          <strong>{_escape(action.get('priority'))} · {_escape(action.get('title'))}</strong>
          <p>负责人建议：{_escape(action.get('owner_suggestion'))} · 复验探针：{_escape(action.get('verification_probe'))}</p>
        </div>
      </li>
    """


def render_report_roi_html(data: Mapping[str, Any]) -> str:
    project = _as_mapping(data.get("project"))
    decision = _as_mapping(data.get("decision_card"))
    report = _as_mapping(data.get("executive_report"))
    roi = _as_mapping(data.get("roi_value_center"))
    roi_cards = _as_list(roi.get("roi_cards"))
    report_sections = _as_list(report.get("report_sections"))
    risks = _as_list(data.get("top_risks"))
    actions = _as_list(data.get("next_actions"))
    copy_blocks = _as_mapping(data.get("copy_blocks"))

    roi_html = "\n".join(_render_roi_card(_as_mapping(card)) for card in roi_cards)
    sections_html = "\n".join(_render_report_section(_as_mapping(section)) for section in report_sections)
    risks_html = "\n".join(_render_risk_card(_as_mapping(risk)) for risk in risks)
    actions_html = "\n".join(_render_action(_as_mapping(action)) for action in actions)

    return f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>QualiBug AI · 领导层报告与 ROI 价值中心</title>
  <link rel=\"stylesheet\" href=\"assets/qualibug_report_roi.css\" />
</head>
<body>
  <main class=\"page\">
    <header class=\"hero\">
      <nav>
        <a href=\"index.html\">产品壳</a>
        <a href=\"dashboard.html\">质量驾驶舱</a>
        <a href=\"risk_evidence.html\">风险证据</a>
      </nav>
      <div class=\"hero__content\">
        <p class=\"eyebrow\">QualiBug AI 企业质量指挥中心 · Phase105G</p>
        <h1>领导层报告 + ROI 价值中心</h1>
        <p>把 AI 测试结果转换成“是否建议上线、为什么、风险价值、节省工时、下一步动作”的管理层语言。</p>
      </div>
      <aside class=\"decision-card decision-{_escape(decision.get('recommendation')).lower()}\">
        <span>上线建议</span>
        <strong>{_escape(decision.get('label'))}</strong>
        <p>{_escape(decision.get('summary'))}</p>
      </aside>
    </header>

    <section class=\"project-strip\">
      <div><span>客户</span><strong>{_escape(project.get('customer_name'))}</strong></div>
      <div><span>系统</span><strong>{_escape(project.get('system_name'))}</strong></div>
      <div><span>项目</span><strong>{_escape(project.get('project_name'))}</strong></div>
      <div><span>计划上线</span><strong>{_escape(project.get('planned_launch_date'))}</strong></div>
    </section>

    <section class=\"kpi-grid\" aria-label=\"领导层核心指标\">
      <article><span>质量健康分</span><strong>{_escape(report.get('quality_health_score'))}</strong><p>综合风险、覆盖、环境和证据可信度。</p></article>
      <article><span>阻断风险</span><strong>{_escape(_as_mapping(report.get('risk_summary')).get('launch_blocking'))}</strong><p>上线前必须修复、接受或降级处理。</p></article>
      <article><span>覆盖率</span><strong>{_escape(_as_mapping(report.get('coverage_summary')).get('coverage_rate'))}%</strong><p>核心业务链路已纳入 AI 测试。</p></article>
      <article><span>证据可信度</span><strong>{_escape(roi.get('evidence_trust_score'))}%</strong><p>证据链默认脱敏，复现信息完整。</p></article>
    </section>

    <section class=\"layout two-col\">
      <div>
        <div class=\"section-title\"><span>领导层报告</span><h2>执行摘要与上线决策</h2></div>
        {sections_html}
      </div>
      <aside class=\"copy-panel\">
        <div class=\"section-title\"><span>可复制摘要</span><h2>汇报材料</h2></div>
        <textarea readonly>{_escape(copy_blocks.get('meeting_note'))}</textarea>
        <button data-copy=\"meeting\">复制汇报摘要</button>
        <p class=\"safe-note\">默认脱敏：不展示 token / cookie / password / session / client_secret 原值。</p>
      </aside>
    </section>

    <section>
      <div class=\"section-title\"><span>ROI 价值中心</span><h2>节省工时、风险价值与计算说明</h2></div>
      <div class=\"roi-grid\">{roi_html}</div>
      <div class=\"notes\">
        <strong>计算说明</strong>
        <ul>{''.join(f'<li>{_escape(note)}</li>' for note in _as_list(roi.get('calculation_notes')))}</ul>
      </div>
    </section>

    <section>
      <div class=\"section-title\"><span>风险价值</span><h2>影响上线结论的 Top 风险</h2></div>
      <div class=\"risk-grid\">{risks_html}</div>
    </section>

    <section class=\"layout two-col\">
      <div>
        <div class=\"section-title\"><span>下一步动作</span><h2>修复、复测与签收队列</h2></div>
        <ol class=\"action-list\">{actions_html}</ol>
      </div>
      <aside class=\"api-panel\">
        <div class=\"section-title\"><span>Phase104 API 动作交接</span><h2>前端后续接入</h2></div>
        <code>GET /api/v1/projects/{{project_id}}/report</code>
        <code>GET /api/v1/projects/{{project_id}}/value</code>
        <code>GET /api/v1/projects/{{project_id}}/command-center</code>
        <code>GET /api/v1/projects/{{project_id}}/risks/{{risk_id}}</code>
      </aside>
    </section>
  </main>
  <script src=\"assets/qualibug_report_roi.js\"></script>
</body>
</html>
"""


def render_report_roi_css() -> str:
    return """
:root { color-scheme: dark; font-family: Inter, 'Microsoft YaHei', system-ui, sans-serif; background: #0b1120; color: #e5eefc; }
* { box-sizing: border-box; }
body { margin: 0; background: radial-gradient(circle at top left, #1d3b63 0, #0b1120 34%, #070b14 100%); }
a { color: inherit; text-decoration: none; }
.page { width: min(1440px, 96vw); margin: 0 auto; padding: 28px 0 48px; }
.hero { display: grid; grid-template-columns: 1.4fr 420px; gap: 24px; min-height: 260px; padding: 28px; border: 1px solid rgba(148,163,184,.22); border-radius: 32px; background: linear-gradient(135deg, rgba(15,23,42,.96), rgba(30,41,59,.8)); box-shadow: 0 30px 90px rgba(0,0,0,.35); }
.hero nav { grid-column: 1 / -1; display: flex; gap: 12px; flex-wrap: wrap; }
.hero nav a { padding: 9px 14px; border: 1px solid rgba(148,163,184,.24); border-radius: 999px; color: #cbd5e1; }
.eyebrow, .section-title span { color: #38bdf8; font-size: 13px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
h1 { margin: 10px 0; font-size: clamp(34px, 5vw, 66px); line-height: .98; }
h2 { margin: 6px 0 18px; font-size: 24px; }
h3 { margin: 0 0 10px; }
p { color: #b6c4d7; line-height: 1.72; }
.decision-card { padding: 24px; border-radius: 28px; background: linear-gradient(160deg, rgba(239,68,68,.22), rgba(15,23,42,.88)); border: 1px solid rgba(248,113,113,.35); align-self: end; }
.decision-card strong { display: block; font-size: 42px; margin: 12px 0; color: #fecaca; }
.project-strip, .kpi-grid, .roi-grid, .risk-grid { display: grid; gap: 16px; margin: 20px 0; }
.project-strip { grid-template-columns: repeat(4, 1fr); }
.project-strip div, .kpi-grid article, .roi-card, .risk-card, .report-section, .copy-panel, .api-panel, .notes { border: 1px solid rgba(148,163,184,.18); border-radius: 24px; background: rgba(15,23,42,.72); padding: 20px; }
.project-strip span, .kpi-grid span, .roi-card span { color: #94a3b8; font-size: 13px; }
.project-strip strong, .kpi-grid strong, .roi-card strong { display: block; margin-top: 8px; font-size: 28px; }
.kpi-grid { grid-template-columns: repeat(4, 1fr); }
.layout { display: grid; gap: 18px; margin: 28px 0; }
.two-col { grid-template-columns: minmax(0, 1.25fr) minmax(340px, .75fr); }
.roi-grid { grid-template-columns: repeat(4, 1fr); }
.risk-grid { grid-template-columns: repeat(3, 1fr); }
.risk-card { border-color: rgba(248,113,113,.28); }
.risk-card__head { display: flex; justify-content: space-between; gap: 12px; color: #94a3b8; margin-bottom: 12px; }
.pill { border-radius: 999px; padding: 5px 10px; font-size: 12px; font-weight: 800; }
.pill.danger { background: rgba(239,68,68,.18); color: #fecaca; }
.risk-card footer { display: flex; gap: 12px; color: #93c5fd; font-size: 13px; }
textarea { width: 100%; min-height: 220px; border: 1px solid rgba(148,163,184,.24); border-radius: 18px; padding: 14px; background: rgba(2,6,23,.62); color: #dbeafe; resize: vertical; line-height: 1.65; }
button { margin-top: 12px; border: 0; border-radius: 999px; padding: 12px 18px; background: #38bdf8; color: #082f49; font-weight: 800; cursor: pointer; }
.action-list { display: grid; gap: 12px; padding: 0; list-style: none; }
.action-item { display: flex; gap: 14px; padding: 16px; border: 1px solid rgba(148,163,184,.16); border-radius: 18px; background: rgba(2,6,23,.32); }
.step { flex: 0 0 34px; width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center; background: rgba(56,189,248,.18); color: #7dd3fc; font-weight: 900; }
.api-panel code { display: block; margin: 10px 0; padding: 12px; border-radius: 12px; background: rgba(2,6,23,.7); color: #bae6fd; white-space: pre-wrap; }
.safe-note { font-size: 13px; color: #86efac; }
@media (max-width: 1000px) { .hero, .two-col, .project-strip, .kpi-grid, .roi-grid, .risk-grid { grid-template-columns: 1fr; } }
""".strip() + "\n"


def render_report_roi_js() -> str:
    return """
(() => {
  const button = document.querySelector('[data-copy="meeting"]');
  const textarea = document.querySelector('textarea');
  if (button && textarea) {
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(textarea.value);
        button.textContent = '已复制汇报摘要';
      } catch (error) {
        textarea.select();
        button.textContent = '请手动复制摘要';
      }
    });
  }
})();
""".strip() + "\n"


def render_report_roi_readme(data: Mapping[str, Any]) -> str:
    project = _as_mapping(data.get("project"))
    decision = _as_mapping(data.get("decision_card"))
    return f"""# Phase105G 领导层报告 + ROI 价值中心

入口文件：`report_roi.html`

本页面用于把 AI 测试结果转换为管理层可读的上线决策材料：

- 上线建议：{_safe_text(decision.get('label'))}
- 执行摘要与可复制汇报摘要
- ROI 价值中心：节省工时、风险价值、证据可信度
- Top 风险业务影响
- 下一步动作、负责人建议和复验探针
- 默认脱敏：不展示 token、cookie、password、session、client_secret 原值

项目：{_safe_text(project.get('project_name'))}
客户：{_safe_text(project.get('customer_name'))}
系统：{_safe_text(project.get('system_name'))}
"""


def build_report_roi_experience(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    data = _extract_report_roi_view_model(scenario=scenario, api_base_url=api_base_url)
    html_text = render_report_roi_html(data)
    css_text = render_report_roi_css()
    js_text = render_report_roi_js()
    readme_text = render_report_roi_readme(data)

    _write_text(output / "report_roi.html", html_text)
    _write_text(output / "assets" / "qualibug_report_roi.css", css_text)
    _write_text(output / "assets" / "qualibug_report_roi.js", js_text)
    _write_text(output / "data" / "report_roi_experience_data.json", _json_dump(data))
    _write_text(output / "README_REPORT_ROI_EXPERIENCE.md", readme_text)

    leaks = scan_report_roi_for_secret_leaks(output)
    manifest = {
        "version": PHASE105G_VERSION,
        "generated_at": _now(),
        "scenario": scenario,
        "entrypoint": "report_roi.html",
        "api_base_url": api_base_url,
        "required_files": list(REQUIRED_REPORT_ROI_FILES),
        "core_labels": list(CORE_REPORT_ROI_LABELS),
        "quality_health_score": _as_mapping(data.get("executive_report")).get("quality_health_score"),
        "launch_recommendation": _as_mapping(data.get("decision_card")).get("recommendation"),
        "roi_card_count": len(_as_list(_as_mapping(data.get("roi_value_center")).get("roi_cards"))),
        "top_risk_count": len(_as_list(data.get("top_risks"))),
        "redaction_status": "safe" if not leaks else "leak_detected",
        "secret_leaks": leaks,
    }
    _write_text(output / REPORT_ROI_MANIFEST, _json_dump(manifest))
    return redact_value(manifest)


def scan_report_roi_for_secret_leaks(output_dir: str | Path) -> list[str]:
    output = Path(output_dir)
    leaks: list[str] = []
    if not output.exists():
        return [f"missing_output_dir:{output}"]
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js", ".json", ".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_REPORT_ROI_PATTERNS:
            if pattern in text:
                leaks.append(f"{path.relative_to(output)} contains forbidden pattern {pattern}")
    return leaks


def validate_report_roi_experience(output_dir: str | Path) -> ReportRoiExperienceAcceptanceReport:
    output = Path(output_dir)
    checks: list[ReportRoiExperienceCheck] = []

    def add(key: str, passed: bool, detail: str, severity: str = "critical") -> None:
        checks.append(ReportRoiExperienceCheck(key=key, passed=passed, detail=detail, severity=severity))

    missing = [name for name in REQUIRED_REPORT_ROI_FILES if not (output / name).exists()]
    add("required_files", not missing, "全部核心文件存在。" if not missing else f"缺少文件：{', '.join(missing)}")

    html_text = (output / "report_roi.html").read_text(encoding="utf-8", errors="ignore") if (output / "report_roi.html").exists() else ""
    missing_labels = [label for label in CORE_REPORT_ROI_LABELS if label not in html_text]
    add("core_labels", not missing_labels, "领导层报告与 ROI 核心标签完整。" if not missing_labels else f"缺少标签：{', '.join(missing_labels)}")

    data_path = output / "data" / "report_roi_experience_data.json"
    data: dict[str, Any] = {}
    if data_path.exists():
        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
            add("data_json", True, "report_roi_experience_data.json 可解析。")
        except json.JSONDecodeError as exc:
            add("data_json", False, f"JSON 解析失败：{exc}")
    else:
        add("data_json", False, "缺少 report_roi_experience_data.json")

    decision = _as_mapping(data.get("decision_card"))
    report = _as_mapping(data.get("executive_report"))
    roi = _as_mapping(data.get("roi_value_center"))
    add("decision_card", bool(decision.get("label") and decision.get("summary")), "上线建议卡片包含结论和原因。" if decision.get("label") and decision.get("summary") else "上线建议卡片不完整。")
    add("report_sections", len(_as_list(report.get("report_sections"))) >= 4, "执行摘要、上线建议、AI 价值和证据可信度段落完整。" if len(_as_list(report.get("report_sections"))) >= 4 else "报告段落不足。")
    add("roi_cards", len(_as_list(roi.get("roi_cards"))) >= 4, "ROI 价值卡片完整。" if len(_as_list(roi.get("roi_cards"))) >= 4 else "ROI 卡片不足。")
    add("top_risks", len(_as_list(data.get("top_risks"))) >= 1, "Top 风险业务影响可展示。" if _as_list(data.get("top_risks")) else "缺少 Top 风险。")
    add("next_actions", len(_as_list(data.get("next_actions"))) >= 1, "下一步动作队列可展示。" if _as_list(data.get("next_actions")) else "缺少下一步动作。")
    add("copy_blocks", bool(_as_mapping(data.get("copy_blocks")).get("meeting_note")), "可复制摘要已生成。" if _as_mapping(data.get("copy_blocks")).get("meeting_note") else "缺少可复制摘要。")

    leaks = scan_report_roi_for_secret_leaks(output)
    add("secret_scan", not leaks, "未发现原始凭证或 traceback 泄露。" if not leaks else "; ".join(leaks))

    passed = all(check.passed for check in checks)
    score = int(round(sum(1 for check in checks if check.passed) / max(len(checks), 1) * 100))
    return ReportRoiExperienceAcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE105G_VERSION,
        scenario=_safe_text(data.get("scenario"), "unknown"),
        output_dir=str(output),
        checks=checks,
        artifacts={"entrypoint": str(output / "report_roi.html"), "manifest": str(output / REPORT_ROI_MANIFEST)},
    )


def write_report_roi_acceptance_report(report: ReportRoiExperienceAcceptanceReport, output_dir: str | Path) -> None:
    output = Path(output_dir)
    _write_text(output / REPORT_ROI_ACCEPTANCE_JSON, _json_dump(report.to_dict()))
    rows = "\n".join(
        f"| {check.key} | {'PASS' if check.passed else 'FAIL'} | {check.detail} |" for check in report.checks
    )
    md = f"""# Phase105G 领导层报告 + ROI 价值中心验收报告

- 结果：{'通过' if report.passed else '未通过'}
- 分数：{report.score}
- 场景：{report.scenario}
- 输出目录：`{report.output_dir}`

| 检查项 | 状态 | 说明 |
|---|---:|---|
{rows}
"""
    _write_text(output / REPORT_ROI_ACCEPTANCE_MD, md)


def run_report_roi_experience_export(
    *,
    output_dir: str | Path,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
    validate_only: bool = False,
) -> dict[str, Any]:
    if not validate_only:
        manifest = build_report_roi_experience(output_dir, scenario=scenario, api_base_url=api_base_url)
    else:
        manifest_path = Path(output_dir) / REPORT_ROI_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    report = validate_report_roi_experience(output_dir)
    write_report_roi_acceptance_report(report, output_dir)
    return {"manifest": redact_value(manifest), "acceptance": report.to_dict()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Phase105G executive report and ROI frontend experience.")
    parser.add_argument("--scenario", default="manufacturing", choices=("manufacturing", "ecommerce", "saas"))
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--output-dir", default="outputs/phase105_report_roi_experience")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    result = run_report_roi_experience_export(
        output_dir=args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(_json_dump(result))
    return 0 if result["acceptance"].get("passed") else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

