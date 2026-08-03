from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.tsx"
TOPBAR = ROOT / "frontend" / "src" / "components" / "Topbar.tsx"
SETTINGS = ROOT / "frontend" / "src" / "pages" / "Settings.tsx"
MATERIALS = ROOT / "frontend" / "src" / "pages" / "Materials.tsx"
RUN_CENTER = ROOT / "frontend" / "src" / "pages" / "EnterpriseCampaigns.tsx"
FINDINGS = ROOT / "frontend" / "src" / "pages" / "Findings.tsx"
DASHBOARD = ROOT / "frontend" / "src" / "pages" / "Dashboard.tsx"
EVIDENCE = ROOT / "frontend" / "src" / "pages" / "EvidenceChain.tsx"
CLIENT = ROOT / "frontend" / "src" / "api" / "client.ts"
README = ROOT / "README.md"


def test_frontend_navigation_uses_run_center_as_single_formal_execution_entry() -> None:
    sidebar = SIDEBAR.read_text(encoding="utf-8")
    topbar = TOPBAR.read_text(encoding="utf-8")
    run_center = RUN_CENTER.read_text(encoding="utf-8")

    assert "label: '运行中心'" in sidebar
    assert "navigateToProjectPath('/campaigns', project)" in topbar
    assert "进入运行中心" in topbar
    assert "usePageTitle('运行中心')" in run_center
    assert "执行标准扫描" in run_center
    assert "受控运行结果" in run_center
    assert "emitScanCompleted(project)" in run_center


def test_frontend_settings_and_materials_surface_readiness_and_parse_summary() -> None:
    settings = SETTINGS.read_text(encoding="utf-8")
    materials = MATERIALS.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    assert "后端联通" in settings
    assert "鉴权材料" in settings
    assert "数据库校验" in settings
    assert "解析结果" in materials
    assert "可执行资料" in materials
    assert "核心资料覆盖" in materials
    assert "正式客户前端：`frontend/` React 控制台" in readme


def _dashboard_surface() -> str:
    """Dashboard.tsx plus the modules its surfaces were extracted into.

    Same rationale as test_customer_delivery_gate_contract: the regression
    closure cards live in components/dashboard/*.tsx after the extraction
    refactor, so the surface is the page and its modules; the test tracks
    the capabilities rather than the file they currently live in.
    """
    parts = [DASHBOARD]
    components = DASHBOARD.parent.parent / "components" / "dashboard"
    if components.is_dir():
        parts.extend(sorted(components.glob("*.tsx")))
    return "\n".join(p.read_text(encoding="utf-8") for p in parts if p.is_file())


def test_frontend_dashboard_and_findings_surface_regression_closure() -> None:
    findings = FINDINGS.read_text(encoding="utf-8")
    dashboard = _dashboard_surface()
    evidence = EVIDENCE.read_text(encoding="utf-8")
    client = CLIENT.read_text(encoding="utf-8")

    assert "生命周期" in findings
    assert "待执行回归" in findings
    assert "回归验证" in findings
    assert "回归历史" in findings
    assert "执行 Release 回归" in findings
    assert "回归闭环" in dashboard
    assert "回归趋势" in dashboard
    assert "发布 / 交付建议" in dashboard
    assert "真实验真摘要" in dashboard
    assert "最小双轮验真" in dashboard
    assert "历史轮次" in dashboard
    assert "已覆盖缺陷" in dashboard
    assert "最近回归" in dashboard
    assert "执行 Smoke 回归" in dashboard
    assert "执行 Release 回归" in dashboard
    assert "回归闭环" in evidence
    assert "最近轨迹" in evidence
    assert "export async function runRegression" in client
    assert "/regression/run" in client
