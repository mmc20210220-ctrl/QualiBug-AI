from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT_API = ROOT / "frontend" / "src" / "api" / "client.ts"
DASHBOARD_PAGE = ROOT / "frontend" / "src" / "pages" / "Dashboard.tsx"


def test_campaign_governance_type_keeps_legacy_projection_fields_for_compatibility() -> None:
    source = CLIENT_API.read_text(encoding="utf-8")

    assert "export type CampaignGovernance = {" in source
    assert "current_campaign_confirmed_slice_count?: number;" in source
    assert "current_campaign_customer_ready_defect_count?: number;" in source
    assert "current_campaign_bundle_finding_count_raw?: number;" in source
    assert "family_customer_ready_defect_count?: number;" in source
    assert "family_historical_carryover_defect_count?: number;" in source


def test_dashboard_reads_formal_delivery_count_from_backend_projection() -> None:
    page = DASHBOARD_PAGE.read_text(encoding="utf-8")

    assert "const formalCounts = asRecord(record.formal_count_projection);" in page
    assert "const continuousCampaign = asRecord(record.continuous_discovery_campaign);" in page
    assert "const campaignSummary = asRecord(continuousCampaign.summary);" in page
    assert "const campaignConfirmed = firstNum(campaignSummary.current_campaign_confirmed_slice_count, campaignSummary.confirmed_slice_count, campaign.confirmed_slice_count);" in page
    assert "const campaignCurrentRawFindings = asNum(campaignSummary.current_campaign_bundle_finding_count_raw);" in page
    assert "const currentScanDefects = asNum(formalCounts.formal_customer_deliverable_count, totalRiskCount);" in page
    assert "const familyShelfDefects = currentScanDefects;" in page


def test_dashboard_separates_formal_delivery_from_internal_findings() -> None:
    page = DASHBOARD_PAGE.read_text(encoding="utf-8")

    assert "<span><em>本轮缺陷</em><b>{currentScanDefects} 条</b></span>" in page
    assert "<span><em>缺陷货架</em><b>{familyShelfDefects} 条</b></span>" in page
    assert "内部原始 finding（非客户交付）" in page
    assert "原始 finding 仅供内部观测" in page
