from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_GATE = ROOT / "frontend" / "src" / "api" / "data.ts"
INTERNAL_CLUES_PAGE = ROOT / "frontend" / "src" / "pages" / "InternalClues.tsx"
DASHBOARD_PAGE = ROOT / "frontend" / "src" / "pages" / "Dashboard.tsx"
FINDING_TYPES = ROOT / "frontend" / "src" / "types" / "index.ts"


def _source() -> str:
    return FRONTEND_GATE.read_text(encoding="utf-8")


def test_customer_ready_findings_require_explicit_business_evidence_status() -> None:
    source = _source()

    assert "function hasPassedBusinessEvidenceStatus" in source
    assert "if (!status) return false" in source
    assert "semantic !== 'SEMANTIC_CONFIRMED'" in source
    assert "business !== 'VALIDATED'" in source
    assert "missing.length === 0" in source


def test_customer_ready_findings_require_validated_replayable_evidence_quality() -> None:
    source = _source()

    assert "CUSTOMER_READY_MIN_EVIDENCE_SCORE = 90" in source
    assert "level === 'validated'" in source
    assert "score >= CUSTOMER_READY_MIN_EVIDENCE_SCORE" in source
    assert "Boolean(quality?.can_reproduce)" in source


def test_customer_ready_findings_require_hard_evidence_and_failure_assertion() -> None:
    source = _source()

    assert "function hasExplicitFailureAssertion" in source
    assert "hasRealReplayAsset(finding)" in source
    assert "hasCustomerFacingHardEvidence(finding)" in source
    assert "hasRequest && hasResponse && hasAssertion && hasTimestamp" in source


def test_customer_ready_findings_keep_internal_clues_out_of_customer_list() -> None:
    source = _source()

    assert "finding.customer_delivery_status !== 'defect'" in source
    assert "finding.bug_status !== 'reproduced'" in source
    assert "!finding.gate_passed" in source
    assert "finding.reproduction?.is_synthetic" in source
    assert "route_blocked" in source
    assert "auth_blocked" in source
    assert "environment_blocked" in source
    assert "coverage_gap" in source
    assert "not_reproduced" in source


def test_internal_clue_page_surfaces_delivery_gate_reasons() -> None:
    page = INTERNAL_CLUES_PAGE.read_text(encoding="utf-8")
    types = FINDING_TYPES.read_text(encoding="utf-8")

    assert "customer_delivery_gate_reasons?: string[]" in types
    assert "GATE_REASON_LABELS" in page
    assert "customer_delivery_gate_reasons" in page
    assert "未进入客户缺陷的原因" in page
    assert "explainGateReason" in page


def test_internal_clue_page_prefers_backend_gate_explanations_with_reason_fallback() -> None:
    page = INTERNAL_CLUES_PAGE.read_text(encoding="utf-8")

    assert "type GateExplanation" in page
    assert "function getGateExplanations" in page
    assert "customer_delivery_gate_explanations" in page
    assert "if (valid.length > 0) return valid" in page
    assert "return reasonCodes.map" in page
    assert "reason.next_action" in page
    assert "下一步：" in page


def test_dashboard_surfaces_delivery_gate_patch_status() -> None:
    page = DASHBOARD_PAGE.read_text(encoding="utf-8")

    assert "function getGatePatchStatus" in page
    assert "customer_delivery_gate_patch" in page
    assert "gatePatchLabel" in page
    assert "交付 Gate 诊断" in page
    assert "严格 Gate 已启用" in page
    assert "严格 Gate 未确认" in page
    assert "active_partition_name" in page
    assert "has_original_partition" in page


def test_dashboard_surfaces_main_chain_contract_status() -> None:
    page = DASHBOARD_PAGE.read_text(encoding="utf-8")

    assert "MAIN_CHAIN_STAGE_LABELS" in page
    assert "function getMainChainContract" in page
    assert "function getMainChainSummary" in page
    assert "function getMainChainStages" in page
    assert "main_chain_contract" in page
    assert "main_chain_contract_summary" in page
    assert "主链路闭合状态" in page
    assert "主链路已闭合" in page
    assert "主链路未闭合" in page
    assert "第一断点" in page
    assert "first_blocked_next_action" in page
    assert "企业资料" in page
    assert "解析知识" in page
    assert "测试计划" in page
    assert "真实执行" in page
    assert "Bug 发现" in page
    assert "证据链" in page
