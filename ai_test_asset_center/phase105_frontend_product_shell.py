from __future__ import annotations

"""Phase105A: frontend product shell generator for QualiBug Command Center.

Phase104 proved that the backend API, API contract, frontend workspace,
runtime smoke, handoff bundle, release readiness ledger, and CI gate can be
produced and verified.  Phase105 shifts the priority to the product display
layer: a customer-facing, framework-neutral frontend shell that makes the V1
quality platform understandable before a full React/Vue implementation exists.

The generated shell is a static single-page product prototype backed by real
Phase104 demo API data.  It focuses on the pages defined by the Phase103 PRD:
quality dashboard, customer intake, environment diagnosis, business flow map,
AI test plan, execution timeline, risks, evidence detail, executive report,
ROI value center, and settings.

Security posture:
* all data comes through existing redaction helpers;
* no raw token/cookie/session/client secret examples are written;
* validation scans generated HTML/CSS/JS/JSON/MD before declaring success.
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

PHASE105A_VERSION = "phase105a-frontend-product-shell-v1"

PRODUCT_SHELL_MANIFEST = "product_shell_manifest.json"
PRODUCT_SHELL_ACCEPTANCE_JSON = "product_shell_acceptance_report.json"
PRODUCT_SHELL_ACCEPTANCE_MD = "product_shell_acceptance_report.md"

PAGE_NAV: tuple[dict[str, str], ...] = (
    {"id": "dashboard", "label": "质量驾驶舱", "intent": "领导 30 秒看懂上线风险、质量态势和价值"},
    {"id": "customer-intake", "label": "客户资料导入", "intent": "把企业资料变成业务模型、环境清单和测试计划输入"},
    {"id": "environment", "label": "环境诊断中心", "intent": "说明客户环境是否可测、哪里阻断、下一步补什么"},
    {"id": "business-map", "label": "业务流程地图", "intent": "用业务链路展示 AI 已理解的系统范围和风险爆点"},
    {"id": "test-plan", "label": "AI 测试计划", "intent": "展示准备测什么、能测什么、不能测什么"},
    {"id": "test-execution", "label": "实时测试执行", "intent": "让客户看到 AI 正在沿业务链路执行并回流证据"},
    {"id": "risks", "label": "风险与 Bug 列表", "intent": "把技术 Bug 翻译成业务风险卡片"},
    {"id": "evidence", "label": "证据链详情", "intent": "证明风险真实、可复现、可修复、可验证"},
    {"id": "executive-report", "label": "领导层报告", "intent": "把测试结果转成上线建议和管理层摘要"},
    {"id": "roi", "label": "ROI 价值中心", "intent": "用保守指标证明 AI 测试创造的价值"},
    {"id": "settings", "label": "系统设置", "intent": "管理安全边界、脱敏状态和联调 API 地址"},
)

REQUIRED_PRODUCT_SHELL_FILES: tuple[str, ...] = (
    "index.html",
    "README_PRODUCT_SHELL.md",
    "data/product_shell_data.json",
    "assets/qualibug_product_shell.css",
    "assets/qualibug_product_shell.js",
    PRODUCT_SHELL_MANIFEST,
)

FORBIDDEN_PRODUCT_SHELL_PATTERNS: tuple[str, ...] = (
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


@dataclass(frozen=True)
class ProductShellCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class ProductShellAcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    checks: list[ProductShellCheck] = field(default_factory=list)
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


def _first_mapping(items: Any) -> dict[str, Any]:
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) and items:
        first = items[0]
        return dict(first) if isinstance(first, Mapping) else {}
    return {}


def _api_data(app: Phase104CommandCenterHttpApp, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
    payload = json.dumps(body or {}, ensure_ascii=False) if body is not None else None
    response = app.handle(method, path, payload)
    envelope = response.json_body()
    if not envelope.get("success"):
        raise RuntimeError(f"API call failed for {method} {path}: {envelope.get('error')}")
    return envelope.get("data")


def collect_product_shell_demo_data(scenario: str = "manufacturing", api_base_url: str = "http://127.0.0.1:8088") -> dict[str, Any]:
    """Collect redacted real demo API data for the product display shell."""
    app = Phase104CommandCenterHttpApp(seed_scenario=scenario)
    health = _api_data(app, "GET", "/api/v1/health")
    projects = _api_data(app, "GET", "/api/v1/projects")
    if not projects:
        raise RuntimeError("seeded Phase104 API did not create a demo project")
    project = dict(projects[0])
    project_id = str(project["project_id"])

    dashboard = _api_data(app, "GET", f"/api/v1/projects/{project_id}/command-center")
    environment = _api_data(app, "GET", f"/api/v1/projects/{project_id}/environment/readiness")
    test_plan = _api_data(app, "GET", f"/api/v1/projects/{project_id}/test-plan")
    live_map = _api_data(app, "GET", f"/api/v1/projects/{project_id}/live-map")
    risks = _api_data(app, "GET", f"/api/v1/projects/{project_id}/risks")
    value_metrics = _api_data(app, "GET", f"/api/v1/projects/{project_id}/value-metrics")
    try:
        executive_report = _api_data(app, "GET", f"/api/v1/projects/{project_id}/reports/executive")
    except RuntimeError:
        executive_report = _api_data(app, "POST", f"/api/v1/projects/{project_id}/reports/generate", {})

    first_risk = _first_mapping(risks)
    risk_detail: dict[str, Any] = {}
    if first_risk.get("risk_id"):
        risk_detail = _api_data(app, "GET", f"/api/v1/projects/{project_id}/risks/{first_risk['risk_id']}")

    onboarding_steps = [
        {"id": "create_project", "label": "创建项目", "status": "done", "description": "已识别客户、系统、行业和上线目标。"},
        {"id": "business_model", "label": "业务建模", "status": "done", "description": "已生成核心业务链路、角色矩阵和风险重点。"},
        {"id": "environment", "label": "环境诊断", "status": "warning" if environment.get("status") != "ready" else "done", "description": "已完成 URL、认证、API smoke 与补料清单诊断。"},
        {"id": "test_plan", "label": "测试计划", "status": "done", "description": "已区分可执行探针、阻断探针和客户需补充资料。"},
        {"id": "execution", "label": "AI 执行", "status": "done", "description": "已产生风险、证据链、ROI 和领导层报告。"},
    ]

    return redact_value(
        {
            "version": PHASE105A_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "api_base_url": api_base_url.rstrip("/"),
            "health": health,
            "project": project,
            "page_nav": list(PAGE_NAV),
            "onboarding_steps": onboarding_steps,
            "dashboard": dashboard,
            "environment": environment,
            "test_plan": test_plan,
            "live_map": live_map,
            "risks": risks,
            "risk_detail": risk_detail,
            "value_metrics": value_metrics,
            "executive_report": executive_report,
            "display_principles": [
                "客户先看到上线风险和业务影响，再看技术细节。",
                "环境问题必须解释阻断原因和客户下一步动作。",
                "风险卡必须用业务语言表达，并保留证据链入口。",
                "前端默认只展示脱敏状态、摘要和可复验结论。",
            ],
        }
    )


def _render_nav_links() -> str:
    return "\n".join(
        f'<button class="qb-nav-item" data-page="{html.escape(item["id"])}"><span>{html.escape(item["label"])}</span><small>{html.escape(item["intent"])}</small></button>'
        for item in PAGE_NAV
    )


def _render_page_sections() -> str:
    sections = [
        ("dashboard", "质量驾驶舱", "领导 30 秒内看懂质量态势、上线建议、关键风险和 AI 价值。"),
        ("customer-intake", "客户资料导入", "上传企业资料、录入系统信息、选择行业，并启动 AI 分析。"),
        ("environment", "环境诊断中心", "诊断客户环境是否可访问、可认证、可执行和可验证。"),
        ("business-map", "业务流程地图", "展示业务链路、节点覆盖、风险爆点和证据回流。"),
        ("test-plan", "AI 测试计划", "说明准备测什么、能测什么、不能测什么。"),
        ("test-execution", "实时测试执行", "展示 AI 执行阶段、事件时间线和风险刷新。"),
        ("risks", "风险与 Bug 列表", "把技术 Bug 翻译成业务风险卡片。"),
        ("evidence", "证据链详情", "证明风险真实、可复现、可修复、可验证。"),
        ("executive-report", "领导层报告", "自动生成领导能看、能汇报、能决策的成果报告。"),
        ("roi", "ROI 价值中心", "用保守可信指标证明 AI 测试创造的价值。"),
        ("settings", "系统设置", "展示联调 API、脱敏模式、安全边界和页面状态。"),
    ]
    return "\n".join(
        f"""
        <section class="qb-page" id="page-{page_id}" data-page="{page_id}">
          <div class="qb-page-header">
            <p class="qb-eyebrow">QualiBug Phase105A</p>
            <h2>{html.escape(title)}</h2>
            <p>{html.escape(desc)}</p>
          </div>
          <div class="qb-page-body" data-render-target="{page_id}"></div>
        </section>
        """.strip()
        for page_id, title, desc in sections
    )


def render_index_html() -> str:
    nav = _render_nav_links()
    sections = _render_page_sections()
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>QualiBug AI 企业质量指挥中心 · Phase105A</title>
  <link rel="stylesheet" href="assets/qualibug_product_shell.css" />
</head>
<body>
  <div class="qb-app-shell">
    <aside class="qb-sidebar">
      <div class="qb-brand">
        <div class="qb-logo">QB</div>
        <div>
          <strong>QualiBug AI</strong>
          <span>企业质量指挥中心</span>
        </div>
      </div>
      <nav class="qb-nav" aria-label="主导航">
        {nav}
      </nav>
    </aside>
    <main class="qb-main">
      <header class="qb-topbar">
        <div>
          <span class="qb-chip">Phase105A Frontend Product Shell</span>
          <h1>前端显示层 MVP</h1>
        </div>
        <div class="qb-project-switcher">
          <span id="projectName">加载项目中...</span>
          <strong id="launchDecision">—</strong>
        </div>
      </header>
      <section class="qb-hero" id="heroMetrics" aria-label="关键质量指标"></section>
      <div class="qb-stage-flow" id="stageFlow"></div>
      {sections}
    </main>
  </div>
  <script src="assets/qualibug_product_shell.js"></script>
</body>
</html>
"""


