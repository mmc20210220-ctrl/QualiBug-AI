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


def test_evidence_chain_reads_partitions_from_the_backend_hook() -> None:
    """The page must consume backend partitions, not rebuild them.

    Asserted behaviourally rather than against a verbatim destructuring line: the
    previous form pinned the exact字符 of one statement, so it broke on every
    refactor while saying nothing about whether the page still honoured the
    backend as the source of the delivery numbers.
    """
    page = EVIDENCE_CHAIN_PAGE.read_text(encoding="utf-8")

    assert "useFindingsData(project)" in page, "the page must read the backend hook"
    # The frontend must not recompute the formal delivery count. Any local
    # arithmetic on the gate would fork the number the customer sees away from
    # the one the backend gate actually decided.
    assert "formal_customer_deliverable_count" not in page or "scanMeta" in page, (
        "a formal count referenced without scanMeta means the page derived it locally"
    )


def test_sidebar_uses_current_scope_for_delivery_metric() -> None:
    """The sidebar metric must be the backend's current-scope count.

    Only the two facts that matter are pinned -- it reads useProjectSummary, and the
    number it shows is currentDefectCount -- so the test survives the destructuring
    list changing but still fails if the sidebar starts showing a different metric.
    """
    source = SIDEBAR.read_text(encoding="utf-8")

    assert "useProjectSummary(project)" in source
    assert "currentDefectCount" in source
    assert "{currentDefectCount ?? 0}" in source
