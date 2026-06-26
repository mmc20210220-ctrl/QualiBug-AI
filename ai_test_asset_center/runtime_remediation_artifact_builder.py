from __future__ import annotations

"""Phase92Z: developer-facing remediation verification artifact builder."""

import json
from typing import Any


HIGH_PRIORITY = {"P0", "P1"}


def _violated_kinds(finding: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in finding.get("violated_invariants") or []:
        if isinstance(item, dict) and item.get("kind"):
            out.append(str(item.get("kind")))
        elif item:
            out.append(str(item))
    return sorted(dict.fromkeys(out))


def _assertion_templates(kinds: list[str]) -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    kind_set = set(kinds)
    if "non_negative_resource_fields" in kind_set:
        templates.append({
            "kind": "non_negative_resource_fields",
            "pytest_template": "assert min(extract_numeric_resource_fields(after_snapshot)) >= 0",
            "service_layer_expectation": "Reject or compensate operations that would create negative stock, balance, amount, points or quota values.",
        })
    if "cross_observer_conservation_reconciliation" in kind_set:
        templates.append({
            "kind": "cross_observer_delta_match",
            "pytest_template": "assert state_delta == ledger_delta, {'state_delta': state_delta, 'ledger_delta': ledger_delta}",
            "service_layer_expectation": "Commit state and ledger/history updates atomically or reject the operation.",
        })
    if "idempotency_no_duplicate_resource" in kind_set:
        templates.append({
            "kind": "idempotency_no_duplicate_resource",
            "pytest_template": "assert len(distinct_resource_ids_for_same_business_key) <= 1",
            "service_layer_expectation": "Enforce idempotency by unique business/idempotency key and return the existing resource for repeats.",
        })
    if "state_unchanged_after_rejection" in kind_set or "terminal_state_immutability" in kind_set:
        templates.append({
            "kind": "state_immutability_after_rejection_or_terminal_state",
            "pytest_template": "assert protected_business_fields(after_snapshot) == protected_business_fields(before_snapshot)",
            "service_layer_expectation": "Perform state/precondition checks before mutation and keep terminal objects immutable.",
        })
    if "ownership_scope_non_mutation" in kind_set:
        templates.append({
            "kind": "ownership_scope_non_mutation",
            "pytest_template": "assert foreign_scope_after_snapshot == foreign_scope_before_snapshot",
            "service_layer_expectation": "Resolve tenant/owner scope server-side and block cross-scope mutation before side effects.",
        })
    if not templates:
        templates.append({
            "kind": "runtime_verdict_regression",
            "pytest_template": "assert rerun_verdict != 'validated_candidate'",
            "service_layer_expectation": "The same grounded runtime probe should no longer validate the documented violation.",
        })
    return templates


def _patch_target_hints(finding: dict[str, Any]) -> list[str]:
    family = ((finding.get("customer_triage") or {}) if isinstance(finding.get("customer_triage"), dict) else {}).get("risk_family")
    hints = ["domain/service layer invariant guard", "transaction boundary around state and observer projection updates"]
    if family == "security_or_data_isolation":
        hints = ["server-side authorization middleware", "tenant/owner scope resolver", "pre-mutation access guard"]
    elif family == "business_resource_integrity":
        hints = ["domain aggregate invariant guard", "ledger/history append and state update transaction", "idempotency key uniqueness constraint"]
    elif family == "workflow_or_state_integrity":
        hints = ["state machine transition validator", "terminal-state mutation guard", "workflow approval policy gate"]
    return hints


def _work_item(finding: dict[str, Any]) -> dict[str, Any]:
    fx = finding.get("fix_verification") if isinstance(finding.get("fix_verification"), dict) else {}
    lifecycle = finding.get("lifecycle_registry") if isinstance(finding.get("lifecycle_registry"), dict) else {}
    kinds = _violated_kinds(finding)
    return {
        "finding_id": finding.get("finding_id"),
        "candidate_id": finding.get("candidate_id"),
        "priority": finding.get("priority"),
        "severity": finding.get("severity"),
        "risk_type": finding.get("risk_type"),
        "endpoint": f"{finding.get('method')} {finding.get('path')}",
        "customer_impact_summary": finding.get("customer_impact_summary"),
        "violated_invariant_kinds": kinds,
        "evidence_grade": finding.get("evidence_grade"),
        "evidence_strength_score": finding.get("evidence_strength_score"),
        "patch_target_hints": _patch_target_hints(finding),
        "developer_fix_checklist": fx.get("fix_verification_checklist") or [],
        "close_criteria": fx.get("fix_close_criteria") or [],
        "assertion_templates": _assertion_templates(kinds),
        "rerun_plan": fx.get("rerun_plan") or {},
        "lifecycle": {
            "status": fx.get("lifecycle_status"),
            "primary_signature": lifecycle.get("primary_lifecycle_signature"),
            "matched_previous_finding_id": lifecycle.get("matched_previous_finding_id"),
            "matched_alias": lifecycle.get("matched_alias"),
        },
        "post_fix_evidence_slots": {
            "after_fix_execution_report": "",
            "after_fix_markdown_report": "",
            "rerun_operator": "",
            "rerun_timestamp": "",
            "observed_lifecycle_status": "",
            "closure_approved_by": "",
        },
    }


def build_remediation_verification_artifact(report: dict[str, Any]) -> dict[str, Any]:
    findings = [f for f in (report.get("findings") or []) if isinstance(f, dict)]
    selected = [f for f in findings if str(f.get("priority") or "") in HIGH_PRIORITY]
    selected.sort(key=lambda f: (str(f.get("priority") or "P9"), str(f.get("finding_id") or "")))
    work_items = [_work_item(f) for f in selected]
    return {
        "engine": "runtime_remediation_artifact_builder_v1_phase92z",
        "project_id": report.get("project_id"),
        "source_execution_report": (report.get("outputs") or {}).get("execution_report") if isinstance(report.get("outputs"), dict) else None,
        "work_item_count": len(work_items),
        "scope": "P0/P1 runtime-validated findings with customer remediation verification plans",
        "work_items": work_items,
        "developer_usage": "Create the regression assertion before patching, fix the service/domain invariant, rerun QualiBug against staging, then fill post_fix_evidence_slots from the rerun report.",
    }


def render_remediation_markdown(artifact: dict[str, Any]) -> str:
    lines = [
        f"# QualiBug Remediation Verification Artifact — {artifact.get('project_id') or ''}",
        "",
        f"Engine: `{artifact.get('engine')}`",
        "",
        f"Work items: **{artifact.get('work_item_count')}**",
        "",
        artifact.get("developer_usage") or "",
        "",
    ]
    for item in artifact.get("work_items") or []:
        lines.extend([
            f"## {item.get('finding_id')} — {item.get('priority')} / {item.get('severity')}",
            "",
            f"- endpoint: `{item.get('endpoint')}`",
            f"- risk_type: `{item.get('risk_type')}`",
            f"- impact: {item.get('customer_impact_summary')}",
            f"- violated invariants: `{', '.join(item.get('violated_invariant_kinds') or [])}`",
            f"- lifecycle: `{(item.get('lifecycle') or {}).get('status')}` / signature `{(item.get('lifecycle') or {}).get('primary_signature')}`",
            "",
            "### Patch target hints",
            "",
        ])
        for hint in item.get("patch_target_hints") or []:
            lines.append(f"- {hint}")
        lines.extend(["", "### Regression assertion templates", ""])
        for template in item.get("assertion_templates") or []:
            lines.append(f"- `{template.get('kind')}`: `{template.get('pytest_template')}`")
        lines.extend(["", "### Close criteria", ""])
        for criterion in (item.get("close_criteria") or [])[:8]:
            lines.append(f"- {criterion}")
        lines.extend(["", "### Post-fix evidence slots", "", "```json", json.dumps(item.get("post_fix_evidence_slots") or {}, ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines)
