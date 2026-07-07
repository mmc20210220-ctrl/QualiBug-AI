"""Evidence-first release gate for enterprise Campaign outcomes.

The gate deliberately prefers ``not_ready`` over an optimistic pass. It never
creates findings and it does not reinterpret candidates as confirmed defects.
"""
from __future__ import annotations

from typing import Any


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _confirmed_findings(value: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        if _text(item.get("confirmation_status")).lower() == "confirmed":
            results.append(item)
    return results


def evaluate_release_gate(
    *,
    campaign: dict[str, Any] | None,
    execution_status: str,
    runtime_contract: dict[str, Any] | None,
    evidence_bundle: dict[str, Any] | None,
    evidence_bundle_verification: dict[str, Any] | None,
    test_data_plan: dict[str, Any] | None,
    findings: list[dict[str, Any]] | None,
    coverage_gaps: list[dict[str, Any]] | None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an auditable release decision from actual execution artifacts.

    A plan-only run is incomplete rather than failed. A blocked contract, a
    deferred/blocked Campaign, or a confirmed P0 finding is an explicit block.
    """
    campaign_value = _record(campaign)
    runtime = _record(runtime_contract)
    bundle = _record(evidence_bundle)
    bundle_verification = _record(evidence_bundle_verification)
    data_plan = _record(test_data_plan)
    gate_policy = _record(policy)
    reasons: list[dict[str, str]] = []
    campaign_status = _text(campaign_value.get("campaign_status")).lower()
    execution = _text(execution_status).lower()
    runtime_status = _text(runtime.get("status")).lower()
    data_status = _text(data_plan.get("status")).lower()
    bundle_status = _text(bundle.get("status")).lower()
    bundle_valid = bundle_verification.get("valid") is True
    allow_p1 = gate_policy.get("allow_confirmed_p1") is True

    if campaign_status in {"blocked", "coverage_deferred"}:
        reasons.append({"code": "CAMPAIGN_NOT_CLOSED", "detail": _text(campaign_value.get("coverage_deferred_reason")) or campaign_status})
    elif campaign_status != "completed":
        reasons.append({"code": "CAMPAIGN_NOT_COMPLETED", "detail": campaign_status or "campaign_status_missing"})
    if execution != "completed":
        reasons.append({"code": "RUNTIME_EXECUTION_NOT_COMPLETED", "detail": execution or "execution_status_missing"})
    if runtime_status != "approved":
        reasons.append({"code": "RUNTIME_CONTRACT_NOT_APPROVED", "detail": runtime_status or "runtime_contract_missing"})
    if data_status != "ready":
        reasons.append({"code": "TEST_DATA_NOT_READY", "detail": data_status or "test_data_plan_missing"})
    if bundle_status != "persisted" or not bundle_valid:
        reasons.append({"code": "EVIDENCE_BUNDLE_NOT_VERIFIED", "detail": _text(bundle_verification.get("code")) or bundle_status or "evidence_bundle_missing"})
    if _list(coverage_gaps):
        reasons.append({"code": "COVERAGE_GAPS_REMAIN", "detail": str(len(_list(coverage_gaps)))})

    confirmed = _confirmed_findings(findings)
    p0 = [item for item in confirmed if _text(item.get("severity")).upper() == "P0"]
    p1 = [item for item in confirmed if _text(item.get("severity")).upper() == "P1"]
    if p0:
        reasons.append({"code": "CONFIRMED_P0_FINDINGS", "detail": str(len(p0))})
    if p1 and not allow_p1:
        reasons.append({"code": "CONFIRMED_P1_FINDINGS", "detail": str(len(p1))})

    campaign_not_closed_blocks = gate_policy.get("campaign_not_closed_verdict") != "not_ready"
    hard_block = any(
        reason["code"] == "CONFIRMED_P0_FINDINGS" or (campaign_not_closed_blocks and reason["code"] == "CAMPAIGN_NOT_CLOSED")
        for reason in reasons
    ) or runtime_status == "blocked"
    if not reasons:
        verdict, status = "pass", "release_ready"
    elif hard_block:
        verdict, status = "fail", "blocked"
    else:
        verdict, status = "not_ready", "inconclusive"
    return {
        "schema_version": "qualibug-release-gate-v1",
        "verdict": verdict,
        "status": status,
        "campaign_id": _text(campaign_value.get("campaign_id")),
        "campaign_status": campaign_status,
        "execution_status": execution,
        "runtime_contract_status": runtime_status,
        "confirmed_finding_count": len(confirmed),
        "confirmed_p0_count": len(p0),
        "confirmed_p1_count": len(p1),
        "evidence_bundle_id": _text(bundle.get("bundle_id")),
        "evidence_bundle_verified": bundle_valid,
        "reasons": reasons,
    }
