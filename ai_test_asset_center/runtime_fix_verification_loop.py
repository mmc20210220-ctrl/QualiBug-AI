from __future__ import annotations

"""Phase92X: customer-executable fix verification and finding lifecycle loop.

This layer is downstream of runtime validation.  It does not invent findings or
weaken the evidence gate.  Its job is to turn an already validated customer
finding into a concrete repair-verification work item and, when a previous
execution report is available, derive a lifecycle status from rerun evidence.
"""

from typing import Any


PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
FIX_REQUIRED_PRIORITIES = {"P0", "P1"}


def _finding_signature(finding: dict[str, Any]) -> str:
    candidate = str(finding.get("candidate_id") or "").strip()
    if candidate:
        return f"candidate:{candidate}"
    return "|".join([
        str(finding.get("risk_type") or "unknown"),
        str(finding.get("method") or "").upper(),
        str(finding.get("path") or ""),
    ])


def _priority_value(finding: dict[str, Any]) -> int:
    return PRIORITY_ORDER.get(str(finding.get("priority") or "P3"), 9)


def _violated_kinds(finding: dict[str, Any]) -> list[str]:
    kinds: list[str] = []
    for item in finding.get("violated_invariants") or []:
        if isinstance(item, dict) and item.get("kind"):
            kinds.append(str(item.get("kind")))
        elif item:
            kinds.append(str(item))
    return sorted(dict.fromkeys(kinds))


def _artifact_paths(finding: dict[str, Any]) -> list[str]:
    entries = finding.get("reproduction_artifact_links") or []
    paths: list[str] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path"):
            paths.append(str(entry.get("path")))
    package = finding.get("evidence_package") if isinstance(finding.get("evidence_package"), dict) else {}
    repro = package.get("reproduction_assets") if isinstance(package.get("reproduction_assets"), dict) else {}
    for entry in repro.get("artifact_links") or []:
        if isinstance(entry, dict) and entry.get("path"):
            paths.append(str(entry.get("path")))
    return sorted(dict.fromkeys(paths))


def _close_criteria(finding: dict[str, Any]) -> list[str]:
    criteria = [
        "Rerun the same grounded probe against the same approved staging/disposable sandbox environment.",
        "The original request/sequence no longer produces a validated_candidate verdict.",
        "Before/after snapshot comparison shows no forbidden business side effect for the same object graph.",
    ]
    kinds = set(_violated_kinds(finding))
    if "non_negative_resource_fields" in kinds:
        criteria.append("All amount, inventory, stock, balance, points and quota-like fields remain non-negative after the probe.")
    if "cross_observer_conservation_reconciliation" in kinds:
        criteria.append("State deltas reconcile with ledger/history/transaction observer deltas within the same business key scope.")
    if "idempotency_no_duplicate_resource" in kinds:
        criteria.append("Repeated submission with the same business/idempotency key creates no duplicate resource or extra side effect.")
    if "state_unchanged_after_rejection" in kinds or "terminal_state_immutability" in kinds:
        criteria.append("Rejected or terminal-state operation leaves the primary resource and related projections unchanged.")
    if "ownership_scope_non_mutation" in kinds:
        criteria.append("Cross-tenant/cross-owner request returns a protective status and leaves target-scope resources unchanged.")
    return criteria


def _regression_assertions(finding: dict[str, Any]) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    kinds = set(_violated_kinds(finding))
    if "state_unchanged_after_rejection" in kinds or "terminal_state_immutability" in kinds:
        assertions.append({"kind": "state_delta_zero", "assertion": "primary_resource_after == primary_resource_before for protected fields"})
    if "ownership_scope_non_mutation" in kinds:
        assertions.append({"kind": "scope_delta_zero", "assertion": "foreign_tenant_or_owner_object_after == before"})
    if "non_negative_resource_fields" in kinds:
        assertions.append({"kind": "non_negative_resources", "assertion": "min(resource_field_values_after) >= 0"})
    if "cross_observer_conservation_reconciliation" in kinds:
        assertions.append({"kind": "cross_observer_delta_match", "assertion": "state_delta == reconciled_ledger_or_history_delta"})
    if "idempotency_no_duplicate_resource" in kinds:
        assertions.append({"kind": "idempotency_single_side_effect", "assertion": "distinct_resource_id_count <= 1 and side_effect_count <= 1"})
    if not assertions:
        assertions.append({"kind": "no_validated_runtime_violation", "assertion": "rerun verdict is falsified_or_protected or needs_more_evidence, not validated_candidate"})
    return assertions


def _checklist(finding: dict[str, Any]) -> list[str]:
    owner = ((finding.get("customer_triage") or {}) if isinstance(finding.get("customer_triage"), dict) else {}).get("recommended_owner") or "backend/business-domain-owner"
    return [
        f"Assign the fix to {owner} with the generated finding id and endpoint.",
        "Add a failing regression test from the generated QualiBug pytest or PowerShell repro asset before changing business logic.",
        "Fix the violated invariant in the service/domain layer, not only at the UI or client validation layer.",
        "Rerun the same QualiBug grounded probe plan against staging/disposable sandbox with fresh auto fixtures.",
        "Attach the new execution report and confirm lifecycle_status is closed_by_rerun or no longer present as a validated candidate.",
    ]


