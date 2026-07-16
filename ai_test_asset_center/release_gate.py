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


def reconcile_release_gate_with_run_readiness(
    release_gate: dict[str, Any] | None,
    run_delivery_readiness: dict[str, Any],
) -> dict[str, Any]:
    """Qualify the legacy gate with canonical current-run publication readiness."""

    gate = dict(_record(release_gate))
    readiness = _record(run_delivery_readiness)
    if readiness.get("schema_version") != "qualibug.run-delivery-readiness.v1":
        raise ValueError("run_delivery_readiness_schema_invalid")
    release_ready = readiness.get("release_ready")
    if not isinstance(release_ready, bool):
        raise ValueError("run_delivery_readiness_release_ready_invalid")
    status = _text(readiness.get("status")).upper()
    if release_ready != (status == "READY"):
        identities = _record(readiness.get("identities"))
        raise ValueError(
            "run_delivery_readiness_contradiction:"
            f"campaign={_text(identities.get('campaign_id'))}:"
            f"run={_text(identities.get('run_id'))}:status={status}:"
            f"release_ready={release_ready}"
        )

    previous = {
        "verdict": _text(gate.get("verdict")) or "not_ready",
        "status": _text(gate.get("status")) or "inconclusive",
    }
    reasons = [dict(item) for item in _list(gate.get("reasons")) if isinstance(item, dict)]
    existing_codes = {_text(item.get("code")) for item in reasons}
    for code in _list(readiness.get("reason_codes")):
        normalized = _text(code)
        if normalized and normalized not in existing_codes:
            reasons.append({
                "code": normalized,
                "detail": "current_run_formal_finding_publication_not_ready",
                "source": "run_delivery_readiness",
            })
            existing_codes.add(normalized)

    if not release_ready:
        if previous["verdict"] == "fail":
            gate["verdict"] = "fail"
            gate["status"] = "blocked"
        else:
            gate["verdict"] = "not_ready"
            gate["status"] = "inconclusive"
    else:
        gate.setdefault("verdict", previous["verdict"])
        gate.setdefault("status", previous["status"])
    gate.update({
        "schema_version": _text(gate.get("schema_version")) or "qualibug-release-gate-v1",
        "scope": "current_run_formal_finding_publication",
        "release_ready": release_ready and gate["verdict"] == "pass",
        "eligibility_status": status,
        "pre_reconciliation": previous,
        "run_delivery_readiness_schema_version": readiness["schema_version"],
        "reasons": reasons,
        "identities": dict(_record(readiness.get("identities"))),
    })
    return gate


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

    # ═══════════════════════════════════════════════════════════════════
    # P3-20: Coverage gap severity analysis — release blocking risk
    # ═══════════════════════════════════════════════════════════════════
    # Even without confirmed findings, if the campaign has high-severity
    # coverage gaps (P0/P1 paths not probed), the release should be blocked
    # because we simply don't know if those paths are safe.
    _HIGH_RISK_GAP_KINDS = {
        "permission", "isolation", "money", "concurrency",
        "authorization", "tenant_isolation", "payment", "idempotency",
        "privilege_escalation", "security_boundary",
    }
    high_risk_gaps: list[dict[str, Any]] = []
    total_gaps = 0
    for gap in _list(coverage_gaps):
        total_gaps += 1
        gap_kind = str(gap.get("kind") or gap.get("code") or "").lower()
        if any(risk in gap_kind for risk in _HIGH_RISK_GAP_KINDS):
            high_risk_gaps.append(dict(gap))

    # P3-20: Block release when high-risk paths have zero coverage
    if high_risk_gaps:
        gap_labels = [str(g.get("kind") or g.get("code") or "unknown") for g in high_risk_gaps[:5]]
        reasons.append({
            "code": "HIGH_RISK_COVERAGE_GAPS",
            "detail": f"{len(high_risk_gaps)} high-risk path(s) not probed: {', '.join(gap_labels[:3])}",
            "p3_20_risk": True,
        })

    campaign_not_closed_blocks = gate_policy.get("campaign_not_closed_verdict") != "not_ready"
    hard_block = any(
        reason["code"] == "CONFIRMED_P0_FINDINGS"
        or reason["code"] == "HIGH_RISK_COVERAGE_GAPS"
        or (campaign_not_closed_blocks and reason["code"] == "CAMPAIGN_NOT_CLOSED")
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
        "p3_20_coverage_risk": {
            "high_risk_gap_count": len(high_risk_gaps),
            "total_gap_count": total_gaps,
            "release_blocked_by_gaps": bool(high_risk_gaps),
        } if coverage_gaps else None,
    }