def render_product_shell_css() -> str:
    return """:root {
  --qb-bg: #0b1020;
  --qb-bg-soft: #101936;
  --qb-panel: #ffffff;
  --qb-panel-soft: #f6f8fc;
  --qb-text: #14213d;
  --qb-muted: #637083;
  --qb-line: #dce4f0;
  --qb-blue: #246bfe;
  --qb-green: #1f9d63;
  --qb-orange: #f59e0b;
  --qb-red: #e5484d;
  --qb-purple: #7c3aed;
  --qb-shadow: 0 22px 50px rgba(20, 33, 61, 0.12);
  font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

* { box-sizing: border-box; }
body { margin: 0; background: #eef3fb; color: var(--qb-text); }
button { font: inherit; }
.qb-app-shell { min-height: 100vh; display: grid; grid-template-columns: 300px 1fr; }
.qb-sidebar { background: linear-gradient(180deg, var(--qb-bg), #121b3c); color: #fff; padding: 24px 18px; position: sticky; top: 0; height: 100vh; overflow: auto; }
.qb-brand { display: flex; gap: 14px; align-items: center; margin-bottom: 26px; }
.qb-logo { width: 46px; height: 46px; border-radius: 16px; display: grid; place-items: center; font-weight: 800; background: linear-gradient(135deg, #60a5fa, #a78bfa); }
.qb-brand strong { display: block; font-size: 18px; }
.qb-brand span { display: block; color: #b8c3d9; font-size: 13px; margin-top: 2px; }
.qb-nav { display: flex; flex-direction: column; gap: 8px; }
.qb-nav-item { width: 100%; border: 1px solid rgba(255,255,255,.08); background: rgba(255,255,255,.04); color: #e9eefb; border-radius: 16px; padding: 12px 14px; text-align: left; cursor: pointer; transition: .18s ease; }
.qb-nav-item span { display: block; font-weight: 700; }
.qb-nav-item small { display: block; margin-top: 4px; color: #aebbd1; line-height: 1.45; }
.qb-nav-item:hover, .qb-nav-item.active { background: rgba(96,165,250,.18); border-color: rgba(147,197,253,.58); transform: translateX(2px); }
.qb-main { padding: 28px 34px 56px; overflow: hidden; }
.qb-topbar { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin-bottom: 22px; }
.qb-chip { display: inline-flex; align-items: center; border-radius: 999px; padding: 6px 12px; background: #dbeafe; color: #1d4ed8; font-size: 12px; font-weight: 700; }
.qb-topbar h1 { margin: 10px 0 0; font-size: 32px; letter-spacing: -0.03em; }
.qb-project-switcher { min-width: 280px; background: var(--qb-panel); border: 1px solid var(--qb-line); box-shadow: var(--qb-shadow); border-radius: 20px; padding: 16px; text-align: right; }
.qb-project-switcher span { display: block; color: var(--qb-muted); font-size: 13px; }
.qb-project-switcher strong { display: block; margin-top: 6px; font-size: 22px; }
.qb-hero { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 14px; margin-bottom: 18px; }
.qb-metric-card { background: var(--qb-panel); border: 1px solid var(--qb-line); border-radius: 22px; padding: 18px; box-shadow: var(--qb-shadow); min-height: 126px; }
.qb-metric-card small { color: var(--qb-muted); font-weight: 700; }
.qb-metric-card strong { display: block; margin-top: 12px; font-size: 30px; line-height: 1; }
.qb-metric-card p { color: var(--qb-muted); margin: 12px 0 0; font-size: 13px; line-height: 1.5; }
.qb-stage-flow { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin: 18px 0 22px; }
.qb-stage { background: rgba(255,255,255,.72); border: 1px solid var(--qb-line); border-radius: 18px; padding: 14px; }
.qb-stage strong { display: block; }
.qb-stage small { display: block; margin-top: 5px; color: var(--qb-muted); line-height: 1.45; }
.qb-stage::before { content: ""; display: block; width: 10px; height: 10px; border-radius: 999px; background: var(--qb-blue); margin-bottom: 10px; }
.qb-stage.warning::before { background: var(--qb-orange); }
.qb-page { display: none; background: var(--qb-panel); border: 1px solid var(--qb-line); box-shadow: var(--qb-shadow); border-radius: 28px; padding: 24px; margin-top: 18px; }
.qb-page.active { display: block; }
.qb-page-header { border-bottom: 1px solid var(--qb-line); padding-bottom: 18px; margin-bottom: 20px; }
.qb-eyebrow { margin: 0 0 8px; color: var(--qb-blue); font-weight: 800; letter-spacing: .08em; text-transform: uppercase; font-size: 12px; }
.qb-page h2 { margin: 0; font-size: 26px; letter-spacing: -0.02em; }
.qb-page-header p:last-child { color: var(--qb-muted); margin-bottom: 0; }
.qb-grid { display: grid; gap: 16px; }
.qb-grid.two { grid-template-columns: 1.2fr .8fr; }
.qb-grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.qb-panel { background: var(--qb-panel-soft); border: 1px solid var(--qb-line); border-radius: 20px; padding: 18px; }
.qb-panel h3 { margin: 0 0 12px; font-size: 18px; }
.qb-panel p, .qb-panel li { color: var(--qb-muted); line-height: 1.65; }
.qb-list { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }
.qb-list li { background: #fff; border: 1px solid var(--qb-line); border-radius: 14px; padding: 12px; }
.qb-risk-card { background: #fff; border: 1px solid var(--qb-line); border-left: 5px solid var(--qb-red); border-radius: 18px; padding: 16px; margin-bottom: 12px; }
.qb-risk-card h3 { margin: 0 0 8px; }
.qb-risk-meta { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.qb-tag { display: inline-flex; border-radius: 999px; padding: 5px 9px; font-size: 12px; font-weight: 800; background: #eef2ff; color: #3730a3; }
.qb-tag.red { background: #fee2e2; color: #b91c1c; }
.qb-tag.green { background: #dcfce7; color: #166534; }
.qb-tag.orange { background: #fef3c7; color: #92400e; }
.qb-flow-map { display: grid; gap: 12px; }
.qb-flow-node { background: #fff; border: 1px solid var(--qb-line); border-radius: 16px; padding: 14px; display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: center; }
.qb-flow-node.risk { border-color: rgba(229,72,77,.45); background: #fff7f7; }
.qb-timeline { border-left: 3px solid #bfdbfe; padding-left: 18px; display: grid; gap: 14px; }
.qb-event { position: relative; background: #fff; border: 1px solid var(--qb-line); border-radius: 16px; padding: 13px; }
.qb-event::before { content: ""; position: absolute; left: -27px; top: 18px; width: 12px; height: 12px; border-radius: 50%; background: var(--qb-blue); box-shadow: 0 0 0 5px #dbeafe; }
.qb-code { white-space: pre-wrap; background: #0f172a; color: #dbeafe; border-radius: 16px; padding: 16px; overflow: auto; font-size: 13px; line-height: 1.6; }
.qb-empty { color: var(--qb-muted); padding: 18px; border: 1px dashed var(--qb-line); border-radius: 16px; }
@media (max-width: 1120px) { .qb-app-shell { grid-template-columns: 1fr; } .qb-sidebar { position: relative; height: auto; } .qb-hero, .qb-stage-flow, .qb-grid.two, .qb-grid.three { grid-template-columns: 1fr; } .qb-topbar { flex-direction: column; } .qb-project-switcher { width: 100%; text-align: left; } }
"""


