from __future__ import annotations

"""Phase93B: probe-level runtime capability matrix.

Phase93A tells whether the customer environment is ready.  Phase93B maps that
readiness down to every grounded probe so reports can explain which high-value
bug checks ran, which were degraded, and which require environment fixes before
P0/P1 evidence can be collected.
"""

from typing import Any

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
READ_METHODS = {"GET", "HEAD"}
HIGH_VALUE_RUNTIME_RISKS = {
    "auth_boundary_probe",
    "ownership_scope_probe",
    "audit_privacy_probe",
    "state_transition_probe",
    "workflow_bypass_probe",
    "approval_flow_probe",
    "conservation_probe",
    "idempotency_replay_probe",
    "async_external_event_probe",
}
AUTH_REQUIRED_RISKS = {"auth_boundary_probe", "ownership_scope_probe", "audit_privacy_probe"}
SNAPSHOT_REQUIRED_RISKS = {
    "state_transition_probe",
    "workflow_bypass_probe",
    "approval_flow_probe",
    "conservation_probe",
    "idempotency_replay_probe",
    "ownership_scope_probe",
}


def _has_strict_grounding(probe: dict[str, Any]) -> bool:
    refs = probe.get("source_refs") if isinstance(probe.get("source_refs"), list) else []
    kinds = {str(r.get("kind") or "") for r in refs if isinstance(r, dict)}
    basis = probe.get("grounding_basis") if isinstance(probe.get("grounding_basis"), dict) else {}
    has_endpoint = "endpoint_contract" in kinds or int(basis.get("endpoint_contract_refs") or 0) >= 1
    has_support = bool(kinds - {"endpoint_contract", ""}) or int(basis.get("supporting_requirement_refs") or 0) >= 1
    return bool(has_endpoint and has_support)


def _check_map(preflight: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(c.get("name")): c for c in (preflight.get("checks") or []) if isinstance(c, dict) and c.get("name")}


def _needs_snapshot(probe: dict[str, Any]) -> bool:
    risk_type = str(probe.get("risk_type") or "")
    evidence = " ".join(str(x) for x in (probe.get("required_evidence") or [])).lower()
    return risk_type in SNAPSHOT_REQUIRED_RISKS or "snapshot" in evidence or "before_after" in evidence


def _expected_evidence_quality(lane: str, missing: list[str], needs_snapshot: bool) -> str:
    if lane.endswith("ready") and needs_snapshot:
        return "strong_runtime_before_after"
    if lane.endswith("ready"):
        return "medium_runtime_request_response"
    if "degraded" in lane:
        return "weak_or_partial_runtime_evidence"
    if lane == "plan_only":
        return "no_runtime_evidence_plan_only"
    return "no_runtime_evidence_blocked"


def _lane(method: str, missing_blocking: list[str], missing_optional: list[str], base_configured: bool) -> str:
    if not base_configured:
        return "plan_only"
    if missing_blocking:
        if method in WRITE_METHODS and any(x in missing_blocking for x in {"write_sandbox", "cleanup"}):
            return "write_sandbox_blocked_by_capability"
        return "blocked_by_preflight"
    if missing_optional:
        return "runtime_degraded"
    if method in WRITE_METHODS:
        return "write_sandbox_runtime_ready"
    if method in READ_METHODS:
        return "read_only_runtime_ready"
    return "unsupported_method_blocked"


