from __future__ import annotations

"""Phase93J: commercial handoff bundle builder.

Phase93A-I produce separate customer-facing runtime onboarding artifacts.  In a
commercial delivery those artifacts need a single receipt/index so customer
admins, QA owners and developers can sign off, assign follow-ups and rerun the
right evidence lanes without hunting through the execution report.

This module aggregates those materials into a deterministic handoff bundle.  It
is a packaging/coordination layer only: it never creates or suppresses bug
findings and it never weakens runtime evidence requirements.
"""

from typing import Any


_ARTIFACTS: list[tuple[str, str, str, str]] = [
    ("execution_report", "grounded_probe_execution_report.json", "Runtime evidence and validated findings", "quality_lead"),
    ("execution_report_md", "grounded_probe_execution_report.md", "Customer-readable runtime report", "quality_lead"),
    ("onboarding_preflight_json", "grounded_probe_onboarding_preflight.json", "Environment readiness checks", "customer_admin"),
    ("runtime_capability_matrix_json", "grounded_probe_runtime_capability_matrix.json", "Probe-level runtime capability lanes", "qa_owner"),
    ("onboarding_remediation_kit_json", "grounded_probe_onboarding_remediation_kit.json", "Customer setup action kit", "customer_admin"),
    ("onboarding_remediation_kit_md", "grounded_probe_onboarding_remediation_kit.md", "Customer setup action guide", "customer_admin"),
    ("runtime_execution_runbook_json", "grounded_probe_runtime_execution_runbook.json", "Execution sequencing plan", "qa_owner"),
    ("runtime_execution_runbook_md", "grounded_probe_runtime_execution_runbook.md", "Execution runbook", "qa_owner"),
    ("runtime_evidence_readiness_sla_gate_json", "grounded_probe_runtime_evidence_readiness_sla_gate.json", "Commercial evidence readiness SLA gate", "delivery_owner"),
    ("runtime_evidence_readiness_sla_gate_md", "grounded_probe_runtime_evidence_readiness_sla_gate.md", "SLA gate explanation", "delivery_owner"),
    ("runtime_evidence_scoreboard_json", "grounded_probe_runtime_evidence_scoreboard.json", "Runtime execution coverage scoreboard", "delivery_owner"),
    ("runtime_evidence_scoreboard_md", "grounded_probe_runtime_evidence_scoreboard.md", "Runtime execution coverage summary", "delivery_owner"),
    ("runtime_evidence_probe_ledger_json", "grounded_probe_runtime_evidence_probe_ledger.json", "Per-probe runtime evidence ledger", "qa_owner"),
    ("runtime_evidence_probe_ledger_md", "grounded_probe_runtime_evidence_probe_ledger.md", "Per-probe evidence gap guide", "qa_owner"),
    ("runtime_customer_reproduction_pack_json", "grounded_probe_runtime_customer_reproduction_pack.json", "Customer-ready reproduction evidence packages", "engineering_owner"),
    ("runtime_customer_reproduction_pack_md", "grounded_probe_runtime_customer_reproduction_pack.md", "Customer/developer reproduction guide", "engineering_owner"),
    ("runtime_evidence_remediation_plan_json", "grounded_probe_runtime_evidence_remediation_plan.json", "Runtime evidence remediation and rerun manifest", "qa_owner"),
    ("runtime_evidence_remediation_plan_md", "grounded_probe_runtime_evidence_remediation_plan.md", "Runtime evidence remediation guide", "qa_owner"),
    ("runtime_evidence_carry_forward_json", "grounded_probe_runtime_evidence_carry_forward.json", "Customer-ready evidence carry-forward ledger", "delivery_owner"),
    ("runtime_evidence_carry_forward_md", "grounded_probe_runtime_evidence_carry_forward.md", "Carry-forward evidence summary", "delivery_owner"),
    ("runtime_evidence_progress_delta_json", "grounded_probe_runtime_evidence_progress_delta.json", "Runtime evidence progress/regression delta", "delivery_owner"),
    ("runtime_evidence_progress_delta_md", "grounded_probe_runtime_evidence_progress_delta.md", "Runtime evidence progress summary", "delivery_owner"),
    ("runtime_evidence_promotion_gate_json", "grounded_probe_runtime_evidence_promotion_gate.json", "Customer-ready runtime evidence promotion gate", "delivery_owner"),
    ("runtime_evidence_promotion_gate_md", "grounded_probe_runtime_evidence_promotion_gate.md", "Promotion gate explanation", "delivery_owner"),
    ("runtime_evidence_customer_delivery_manifest_json", "grounded_probe_runtime_evidence_customer_delivery_manifest.json", "Frozen customer delivery evidence manifest", "delivery_owner"),
    ("runtime_evidence_customer_delivery_manifest_md", "grounded_probe_runtime_evidence_customer_delivery_manifest.md", "Customer delivery manifest summary", "delivery_owner"),
    ("runtime_evidence_delivery_manifest_verification_json", "grounded_probe_runtime_evidence_delivery_manifest_verification.json", "Delivery manifest hash verification", "security_owner"),
    ("runtime_evidence_delivery_manifest_verification_md", "grounded_probe_runtime_evidence_delivery_manifest_verification.md", "Delivery manifest verification summary", "security_owner"),
    ("runtime_sla_execution_policy_json", "grounded_probe_runtime_sla_execution_policy.json", "SLA-gated execution policy", "qa_owner"),
    ("runtime_sla_execution_policy_md", "grounded_probe_runtime_sla_execution_policy.md", "SLA execution policy guide", "qa_owner"),
    ("runtime_sla_gap_prioritizer_json", "grounded_probe_runtime_sla_gap_prioritizer.json", "Prioritized onboarding delta plan", "customer_admin"),
    ("runtime_sla_gap_prioritizer_md", "grounded_probe_runtime_sla_gap_prioritizer.md", "Onboarding gap guide", "customer_admin"),
    ("onboarding_patch_safety_validation_json", "grounded_probe_onboarding_patch_safety_validation.json", "Patch safety validation", "security_owner"),
    ("onboarding_patch_safety_validation_md", "grounded_probe_onboarding_patch_safety_validation.md", "Patch safety review", "security_owner"),
    ("write_sandbox_approval_packet_json", "grounded_probe_write_sandbox_approval_packet.json", "Write-sandbox approval contract", "customer_admin"),
    ("write_sandbox_approval_packet_md", "grounded_probe_write_sandbox_approval_packet.md", "Write-sandbox approval guide", "customer_admin"),
    ("remediation_verification_json", "grounded_probe_remediation_verification.json", "Developer remediation verification work items", "engineering_owner"),
    ("remediation_verification_md", "grounded_probe_remediation_verification.md", "Developer remediation guide", "engineering_owner"),
    ("repro_ps1", "grounded_probe_repro.ps1", "Reviewable reproduction script", "qa_owner"),
    ("regression_pytest", "grounded_probe_regression_pytest.py", "Regression rerun asset", "engineering_owner"),
]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _count_by_priority(findings: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for finding in findings:
        priority = str(finding.get("priority") or "untriaged")
        out[priority] = out.get(priority, 0) + 1
    return out


def _handoff_status(report: dict[str, Any]) -> tuple[str, str]:
    gate = _as_dict(report.get("runtime_evidence_readiness_sla_gate"))
    promotion_gate = _as_dict(report.get("runtime_evidence_promotion_gate"))
    delivery_manifest = _as_dict(report.get("runtime_evidence_customer_delivery_manifest"))
    manifest_verification = _as_dict(report.get("runtime_evidence_delivery_manifest_verification"))
    secret_audit = _as_dict(report.get("commercial_handoff_secret_audit"))
    safety = _as_dict(report.get("onboarding_patch_safety_validation"))
    approval = _as_dict(report.get("write_sandbox_approval_packet"))
    gap = _as_dict(report.get("runtime_sla_gap_prioritizer"))
    findings = [x for x in _as_list(report.get("findings")) if isinstance(x, dict)]

    if str(safety.get("status") or "") == "unsafe_blocked" or safety.get("safe_to_send_to_customer") is False:
        return "handoff_blocked_by_patch_safety", "Do not send the onboarding patch until production targets, raw secrets or unsafe cleanup gaps are removed."
    if secret_audit and secret_audit.get("safe_for_customer_handoff") is False:
        return "handoff_blocked_by_secret_audit", "Commercial handoff/runtime evidence contains raw secret indicators; redact customer-facing artifacts before handoff."
    if approval.get("write_approval_required") and not approval.get("ready_for_customer_approval"):
        return "handoff_blocked_by_write_sandbox_approval", "Write-sandbox evidence is required or requested, but the customer approval packet is not ready."
    if promotion_gate and not promotion_gate.get("promotion_ready"):
        return "handoff_blocked_by_runtime_evidence_promotion_gate", "Runtime evidence promotion gate is not approved; resolve promotion blockers before customer handoff."
    if delivery_manifest and not delivery_manifest.get("customer_ready"):
        return "handoff_blocked_by_runtime_delivery_manifest", "Runtime customer delivery manifest is not customer-ready; regenerate evidence artifacts after resolving missing or blocked delivery items."
    if manifest_verification and not manifest_verification.get("verified"):
        return "handoff_blocked_by_runtime_delivery_manifest_verification", "Frozen runtime delivery manifest failed hash verification; do not hand off until drift, missing artifacts or tampering signals are resolved."
    if not gate.get("sla_gate_passed"):
        if gap.get("action_count"):
            return "conditional_handoff_onboarding_delta_required", "Commercial SLA is not yet claimable; send the prioritized onboarding delta and rerun preflight/SLA gate."
        return "conditional_handoff_manual_onboarding_review", "Commercial SLA is not yet claimable and no structured onboarding delta was available."
    if findings:
        return "commercial_handoff_ready_with_validated_findings", "SLA evidence gate is acceptable; assign validated findings and rerun remediation verification after fixes."
    return "commercial_handoff_ready_no_validated_findings", "SLA evidence gate is acceptable and no runtime-validated findings were produced in this run."


def _artifact_manifest(report: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = _as_dict(report.get("outputs"))
    manifest: list[dict[str, Any]] = []
    for key, default_name, purpose, owner in _ARTIFACTS:
        path = outputs.get(key) or default_name
        manifest.append({
            "artifact_key": key,
            "path": path,
            "purpose": purpose,
            "primary_owner": owner,
            "required_for_handoff": key in {
                "execution_report",
                "onboarding_preflight_json",
                "runtime_capability_matrix_json",
                "runtime_evidence_readiness_sla_gate_json",
                "runtime_evidence_scoreboard_json",
                "runtime_evidence_probe_ledger_json",
                "runtime_customer_reproduction_pack_json",
                "runtime_evidence_remediation_plan_json",
                "runtime_evidence_promotion_gate_json",
                "runtime_evidence_customer_delivery_manifest_json",
                "runtime_evidence_delivery_manifest_verification_json",
                "runtime_sla_execution_policy_json",
                "runtime_execution_runbook_md",
            },
        })
    return manifest


def _signoff_checklist(report: dict[str, Any], status: str) -> list[dict[str, Any]]:
    gate = _as_dict(report.get("runtime_evidence_readiness_sla_gate"))
    approval = _as_dict(report.get("write_sandbox_approval_packet"))
    safety = _as_dict(report.get("onboarding_patch_safety_validation"))
    secret_audit = _as_dict(report.get("commercial_handoff_secret_audit"))
    findings = [x for x in _as_list(report.get("findings")) if isinstance(x, dict)]
    p0p1 = [f for f in findings if str(f.get("priority")) in {"P0", "P1"}]
    minimum_failures = [str(x) for x in _as_list(gate.get("minimum_commercial_gate_failures")) if str(x)]

    return [
        {
            "item_id": "HANDOFF-NON-PROD",
            "owner": "customer_admin",
            "required": True,
            "passed": status != "handoff_blocked_by_patch_safety",
            "text": "Confirm all runtime and onboarding artifacts point to staging/QA/UAT/sandbox, not production.",
        },
        {
            "item_id": "HANDOFF-SLA-GATE",
            "owner": "delivery_owner",
            "required": True,
            "passed": bool(gate.get("sla_gate_passed")),
            "text": "Review evidence readiness score and whether commercial runtime SLA can be claimed.",
            "minimum_gate_failures": minimum_failures,
        },
        {
            "item_id": "HANDOFF-MINIMUM-COMMERCIAL-GATE",
            "owner": "delivery_owner",
            "required": True,
            "passed": not minimum_failures,
            "text": "Confirm all minimum commercial gate checks passed, including verified auth session, non-production target, base URL and document grounding.",
            "minimum_gate_failures": minimum_failures,
        },
        {
            "item_id": "HANDOFF-RUNTIME-PROMOTION-GATE",
            "owner": "delivery_owner",
            "required": bool(_as_dict(report.get("runtime_evidence_promotion_gate"))),
            "passed": (not _as_dict(report.get("runtime_evidence_promotion_gate"))) or bool(_as_dict(report.get("runtime_evidence_promotion_gate")).get("promotion_ready")),
            "text": "Confirm the Phase95 runtime evidence promotion gate approved this run for customer-ready delivery.",
            "promotion_blockers": _as_list(_as_dict(report.get("runtime_evidence_promotion_gate")).get("blockers")),
        },
        {
            "item_id": "HANDOFF-RUNTIME-DELIVERY-MANIFEST",
            "owner": "delivery_owner",
            "required": bool(_as_dict(report.get("runtime_evidence_customer_delivery_manifest"))),
            "passed": (not _as_dict(report.get("runtime_evidence_customer_delivery_manifest"))) or bool(_as_dict(report.get("runtime_evidence_customer_delivery_manifest")).get("customer_ready")),
            "text": "Confirm customer delivery evidence was frozen into a manifest with required artifact hashes.",
            "delivery_manifest_status": _as_dict(report.get("runtime_evidence_customer_delivery_manifest")).get("status"),
        },
        {
            "item_id": "HANDOFF-RUNTIME-MANIFEST-VERIFY",
            "owner": "security_owner",
            "required": bool(_as_dict(report.get("runtime_evidence_delivery_manifest_verification"))),
            "passed": (not _as_dict(report.get("runtime_evidence_delivery_manifest_verification"))) or bool(_as_dict(report.get("runtime_evidence_delivery_manifest_verification")).get("verified")),
            "text": "Verify the frozen runtime delivery manifest hashes still match before sending customer artifacts.",
            "verification_blockers": _as_list(_as_dict(report.get("runtime_evidence_delivery_manifest_verification")).get("blockers")),
        },
        {
            "item_id": "HANDOFF-PATCH-SAFETY",
            "owner": "security_owner",
            "required": True,
            "passed": bool(safety.get("safe_to_send_to_customer", True)) and str(safety.get("status") or "safe_to_send") != "unsafe_blocked",
            "text": "Verify onboarding patch preview contains placeholders/redactions only and declares cleanup for write lanes.",
        },
        {
            "item_id": "HANDOFF-SECRET-AUDIT",
            "owner": "security_owner",
            "required": bool(secret_audit),
            "passed": (not secret_audit) or bool(secret_audit.get("safe_for_customer_handoff")),
            "text": "Verify commercial handoff and Phase95 runtime evidence artifacts do not contain raw credentials, bearer tokens, cookies or API keys.",
            "secret_audit_status": secret_audit.get("status"),
            "secret_audit_issue_count": secret_audit.get("issue_count", 0),
            "runtime_evidence_issue_count": secret_audit.get("runtime_evidence_issue_count", 0),
        },
        {
            "item_id": "HANDOFF-WRITE-APPROVAL",
            "owner": "customer_admin",
            "required": bool(approval.get("write_approval_required")),
            "passed": (not approval.get("write_approval_required")) or bool(approval.get("ready_for_customer_approval")),
            "text": "Approve write-sandbox probes only after safety, cleanup and non-production checks pass.",
        },
        {
            "item_id": "HANDOFF-P0P1-ASSIGNMENT",
            "owner": "engineering_owner",
            "required": bool(p0p1),
            "passed": True,
            "text": "Assign P0/P1 remediation verification cards to engineering owners.",
            "finding_count": len(p0p1),
        },
        {
            "item_id": "HANDOFF-RERUN-PLAN",
            "owner": "qa_owner",
            "required": True,
            "passed": True,
            "text": "Use the runbook and remediation verification artifacts for the next rerun/closure cycle.",
        },
    ]


def _customer_routes(report: dict[str, Any]) -> list[dict[str, Any]]:
    gap = _as_dict(report.get("runtime_sla_gap_prioritizer"))
    policy = _as_dict(report.get("runtime_sla_execution_policy"))
    findings = [x for x in _as_list(report.get("findings")) if isinstance(x, dict)]
    p0p1_ids = [str(f.get("finding_id")) for f in findings if str(f.get("priority")) in {"P0", "P1"}]
    return [
        {
            "route_id": "ROUTE-ONBOARDING",
            "when": "SLA gate is not passed or preflight has blockers/degraded lanes.",
            "entry_artifacts": ["grounded_probe_onboarding_remediation_kit.md", "grounded_probe_runtime_sla_gap_prioritizer.md", "grounded_probe_onboarding_patch_safety_validation.md"],
            "next_action": gap.get("recommendation") or "Apply onboarding delta and rerun preflight.",
        },
        {
            "route_id": "ROUTE-SLA-RUNTIME",
            "when": "Environment is ready enough to execute mandatory SLA probes.",
            "entry_artifacts": ["grounded_probe_runtime_execution_runbook.md", "grounded_probe_runtime_sla_execution_policy.md", "grounded_probe_write_sandbox_approval_packet.md"],
            "mandatory_candidate_ids": [str(x.get("candidate_id")) for x in _as_list(policy.get("must_run_for_sla")) if isinstance(x, dict) and x.get("candidate_id")],
        },
        {
            "route_id": "ROUTE-REMEDIATION",
            "when": "Runtime-validated findings exist.",
            "entry_artifacts": ["grounded_probe_remediation_verification.md", "grounded_probe_regression_pytest.py", "grounded_probe_repro.ps1"],
            "p0_p1_finding_ids": p0p1_ids,
        },
        {
            "route_id": "ROUTE-CLOSURE-RERUN",
            "when": "Customer has applied fixes or onboarding deltas.",
            "entry_artifacts": ["grounded_probe_runtime_execution_runbook.md", "grounded_probe_remediation_verification.json"],
            "next_action": "Rerun QualiBug with the previous execution report configured so Phase92X/Y can close, keep open or reopen findings.",
        },
    ]


def build_commercial_handoff_bundle(report: dict[str, Any]) -> dict[str, Any]:
    """Build a single customer handoff index for Phase93A-I artifacts."""

    summary = _as_dict(report.get("summary"))
    gate = _as_dict(report.get("runtime_evidence_readiness_sla_gate"))
    policy = _as_dict(report.get("runtime_sla_execution_policy"))
    approval = _as_dict(report.get("write_sandbox_approval_packet"))
    preflight = _as_dict(report.get("onboarding_preflight"))
    findings = [x for x in _as_list(report.get("findings")) if isinstance(x, dict)]
    status, recommendation = _handoff_status(report)
    signoff = _signoff_checklist(report, status)

    return {
        "engine": "runtime_commercial_handoff_bundle_v1_phase93j",
        "status": status,
        "project_id": report.get("project_id"),
        "created_at": report.get("created_at"),
        "recommendation": recommendation,
        "executive_summary": {
            "commercial_readiness_score": gate.get("commercial_readiness_score", summary.get("runtime_evidence_readiness_score", 0)),
            "sla_gate_passed": bool(gate.get("sla_gate_passed")),
            "commercial_readiness_level": gate.get("commercial_readiness_level"),
            "preflight_status": preflight.get("status"),
            "runtime_sla_policy_status": policy.get("status"),
            "write_sandbox_approval_status": approval.get("status"),
            "runtime_promotion_gate_status": _as_dict(report.get("runtime_evidence_promotion_gate")).get("status"),
            "runtime_promotion_gate_ready": bool(_as_dict(report.get("runtime_evidence_promotion_gate")).get("promotion_ready")) if _as_dict(report.get("runtime_evidence_promotion_gate")) else None,
            "runtime_delivery_manifest_status": _as_dict(report.get("runtime_evidence_customer_delivery_manifest")).get("status"),
            "runtime_delivery_manifest_ready": bool(_as_dict(report.get("runtime_evidence_customer_delivery_manifest")).get("customer_ready")) if _as_dict(report.get("runtime_evidence_customer_delivery_manifest")) else None,
            "runtime_delivery_manifest_verification_status": _as_dict(report.get("runtime_evidence_delivery_manifest_verification")).get("status"),
            "runtime_delivery_manifest_verified": bool(_as_dict(report.get("runtime_evidence_delivery_manifest_verification")).get("verified")) if _as_dict(report.get("runtime_evidence_delivery_manifest_verification")) else None,
            "commercial_handoff_secret_audit_status": _as_dict(report.get("commercial_handoff_secret_audit")).get("status"),
            "commercial_handoff_safe_for_customer": bool(_as_dict(report.get("commercial_handoff_secret_audit")).get("safe_for_customer_handoff")) if _as_dict(report.get("commercial_handoff_secret_audit")) else None,
            "runtime_evidence_secret_issue_count": _as_dict(report.get("commercial_handoff_secret_audit")).get("runtime_evidence_issue_count", 0),
            "validated_candidate_count": summary.get("validated_candidate_count", len(findings)),
            "finding_count_by_priority": _count_by_priority(findings),
            "p0_p1_finding_count": sum(1 for f in findings if str(f.get("priority")) in {"P0", "P1"}),
            "runtime_sla_must_run_count": policy.get("must_run_for_sla_count", summary.get("runtime_sla_must_run_count", 0)),
            "blocked_before_sla_count": policy.get("blocked_before_sla_count", summary.get("runtime_sla_blocked_before_sla_count", 0)),
            "minimum_commercial_gate_failures": _as_list(gate.get("minimum_commercial_gate_failures")),
            "commercial_blocking_reasons": _as_list(gate.get("commercial_blocking_reasons")),
            "handoff_blocker_count": sum(1 for item in signoff if item.get("required") and not item.get("passed")),
        },
        "artifact_manifest": _artifact_manifest(report),
        "customer_signoff_checklist": signoff,
        "handoff_routes": _customer_routes(report),
        "stakeholder_assignment_map": {
            "customer_admin": ["non-production target", "accounts/tenant roles", "write-sandbox approval"],
            "security_owner": ["patch safety", "secret redaction", "production guard"],
            "qa_owner": ["runbook", "mandatory SLA probe execution", "rerun evidence capture"],
            "engineering_owner": ["P0/P1 remediation cards", "regression assertions", "fix verification"],
            "delivery_owner": ["SLA gate", "commercial handoff decision", "customer acceptance"],
        },
        "acceptance_statement_template": "Customer acknowledges receipt of the QualiBug runtime handoff bundle, confirms the target is non-production, and will use the attached runbook/SLA policy for any approved write-sandbox or remediation rerun activity.",
        "customer_safe_note": "This bundle is an index and signoff artifact. It does not execute probes and does not contain raw customer secrets.",
    }


def render_commercial_handoff_markdown(bundle: dict[str, Any]) -> str:
    lines = [
        "# QualiBug Commercial Runtime Handoff Bundle",
        "",
        f"- engine: `{bundle.get('engine')}`",
        f"- status: `{bundle.get('status')}`",
        f"- project: `{bundle.get('project_id')}`",
        f"- recommendation: {bundle.get('recommendation')}",
        "",
        "## Executive summary",
        "",
    ]
    summary = _as_dict(bundle.get("executive_summary"))
    for key in [
        "commercial_readiness_score",
        "sla_gate_passed",
        "commercial_readiness_level",
        "preflight_status",
        "runtime_sla_policy_status",
        "write_sandbox_approval_status",
        "runtime_promotion_gate_status",
        "runtime_promotion_gate_ready",
        "runtime_delivery_manifest_status",
        "runtime_delivery_manifest_ready",
        "runtime_delivery_manifest_verification_status",
        "runtime_delivery_manifest_verified",
        "commercial_handoff_secret_audit_status",
        "commercial_handoff_safe_for_customer",
        "runtime_evidence_secret_issue_count",
        "validated_candidate_count",
        "p0_p1_finding_count",
        "runtime_sla_must_run_count",
        "blocked_before_sla_count",
        "minimum_commercial_gate_failures",
        "commercial_blocking_reasons",
        "handoff_blocker_count",
    ]:
        lines.append(f"- {key}: `{summary.get(key)}`")
    lines.extend(["", "## Artifact manifest", ""])
    for artifact in _as_list(bundle.get("artifact_manifest")):
        if isinstance(artifact, dict):
            required = "required" if artifact.get("required_for_handoff") else "supporting"
            lines.append(f"- `{artifact.get('artifact_key')}` ({required}, owner `{artifact.get('primary_owner')}`): `{artifact.get('path')}` — {artifact.get('purpose')}")
    lines.extend(["", "## Customer signoff checklist", ""])
    for item in _as_list(bundle.get("customer_signoff_checklist")):
        if isinstance(item, dict):
            lines.append(f"- `{item.get('item_id')}` owner `{item.get('owner')}` required=`{item.get('required')}` passed=`{item.get('passed')}` — {item.get('text')}")
    lines.extend(["", "## Handoff routes", ""])
    for route in _as_list(bundle.get("handoff_routes")):
        if isinstance(route, dict):
            lines.append(f"### {route.get('route_id')}")
            lines.append(f"- when: {route.get('when')}")
            if route.get("next_action"):
                lines.append(f"- next action: {route.get('next_action')}")
            if route.get("entry_artifacts"):
                lines.append(f"- entry artifacts: {', '.join(str(x) for x in route.get('entry_artifacts') or [])}")
            lines.append("")
    lines.extend([
        "## Acceptance statement template",
        "",
        f"> {bundle.get('acceptance_statement_template')}",
        "",
        f"> {bundle.get('customer_safe_note')}",
    ])
    return "\n".join(lines)
