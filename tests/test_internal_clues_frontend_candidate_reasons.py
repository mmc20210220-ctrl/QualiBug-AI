from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTERNAL_CLUES = ROOT / "frontend" / "src" / "pages" / "InternalClues.tsx"
TYPES = ROOT / "frontend" / "src" / "types" / "index.ts"


def test_internal_clues_page_surfaces_candidate_gate_failures_and_block_reasons() -> None:
    page = INTERNAL_CLUES.read_text(encoding="utf-8")
    types = TYPES.read_text(encoding="utf-8")

    assert "getClueReasonCodes" in page
    assert "gate_failures" in page
    assert "evidenceStatus.missing_requirements" in page
    assert "record.execution_block" in page
    assert "record.confirmation_status" in page
    assert "未进入客户缺陷的原因" in page
    assert "customer_delivery_gate_explanations" in types
    assert "confirmation_status?: string" in types


def test_internal_clues_page_links_clues_to_execution_and_evidence_workspaces() -> None:
    page = INTERNAL_CLUES.read_text(encoding="utf-8")

    assert "补证动作" in page
    assert "进入运行中心补证" in page
    assert "navigateToProjectPath('/campaigns', project)" in page
    assert "navigateToProjectPath('/evidence', project)" in page
    assert "navigateToProjectPath('/materials', project)" in page