def render_product_shell_js() -> str:
    return r"""const state = { data: null, page: "dashboard" };

const esc = (value) => String(value ?? "—").replace(/[&<>'"]/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[ch]));
const pct = (value) => value == null ? "—" : `${Math.round(Number(value) * 100)}%`;
const money = (value, currency = "CNY") => value == null ? "—" : `${currency} ${Number(value).toLocaleString("zh-CN")}`;
const score = (value) => value == null ? "—" : `${Math.round(Number(value))}`;

function setPage(page) {
  state.page = page;
  document.querySelectorAll(".qb-nav-item").forEach((item) => item.classList.toggle("active", item.dataset.page === page));
  document.querySelectorAll(".qb-page").forEach((item) => item.classList.toggle("active", item.dataset.page === page));
}

function metric(label, value, help) {
  return `<article class="qb-metric-card"><small>${esc(label)}</small><strong>${esc(value)}</strong><p>${esc(help)}</p></article>`;
}

function panel(title, body) {
  return `<section class="qb-panel"><h3>${esc(title)}</h3>${body}</section>`;
}

function list(items, empty = "暂无数据") {
  if (!items || !items.length) return `<div class="qb-empty">${esc(empty)}</div>`;
  return `<ul class="qb-list">${items.map((item) => `<li>${item}</li>`).join("")}</ul>`;
}

function renderHero(data) {
  const d = data.dashboard || {};
  const env = data.environment || {};
  const value = data.value_metrics || {};
  const riskSummary = d.risk_summary || {};
  document.getElementById("projectName").textContent = `${data.project?.customer_name || "客户"} · ${data.project?.system_name || data.project?.project_name || "项目"}`;
  document.getElementById("launchDecision").textContent = d.launch_decision?.title || d.launch_decision?.recommendation || "待评估";
  document.getElementById("heroMetrics").innerHTML = [
    metric("质量健康分", score(d.quality_health_score), "综合环境、覆盖、风险和证据可信度。"),
    metric("上线建议", d.launch_decision?.recommendation || "—", d.launch_decision?.summary || "等待生成上线建议。"),
    metric("高危 / 阻断风险", riskSummary.launch_blocking ?? value.launch_blocking_risks ?? 0, "优先处理会影响上线决策的风险。"),
    metric("核心链路覆盖", pct(d.business_flow_summary?.coverage_rate), "用业务链路而不是单纯用例数解释覆盖。"),
    metric("预计节省工时", `${value.estimated_hours_saved ?? "—"}h`, "保守估算 AI 等价测试点带来的人工节省。"),
  ].join("");
  document.getElementById("stageFlow").innerHTML = (data.onboarding_steps || []).map((step) => `
    <div class="qb-stage ${esc(step.status)}"><strong>${esc(step.label)}</strong><small>${esc(step.description)}</small></div>
  `).join("");
}

function renderDashboard(data) {
  const d = data.dashboard || {};
  const topRisks = d.top_risks || [];
  return `<div class="qb-grid two">
    ${panel("执行摘要", `<p>${esc(d.executive_summary)}</p><p><strong>${esc(d.launch_decision?.title || "待决策")}</strong>：${esc(d.launch_decision?.summary)}</p>`)}
    ${panel("业务覆盖", list([
      `覆盖链路：${esc(d.business_flow_summary?.covered)} / ${esc(d.business_flow_summary?.total)}`,
      `覆盖率：${pct(d.business_flow_summary?.coverage_rate)}`,
      `带风险链路：${esc(d.business_flow_summary?.covered_with_risk)}`,
      `环境状态：${esc(d.environment_readiness?.status)} · ${esc(d.environment_readiness?.score)}分`,
    ]))}
    ${panel("Top 风险", topRisks.map(renderRiskCard).join("") || `<div class="qb-empty">暂无风险</div>`)}
    ${panel("下一步动作", list((d.launch_decision?.required_actions || []).map(esc), "暂无下一步动作"))}
  </div>`;
}

function renderCustomerIntake(data) {
  const project = data.project || {};
  return `<div class="qb-grid two">
    ${panel("资料导入入口", `<p>该页面是 V1 产品入口：客户资料 → 行业理解 → 业务建模 → 环境适配 → AI 测试计划。</p>${list([
      `客户名称：${esc(project.customer_name)}`,
      `系统名称：${esc(project.system_name || project.project_name)}`,
      `行业场景：${esc(data.scenario)}`,
      `测试目标：上线前质量风险识别、证据生成和上线决策`,
    ])}`)}
    ${panel("建议上传资料", list([
      "PRD / 业务流程说明 / 流程图",
      "OpenAPI / Postman / 接口清单",
      "角色与账号矩阵",
      "环境 URL、认证方式、只读测试边界",
      "上线计划、核心链路、不可影响的数据范围",
    ].map(esc)))}
  </div>`;
}

function renderEnvironment(data) {
  const env = data.environment || {};
  const checks = env.checks || {};
  return `<div class="qb-grid two">
    ${panel("环境可测性", list([
      `状态：${esc(env.status)}`,
      `评分：${esc(env.score)} / 100`,
      `允许正式测试：${esc(env.allow_formal_test)}`,
      `安全执行模式：${esc(env.safe_execution_mode)}`,
      `脱敏状态：${esc(env.redaction_status)}`,
    ]))}
    ${panel("当前阻断与补料", list([...(env.current_blockers || []), ...(env.suggested_actions || [])].map(esc), "暂无阻断"))}
    ${panel("检查项摘要", list(Object.entries(checks).map(([key, value]) => `${esc(key)}：${esc(value?.status || value?.result || "已检查")}`), "暂无检查项"))}
    ${panel("客户需要补充", list((env.required_customer_inputs || []).map((item) => `${esc(item.name || item.input_key || item.type)}：${esc(item.reason || item.description || "待补充")}`), "暂无需补充资料"))}
  </div>`;
}

function renderBusinessMap(data) {
  const nodes = data.live_map?.nodes || [];
  const overlays = data.live_map?.risk_overlays || [];
  return `<div class="qb-grid two">
    ${panel("业务节点", `<div class="qb-flow-map">${nodes.slice(0, 14).map((node) => {
      const risk = overlays.some((item) => item.node_id === node.node_id || item.business_flow_id === node.business_flow_id);
      return `<div class="qb-flow-node ${risk ? "risk" : ""}"><div><strong>${esc(node.label || node.name || node.node_id)}</strong><p>${esc(node.business_flow_name || node.business_flow_id || "业务节点")}</p></div><span class="qb-tag ${risk ? "red" : "green"}">${risk ? "有风险" : esc(node.status || "已覆盖")}</span></div>`;
    }).join("")}</div>`)}
    ${panel("风险爆点", list(overlays.map((item) => `${esc(item.title || item.risk_title)} · ${esc(item.severity)} · ${esc(item.business_flow_name || item.business_flow_id)}`), "暂无风险爆点"))}
  </div>`;
}

function renderTestPlan(data) {
  const plan = data.test_plan || {};
  const groups = plan.probe_groups || [];
  return `<div class="qb-grid two">
    ${panel("计划概览", list([
      `计划名称：${esc(plan.name)}`,
      `业务链路总数：${esc(plan.coverage_summary?.business_flow_total)}`,
      `可执行链路：${esc(plan.coverage_summary?.business_flow_executable)}`,
      `阻断链路：${esc(plan.coverage_summary?.business_flow_blocked)}`,
      `预计测试点：${esc(plan.estimated_value?.equivalent_test_points)}`,
    ]))}
    ${panel("探针分组", list(groups.map((group) => `${esc(group.business_flow_name)}：可执行 ${esc(group.probe_executable)} / 总计 ${esc(group.probe_total)}，阻断 ${esc(group.probe_blocked)}`), "暂无探针分组"))}
  </div>`;
}

function renderExecution(data) {
  const events = data.live_map?.events || [];
  return `<div class="qb-grid two">
    ${panel("执行状态", list([
      `运行 ID：${esc(data.live_map?.run_id)}`,
      `事件数量：${esc(events.length)}`,
      `风险覆盖：${esc((data.live_map?.risk_overlays || []).length)}`,
      "模式：前端显示层读取 Phase104 API 数据，不直接暴露敏感原文。",
    ]))}
    ${panel("事件时间线", `<div class="qb-timeline">${events.slice(0, 12).map((event) => `<div class="qb-event"><strong>${esc(event.title || event.event_type || event.type)}</strong><p>${esc(event.description || event.message || event.status || "AI 测试事件")}</p></div>`).join("") || `<div class="qb-empty">暂无事件</div>`}</div>`)}
  </div>`;
}

function renderRiskCard(risk) {
  return `<article class="qb-risk-card">
    <div class="qb-risk-meta"><span class="qb-tag red">${esc(risk.severity)}</span><span class="qb-tag ${risk.launch_blocking ? "orange" : "green"}">${risk.launch_blocking ? "阻断上线" : "非阻断"}</span><span class="qb-tag">${esc(risk.status)}</span></div>
    <h3>${esc(risk.title)}</h3>
    <p>${esc(risk.business_impact)}</p>
    <p><strong>建议：</strong>${esc(risk.suggested_action)}</p>
  </article>`;
}

function renderRisks(data) {
  const risks = data.risks || [];
  return `<div class="qb-grid two">
    ${panel("风险卡片", risks.map(renderRiskCard).join("") || `<div class="qb-empty">暂无风险</div>`)}
    ${panel("筛选建议", list([
      "默认按 severity + launch_blocking 排序。",
      "列表优先展示业务影响、阻断上线、证据完整度。",
      "进入详情页后再展示请求/响应摘要和复现步骤。",
    ].map(esc)))}
  </div>`;
}

function renderEvidence(data) {
  const detail = data.risk_detail || {};
  const risk = detail.risk || {};
  const evidence = detail.evidence_bundle || {};
  return `<div class="qb-grid two">
    ${panel("风险证明", `<h3>${esc(risk.title)}</h3><p>${esc(risk.business_impact)}</p>${list([
      `证据评分：${esc(risk.evidence_score)}`,
      `复现稳定性：${esc(risk.reproducibility_score)}`,
      `脱敏状态：${esc(evidence.redaction_status)}`,
      `证据 ID：${esc(evidence.evidence_id || evidence.bundle_id)}`,
    ])}`)}
    ${panel("复现与修复", list([
      ...(evidence.reproduction_steps || []).map((item) => `复现：${esc(item)}`),
      `修复建议：${esc(risk.suggested_action)}`,
      "关闭条件：修复后执行关联回归探针，并保留新证据链。",
    ], "暂无证据链"))}
  </div>`;
}

function renderExecutiveReport(data) {
  const report = data.executive_report || {};
  return `<div class="qb-grid two">
    ${panel(report.title || "上线质量风险评估报告", `<p>${esc(report.executive_summary)}</p><p><strong>上线建议：</strong>${esc(report.launch_recommendation)}</p>`)}
    ${panel("管理层摘要", list([
      `质量健康分：${esc(report.quality_health_score)}`,
      `Top 风险数：${esc((report.top_risks || []).length)}`,
      `下一步动作数：${esc((report.next_actions || []).length)}`,
      `证据可信：${esc(report.evidence_trust_summary?.statement)}`,
    ]))}
    ${panel("下一步建议", list((report.next_actions || []).map(esc), "暂无下一步建议"))}
    ${panel("可复制 Markdown 摘要", `<pre class="qb-code">${esc((report.markdown || "").slice(0, 1800))}</pre>`)}
  </div>`;
}

function renderRoi(data) {
  const value = data.value_metrics || {};
  return `<div class="qb-grid three">
    ${metric("AI 等价测试点", value.ai_equivalent_test_points, "用于保守估算人工测试替代工作量。")}
    ${metric("预计节省工时", `${value.estimated_hours_saved ?? "—"}h`, "按单个测试点平均耗时估算。")}
    ${metric("潜在影响区间", `${money(value.estimated_business_impact_min, value.currency)} - ${money(value.estimated_business_impact_max, value.currency)}`, "风险暴露估算，不代表确定收益。")}
    ${panel("计算说明", list((value.calculation_notes || []).map(esc), "暂无计算说明"))}
  </div>`;
}

function renderSettings(data) {
  return `<div class="qb-grid two">
    ${panel("联调设置", list([
      `API Base URL：${esc(data.api_base_url)}`,
      `场景：${esc(data.scenario)}`,
      `版本：${esc(data.version)}`,
      `生成时间：${esc(data.generated_at)}`,
    ]))}
    ${panel("安全显示原则", list((data.display_principles || []).map(esc)))}
  </div>`;
}

const renderers = {
  "dashboard": renderDashboard,
  "customer-intake": renderCustomerIntake,
  "environment": renderEnvironment,
  "business-map": renderBusinessMap,
  "test-plan": renderTestPlan,
  "test-execution": renderExecution,
  "risks": renderRisks,
  "evidence": renderEvidence,
  "executive-report": renderExecutiveReport,
  "roi": renderRoi,
  "settings": renderSettings,
};

async function boot() {
  const response = await fetch("data/product_shell_data.json", { cache: "no-store" });
  state.data = await response.json();
  renderHero(state.data);
  Object.entries(renderers).forEach(([page, renderer]) => {
    const target = document.querySelector(`[data-render-target="${page}"]`);
    if (target) target.innerHTML = renderer(state.data);
  });
  document.querySelectorAll(".qb-nav-item").forEach((item) => item.addEventListener("click", () => setPage(item.dataset.page)));
  setPage("dashboard");
}

boot().catch((error) => {
  document.body.innerHTML = `<main style="padding:24px;font-family:sans-serif"><h1>产品壳加载失败</h1><pre>${esc(error.message)}</pre></main>`;
});
"""


