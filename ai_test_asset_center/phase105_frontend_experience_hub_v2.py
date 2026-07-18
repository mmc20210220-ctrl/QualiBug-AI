from __future__ import annotations

"""Phase105J: unified frontend experience hub v2 for QualiBug.

Phase105H assembled the first frontend hub from Phase105A-G.  Phase105I then
added the missing execution-control page for AI test plan and realtime test
execution.  Phase105J upgrades the hub so the customer journey becomes complete:
customer intake -> environment diagnosis -> business flow map -> AI test plan ->
realtime execution -> risk/evidence -> executive report and ROI.

The exporter is static, framework-neutral, and dependency-free.  It can be used
as a customer-facing prototype before a React/Vue implementation exists, while
keeping the generated data redacted and reusable by the future frontend.
"""

import argparse
import html
import json
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase105_business_flow_map_experience import build_business_flow_map_experience
from ai_test_asset_center.phase105_customer_intake_experience import build_customer_intake_experience
from ai_test_asset_center.phase105_dashboard_experience import build_dashboard_experience
from ai_test_asset_center.phase105_environment_diagnosis_experience import build_environment_diagnosis_experience
from ai_test_asset_center.phase105_frontend_product_shell import build_frontend_product_shell
from ai_test_asset_center.phase105_report_roi_experience import build_report_roi_experience
from ai_test_asset_center.phase105_risk_evidence_experience import build_risk_evidence_experience
from ai_test_asset_center.phase105_test_execution_experience import build_test_execution_experience

PHASE105J_VERSION = "phase105j-frontend-experience-hub-v2"

FRONTEND_HUB_V2_MANIFEST = "frontend_experience_hub_v2_manifest.json"
FRONTEND_HUB_V2_ACCEPTANCE_JSON = "frontend_experience_hub_v2_acceptance_report.json"
FRONTEND_HUB_V2_ACCEPTANCE_MD = "frontend_experience_hub_v2_acceptance_report.md"
FRONTEND_HUB_V2_ZIP = "phase105_frontend_experience_hub_v2.zip"

REQUIRED_FRONTEND_HUB_V2_FILES: tuple[str, ...] = (
    "index.html",
    "README_FRONTEND_EXPERIENCE_HUB_V2.md",
    "data/frontend_experience_hub_v2_data.json",
    "assets/qualibug_frontend_hub_v2.css",
    "assets/qualibug_frontend_hub_v2.js",
    FRONTEND_HUB_V2_MANIFEST,
)

CORE_FRONTEND_HUB_V2_LABELS: tuple[str, ...] = (
    "前端显示层总装 V2",
    "质量驾驶舱",
    "客户资料导入",
    "环境诊断中心",
    "业务流程地图",
    "AI 测试计划",
    "实时测试执行",
    "风险与证据链",
    "领导层报告",
    "ROI 价值中心",
    "端到端体验验收门禁",
    "默认脱敏",
)

