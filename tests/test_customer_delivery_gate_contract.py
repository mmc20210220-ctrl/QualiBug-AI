from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_GATE = ROOT / "frontend" / "src" / "api" / "data.ts"
INTERNAL_CLUES_PAGE = ROOT / "frontend" / "src" / "pages" / "InternalClues.tsx"
DASHBOARD_PAGE = ROOT / "frontend" / "src" / "pages" / "Dashboard.tsx"
FINDING_TYPES = ROOT / "frontend" / "src" / "types" / "index.ts"


def _source() -> str:
    return FRONTEND_GATE.read_text(encoding="utf-8")


def test_customer_ready_findings_do_not_recompute_backend_business_gate() -> None:
    source = _source()

    assert "function hasPassedBusinessEvidenceStatus" not in source
    assert "Backend SSOT" in source or "Trust backend delivery gate projection" in source


def test_customer_ready_findings_do_not_recompute_backend_evidence_score() -> None:
    source = _source()

    assert "CUSTOMER_READY_MIN_EVIDENCE_SCORE" not in source
    assert "hasValidatedEvidenceQuality" not in source


def test_customer_ready_findings_require_hard_evidence_and_failure_assertion() -> None:
    source = _source()

    assert "function hasExplicitFailureAssertion" in source
    assert "export function hasRealReplayAsset" in source
    assert "export function hasCustomerFacingHardEvidence" in source
    assert "hasRequest && hasResponse && hasAssertion && hasTimestamp" in source
    assert "Backend SSOT" in source or "Trust backend delivery gate projection" in source


def test_customer_ready_findings_keep_internal_clues_out_of_customer_list() -> None:
    source = _source()

    assert "finding.customer_delivery_status !== 'defect'" in source
    assert "finding.bug_status !== 'reproduced'" in source
    assert "!finding.gate_passed" in source
    assert "finding.reproduction?.is_synthetic" in source
    assert "finding_classification" in source
    assert "classifiedRows(raw, 'deliverable', 'defects')" in source
    assert "classifiedRows(raw, 'candidate', 'clues')" in source
    assert "classifiedRows(raw, 'rejected', 'rejected_findings')" in source


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
    assert "core_gate_direct" in page
    assert "has_original_partition" not in page


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


def test_dashboard_surfaces_evidence_normalization_blockers() -> None:
    page = DASHBOARD_PAGE.read_text(encoding="utf-8")

    assert "EVIDENCE_MISSING_FIELD_LABELS" in page
    assert "function getEvidenceNormalizationSummary" in page
    assert "function evidenceMissingEntries" in page
    assert "function evidenceMissingFieldLabel" in page
    assert "evidence_bundle_normalization_summary" in page
    assert "missing_fields" in page
    assert "证据标准化阻断项" in page
    assert "证据标准化未完成" in page
    assert "证据字段已标准化" in page
    assert "execution_receipt" in page
    assert "reproduction / replay" in page
    assert "actual" in page
    assert "原始 request" in page
    assert "原始 response" in page


def test_dashboard_surfaces_per_item_evidence_actions() -> None:
    page = DASHBOARD_PAGE.read_text(encoding="utf-8")

    assert "function getEvidenceNormalizationReport" in page
    assert "function evidenceNormalizationItems" in page
    assert "function evidenceItemTitle" in page
    assert "function evidenceItemAction" in page
    assert "evidence_bundle_normalization_report" in page
    assert "blockedEvidenceActionItems" in page
    assert "evidence_id" in page
    assert "issue_id" in page
    assert "probe_id" in page
    assert "trace_id" in page
    assert "next_action" in page
    assert "未命名证据项" in page
    assert "补齐该证据项缺失字段后重新运行主链路合同" in page