def render_readme(data: Mapping[str, Any]) -> str:
    pages = "\n".join(f"- {item['label']}：{item['intent']}" for item in PAGE_NAV)
    return f"""# Phase105A 前端产品壳

Phase105A 将开发重点切到前端显示层。它生成一个可直接打开的企业级产品壳，用于验证 QualiBug AI 企业质量指挥中心的页面信息架构、导航、指标展示、风险卡、证据链和领导层报告表达。

## 定位

- 让客户和领导一眼看懂质量风险、上线建议和 AI 价值。
- 让前端团队有一个真实数据驱动的页面骨架，而不是只拿 API 文档。
- 让后端 Phase104 API 能力被包装成清晰的产品界面。

## 页面范围

{pages}

## 本地打开

```powershell
python -m ai_test_asset_center.phase105_frontend_product_shell --output-dir .\\outputs\\phase105_frontend_product_shell
Start-Process .\\outputs\\phase105_frontend_product_shell\\index.html
```

## 当前演示数据

- 场景：{data.get('scenario')}
- 客户：{_safe_text(data.get('project', {}).get('customer_name'))}
- 系统：{_safe_text(data.get('project', {}).get('system_name') or data.get('project', {}).get('project_name'))}
- 上线建议：{_safe_text(data.get('dashboard', {}).get('launch_decision', {}).get('recommendation'))}
- 脱敏策略：前端只展示状态、摘要、证据评分、响应类型和已脱敏内容，不展示原始敏感凭证。
"""