FORBIDDEN_FRONTEND_HUB_V2_PATTERNS: tuple[str, ...] = (
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
class HubV2PageSpec:
    key: str
    label: str
    description: str
    relative_dir: str
    entrypoint: str
    phase: str
    business_value: str
    journey_stage: str
    build: Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class FrontendHubV2Check:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendHubV2AcceptanceReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    checks: list[FrontendHubV2Check] = field(default_factory=list)
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


PAGE_SPECS_V2: tuple[HubV2PageSpec, ...] = (
    HubV2PageSpec(
        key="product_shell",
        label="产品主界面",
        description="统一导航、项目栏、页面骨架和全局信息架构。",
        relative_dir="pages/product_shell",
        entrypoint="index.html",
        phase="Phase105A",
        business_value="让产品先像一个完整企业质量平台，而不是孤立脚本。",
        journey_stage="产品入口",
        build=build_frontend_product_shell,
    ),
    HubV2PageSpec(
        key="dashboard",
        label="质量驾驶舱",
        description="上线建议、质量分、Top 风险、环境状态、覆盖率和 ROI。",
        relative_dir="pages/dashboard",
        entrypoint="dashboard.html",
        phase="Phase105B",
        business_value="让领导 30 秒内理解是否能上线和为什么。",
        journey_stage="态势总览",
        build=build_dashboard_experience,
    ),
    HubV2PageSpec(
        key="customer_intake",
        label="客户资料导入",
        description="资料上传、行业识别、业务模型草案、环境补料和测试计划入口。",
        relative_dir="pages/customer_intake",
        entrypoint="customer_intake.html",
        phase="Phase105C",
        business_value="把企业资料入口产品化，解释 AI 会如何理解客户系统。",
        journey_stage="资料进入",
        build=build_customer_intake_experience,
    ),
    HubV2PageSpec(
        key="environment_diagnosis",
        label="环境诊断中心",
        description="URL/DNS/HTTP、认证、会话、API Smoke、阻断原因和补料清单。",
        relative_dir="pages/environment_diagnosis",
        entrypoint="environment_diagnosis.html",
        phase="Phase105D",
        business_value="把环境不通的问题转成客户可执行的下一步动作。",
        journey_stage="环境可测",
        build=build_environment_diagnosis_experience,
    ),
    HubV2PageSpec(
        key="business_flow_map",
        label="业务流程地图",
        description="业务节点、链路线、覆盖状态、风险爆点、环境阻断和证据回流。",
        relative_dir="pages/business_flow_map",
        entrypoint="business_flow_map.html",
        phase="Phase105E",
        business_value="证明 AI 不是乱测，而是沿客户业务链路理解和执行。",
        journey_stage="业务理解",
        build=build_business_flow_map_experience,
    ),
    HubV2PageSpec(
        key="test_execution",
        label="AI 测试计划 / 实时测试执行",
        description="测试计划、可执行探针、阻断探针、运行状态、事件时间线和证据回流。",
        relative_dir="pages/test_execution",
        entrypoint="test_execution.html",
        phase="Phase105I",
        business_value="让客户看到 AI 准备测什么、正在测什么，以及证据如何回流。",
        journey_stage="AI 执行",
        build=build_test_execution_experience,
    ),
    HubV2PageSpec(
        key="risk_evidence",
        label="风险与证据链",
        description="风险列表、业务影响、上线阻断、复现步骤、证据可信度和修复建议。",
        relative_dir="pages/risk_evidence",
        entrypoint="risk_evidence.html",
        phase="Phase105F",
        business_value="让客户相信 Bug 真实、可复现、可修复、可复验。",
        journey_stage="风险证明",
        build=build_risk_evidence_experience,
    ),
    HubV2PageSpec(
        key="report_roi",
        label="领导层报告 / ROI",
        description="执行摘要、上线建议、AI 价值、节省工时、潜在影响和下一步动作。",
        relative_dir="pages/report_roi",
        entrypoint="report_roi.html",
        phase="Phase105G",
        business_value="让测试成果能直接汇报、签收和推动续费。",
        journey_stage="决策汇报",
        build=build_report_roi_experience,
    ),
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


def _html_attr(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _page_url(spec: HubV2PageSpec) -> str:
    return f"{spec.relative_dir}/{spec.entrypoint}"


def _scan_files(root: Path, patterns: Sequence[str]) -> list[str]:
    leaks: list[str] = []
    if not root.exists():
        return [f"missing_output_dir:{root}"]
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js", ".json", ".md", ".txt", ".yml", ".yaml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            if pattern in text:
                leaks.append(f"{path.relative_to(root)} contains forbidden pattern {pattern}")
    return leaks


def scan_frontend_hub_v2_for_secret_leaks(output_dir: str | Path) -> list[str]:
    return _scan_files(Path(output_dir), FORBIDDEN_FRONTEND_HUB_V2_PATTERNS)


def _page_card(spec: HubV2PageSpec, manifest: Mapping[str, Any]) -> dict[str, Any]:
    redaction = _safe_text(manifest.get("redaction_status"), "safe")
    return {
        "key": spec.key,
        "label": spec.label,
        "description": spec.description,
        "phase": spec.phase,
        "business_value": spec.business_value,
        "journey_stage": spec.journey_stage,
        "relative_dir": spec.relative_dir,
        "entrypoint": spec.entrypoint,
        "url": _page_url(spec),
        "redaction_status": redaction,
        "manifest_version": _safe_text(manifest.get("version")),
        "status": "ready" if redaction in {"safe", "passed", "ok"} else "attention",
    }


def _build_page_outputs(output: Path, scenario: str, api_base_url: str) -> list[dict[str, Any]]:
    page_cards: list[dict[str, Any]] = []
    for spec in PAGE_SPECS_V2:
        page_dir = output / spec.relative_dir
        page_dir.mkdir(parents=True, exist_ok=True)
        manifest = spec.build(page_dir, scenario=scenario, api_base_url=api_base_url)
        page_cards.append(_page_card(spec, manifest))
    return page_cards


def _build_hub_data(page_cards: Sequence[Mapping[str, Any]], *, scenario: str, api_base_url: str) -> dict[str, Any]:
    ready_count = sum(1 for page in page_cards if page.get("status") == "ready")
    total = len(page_cards)
    return redact_value(
        {
            "version": PHASE105J_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "api_base_url": api_base_url.rstrip("/"),
            "hub_title": "QualiBug AI 企业质量指挥中心",
            "hub_subtitle": "前端显示层总装 V2：补齐 AI 测试计划与实时执行的完整产品旅程",
            "readiness": {
                "page_count": total,
                "ready_page_count": ready_count,
                "readiness_rate": round(ready_count / total * 100, 2) if total else 0,
                "redaction_status": "safe" if ready_count == total else "attention",
            },
            "journey_steps": [
                {"step": 1, "label": "客户资料", "page": "客户资料导入", "outcome": "形成项目草案、业务模型和补料清单。"},
                {"step": 2, "label": "环境诊断", "page": "环境诊断中心", "outcome": "确认 URL、认证、API Smoke 和安全执行边界。"},
                {"step": 3, "label": "业务理解", "page": "业务流程地图", "outcome": "展示业务链路覆盖、风险爆点和环境阻断。"},
                {"step": 4, "label": "AI 执行", "page": "AI 测试计划 / 实时测试执行", "outcome": "展示可执行探针、阻断探针、运行事件和证据回流。"},
                {"step": 5, "label": "风险证明", "page": "风险与证据链", "outcome": "用复现步骤、证据摘要和修复建议证明风险。"},
                {"step": 6, "label": "上线决策", "page": "领导层报告 / ROI", "outcome": "输出上线建议、ROI 价值和下一步动作。"},
            ],
            "pages": list(page_cards),
            "experience_principles": [
                "先讲业务价值和上线风险，再展示技术细节。",
                "把测试计划、实时执行和证据回流作为前端主链路的一等页面。",
                "每个页面都必须解释客户下一步该做什么。",
                "证据和环境信息默认脱敏，不展示原始凭证。",
                "所有页面都能独立打开，也能从总入口跳转。",
            ],
        }
    )


def render_frontend_hub_v2_html() -> str:
    cards = "\n".join(
        f"""
        <a class="hub-page-card" href="{_html_attr(_page_url(spec))}">
          <span class="hub-stage">{html.escape(spec.journey_stage)}</span>
          <span class="hub-phase">{html.escape(spec.phase)}</span>
          <h2>{html.escape(spec.label)}</h2>
          <p>{html.escape(spec.description)}</p>
          <strong>{html.escape(spec.business_value)}</strong>
        </a>
        """.strip()
        for spec in PAGE_SPECS_V2
    )
    nav_links = "\n".join(
        f'<a href="{_html_attr(_page_url(spec))}">{html.escape(spec.label)}</a>' for spec in PAGE_SPECS_V2
    )
    journey = "\n".join(
        f"""
        <li>
          <span>{index}</span>
          <div><b>{html.escape(label)}</b><p>{html.escape(text)}</p></div>
        </li>
        """.strip()
        for index, label, text in [
            (1, "资料进入", "客户资料导入后生成项目草案、业务链路和环境补料清单。"),
            (2, "环境可测", "环境诊断解释 URL、认证、会话、API Smoke 和安全边界。"),
            (3, "业务可视", "业务流程地图展示覆盖状态、风险爆点、证据回流和阻断节点。"),
            (4, "AI 执行", "AI 测试计划和实时执行页展示探针、进度、事件、风险和证据回流。"),
            (5, "风险可信", "风险与证据链页面证明 Bug 真实、可复现、可修复、可复验。"),
            (6, "决策汇报", "领导层报告和 ROI 页面支持上线决策、复盘和商业价值说明。"),
        ]
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>QualiBug AI 企业质量指挥中心 · 前端显示层总装 V2</title>
  <link rel="stylesheet" href="assets/qualibug_frontend_hub_v2.css" />
</head>
<body>
  <aside class="hub-sidebar">
    <div class="hub-brand">
      <span>QB</span>
      <div><b>QualiBug AI</b><small>企业质量指挥中心</small></div>
    </div>
    <nav>{nav_links}</nav>
    <div class="hub-safe">默认脱敏 · 本地静态可打开 · Phase104 数据驱动</div>
  </aside>
  <main class="hub-main">
    <section class="hub-hero">
      <p class="eyebrow">Phase105J · 前端显示层总装 V2</p>
      <h1>把 AI 测试计划和实时执行接入完整产品旅程</h1>
      <p>从客户资料、环境诊断、业务流程地图、AI 测试计划、实时测试执行、风险证据，到领导层报告和 ROI 价值中心，形成一个真正端到端的企业级 AI 质量平台体验。</p>
      <div class="hub-actions">
        <a href="pages/dashboard/dashboard.html">打开质量驾驶舱</a>
        <a href="pages/test_execution/test_execution.html">进入 AI 测试执行</a>
        <a href="pages/risk_evidence/risk_evidence.html">查看风险证据链</a>
        <button id="copySummary">复制体验摘要</button>
      </div>
    </section>

    <section class="hub-kpis" id="hubKpis">
      <article><span>8</span><p>核心页面已总装</p></article>
      <article><span>100%</span><p>静态打开能力</p></article>
      <article><span>执行页</span><p>测试计划与实时执行已接入</p></article>
      <article><span>安全</span><p>默认脱敏展示</p></article>
    </section>

    <section class="hub-section">
      <div class="section-title"><p>Product Journey</p><h2>客户视角端到端流程</h2></div>
      <ol class="hub-journey">{journey}</ol>
    </section>

    <section class="hub-section">
      <div class="section-title"><p>Pages</p><h2>前端页面入口</h2></div>
      <div class="hub-page-grid">{cards}</div>
    </section>

    <section class="hub-section hub-gate">
      <div>
        <p class="eyebrow">Experience Gate</p>
        <h2>端到端体验验收门禁</h2>
        <p>总装 V2 校验所有核心页面、页面跳转、测试执行页接入、必备文案、子页面清单、脱敏状态和输出归档。它把前端显示层的产品旅程固定下来，后续 React/Vue 实现可以直接照这个体验标准落地。</p>
      </div>
      <a href="frontend_experience_hub_v2_acceptance_report.md">查看验收报告</a>
    </section>
  </main>
  <script src="assets/qualibug_frontend_hub_v2.js"></script>
</body>
</html>
"""


def render_frontend_hub_v2_css() -> str:
    return """:root{font-family:Inter,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#102033;background:#eef3fb}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;grid-template-columns:288px 1fr}.hub-sidebar{background:#071527;color:white;padding:24px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:24px}.hub-brand{display:flex;align-items:center;gap:14px}.hub-brand span{width:44px;height:44px;border-radius:16px;background:linear-gradient(135deg,#73f0ff,#8b8cff);display:grid;place-items:center;color:#071527;font-weight:900}.hub-brand b{display:block}.hub-brand small{color:#9fb3cc}nav{display:flex;flex-direction:column;gap:8px;overflow:auto}nav a{color:#d9e7ff;text-decoration:none;border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:10px 12px;background:rgba(255,255,255,.04);font-size:14px}nav a:hover{background:rgba(115,240,255,.14);border-color:rgba(115,240,255,.35)}.hub-safe{margin-top:auto;color:#a8bdd6;font-size:13px;line-height:1.7;border-top:1px solid rgba(255,255,255,.1);padding-top:18px}.hub-main{padding:32px;display:flex;flex-direction:column;gap:24px}.hub-hero{border-radius:28px;padding:36px;background:radial-gradient(circle at 15% 20%,rgba(115,240,255,.35),transparent 28%),linear-gradient(135deg,#10233f,#1b3d68);color:white;box-shadow:0 22px 60px rgba(18,40,71,.22)}.eyebrow,.section-title p{letter-spacing:.12em;text-transform:uppercase;font-size:12px;font-weight:800;color:#60d8ff;margin:0 0 10px}.hub-hero h1{font-size:42px;line-height:1.12;margin:0 0 16px}.hub-hero p{max-width:960px;line-height:1.8;color:#dbeaff}.hub-actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}.hub-actions a,.hub-actions button,.hub-gate a{border:0;border-radius:14px;background:#73f0ff;color:#071527;text-decoration:none;font-weight:800;padding:12px 16px;cursor:pointer}.hub-actions a:nth-child(2){background:white}.hub-actions a:nth-child(3){background:#dff8ff}.hub-actions button{background:rgba(255,255,255,.12);color:white;border:1px solid rgba(255,255,255,.25)}.hub-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.hub-kpis article,.hub-section{background:white;border:1px solid #dce6f2;border-radius:24px;padding:22px;box-shadow:0 12px 32px rgba(16,32,51,.06)}.hub-kpis span{font-size:30px;font-weight:900;color:#12365f}.hub-kpis p{margin:6px 0 0;color:#60738b}.section-title h2{margin:0 0 18px;font-size:24px}.hub-journey{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;list-style:none;padding:0;margin:0}.hub-journey li{border:1px solid #e2eaf4;border-radius:18px;padding:16px;background:#f8fbff}.hub-journey span{display:inline-grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#153c69;color:white;font-weight:800;margin-bottom:10px}.hub-journey b{display:block;margin-bottom:8px}.hub-journey p{margin:0;color:#65788d;font-size:13px;line-height:1.6}.hub-page-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.hub-page-card{text-decoration:none;color:#102033;border:1px solid #dde8f3;border-radius:22px;padding:20px;background:linear-gradient(180deg,#fff,#f8fbff);transition:.18s transform,.18s box-shadow}.hub-page-card:hover{transform:translateY(-3px);box-shadow:0 18px 42px rgba(16,32,51,.13)}.hub-phase,.hub-stage{display:inline-block;font-size:12px;font-weight:900;border-radius:999px;padding:6px 10px;margin-right:6px}.hub-phase{color:#1a6dd8;background:#eaf3ff}.hub-stage{color:#07505f;background:#e7fbff}.hub-page-card h2{margin:14px 0 8px}.hub-page-card p{min-height:76px;color:#64758a;line-height:1.6}.hub-page-card strong{display:block;color:#12365f;line-height:1.55}.hub-gate{display:flex;align-items:center;justify-content:space-between;gap:20px}.hub-gate h2{margin:0 0 10px}.hub-gate p{color:#62768b;line-height:1.7}@media(max-width:1280px){.hub-journey,.hub-page-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:960px){body{grid-template-columns:1fr}.hub-sidebar{position:relative;height:auto}.hub-kpis{grid-template-columns:1fr 1fr}}@media(max-width:720px){.hub-main{padding:16px}.hub-hero h1{font-size:30px}.hub-kpis,.hub-journey,.hub-page-grid{grid-template-columns:1fr}.hub-gate{align-items:flex-start;flex-direction:column}}"""


def render_frontend_hub_v2_js() -> str:
    return """const summary='QualiBug Phase105J 前端显示层总装 V2：已统一客户资料导入、环境诊断、业务流程地图、AI 测试计划、实时测试执行、风险证据链、领导层报告与 ROI 价值中心，形成从企业资料到上线决策的端到端产品体验。';
document.getElementById('copySummary')?.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(summary);alert('体验摘要已复制');}catch(err){window.prompt('复制体验摘要',summary);}});
fetch('data/frontend_experience_hub_v2_data.json').then(r=>r.json()).then(data=>{const kpis=document.getElementById('hubKpis'); if(!kpis) return; const ready=data.readiness||{}; const hasExecution=(data.pages||[]).some(p=>p.key==='test_execution'&&p.status==='ready'); kpis.innerHTML=`<article><span>${ready.page_count||8}</span><p>核心页面已总装</p></article><article><span>${ready.readiness_rate||100}%</span><p>页面就绪率</p></article><article><span>${hasExecution?'已接入':'待接入'}</span><p>AI 测试执行页</p></article><article><span>${ready.redaction_status||'safe'}</span><p>默认脱敏状态</p></article>`;}).catch(()=>{});
"""


def render_frontend_hub_v2_readme(data: Mapping[str, Any]) -> str:
    pages = data.get("pages") if isinstance(data.get("pages"), Sequence) else []
    rows = "\n".join(
        f"| {_safe_text(page.get('phase'))} | {_safe_text(page.get('journey_stage'))} | [{_safe_text(page.get('label'))}]({_safe_text(page.get('url'))}) | {_safe_text(page.get('business_value'))} |"
        for page in pages
        if isinstance(page, Mapping)
    )
    return f"""# Phase105J 前端显示层总装 V2

Phase105J 在 Phase105H 总装站点基础上接入 Phase105I 的 AI 测试计划与实时测试执行页，让前端显示层从“页面集合”升级为端到端客户体验链路。

## 页面入口

| 阶段 | 旅程 | 页面 | 价值 |
|---|---|---|---|
{rows}

## 使用方式

```powershell
python -m ai_test_asset_center.phase105_frontend_experience_hub_v2 --scenario manufacturing --output-dir .\\outputs\\phase105_frontend_experience_hub_v2
Start-Process .\\outputs\\phase105_frontend_experience_hub_v2\\index.html
```

## 验收重点

- 总入口 `index.html` 可以打开。
- 8 个核心页面均可从总入口跳转。
- 新增 `pages/test_execution/test_execution.html`，覆盖 AI 测试计划、可执行探针、阻断探针、实时执行、事件时间线和证据回流。
- 客户旅程完整覆盖：客户资料导入 → 环境诊断 → 业务流程地图 → AI 测试计划 / 实时执行 → 风险证据链 → 领导层报告 / ROI。
- 所有输出默认脱敏，不展示原始 token、cookie、password、session 或 client secret。
- 生成 `frontend_experience_hub_v2_acceptance_report.json/.md` 作为端到端体验验收门禁。
"""


def _write_zip(output: Path) -> str:
    zip_path = output / FRONTEND_HUB_V2_ZIP
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob("*")):
            if not path.is_file() or path == zip_path:
                continue
            archive.write(path, path.relative_to(output).as_posix())
    return zip_path.name


def build_frontend_experience_hub_v2(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
    create_zip: bool = True,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    page_cards = _build_page_outputs(output, scenario=scenario, api_base_url=api_base_url)
    data = _build_hub_data(page_cards, scenario=scenario, api_base_url=api_base_url)

    _write_text(output / "index.html", render_frontend_hub_v2_html())
    _write_text(output / "assets" / "qualibug_frontend_hub_v2.css", render_frontend_hub_v2_css())
    _write_text(output / "assets" / "qualibug_frontend_hub_v2.js", render_frontend_hub_v2_js())
    _write_text(output / "data" / "frontend_experience_hub_v2_data.json", _json_dump(data))
    _write_text(output / "README_FRONTEND_EXPERIENCE_HUB_V2.md", render_frontend_hub_v2_readme(data))

    leaks = scan_frontend_hub_v2_for_secret_leaks(output)
    manifest = redact_value(
        {
            "version": PHASE105J_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "api_base_url": api_base_url.rstrip("/"),
            "entrypoint": "index.html",
            "page_count": len(page_cards),
            "ready_page_count": sum(1 for page in page_cards if page.get("status") == "ready"),
            "pages": page_cards,
            "required_files": list(REQUIRED_FRONTEND_HUB_V2_FILES),
            "core_labels": list(CORE_FRONTEND_HUB_V2_LABELS),
            "execution_page": "pages/test_execution/test_execution.html",
            "redaction_status": "safe" if not leaks else "leak_detected",
            "secret_leaks": leaks,
        }
    )
    _write_text(output / FRONTEND_HUB_V2_MANIFEST, _json_dump(manifest))

    report = validate_frontend_experience_hub_v2(output)
    write_frontend_hub_v2_acceptance_report(output, report)
    if create_zip:
        zip_name = _write_zip(output)
        manifest["zip_file"] = zip_name
        _write_text(output / FRONTEND_HUB_V2_MANIFEST, _json_dump(manifest))
    return manifest


def _check_file_contains(output: Path, rel: str, labels: Sequence[str]) -> tuple[bool, str]:
    path = output / rel
    if not path.exists():
        return False, f"missing {rel}"
    text = path.read_text(encoding="utf-8", errors="ignore")
    missing = [label for label in labels if label not in text]
    if missing:
        return False, f"{rel} missing labels: {', '.join(missing)}"
    return True, f"{rel} contains required labels"


def validate_frontend_experience_hub_v2(output_dir: str | Path) -> FrontendHubV2AcceptanceReport:
    output = Path(output_dir)
    checks: list[FrontendHubV2Check] = []

    def add(key: str, passed: bool, detail: str, severity: str = "critical") -> None:
        checks.append(FrontendHubV2Check(key=key, passed=passed, detail=detail, severity=severity))

    manifest_path = output / FRONTEND_HUB_V2_MANIFEST
    manifest = _read_json(manifest_path)
    scenario = _safe_text(manifest.get("scenario"), "unknown")

    missing = [rel for rel in REQUIRED_FRONTEND_HUB_V2_FILES if not (output / rel).exists()]
    add("required_files", not missing, "前端总装 V2 必备文件已生成。" if not missing else f"缺少文件：{', '.join(missing)}")

    ok, detail = _check_file_contains(output, "index.html", CORE_FRONTEND_HUB_V2_LABELS)
    add("core_labels", ok, detail)

    child_missing: list[str] = []
    for spec in PAGE_SPECS_V2:
        if not (output / spec.relative_dir / spec.entrypoint).exists():
            child_missing.append(f"{spec.label}:{spec.relative_dir}/{spec.entrypoint}")
    add("child_pages", not child_missing, "所有 Phase105A-I 子页面入口已生成。" if not child_missing else "缺少子页面：" + ", ".join(child_missing))

    execution_path = output / "pages" / "test_execution" / "test_execution.html"
    add("execution_page", execution_path.exists(), "AI 测试计划 / 实时测试执行页已接入总装。" if execution_path.exists() else "缺少 AI 测试执行页。")

    index_text = (output / "index.html").read_text(encoding="utf-8", errors="ignore") if (output / "index.html").exists() else ""
    missing_links = [_page_url(spec) for spec in PAGE_SPECS_V2 if _page_url(spec) not in index_text]
    add("navigation_links", not missing_links, "总入口包含所有子页面跳转链接。" if not missing_links else "缺少跳转链接：" + ", ".join(missing_links))

    data = _read_json(output / "data" / "frontend_experience_hub_v2_data.json")
    pages = data.get("pages") if isinstance(data.get("pages"), list) else []
    ready_count = sum(1 for page in pages if isinstance(page, Mapping) and page.get("status") == "ready")
    add("page_readiness", ready_count == len(PAGE_SPECS_V2), f"{ready_count}/{len(PAGE_SPECS_V2)} 个页面状态 ready。")

    journey_steps = data.get("journey_steps") if isinstance(data.get("journey_steps"), list) else []
    journey_text = json.dumps(journey_steps, ensure_ascii=False)
    add(
        "journey_includes_execution",
        len(journey_steps) >= 6 and "AI 执行" in journey_text and ("实时执行" in journey_text or "实时测试执行" in journey_text),
        "客户旅程已包含 AI 执行与实时执行阶段。" if len(journey_steps) >= 6 and "AI 执行" in journey_text and ("实时执行" in journey_text or "实时测试执行" in journey_text) else "客户旅程缺少 AI 执行阶段。",
    )

    child_manifests_missing: list[str] = []
    for spec in PAGE_SPECS_V2:
        manifest_candidates = list((output / spec.relative_dir).glob("*_manifest.json"))
        if not manifest_candidates:
            child_manifests_missing.append(spec.label)
    add("child_manifests", not child_manifests_missing, "所有子页面均保留独立 manifest。" if not child_manifests_missing else "缺少子页面 manifest：" + ", ".join(child_manifests_missing))

    leaks = scan_frontend_hub_v2_for_secret_leaks(output)
    add("redaction_guard", not leaks, "未发现原始凭证、会话或 traceback 泄露。" if not leaks else "发现疑似泄露：" + "; ".join(leaks))

    zip_path = output / FRONTEND_HUB_V2_ZIP
    if zip_path.exists():
        try:
            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
            needed = {
                "index.html",
                "data/frontend_experience_hub_v2_data.json",
                "pages/dashboard/dashboard.html",
                "pages/test_execution/test_execution.html",
                "pages/risk_evidence/risk_evidence.html",
                "pages/report_roi/report_roi.html",
            }
            missing_zip_entries = sorted(needed - names)
            add("zip_archive", not missing_zip_entries, "总装 V2 zip 包可读且包含关键入口。" if not missing_zip_entries else "zip 缺少关键文件：" + ", ".join(missing_zip_entries), severity="major")
        except zipfile.BadZipFile:
            add("zip_archive", False, "总装 V2 zip 包不可读。", severity="major")
    else:
        add("zip_archive", True, "未要求生成 zip 包，跳过归档检查。", severity="minor")

    passed = all(check.passed for check in checks if check.severity in {"critical", "major"})
    score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    return FrontendHubV2AcceptanceReport(
        passed=passed,
        score=score,
        version=PHASE105J_VERSION,
        scenario=scenario,
        output_dir=str(output),
        checks=checks,
        artifacts={
            "entrypoint": "index.html",
            "manifest": FRONTEND_HUB_V2_MANIFEST,
            "page_count": len(PAGE_SPECS_V2),
            "execution_page": "pages/test_execution/test_execution.html",
            "zip_file": FRONTEND_HUB_V2_ZIP if zip_path.exists() else None,
        },
    )


def write_frontend_hub_v2_acceptance_report(output: Path, report: FrontendHubV2AcceptanceReport) -> None:
    data = report.to_dict()
    _write_text(output / FRONTEND_HUB_V2_ACCEPTANCE_JSON, _json_dump(data))
    lines = [
        "# Phase105J 前端显示层总装 V2 验收报告",
        "",
        f"- 通过：{report.passed}",
        f"- 分数：{report.score}",
        f"- 场景：{report.scenario}",
        f"- 版本：{report.version}",
        "",
        "## 检查项",
    ]
    for check in report.checks:
        mark = "✅" if check.passed else "❌"
        lines.append(f"- {mark} **{check.key}**：{check.detail}")
    _write_text(output / FRONTEND_HUB_V2_ACCEPTANCE_MD, "\n".join(lines) + "\n")


def run_frontend_experience_hub_v2_export(
    *,
    output_dir: str | Path,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
    validate_only: bool = False,
    create_zip: bool = True,
) -> dict[str, Any]:
    output = Path(output_dir)
    manifest: dict[str, Any] = _read_json(output / FRONTEND_HUB_V2_MANIFEST)
    if not validate_only:
        manifest = build_frontend_experience_hub_v2(output, scenario=scenario, api_base_url=api_base_url, create_zip=create_zip)
    report = validate_frontend_experience_hub_v2(output)
    write_frontend_hub_v2_acceptance_report(output, report)
    return redact_value({"manifest": manifest, "acceptance": report.to_dict()})


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or validate the Phase105J unified frontend experience hub v2.")
    parser.add_argument("--output-dir", default="outputs/phase105_frontend_experience_hub_v2")
    parser.add_argument("--scenario", default="manufacturing", choices=("manufacturing", "ecommerce", "saas"))
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--no-zip", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_frontend_experience_hub_v2_export(
        output_dir=args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        validate_only=args.validate_only,
        create_zip=not args.no_zip,
    )
    print(_json_dump(result))
    return 0 if result["acceptance"]["passed"] else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

