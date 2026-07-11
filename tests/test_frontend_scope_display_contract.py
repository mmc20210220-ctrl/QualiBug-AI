from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_API = ROOT / "frontend" / "src" / "api" / "data.ts"
DASHBOARD_PAGE = ROOT / "frontend" / "src" / "pages" / "Dashboard.tsx"
EVIDENCE_CHAIN_PAGE = ROOT / "frontend" / "src" / "pages" / "EvidenceChain.tsx"
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.tsx"


def test_use_findings_data_exposes_fixed_partitions_and_obligation_projection() -> None:
    source = DATA_API.read_text(encoding="utf-8")

    assert "const [rejected, setRejected] = useState<Finding[]>([])" in source
    assert "const [obligationProjection, setObligationProjection] = useState<JsonRecord>({})" in source
    assert "setRejected(getReportRejected(raw))" in source
    assert "setObligationProjection(asRecord(record.obligation_execution_projection || meta.obligation_execution_projection))" in source
    assert "return { findings, clues, rejected, commercialAssets, scanMeta, obligationProjection, loading, error, refetch: load };" in source


def test_pipeline_normalization_prefers_formal_backend_projection_for_delivery_count() -> None:
    source = DATA_API.read_text(encoding="utf-8")

    assert "const nestedCampaign = asRecord(continuous.campaign);" in source
    assert "if (Object.keys(nestedCampaign).length > 0) return nestedCampaign;" in source
    assert "const formalCounts = asRecord(record.formal_count_projection);" in source
    assert "formalCounts.formal_customer_deliverable_count" in source
    assert "current_report_customer_ready_defect_count: currentScopeDefectCount," in source
    assert "current_campaign_scope: currentCampaignScope," in source


def test_project_summary_reuses_formal_projection_and_backend_partitions() -> None:
    source = DATA_API.read_text(encoding="utf-8")

    assert "const normalized = normalizeCampaignSnapshot(raw);" in source
    assert "const findings = getReportFindings(raw);" in source
    assert "const formalCounts = asRecord(field(normalized, 'formal_count_projection'));" in source
    assert "currentDefectCount: firstFiniteNumber(formalCounts.formal_customer_deliverable_count" in source
    assert "clueCount: getReportClues(raw).length," in source


def test_dashboard_uses_one_formal_count_for_current_delivery_scope() -> None:
    page = DASHBOARD_PAGE.read_text(encoding="utf-8")

    assert "const currentScanDefects = asNum(formalCounts.formal_customer_deliverable_count, totalRiskCount);" in page
    assert "const familyShelfDefects = currentScanDefects;" in page
    assert "const focusFindings = currentScanDefects > 0 ? topFindings : [];" in page
    assert "{ label: '本轮可交付', val: currentScanDefects, tone: 'primary'" in page


def test_evidence_chain_displays_deliverable_candidate_rejected_partitions() -> None:
    page = EVIDENCE_CHAIN_PAGE.read_text(encoding="utf-8")

    assert "const { findings, clues, rejected, commercialAssets, scanMeta, obligationProjection, loading } = useFindingsData(project);" in page
    assert "const currentScanDefects = asNum(scanMeta.formal_customer_deliverable_count, customerFindings.length);" in page
    assert "const familyShelfDefects = currentScanDefects;" in page
    assert "<span className=\"summary-pill strong\">可交付 {currentScanDefects}</span>" in page
    assert "<span className=\"summary-pill\">候选 {clues.length}</span>" in page
    assert "<span className=\"summary-pill\">已拒绝 {rejected.length}</span>" in page


def test_sidebar_uses_current_scope_for_delivery_metric() -> None:
    source = SIDEBAR.read_text(encoding="utf-8")

    assert "const { projectName, findingsCount, currentDefectCount, clueCount, p0Count } = useProjectSummary(project);" in source
    assert "<strong>{currentDefectCount ?? 0}</strong>" in source