def scan_product_shell_for_secret_leaks(base: Path) -> list[str]:
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
        for pattern in FORBIDDEN_PRODUCT_SHELL_PATTERNS:
            if pattern.lower() in lowered:
                findings.append(f"{path.relative_to(base).as_posix()}: contains forbidden pattern {pattern}")
    return findings


def build_frontend_product_shell(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
) -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = collect_product_shell_demo_data(scenario=scenario, api_base_url=api_base_url)

    _write_text(out / "index.html", render_index_html())
    _write_text(out / "assets" / "qualibug_product_shell.css", render_product_shell_css())
    _write_text(out / "assets" / "qualibug_product_shell.js", render_product_shell_js())
    _write_text(out / "data" / "product_shell_data.json", _json_dump(data))
    _write_text(out / "README_PRODUCT_SHELL.md", render_readme(data))

    manifest = redact_value(
        {
            "version": PHASE105A_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "api_base_url": api_base_url.rstrip("/"),
            "page_count": len(PAGE_NAV),
            "pages": list(PAGE_NAV),
            "required_files": list(REQUIRED_PRODUCT_SHELL_FILES),
            "data_source": "Phase104CommandCenterHttpApp seeded demo API",
            "redaction_status": "safe" if not scan_product_shell_for_secret_leaks(out) else "failed",
        }
    )
    _write_text(out / PRODUCT_SHELL_MANIFEST, _json_dump(manifest))
    return manifest


