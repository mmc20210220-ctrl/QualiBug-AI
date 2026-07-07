from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.tsx"
TOPBAR = ROOT / "frontend" / "src" / "components" / "Topbar.tsx"
SETTINGS = ROOT / "frontend" / "src" / "pages" / "Settings.tsx"
MATERIALS = ROOT / "frontend" / "src" / "pages" / "EnterpriseMaterials.tsx"
RUN_CENTER = ROOT / "frontend" / "src" / "pages" / "EnterpriseCampaigns.tsx"
FINDINGS = ROOT / "frontend" / "src" / "pages" / "Findings.tsx"
DASHBOARD = ROOT / "frontend" / "src" / "pages" / "Dashboard.tsx"
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


def test_frontend_dashboard_and_findings_surface_regression_closure() -> None:
    findings = FINDINGS.read_text(encoding="utf-8")
    dashboard = DASHBOARD.read_text(encoding="utf-8")

    assert "回归状态" in findings
    assert "待执行回归" in findings
    assert "回归验证" in findings
    assert "回归闭环" in dashboard
    assert "已覆盖缺陷" in dashboard
    assert "最近回归" in dashboard
