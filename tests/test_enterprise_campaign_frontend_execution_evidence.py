from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "src" / "pages" / "EnterpriseCampaigns.tsx"
API = ROOT / "frontend" / "src" / "api" / "enterprise.ts"


def test_enterprise_campaign_page_surfaces_real_execution_evidence_fields() -> None:
    page = PAGE.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")

    assert "auto_har?: JsonRecord" in api
    assert "execution_evidence_summary?: JsonRecord" in api
    assert "真实执行证据" in page
    assert "HAR 状态" in page
    assert "harEntries(result.auto_har).length" in page
    assert "HTTP {harStatusLabel(entry)}" in page