def validate_frontend_product_shell(output_dir: str | Path) -> ProductShellAcceptanceReport:
    out = Path(output_dir)
    scenario = "unknown"
    checks: list[ProductShellCheck] = []

    missing = [rel for rel in REQUIRED_PRODUCT_SHELL_FILES if not (out / rel).exists()]
    checks.append(
        ProductShellCheck(
            "required_files",
            not missing,
            "required product shell files are present" if not missing else "missing files: " + ", ".join(missing),
        )
    )

    data: dict[str, Any] = {}
    data_path = out / "data" / "product_shell_data.json"
    if data_path.exists():
        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
            scenario = str(data.get("scenario") or scenario)
        except json.JSONDecodeError as exc:
            checks.append(ProductShellCheck("data_json", False, f"product shell data is invalid JSON: {exc}"))
        else:
            required_data_keys = {"project", "page_nav", "dashboard", "environment", "test_plan", "live_map", "risks", "risk_detail", "value_metrics", "executive_report"}
            missing_keys = sorted(required_data_keys - set(data.keys()))
            checks.append(
                ProductShellCheck(
                    "data_contract",
                    not missing_keys,
                    "product shell data contains all page view-model inputs" if not missing_keys else "missing data keys: " + ", ".join(missing_keys),
                )
            )
            nav_ids = {item.get("id") for item in data.get("page_nav", []) if isinstance(item, Mapping)}
            expected_nav_ids = {item["id"] for item in PAGE_NAV}
            missing_nav = sorted(expected_nav_ids - nav_ids)
            checks.append(
                ProductShellCheck(
                    "navigation_model",
                    not missing_nav and len(nav_ids) >= 10,
                    "navigation covers V1 display pages" if not missing_nav and len(nav_ids) >= 10 else "missing nav pages: " + ", ".join(missing_nav),
                )
            )
            checks.append(
                ProductShellCheck(
                    "dashboard_value",
                    isinstance(data.get("dashboard"), Mapping)
                    and "quality_health_score" in data["dashboard"]
                    and "launch_decision" in data["dashboard"]
                    and bool(data.get("risks")),
                    "dashboard contains quality score, launch decision, and risk cards",
                )
            )
            checks.append(
                ProductShellCheck(
                    "evidence_and_roi",
                    isinstance(data.get("risk_detail"), Mapping)
                    and isinstance(data.get("value_metrics"), Mapping)
                    and "estimated_hours_saved" in data["value_metrics"],
                    "evidence detail and ROI value metrics are available",
                )
            )

    html_path = out / "index.html"
    if html_path.exists():
        text = html_path.read_text(encoding="utf-8")
        required_labels = ["质量驾驶舱", "客户资料导入", "环境诊断中心", "业务流程地图", "风险与 Bug 列表", "证据链详情", "领导层报告", "ROI 价值中心"]
        missing_labels = [label for label in required_labels if label not in text]
        checks.append(
            ProductShellCheck(
                "page_labels",
                not missing_labels,
                "index contains core product display labels" if not missing_labels else "missing labels: " + ", ".join(missing_labels),
            )
        )

    js_path = out / "assets" / "qualibug_product_shell.js"
    if js_path.exists():
        js_text = js_path.read_text(encoding="utf-8")
        required_renderers = ["renderDashboard", "renderEnvironment", "renderBusinessMap", "renderRisks", "renderEvidence", "renderExecutiveReport", "renderRoi"]
        missing_renderers = [name for name in required_renderers if name not in js_text]
        checks.append(
            ProductShellCheck(
                "renderers",
                not missing_renderers,
                "JavaScript renderers cover core pages" if not missing_renderers else "missing renderers: " + ", ".join(missing_renderers),
            )
        )

    leaks = scan_product_shell_for_secret_leaks(out)
    checks.append(ProductShellCheck("redaction", not leaks, "no forbidden credential patterns found" if not leaks else "; ".join(leaks)))

    passed_count = sum(1 for check in checks if check.passed)
    score = int(round((passed_count / max(len(checks), 1)) * 100))
    report = ProductShellAcceptanceReport(
        passed=all(check.passed for check in checks),
        score=score,
        version=PHASE105A_VERSION,
        scenario=scenario,
        output_dir=str(out),
        checks=checks,
        artifacts={"required_files": list(REQUIRED_PRODUCT_SHELL_FILES), "page_count": len(PAGE_NAV)},
    )
    return report


