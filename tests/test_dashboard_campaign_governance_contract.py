from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT_API = ROOT / "frontend" / "src" / "api" / "client.ts"
DASHBOARD_PAGE = ROOT / "frontend" / "src" / "pages" / "Dashboard.tsx"


def test_campaign_governance_type_exposes_current_and_family_projection_fields() -> None:
    source = CLIENT_API.read_text(encoding="utf-8")

    assert "export type CampaignGovernance = {" in source
    assert "current_campaign_confirmed_slice_count?: number;" in source
    assert "current_campaign_customer_ready_defect_count?: number;" in source
    assert "current_campaign_bundle_finding_count_raw?: number;" in source
    assert "family_customer_ready_defect_count?: number;" in source
    assert "family_report_real_finding_count?: number;" in source
    assert "family_historical_carryover_defect_count?: number;" in source
    assert "confirmed_shelf_alignment_status?: string;" in source
    assert "confirmed_shelf_reporting_scope?: string;" in source


def test_dashboard_campaign_governance_reads_four_tier_story_from_campaign_summary() -> None:
    page = DASHBOARD_PAGE.read_text(encoding="utf-8")

    assert "const continuousCampaign = asRecord(record.continuous_discovery_campaign);" in page
    assert "const campaignSummary = asRecord(continuousCampaign.summary);" in page
    assert "const campaignConfirmed = firstNum(campaignSummary.current_campaign_confirmed_slice_count, campaignSummary.confirmed_slice_count, campaign.confirmed_slice_count);" in page
    assert "const campaignCurrentDefects = asNum(campaignSummary.current_campaign_customer_ready_defect_count);" in page
    assert "const campaignCurrentRawFindings = asNum(campaignSummary.current_campaign_bundle_finding_count_raw);" in page
    assert "const campaignFamilyDefects = asNum(campaignSummary.family_customer_ready_defect_count, totalRiskCount);" in page
    assert "const campaignCarryoverDefects = asNum(campaignSummary.family_historical_carryover_defect_count);" in page


def test_dashboard_campaign_governance_surfaces_receipts_defects_shelf_and_carryover_labels() -> None:
    page = DASHBOARD_PAGE.read_text(encoding="utf-8")

    assert "Campaign 治理" in page
    assert "<span><em>确认回执</em><b>{campaignConfirmed}/{campaignAttempted || 0}</b></span>" in page
    assert "<span><em>本轮缺陷</em><b>{currentScanDefects || campaignCurrentDefects || 0} 条</b></span>" in page
    assert "<span><em>缺陷货架</em><b>{familyShelfDefects} 条</b></span>" in page
    assert "<span><em>历史延续</em><b>{campaignCarryoverDefects} 条</b></span>" in page
    assert "<span><em>本轮原始 finding</em><b>{campaignCurrentRawFindings || currentScanFindings}</b></span>" in page
    assert "<span><em>口径说明</em><b>回执 {campaignConfirmed} → 本轮缺陷 {currentScanDefects || campaignCurrentDefects || 0} → 货架 {familyShelfDefects}</b></span>" in page
