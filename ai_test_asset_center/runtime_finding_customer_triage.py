from __future__ import annotations

"""Phase92U: customer-facing triage for runtime-validated findings.

The triage layer is deliberately downstream of evidence validation.  It does
not create findings.  It converts a validated evidence package into severity,
priority and business-impact language that is easier for customer engineering
and product teams to act on.
"""

from typing import Any


SECURITY_RISKS = {"auth_boundary_probe", "anonymous_auth_boundary_probe", "cross_tenant_auth_boundary_probe", "role_downgrade_auth_boundary_probe", "ownership_scope_probe", "audit_privacy_probe"}
FINANCIAL_RESOURCE_RISKS = {"conservation_probe", "idempotency_replay_probe"}
STATE_RISKS = {"state_transition_probe", "workflow_bypass_probe", "approval_flow_probe"}

CRITICAL_INVARIANTS = {
    "state_unchanged_after_rejection",
    "ownership_scope_non_mutation",
    "cross_observer_conservation_reconciliation",
    "idempotency_no_duplicate_resource",
}
HIGH_INVARIANTS = {
    "non_negative_resource_fields",
    "terminal_state_immutability",
}


def _score_value(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _risk_family(risk_type: str) -> str:
    if risk_type in SECURITY_RISKS:
        return "security_or_data_isolation"
    if risk_type in FINANCIAL_RESOURCE_RISKS:
        return "business_resource_integrity"
    if risk_type in STATE_RISKS:
        return "workflow_or_state_integrity"
    return "runtime_contract_violation"


def _violated_kinds(evidence_package: dict[str, Any]) -> set[str]:
    return {str(x.get("kind")) for x in (evidence_package.get("violated_invariants") or []) if isinstance(x, dict) and x.get("kind")}


def _observer_kinds(evidence_package: dict[str, Any]) -> set[str]:
    chain = evidence_package.get("evidence_chain") if isinstance(evidence_package.get("evidence_chain"), dict) else {}
    return {str(x) for x in (chain.get("observer_kinds") or []) if str(x)}


def _has_cross_observer_failure(evidence_package: dict[str, Any]) -> bool:
    delta = evidence_package.get("delta_summary") if isinstance(evidence_package.get("delta_summary"), dict) else {}
    return bool(delta.get("cross_observer_failures"))


def _severity_from_score(score: float) -> str:
    if score >= 0.9:
        return "critical"
    if score >= 0.74:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _priority_from_severity(severity: str) -> str:
    return {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3"}.get(severity, "P3")


def _impact_summary(risk_type: str, family: str, violated: set[str]) -> str:
    if family == "security_or_data_isolation":
        if "state_unchanged_after_rejection" in violated or "ownership_scope_non_mutation" in violated:
            return "Rejected or cross-scope operation produced an observable business side effect; tenant/user isolation may be bypassable."
        return "Protected business data or operation appears reachable outside the documented access boundary."
    if family == "business_resource_integrity":
        if "cross_observer_conservation_reconciliation" in violated:
            return "Observed business resource delta is not reconciled across state and ledger/history views; inventory, balance, quota or amount integrity may be broken."
        if "non_negative_resource_fields" in violated:
            return "Observed resource state contains negative stock, balance, points, quota or amount-like values."
        if "idempotency_no_duplicate_resource" in violated:
            return "Repeated submission created multiple resources or side effects for one business operation."
        return "Observed write behavior may break amount, inventory, quota, balance or idempotency guarantees."
    if family == "workflow_or_state_integrity":
        return "Observed workflow/state transition violates documented lifecycle constraints or mutates a terminal object."
    return f"Runtime evidence validates a documented {risk_type or 'business'} contract violation."


def triage_runtime_finding(finding: dict[str, Any]) -> dict[str, Any]:
    evidence_package = finding.get("evidence_package") if isinstance(finding.get("evidence_package"), dict) else {}
    risk_type = str(finding.get("risk_type") or "")
    family = _risk_family(risk_type)
    score = _score_value(evidence_package.get("evidence_strength_score") or finding.get("evidence_strength_score"))
    violated = _violated_kinds(evidence_package)
    observer_kinds = _observer_kinds(evidence_package)

    severity_points = 0.0
    if family == "security_or_data_isolation":
        severity_points += 0.38
    elif family == "business_resource_integrity":
        severity_points += 0.34
    elif family == "workflow_or_state_integrity":
        severity_points += 0.24
    else:
        severity_points += 0.16
    severity_points += min(score, 1.0) * 0.36
    if violated & CRITICAL_INVARIANTS:
        severity_points += 0.22
    elif violated & HIGH_INVARIANTS:
        severity_points += 0.15
    if _has_cross_observer_failure(evidence_package):
        severity_points += 0.12
    if len(observer_kinds) >= 2:
        severity_points += 0.05
    if evidence_package.get("evidence_grade") == "strong":
        severity_points += 0.04

    severity = _severity_from_score(min(severity_points, 0.99))
    priority = _priority_from_severity(severity)
    impact = _impact_summary(risk_type, family, violated)
    blast_radius = []
    if family == "security_or_data_isolation":
        blast_radius.append("tenant_or_user_boundary")
    if family == "business_resource_integrity":
        blast_radius.append("financial_or_resource_state")
    if "business_ledger_projection" in observer_kinds:
        blast_radius.append("ledger_or_audit_trail")
    if "inventory_projection" in observer_kinds:
        blast_radius.append("inventory_projection")
    if "account_projection" in observer_kinds:
        blast_radius.append("account_or_wallet_projection")
    if not blast_radius:
        blast_radius.append("single_endpoint_runtime_contract")

    return {
        "engine": "runtime_finding_customer_triage_v1_phase92u",
        "severity": severity,
        "priority": priority,
        "risk_family": family,
        "triage_score": round(min(severity_points, 0.99), 2),
        "customer_impact_summary": impact,
        "blast_radius_signals": blast_radius,
        "escalation_reason": {
            "risk_type": risk_type,
            "evidence_grade": evidence_package.get("evidence_grade"),
            "violated_invariant_kinds": sorted(violated),
            "observer_kind_count": len(observer_kinds),
            "cross_observer_failure_present": _has_cross_observer_failure(evidence_package),
        },
        "recommended_owner": "backend/business-domain-owner" if family != "security_or_data_isolation" else "security-and-backend-owner",
        "recommended_next_step": "Create a minimal failing regression from the generated repro asset, fix the violated invariant at service/domain layer, then rerun the same QualiBug probe against staging.",
    }
