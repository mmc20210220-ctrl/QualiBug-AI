from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
from urllib.request import Request, urlopen

from aitestops.ui_journey_tester import UIJourneyConfig, run_ui_journey
from scripts.remote_sut_platform_run import run as run_asset_pipeline


@dataclass
class EnterpriseAutomationConfig:
    project: str = "enterprise_shop"
    project_name: str = "企业商城演示"
    inputs: Path = Path("platform_inputs/enterprise_shop")
    workspace_root: Path = Path("platform_workspace")
    output_root: Path = Path("platform_outputs")
    engine: str = "auto"
    base_url: str = "http://127.0.0.1:8000"
    username: str = "alice"
    password: str = "Alice123!"
    admin_username: str = "admin"
    admin_password: str = "Admin123!"
    browser: str = "chromium"
    headless: bool = True
    max_pages: int = 8
    execute_browser: bool = True
    auto_start_demo_sut: bool = True


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def reachable(url: str, timeout: int = 3) -> tuple[bool, str | None]:
    try:
        req = Request(url, headers={"User-Agent": "Enterprise-AI-Automation/1.0"})
        with urlopen(req, timeout=timeout) as resp:  # nosec - user-configured SUT URL
            return 200 <= getattr(resp, "status", 200) < 500, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def ensure_demo_sut(config: EnterpriseAutomationConfig, root: Path) -> Dict[str, Any]:
    ok, error = reachable(config.base_url)
    if ok:
        return {"started": False, "reachable": True, "base_url": config.base_url, "message": "SUT already reachable"}

    should_start = (
        config.auto_start_demo_sut
        and config.project == "enterprise_shop"
        and config.base_url.rstrip("/") == "http://127.0.0.1:8000"
    )
    if not should_start:
        return {"started": False, "reachable": False, "base_url": config.base_url, "message": error}

    proc = subprocess.Popen(
        [sys.executable, "scripts/demo_shop_sut.py"],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    for _ in range(20):
        time.sleep(0.5)
        ok, error = reachable(config.base_url)
        if ok:
            return {"started": True, "reachable": True, "base_url": config.base_url, "pid": proc.pid, "message": "Demo SUT started"}
    return {"started": True, "reachable": False, "base_url": config.base_url, "pid": proc.pid, "message": error or "SUT did not become reachable"}


def quality_gate(asset_data: Dict[str, Any], ui_result: Dict[str, Any]) -> Dict[str, Any]:
    risk = asset_data.get("risk_overview", {})
    api_execution = asset_data.get("api_execution", {})
    ui_status = ui_result.get("execution", {}).get("status")
    blockers = []
    if int(api_execution.get("failed") or 0) > 0:
        blockers.append("接口实时执行存在失败")
    if ui_status not in {"passed", "skipped"}:
        blockers.append("UI 旅程执行失败")
    if int(risk.get("P0") or 0) > 0 and ui_status == "failed":
        blockers.append("存在 P0 风险且 UI 旅程失败")
    decision = "pass" if not blockers and ui_status == "passed" else "review" if not blockers and ui_status == "skipped" else "block"
    return {
        "decision": decision,
        "blockers": blockers,
        "rules": [
            "必须生成 P0/P1 风险对应的测试资产",
            "被测系统可访问时，接口实时执行必须通过",
            "演示被测系统的 UI 下单冒烟旅程应通过",
            "UI 执行被跳过时需要人工复核测试环境",
            "任一接口或 UI 旅程失败都会阻断产品演示准出",
        ],
    }


def render_html(data: Dict[str, Any]) -> str:
    gate = data["quality_gate"]
    asset = data["asset_pipeline"].get("project_overview", {})
    risk = data["asset_pipeline"].get("risk_overview", {})
    test_assets = data["asset_pipeline"].get("test_asset_overview", {})
    api_analysis = data["asset_pipeline"].get("api_test_analysis", [])
    api_execution = data["asset_pipeline"].get("api_execution", {})
    ui = data["ui_journey"].get("execution", {})
    artifacts = data["artifacts"]
    decision_text = {"pass": "通过", "review": "需复核", "block": "阻断"}.get(gate.get("decision"), gate.get("decision", "-"))
    status_text = {"passed": "通过", "failed": "失败", "skipped": "跳过", "running": "运行中"}.get(ui.get("status"), ui.get("status", "-"))
    yes_no = lambda value: "是" if value else "否"
    rows = "".join(f"<tr><td>{k}</td><td><code>{v}</code></td></tr>" for k, v in artifacts.items())
    api_rows = "".join(
        "<tr>"
        f"<td>{item.get('method','')}</td>"
        f"<td>{item.get('path','')}</td>"
        f"<td>{item.get('risk_level','')}</td>"
        f"<td>{yes_no(item.get('requires_auth'))}</td>"
        f"<td>{yes_no(item.get('involves_amount'))}</td>"
        f"<td>{yes_no(item.get('involves_inventory'))}</td>"
        f"<td>{', '.join(item.get('recommended_test_types', []))}</td>"
        "</tr>"
        for item in api_analysis[:30]
    )
    api_failure_rows = "".join(
        "<tr>"
        f"<td>{item.get('case_id','')}</td>"
        f"<td>{item.get('method','')}</td>"
        f"<td>{item.get('path','')}</td>"
        f"<td>{item.get('expected_status','')}</td>"
        f"<td>{item.get('actual_status','')}</td>"
        f"<td>{'; '.join(item.get('failures', []))}</td>"
        "</tr>"
        for item in api_execution.get("failures", [])[:20]
    ) or "<tr><td colspan='6'>暂无接口实时执行失败。</td></tr>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>企业级 AI 自动化报告</title>
  <style>
    body{{margin:0;font-family:Segoe UI,Microsoft YaHei,Arial,sans-serif;background:#eef3f8;color:#142033}}
    header{{background:#10213f;color:white;padding:28px 36px}} main{{padding:24px 36px;display:grid;gap:18px}}
    section{{background:white;border:1px solid #d8e1ee;border-radius:8px;padding:18px;box-shadow:0 8px 24px rgba(15,23,42,.08)}}
    .metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}} .metric{{background:#f8fafc;border:1px solid #d8e1ee;border-radius:8px;padding:16px}}
    .metric b{{display:block;color:#2563eb;font-size:28px}} table{{width:100%;border-collapse:collapse}}td,th{{border-bottom:1px solid #d8e1ee;padding:10px;text-align:left}}
    code{{white-space:pre-wrap}} .decision{{font-weight:800;color:{'#047857' if gate['decision']=='pass' else '#b45309' if gate['decision']=='review' else '#b91c1c'}}}
  </style>
</head>
<body>
<header><h1>企业级 AI 自动化报告</h1><p>从 PRD、OpenAPI 和被测系统地址出发，自动生成测试资产、执行 UI 旅程、收集证据并应用质量门禁。</p></header>
<main>
  <div class="metrics">
    <div class="metric"><b>{decision_text}</b><span>门禁结论</span></div>
    <div class="metric"><b>{asset.get('generated_test_case_count','-')}</b><span>生成用例</span></div>
    <div class="metric"><b>{api_execution.get('failed','-')}</b><span>接口失败</span></div>
    <div class="metric"><b>{asset.get('high_risk_count','-')}</b><span>高风险</span></div>
    <div class="metric"><b>{status_text}</b><span>UI 旅程</span></div>
    <div class="metric"><b>{ui.get('duration_sec','-')}s</b><span>UI 耗时</span></div>
  </div>
  <section><h2>质量门禁</h2><p class="decision">{decision_text}</p><ul>{''.join(f'<li>{x}</li>' for x in gate['rules'])}</ul><p>{'；'.join(gate['blockers']) or '暂无阻断项。'}</p></section>
  <section><h2>业务结果</h2><p>平台已从一次产品级触发中生成受控测试资产、接口测试资产、UI 旅程 DSL、浏览器证据、失败归因和可视化报告。</p></section>
  <section><h2>接口测试报告</h2>
    <p>OpenAPI 接口数：{asset.get('openapi_endpoint_count','-')} · 接口用例数：{test_assets.get('api_cases','-')} · P0/P1 风险：{risk.get('P0','-')}/{risk.get('P1','-')}</p>
    <p><a href="../visual_report.html" target="_blank">打开完整接口测试报告</a></p>
    <table><thead><tr><th>方法</th><th>路径</th><th>风险</th><th>鉴权</th><th>金额</th><th>库存</th><th>测试类型</th></tr></thead><tbody>{api_rows}</tbody></table>
    <h3>接口实时执行失败</h3>
    <p>状态：{api_execution.get('status','-')} · 通过/失败：{api_execution.get('passed','-')}/{api_execution.get('failed','-')} · 基础地址：{api_execution.get('base_url','-')}</p>
    <table><thead><tr><th>用例</th><th>方法</th><th>路径</th><th>预期</th><th>实际</th><th>失败原因</th></tr></thead><tbody>{api_failure_rows}</tbody></table>
  </section>
  <section><h2>UI 旅程报告</h2>
    <p>状态：{status_text} · 总步骤：{ui.get('total_steps','-')} · 通过：{ui.get('passed_steps','-')} · 失败：{ui.get('failed_steps','-')}</p>
    <p><a href="../ui/ui_visual_report.html" target="_blank">打开完整 UI 旅程报告</a></p>
  </section>
  <section><h2>产物清单</h2><table><tbody>{rows}</tbody></table></section>
</main>
</body>
</html>"""


def render_md(data: Dict[str, Any]) -> str:
    gate = data["quality_gate"]
    artifacts = "\n".join(f"- {k}: `{v}`" for k, v in data["artifacts"].items())
    return f"""# 企业级 AI 自动化报告

## 质量门禁

- 结论：{gate['decision']}
- 阻断项：{', '.join(gate['blockers']) if gate['blockers'] else '无'}

## 业务结果

本次产品级触发已生成 AI 测试资产、接口资产、UI 旅程 DSL、浏览器证据、失败归因和可视化报告。

## 接口测试报告

- 完整报告：`{data['artifacts']['asset_visual_report']}`
- OpenAPI 接口数：{data['asset_pipeline'].get('project_overview', {}).get('openapi_endpoint_count')}
- 接口测试用例数：{data['asset_pipeline'].get('test_asset_overview', {}).get('api_cases')}
- P0/P1 风险：{data['asset_pipeline'].get('risk_overview', {}).get('P0')}/{data['asset_pipeline'].get('risk_overview', {}).get('P1')}

## UI 旅程报告

- 完整报告：`{data['artifacts']['ui_visual_report']}`
- 状态：{data['ui_journey'].get('execution', {}).get('status')}

## 产物清单

{artifacts}
"""


def run_enterprise_ai_automation(config: EnterpriseAutomationConfig, root: Path | None = None) -> Dict[str, Any]:
    root = root or Path.cwd()
    out_dir = config.output_root / config.project
    workspace = config.workspace_root / config.project
    enterprise_dir = out_dir / "enterprise_automation"
    enterprise_dir.mkdir(parents=True, exist_ok=True)

    sut_state = ensure_demo_sut(config, root)

    asset_data = run_asset_pipeline(config.project, config.inputs, config.workspace_root, out_dir, config.engine)
    ui_config = UIJourneyConfig(
        project=config.project,
        base_url=config.base_url,
        username=config.username,
        password=config.password,
        admin_username=config.admin_username,
        admin_password=config.admin_password,
        execute_browser=config.execute_browser,
        browser=config.browser,
        headless=config.headless,
        max_pages=config.max_pages,
        mode=config.engine,
    )
    ui_data = run_ui_journey(config.project, config.inputs, config.workspace_root, config.output_root, ui_config)
    gate = quality_gate(asset_data, ui_data)

    artifacts = {
        "asset_visual_report": str(out_dir / "visual_report.html"),
        "ui_visual_report": str(out_dir / "ui" / "ui_visual_report.html"),
        "ui_evidence_bundle": str(workspace / "ui_execution" / "evidence_bundle.json"),
        "ui_journey_dsl": str(workspace / "ui_journeys" / "ui_journey_dsl.yaml"),
        "quality_gate_policy": str(out_dir / "quality_gate_policy.json"),
        "enterprise_report": str(enterprise_dir / "enterprise_ai_automation_report.html"),
    }
    data = {
        "project": config.project,
        "project_name": config.project_name,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sut": sut_state,
        "asset_pipeline": asset_data,
        "ui_journey": ui_data,
        "quality_gate": gate,
        "artifacts": artifacts,
    }
    write_json(enterprise_dir / "enterprise_ai_automation_report.json", data)
    (enterprise_dir / "enterprise_ai_automation_report.html").write_text(render_html(data), encoding="utf-8")
    (enterprise_dir / "enterprise_ai_automation_report.md").write_text(render_md(data), encoding="utf-8")
    return data
