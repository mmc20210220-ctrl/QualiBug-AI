from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.tsx"
TOPBAR = ROOT / "frontend" / "src" / "components" / "Topbar.tsx"
SETTINGS = ROOT / "frontend" / "src" / "pages" / "Settings.tsx"
MATERIALS = ROOT / "frontend" / "src" / "pages" / "Materials.tsx"
RUN_CENTER = ROOT / "frontend" / "src" / "pages" / "EnterpriseCampaigns.tsx"
RUN_PREFLIGHT = ROOT / "frontend" / "src" / "lib" / "run-preflight-presentation.ts"
FINDINGS = ROOT / "frontend" / "src" / "pages" / "Findings.tsx"
DASHBOARD = ROOT / "frontend" / "src" / "pages" / "Dashboard.tsx"
EVIDENCE = ROOT / "frontend" / "src" / "pages" / "EvidenceChain.tsx"
CLIENT = ROOT / "frontend" / "src" / "api" / "client.ts"
README = ROOT / "README.md"


def test_frontend_navigation_uses_run_center_as_single_formal_execution_entry() -> None:
    sidebar = SIDEBAR.read_text(encoding="utf-8")
    topbar = TOPBAR.read_text(encoding="utf-8")
    run_center = RUN_CENTER.read_text(encoding="utf-8")
    run_preflight = RUN_PREFLIGHT.read_text(encoding="utf-8")

    assert "label: '运行中心'" in sidebar
    assert "navigateToProjectPath('/campaigns', project)" in topbar
    assert "进入运行中心" in topbar
    assert "usePageTitle('运行中心')" in run_center
    assert "执行标准扫描" in run_preflight
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
    assert "资料类型" in materials
    assert "资料来源结构" in materials
    assert "正式客户前端：`frontend/` React 控制台" in readme


def _page_surface(page: Path, module_dir: str) -> str:
    """A page plus the sibling component modules its surfaces were extracted into.

    The extraction refactor moved the regression/verification closure cards,
    run-center decisions and finding verification panels out of the page files
    into components/<module_dir>/*.tsx. The customer-facing workflow was later
    renamed from "回归" to "验证" (verification-only), so the test tracks the
    capability copy as it lives today rather than the file it lives in or the
    pre-rename wording.
    """
    parts = [page]
    components = page.parent.parent / "components" / module_dir
    if components.is_dir():
        parts.extend(sorted(components.glob("*.tsx")))
    return "\n".join(p.read_text(encoding="utf-8") for p in parts if p.is_file())


def _dashboard_surface() -> str:
    return _page_surface(DASHBOARD, "dashboard")


def _findings_surface() -> str:
    return _page_surface(FINDINGS, "findings")


def _evidence_surface() -> str:
    return _page_surface(EVIDENCE, "findings")


def test_frontend_dashboard_and_findings_surface_regression_closure() -> None:
    findings = _findings_surface()
    dashboard = _dashboard_surface()
    evidence = _evidence_surface()
    client = CLIENT.read_text(encoding="utf-8")

    assert "真实验证历史" in findings
    assert "等待重新验证" in findings
    assert "验证闭环" in findings
    assert "验证历史" in findings
    assert "修复后重新验证" in findings
    assert "回归闭环" in dashboard
    assert "验证趋势" in dashboard
    assert "发布 / 交付建议" in dashboard
    assert "真实验真摘要" in dashboard
    assert "最小双轮验真" in dashboard
    assert "历史轮次" in dashboard
    assert "已纳入问题" in dashboard
    assert "最近验证" in dashboard
    assert "执行 Smoke 验证" in dashboard
    assert "执行 Release 验证" in dashboard
    assert "QualiBug 验证闭环" in evidence
    assert "真实验证历史" in evidence
    assert "export async function runRegression" in client
    assert "/regression/run" in client
