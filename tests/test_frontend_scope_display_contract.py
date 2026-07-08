from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_API = ROOT / "frontend" / "src" / "api" / "data.ts"
DASHBOARD_PAGE = ROOT / "frontend" / "src" / "pages" / "Dashboard.tsx"
EVIDENCE_CHAIN_PAGE = ROOT / "frontend" / "src" / "pages" / "EvidenceChain.tsx"
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.tsx"


def test_use_findings_data_exposes_scan_meta_for_scope_aware_pages() -> None:
    source = DATA_API.read_text(encoding="utf-8")

    assert "const [scanMeta, setScanMeta] = useState<JsonRecord>({});" in source
    assert "setScanMeta(asRecord(record.scan_meta));" in source
    assert "setScanMeta({});" in source
    assert "return { findings, clues, commercialAssets, scanMeta, loading, error, refetch: load };" in source


def test_pipeline_normalization_prefers_continuous_campaign_and_current_scope_summary() -> None:
    source = DATA_API.read_text(encoding="utf-8")

    assert "const nestedCampaign = asRecord(continuous.campaign);" in source
    assert "if (Object.keys(nestedCampaign).length > 0) return nestedCampaign;" in source
    assert "const summary = asRecord(continuous.summary);" in source
    assert "const currentCampaignScope = asRecord(" in source
    assert "current_campaign_scope: currentCampaignScope," in source
    assert "scope_id: asString(campaign.scope_id || summary.scope_id || currentRun.scope_id)," in source
    assert "environment_ref: asString(campaign.environment_ref || campaign.target_environment || summary.environment_ref || summary.target_environment || currentRun.environment_ref || currentRun.target_environment)," in source
    assert "const currentScopeFindingCount = firstFiniteNumber(" in source
    assert "summary.current_campaign_bundle_finding_count_raw" in source
    assert "const currentScopeDefectCount = firstFiniteNumber(" in source
    assert "summary.current_campaign_customer_ready_defect_count" in source
    assert "current_report_total_findings: currentScopeFindingCount," in source
    assert "current_report_customer_ready_defect_count: currentScopeDefectCount," in source
    assert "family_customer_ready_defect_count: firstFiniteNumber(" in source


def test_project_summary_reuses_normalized_campaign_snapshot() -> None:
    source = DATA_API.read_text(encoding="utf-8")

    assert "const normalized = normalizeCampaignSnapshot(raw);" in source
    assert "const record = asRecord(normalized);" in source
    assert "const findings = getReportFindings(raw);" in source
    assert "const scanMeta = asRecord(field(normalized, 'scan_meta'));" in source
    assert "resolvedProjectId: getResolvedProjectId(normalized)," in source
    assert "clueCount: getReportClues(raw).length," in source


def test_dashboard_surfaces_current_scan_and_family_shelf_separately() -> None:
    page = DASHBOARD_PAGE.read_text(encoding="utf-8")

    assert "const currentScanDefects = asNum(scanMeta.current_report_customer_ready_defect_count" in page
    assert "const familyShelfDefects = asNum(scanMeta.family_customer_ready_defect_count, campaignFamilyDefects);" in page
    assert "const focusFindings = currentScanDefects > 0 ? topFindings : [];" in page
    assert "const shelfCarryoverCount = Math.max(0, familyShelfDefects - currentScanDefects);" in page
    assert "<span className=\"summary-pill strong\">本轮缺陷 {currentScanDefects}</span>" in page
    assert "<span className=\"summary-pill\">缺陷货架 {familyShelfDefects}</span>" in page
    assert "当前货架仍保留 {familyShelfDefects} 条历史缺陷，但本轮没有新增 confirmed 缺陷。" in page
    assert "{ label: '本轮可交付', val: currentScanDefects, tone: 'primary'" in page
    assert "{ label: '缺陷货架', val: familyShelfDefects, tone: familyShelfDefects > currentScanDefects ? 'warning' : 'neutral'" in page
    assert "本页显式区分本轮扫描与累计 defect shelf" in page


def test_evidence_chain_surfaces_scan_scope_before_evidence_pack_counts() -> None:
    page = EVIDENCE_CHAIN_PAGE.read_text(encoding="utf-8")

    assert "const { findings, clues, commercialAssets, scanMeta, loading } = useFindingsData(project);" in page
    assert "const currentScanDefects = asNum(scanMeta.current_report_customer_ready_defect_count, asNum(scanMeta.customer_ready_defects, customerFindings.length));" in page
    assert "const familyShelfDefects = asNum(scanMeta.family_customer_ready_defect_count, findings.length);" in page
    assert "<span className=\"summary-pill strong\">本轮缺陷 {currentScanDefects}</span>" in page
    assert "<span className=\"summary-pill\">缺陷货架 {familyShelfDefects}</span>" in page
    assert "{ label: '本轮确认缺陷', value: currentScanDefects, tone: 'primary'" in page
    assert "{ label: '累计缺陷货架', value: familyShelfDefects, tone: familyShelfDefects > currentScanDefects ? 'warning' : 'neutral'" in page


def test_sidebar_uses_current_scope_for_delivery_metric_and_shelf_for_badge() -> None:
    source = SIDEBAR.read_text(encoding="utf-8")

    assert "const { projectName, findingsCount, currentDefectCount, clueCount, p0Count } = useProjectSummary(project);" in source
    assert "const shelfCount = findingsCount;" in source
    assert "<span>本轮可交付</span>" in source
    assert "<strong>{currentDefectCount ?? 0}</strong>" in source
    assert "? shelfCount" in source
    assert "? '已有历史货架'" in source