def render_acceptance_markdown(report: ProductShellAcceptanceReport) -> str:
    status = "PASSED" if report.passed else "FAILED"
    rows = "\n".join(
        f"| {check.key} | {'PASS' if check.passed else 'FAIL'} | {check.severity} | {check.detail} |" for check in report.checks
    )
    return f"""# Phase105A 前端产品壳验收报告

- 状态：**{status}**
- 分数：**{report.score} / 100**
- 场景：{report.scenario}
- 输出目录：`{report.output_dir}`
- 版本：{report.version}

| 检查项 | 结果 | 严重级别 | 说明 |
|---|---|---|---|
{rows}
"""


def run_frontend_product_shell_export(
    *,
    output_dir: str | Path,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
    validate_only: bool = False,
) -> dict[str, Any]:
    out = Path(output_dir)
    if not validate_only:
        build_frontend_product_shell(out, scenario=scenario, api_base_url=api_base_url)
    report = validate_frontend_product_shell(out)
    _write_text(out / PRODUCT_SHELL_ACCEPTANCE_JSON, _json_dump(report.to_dict()))
    _write_text(out / PRODUCT_SHELL_ACCEPTANCE_MD, render_acceptance_markdown(report))
    return {"manifest": json.loads((out / PRODUCT_SHELL_MANIFEST).read_text(encoding="utf-8")) if (out / PRODUCT_SHELL_MANIFEST).exists() else None, "acceptance": report.to_dict()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or validate the Phase105A frontend product shell.")
    parser.add_argument("--output-dir", default="outputs/phase105_frontend_product_shell")
    parser.add_argument("--scenario", default="manufacturing", choices=["manufacturing", "ecommerce", "saas"])
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    result = run_frontend_product_shell_export(
        output_dir=args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
    )
    print(json.dumps(result["acceptance"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["acceptance"].get("passed") else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