def _rerun_plan(finding: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    outputs = report.get("outputs") if isinstance(report.get("outputs"), dict) else {}
    probe_plan = report.get("probe_plan") or "grounded_probe_plan.json"
    return {
        "target_finding_id": finding.get("finding_id"),
        "candidate_id": finding.get("candidate_id"),
        "endpoint": f"{finding.get('method')} {finding.get('path')}",
        "probe_plan": str(probe_plan),
        "recommended_scope": "rerun_same_candidate_first_then_full_plan" if finding.get("candidate_id") else "rerun_full_plan",
        "minimum_required_outputs": [
            "grounded_probe_execution_report.json",
            "grounded_probe_execution_report.md",
            "grounded_probe_regression_pytest.py",
        ],
        "existing_reproduction_assets": _artifact_paths(finding) or [str(v) for v in outputs.values() if v],
        "manual_command_template": "python -m ai_test_asset_center.grounded_probe_executor --probe-plan <same_plan> --out-dir <new_rerun_out> --probe-config <same_staging_config> --allow-write-sandbox",
        "automatic_close_basis": "signature_absent_or_verdict_not_validated_candidate_on_rerun",
    }


def _plan_for_finding(finding: dict[str, Any], report: dict[str, Any], lifecycle_status: str) -> dict[str, Any]:
    return {
        "engine": "runtime_fix_verification_loop_v1_phase92x",
        "finding_signature": _finding_signature(finding),
        "lifecycle_status": lifecycle_status,
        "verification_required": str(finding.get("priority") or "") in FIX_REQUIRED_PRIORITIES,
        "fix_verification_checklist": _checklist(finding),
        "fix_close_criteria": _close_criteria(finding),
        "regression_assertions": _regression_assertions(finding),
        "rerun_plan": _rerun_plan(finding, report),
        "fix_before_after_evidence_template": {
            "before_fix_report": "attach current grounded_probe_execution_report.json",
            "after_fix_report": "attach rerun grounded_probe_execution_report.json",
            "expected_after_fix_status": "closed_by_rerun",
            "compare_fields": ["status", "evidence_strength_score", "violated_invariants", "delta_summary", "customer_triage"],
        },
    }


def _previous_findings(previous_report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(previous_report, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for finding in previous_report.get("findings") or []:
        if isinstance(finding, dict):
            out[_finding_signature(finding)] = finding
    return out


def attach_fix_verification_loop(report: dict[str, Any], previous_report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach fix-verification plans and rerun lifecycle information to report."""
    findings = [f for f in (report.get("findings") or []) if isinstance(f, dict)]
    previous_by_signature = _previous_findings(previous_report)
    current_by_signature = {_finding_signature(f): f for f in findings}

    open_count = 0
    still_open_count = 0
    reopened_count = 0
    verification_required_count = 0
    high_priority_work_items: list[dict[str, Any]] = []

    for finding in sorted(findings, key=_priority_value):
        sig = _finding_signature(finding)
        prev = previous_by_signature.get(sig)
        lifecycle_status = "open"
        if prev:
            prev_lifecycle = (prev.get("fix_verification") or {}).get("lifecycle_status") if isinstance(prev.get("fix_verification"), dict) else None
            lifecycle_status = "reopened" if prev_lifecycle == "closed_by_rerun" else "still_open_after_rerun"
        if lifecycle_status == "open":
            open_count += 1
        elif lifecycle_status == "still_open_after_rerun":
            still_open_count += 1
        elif lifecycle_status == "reopened":
            reopened_count += 1

        plan = _plan_for_finding(finding, report, lifecycle_status)
        finding["fix_verification"] = plan
        if plan.get("verification_required"):
            verification_required_count += 1
            high_priority_work_items.append({
                "finding_id": finding.get("finding_id"),
                "priority": finding.get("priority"),
                "severity": finding.get("severity"),
                "endpoint": f"{finding.get('method')} {finding.get('path')}",
                "lifecycle_status": lifecycle_status,
                "close_criteria": plan.get("fix_close_criteria")[:4],
                "regression_assertion_kinds": [a.get("kind") for a in (plan.get("regression_assertions") or [])],
            })

    closed_by_rerun: list[dict[str, Any]] = []
    for sig, prev in previous_by_signature.items():
        if sig in current_by_signature:
            continue
        if prev.get("status") == "validated_candidate":
            closed_by_rerun.append({
                "previous_finding_id": prev.get("finding_id"),
                "finding_signature": sig,
                "candidate_id": prev.get("candidate_id"),
                "endpoint": f"{prev.get('method')} {prev.get('path')}",
                "lifecycle_status": "closed_by_rerun",
                "close_basis": "previous validated finding signature is absent from this rerun report",
            })

    report["findings"] = findings
    report["fix_verification_loop_index"] = {
        "engine": "runtime_fix_verification_loop_v1_phase92x",
        "enabled": True,
        "previous_report_present": isinstance(previous_report, dict),
        "current_validated_finding_count": len(findings),
        "verification_required_finding_count": verification_required_count,
        "open_finding_count": open_count,
        "still_open_after_rerun_count": still_open_count,
        "reopened_finding_count": reopened_count,
        "closed_by_rerun_count": len(closed_by_rerun),
        "closed_by_rerun": closed_by_rerun,
        "high_priority_fix_work_items": high_priority_work_items[:50],
        "recommended_loop": "Fix P0/P1 first, add the generated regression assertion, rerun the same grounded probe plan, then accept closure only when the finding signature disappears or no longer validates with before/after evidence.",
    }
    return report
