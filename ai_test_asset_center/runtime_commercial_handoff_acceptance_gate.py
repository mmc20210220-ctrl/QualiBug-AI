from __future__ import annotations

"""Phase93K: commercial handoff bundle acceptance gate.

Phase93J builds the customer handoff bundle.  Phase93K validates whether that
bundle is actually acceptable to hand over: required artifact paths are present,
required signoff checklist items have passed, and the handoff status is not a
blocked/conditional onboarding state unless it is explicitly marked as a
conditional acceptance.
"""

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _required_artifacts(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [x for x in _as_list(bundle.get("artifact_manifest")) if isinstance(x, dict) and x.get("required_for_handoff")]


def _signoff_blockers(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for item in _as_list(bundle.get("customer_signoff_checklist")):
        if isinstance(item, dict) and item.get("required") and not item.get("passed"):
            blockers.append(item)
    return blockers


def validate_commercial_handoff_acceptance(report_or_bundle: dict[str, Any]) -> dict[str, Any]:
    """Validate whether the Phase93J handoff bundle can be accepted."""

    bundle = _as_dict(report_or_bundle.get("commercial_handoff_bundle")) or report_or_bundle
    status = str(bundle.get("status") or "missing_handoff_bundle")
    summary = _as_dict(bundle.get("executive_summary"))
    minimum_failures = [str(x) for x in _as_list(summary.get("minimum_commercial_gate_failures")) if str(x)]
    commercial_blockers = [str(x) for x in _as_list(summary.get("commercial_blocking_reasons")) if str(x)]
    required_artifacts = _required_artifacts(bundle)
    signoff_blockers = _signoff_blockers(bundle)
    missing_paths = [
        {
            "artifact_key": a.get("artifact_key"),
            "purpose": a.get("purpose"),
            "primary_owner": a.get("primary_owner"),
            "reason": "required handoff artifact has no declared path",
        }
        for a in required_artifacts
        if not str(a.get("path") or "").strip()
    ]

    violations: list[dict[str, Any]] = []
    if status.startswith("handoff_blocked"):
        violations.append({"violation_id": "HANDOFF-BLOCKED-STATUS", "severity": "P0", "reason": f"Bundle status is {status}."})
    if missing_paths:
        violations.append({"violation_id": "HANDOFF-REQUIRED-ARTIFACT-MISSING", "severity": "P0", "reason": "One or more required handoff artifacts has no declared path.", "items": missing_paths})
    if minimum_failures:
        violations.append({
            "violation_id": "HANDOFF-MINIMUM-COMMERCIAL-GATE-FAILED",
            "severity": "P0",
            "reason": "Minimum commercial gate checks failed; the handoff cannot be accepted as commercial runtime-ready.",
            "minimum_commercial_gate_failures": minimum_failures,
            "commercial_blocking_reasons": commercial_blockers,
        })
    for item in signoff_blockers:
        violations.append({"violation_id": "HANDOFF-SIGNOFF-BLOCKER", "severity": "P1", "reason": item.get("text"), "item_id": item.get("item_id"), "owner": item.get("owner")})

    if status.startswith("commercial_handoff_ready") and not violations:
        gate_status = "ready_for_customer_acceptance"
        acceptance_gate_passed = True
        recommendation = "Customer can accept the handoff bundle and execute the attached runbook/SLA policy."
    elif status.startswith("conditional_handoff") and not any(v.get("severity") == "P0" for v in violations):
        gate_status = "conditional_acceptance_onboarding_required"
        acceptance_gate_passed = False
        recommendation = "Customer may accept this as an onboarding delta package, but commercial runtime SLA is not yet claimable."
    else:
        gate_status = "acceptance_blocked"
        acceptance_gate_passed = False
        recommendation = "Resolve handoff blockers before asking the customer to sign off on the commercial runtime package."

    return {
        "engine": "runtime_commercial_handoff_acceptance_gate_v1_phase93k",
        "status": gate_status,
        "acceptance_gate_passed": acceptance_gate_passed,
        "source_handoff_status": status,
        "project_id": bundle.get("project_id"),
        "commercial_readiness_score": summary.get("commercial_readiness_score"),
        "sla_gate_passed": summary.get("sla_gate_passed"),
        "minimum_commercial_gate_failures": minimum_failures,
        "commercial_blocking_reasons": commercial_blockers,
        "required_artifact_count": len(required_artifacts),
        "missing_required_artifact_path_count": len(missing_paths),
        "signoff_blocker_count": len(signoff_blockers),
        "violation_count": len(violations),
        "violations": violations,
        "missing_required_artifacts": missing_paths,
        "signoff_blockers": signoff_blockers,
        "customer_acceptance_packet": {
            "acceptance_statement_template": bundle.get("acceptance_statement_template"),
            "safe_note": bundle.get("customer_safe_note"),
            "artifact_count": len(_as_list(bundle.get("artifact_manifest"))),
            "required_artifact_keys": [a.get("artifact_key") for a in required_artifacts],
            "handoff_routes": [r.get("route_id") for r in _as_list(bundle.get("handoff_routes")) if isinstance(r, dict)],
        },
        "recommendation": recommendation,
    }


def render_commercial_handoff_acceptance_markdown(gate: dict[str, Any]) -> str:
    lines = [
        "# Commercial Handoff Acceptance Gate",
        "",
        f"- engine: `{gate.get('engine')}`",
        f"- status: `{gate.get('status')}`",
        f"- acceptance gate passed: `{gate.get('acceptance_gate_passed')}`",
        f"- source handoff status: `{gate.get('source_handoff_status')}`",
        f"- readiness score: `{gate.get('commercial_readiness_score')}`",
        f"- SLA gate passed: `{gate.get('sla_gate_passed')}`",
        f"- required artifacts: `{gate.get('required_artifact_count')}`",
        f"- signoff blockers: `{gate.get('signoff_blocker_count')}`",
        f"- violations: `{gate.get('violation_count')}`",
        f"- recommendation: {gate.get('recommendation')}",
        "",
    ]
    if gate.get("violations"):
        lines.extend(["## Violations", ""])
        for v in _as_list(gate.get("violations")):
            if isinstance(v, dict):
                lines.append(f"- `{v.get('violation_id')}` severity `{v.get('severity')}` — {v.get('reason')}")
        lines.append("")
    packet = _as_dict(gate.get("customer_acceptance_packet"))
    lines.extend([
        "## Acceptance packet",
        "",
        f"- artifact count: `{packet.get('artifact_count')}`",
        f"- required artifact keys: `{', '.join(str(x) for x in _as_list(packet.get('required_artifact_keys')))} `",
        f"- handoff routes: `{', '.join(str(x) for x in _as_list(packet.get('handoff_routes')))} `",
        "",
        "## Acceptance statement template",
        "",
        f"> {packet.get('acceptance_statement_template')}",
        "",
        f"> {packet.get('safe_note')}",
    ])
    return "\n".join(lines)
