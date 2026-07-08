"""Contract test: the customer-facing one-click "run detection" entry must exist
and be fully wired end-to-end.

Regression guard for the exact main-chain breakpoint found in this round: the
"single-backend cleanup" commit deleted the Run Center page (EnterpriseCampaigns)
and its /campaigns route while leaving the Sidebar/Topbar CTA and the closure test
dangling — so the primary customer action ("进入运行中心" → 一键运行检测) navigated
to a route that did not exist and `runV12Scan` (`/api/v1/scan`) was never invoked
from any UI.

These assertions read the real source files (no mocks) so the wiring cannot silently
rot again.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.tsx"
RUN_CENTER = ROOT / "frontend" / "src" / "pages" / "EnterpriseCampaigns.tsx"
CLIENT = ROOT / "frontend" / "src" / "api" / "client.ts"
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.tsx"
TOPBAR = ROOT / "frontend" / "src" / "components" / "Topbar.tsx"
SERVICE = ROOT / "ai_test_asset_center" / "private_pilot_service.py"


def test_run_center_page_exists_and_is_routed() -> None:
    app = APP.read_text(encoding="utf-8")
    assert "import { EnterpriseCampaigns } from './pages/EnterpriseCampaigns';" in app
    assert 'path="/campaigns" element={<EnterpriseCampaigns />}' in app
    # The Run Center page must be a real file.
    assert RUN_CENTER.exists(), "Run Center page (EnterpriseCampaigns.tsx) must exist"


def test_run_center_invokes_real_scan_endpoint_and_refreshes_downstream() -> None:
    run_center = RUN_CENTER.read_text(encoding="utf-8")
    # One-click scan calls the single-backend /api/v1/scan wrapper.
    assert "runV12Scan(project" in run_center
    assert "执行标准扫描" in run_center
    # Auto-reads project context / readiness before running.
    assert "getScanPreflight" in run_center
    assert "getServiceCredentials" in run_center
    assert "getKnowledgeAsset" in run_center
    # Passes real scope / environment / test data contract into the scan body.
    assert "scope_id" in run_center
    assert "environment_ref" in run_center
    assert "test_data_contract" in run_center
    # On completion, downstream Dashboard / Findings / EvidenceChain auto-refresh.
    assert "emitScanCompleted(project)" in run_center
    assert "navigateToProjectPath('/dashboard', project)" in run_center
    assert "navigateToProjectPath('/findings', project)" in run_center
    assert "navigateToProjectPath('/evidence', project)" in run_center


def test_run_center_surfaces_blocked_and_partial_states_honestly() -> None:
    run_center = RUN_CENTER.read_text(encoding="utf-8")
    # Blocked / plan-only / partial must not be dressed up as a clean pass.
    assert "plan_only" in run_center
    assert "partial_coverage" in run_center
    assert "coverage_gaps" in run_center
    assert "test_data_plan" in run_center
    # Missing prerequisites must be shown, not silently swallowed.
    assert "运行前检查" in run_center
    assert "blockers" in run_center


def test_client_exposes_scan_preflight_against_v1_endpoint() -> None:
    client = CLIENT.read_text(encoding="utf-8")
    assert "export async function getScanPreflight" in client
    assert "/scan/preflight?project=" in client
    assert "export function runV12Scan" in client
    assert "/v1/scan" in client


def test_nav_has_no_dead_source_assets_entry() -> None:
    sidebar = SIDEBAR.read_text(encoding="utf-8")
    topbar = TOPBAR.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    # /source-assets was a dangling nav link with no route/page; it must be gone.
    assert "source-assets" not in sidebar
    assert "/source-assets" not in topbar
    assert "/source-assets" not in app
    # The Run Center entry stays as the single formal execution entry.
    assert "label: '运行中心'" in sidebar
    assert "navigateToProjectPath('/campaigns', project)" in topbar


def test_backend_serves_scan_preflight_on_get() -> None:
    service = SERVICE.read_text(encoding="utf-8")
    # The preflight readiness endpoint the Run Center calls must be dispatched on GET.
    assert '"/api/v1/scan/preflight"' in service
    assert "_handle_scan_preflight" in service
    # And the actual scan endpoint the one-click button hits must exist.
    assert '"/api/v1/scan"' in service