def build_runtime_probe_capability_matrix(probes: list[dict[str, Any]], preflight: dict[str, Any]) -> dict[str, Any]:
    checks = _check_map(preflight)
    base_configured = bool((checks.get("base_url_configured") or {}).get("ok"))
    non_prod_ok = bool((checks.get("non_production_target") or {}).get("ok"))
    placeholder_ok = bool((checks.get("config_placeholders_resolved") or {}).get("ok"))
    auth_ok = bool((checks.get("auth_session_ready") or {}).get("ok"))
    sandbox_ok = bool(((preflight.get("sandbox_readiness") or {}).get("ok")))
    cleanup_ok = bool((checks.get("cleanup_health_declared") or {}).get("ok"))
    snapshot_ok = bool((checks.get("snapshot_observer_ready") or {}).get("ok"))

    rows: list[dict[str, Any]] = []
    for idx, probe in enumerate([p for p in probes if isinstance(p, dict)], start=1):
        ep = probe.get("endpoint") if isinstance(probe.get("endpoint"), dict) else {}
        method = str(ep.get("method") or "GET").upper()
        path = str(ep.get("path") or "")
        risk_type = str(probe.get("risk_type") or "")
        missing_blocking: list[str] = []
        missing_optional: list[str] = []
        capabilities = {
            "base_url": base_configured,
            "non_production_target": non_prod_ok,
            "strict_document_grounding": _has_strict_grounding(probe),
            "config_placeholders_resolved": placeholder_ok,
            "auth_session": True,
            "write_sandbox": True,
            "cleanup": True,
            "snapshot_observer": True,
        }
        if not base_configured:
            missing_blocking.append("base_url")
        if not non_prod_ok:
            missing_blocking.append("non_production_target")
        if not capabilities["strict_document_grounding"]:
            missing_blocking.append("strict_document_grounding")
        if not placeholder_ok:
            missing_blocking.append("config_placeholders_resolved")
        if risk_type in AUTH_REQUIRED_RISKS:
            capabilities["auth_session"] = auth_ok
            if not auth_ok:
                missing_optional.append("auth_session")
        if method in WRITE_METHODS:
            capabilities["write_sandbox"] = sandbox_ok
            capabilities["cleanup"] = cleanup_ok
            if not sandbox_ok:
                missing_blocking.append("write_sandbox")
            if not cleanup_ok:
                missing_blocking.append("cleanup")
        needs_snapshot = _needs_snapshot(probe)
        if needs_snapshot:
            capabilities["snapshot_observer"] = snapshot_ok
            if not snapshot_ok:
                missing_optional.append("snapshot_observer")
        lane = _lane(method, missing_blocking, missing_optional, base_configured)
        rows.append({
            "row_no": idx,
            "candidate_id": probe.get("candidate_id"),
            "risk_type": risk_type,
            "method": method,
            "path": path,
            "high_value_runtime_risk": risk_type in HIGH_VALUE_RUNTIME_RISKS,
            "requires_before_after_snapshot": needs_snapshot,
            "capabilities": capabilities,
            "missing_blocking_capabilities": missing_blocking,
            "missing_optional_capabilities": missing_optional,
            "preflight_lane": lane,
            "expected_evidence_quality": _expected_evidence_quality(lane, missing_blocking + missing_optional, needs_snapshot),
            "customer_action": _customer_action(lane, missing_blocking, missing_optional),
        })

    by_lane: dict[str, int] = {}
    by_quality: dict[str, int] = {}
    for row in rows:
        by_lane[row["preflight_lane"]] = by_lane.get(row["preflight_lane"], 0) + 1
        by_quality[row["expected_evidence_quality"]] = by_quality.get(row["expected_evidence_quality"], 0) + 1
    ready_rows = [r for r in rows if str(r.get("preflight_lane") or "").endswith("ready")]
    blocked_rows = [r for r in rows if "blocked" in str(r.get("preflight_lane") or "")]
    degraded_rows = [r for r in rows if "degraded" in str(r.get("preflight_lane") or "")]
    return {
        "engine": "runtime_probe_capability_matrix_v1_phase93b",
        "probe_count": len(rows),
        "runtime_ready_probe_count": len(ready_rows),
        "blocked_probe_count": len(blocked_rows),
        "degraded_probe_count": len(degraded_rows),
        "high_value_runtime_ready_count": sum(1 for r in ready_rows if r.get("high_value_runtime_risk")),
        "by_preflight_lane": dict(sorted(by_lane.items())),
        "by_expected_evidence_quality": dict(sorted(by_quality.items())),
        "rows": rows,
        "recommended_usage": "Use this matrix before execution reviews: blocked rows are environment/setup gaps, degraded rows can run but may not produce strong before/after evidence, ready rows are eligible for runtime validation.",
    }


def annotate_decisions_with_capability(decisions: list[dict[str, Any]], matrix: dict[str, Any]) -> list[dict[str, Any]]:
    by_cid = {str(row.get("candidate_id")): row for row in (matrix.get("rows") or []) if isinstance(row, dict)}
    out: list[dict[str, Any]] = []
    for decision in decisions:
        d = dict(decision)
        row = by_cid.get(str(d.get("candidate_id") or ""))
        if row:
            d["preflight_lane"] = row.get("preflight_lane")
            d["expected_evidence_quality"] = row.get("expected_evidence_quality")
            d["missing_preflight_capabilities"] = list(row.get("missing_blocking_capabilities") or []) + list(row.get("missing_optional_capabilities") or [])
        out.append(d)
    return out


def _customer_action(lane: str, missing_blocking: list[str], missing_optional: list[str]) -> str:
    missing = missing_blocking + missing_optional
    if lane.endswith("ready"):
        return "No onboarding action required for this probe; proceed under the configured execution flags."
    if lane == "plan_only":
        return "Configure staging base_url and rerun onboarding preflight before collecting runtime evidence."
    if "non_production_target" in missing:
        return "Switch to a clearly declared staging/QA/sandbox URL before running any probe."
    if "strict_document_grounding" in missing:
        return "Regenerate the probe plan from customer input documents so endpoint and business-rule source refs are present."
    if "write_sandbox" in missing:
        return "Enable disposable sandbox write execution, auto fixture creation and approval for this test environment."
    if "cleanup" in missing:
        return "Declare a supported cleanup/reset strategy such as fixture_reset, auto_delete or transaction_rollback."
    if "auth_session" in missing:
        return "Provide usable test accounts or auth_flow so authenticated and boundary probes can derive runtime sessions."
    if "snapshot_observer" in missing:
        return "Provide OpenAPI/read endpoints or snapshot config for before/after evidence observers."
    if "config_placeholders_resolved" in missing:
        return "Replace template <FILL:...> placeholders with disposable staging values."
    return "Review onboarding preflight details and rerun after environment setup is complete."
