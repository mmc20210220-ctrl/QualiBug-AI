from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_GATE = ROOT / "frontend" / "src" / "api" / "data.ts"
DASHBOARD_PAGE = ROOT / "frontend" / "src" / "pages" / "Dashboard.tsx"
FINDING_TYPES = ROOT / "frontend" / "src" / "types" / "index.ts"


def _source() -> str:
    return FRONTEND_GATE.read_text(encoding="utf-8")



def _dashboard_surface() -> str:
    """Dashboard.tsx plus the modules its logic was extracted into.

    These assertions name capabilities the dashboard must surface. Pinning them to
    Dashboard.tsx alone made a pure extraction refactor -- moving eight helpers into
    lib/dashboard-utils.ts -- read as the capabilities disappearing. The surface is
    the page and the modules it was split into, so the test tracks the behaviour
    rather than the file it currently lives in.
    """
    parts = [DASHBOARD_PAGE]
    parts.append(ROOT / "frontend" / "src" / "lib" / "dashboard-utils.ts")
    components = ROOT / "frontend" / "src" / "components" / "dashboard"
    if components.is_dir():
        parts.extend(sorted(components.glob("*.tsx")))
    return "\n".join(p.read_text(encoding="utf-8") for p in parts if p.is_file())


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


def test_dashboard_surfaces_delivery_gate_patch_status() -> None:
    page = _dashboard_surface()

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
    page = _dashboard_surface()

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
    page = _dashboard_surface()

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
    page = _dashboard_surface()

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
