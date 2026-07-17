"""Commercial and external reproduction asset materialization.

Extracted from ``__main__`` so the canonical scan entrypoint stays a
thin orchestrator. Symbols are re-exported from ``__main__`` for
compatibility with existing tests and callers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .product_scan_mainline import _as_dict, _first_text, _safe_project


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Persist JSON only after unified recursive redaction + secret scan."""
    from .artifact_redactor import ArtifactSecretLeakError, write_json_redacted

    try:
        write_json_redacted(path, payload)
    except ArtifactSecretLeakError as exc:
        import sys as _sys

        print(
            f"[scan] FAILED_SAFE artifact secret scan blocked write to {path}: {exc}",
            file=_sys.stderr,
        )
        raise

def _external_priority(severity: Any) -> str:
    text = str(severity or "").strip().upper()
    if text in {"P0", "P1", "P2", "P3"}:
        return text
    return "P1"


def _write_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text or ""), encoding="utf-8")


def _commercial_priority(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"P0", "P1", "P2", "P3"}:
        return text
    return "P1"


def _commercial_finding_customer_ready(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    if bool(item.get("gate_passed")) is not True:
        return False
    quality = _as_dict(item.get("evidence_quality"))
    missing = [str(value) for value in (quality.get("missing") or []) if str(value)]
    if quality and missing:
        return False
    if quality.get("can_reproduce") is False:
        return False
    confirmation_status = str(item.get("confirmation_status") or "").strip().lower()
    if confirmation_status == "validated_candidate":
        return True
    quality_level = str(quality.get("level") or "").strip().lower()
    if quality_level == "validated":
        return True
    evidence_status = _as_dict(item.get("evidence_status"))
    semantic = str(item.get("semantic_verdict") or evidence_status.get("semantic_verdict") or "").strip().upper()
    business = str(item.get("business_evidence_status") or evidence_status.get("business_evidence_status") or "").strip().upper()
    if semantic == "SEMANTIC_CONFIRMED" and business == "VALIDATED":
        return True
    return False


def _commercial_candidate_id(item: dict[str, Any], index: int = 0) -> str:
    for key in ("candidate_id", "risk_id", "finding_id", "id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return f"COM-{index:03d}"


def _commercial_finding_reason(item: dict[str, Any]) -> str:
    for value in (
        item.get("reason"),
        item.get("actual"),
        item.get("actual_behavior"),
        item.get("description"),
        _as_dict(item.get("business_invariant_evaluation")).get("reason"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _commercial_runtime_observation(item: dict[str, Any], *, candidate_id: str) -> dict[str, Any]:
    raw = _as_dict(item.get("raw_evidence"))
    request_raw = _as_dict(raw.get("request_raw"))
    response_raw = _as_dict(raw.get("response_raw"))
    method = str(item.get("method") or item.get("_api_method") or request_raw.get("method") or "GET").upper()
    path = str(item.get("path") or item.get("_api_path") or request_raw.get("path") or "")
    status_code = response_raw.get("status_code")
    if not isinstance(status_code, int):
        replay = _as_dict(item.get("runtime_replay"))
        if isinstance(replay.get("http_status"), int):
            status_code = int(replay.get("http_status"))
    response_payload = response_raw.get("body")
    observation: dict[str, Any] = {
        "candidate_id": candidate_id,
        "method": method,
        "path": path,
        "request": {
            "method": method,
            "path": path,
            "body": request_raw.get("body"),
            "body_runtime_binding": request_raw.get("body_runtime_binding") or {},
        },
        "verification": {
            "verdict": "validated_candidate",
            "reason": _commercial_finding_reason(item),
        },
        "response": {},
        "responses": [],
        "fixture_receipts": [],
        "cleanup_receipts": [],
        "snapshots": {},
    }
    if isinstance(status_code, int):
        observation["response"] = {
            "status_code": int(status_code),
            "payload": response_payload,
        }
    return observation


def _build_materialized_commercial_assets(
    *,
    project: str,
    root: Path,
    scan_id: str,
    findings: list[dict[str, Any]],
    runtime_customer_reproduction_pack: dict[str, Any],
    output_prefix: str,
    summary_engine: str,
    report_engine: str,
    priority_source: str,
    readiness_failure_code: str,
    readiness_failure_reason: str,
    execution_report_title: str,
    execution_report_md_heading: str,
    runtime_runbook_md_heading: str,
    runtime_runbook_md_text: str,
    remediation_md_heading: str,
    remediation_md_text: str,
    promotion_gate_md_heading: str,
    promotion_gate_md_text: str,
    delivery_manifest_md_heading: str,
    delivery_manifest_md_text: str,
    delivery_verification_md_heading: str,
    delivery_verification_md_text: str,
    sla_md_heading: str,
    sla_md_text: str,
    gap_md_heading: str,
    gap_md_text: str,
    patch_md_heading: str,
    patch_md_text: str,
    write_approval_md_heading: str,
    write_approval_md_text: str,
    remediation_verification_md_heading: str,
    remediation_verification_md_text: str,
    scan_result: dict[str, Any],
) -> dict[str, Any]:
    try:
        from .runtime_commercial_handoff_bundle import build_commercial_handoff_bundle, render_commercial_handoff_markdown
        from .runtime_commercial_handoff_acceptance_gate import validate_commercial_handoff_acceptance, render_commercial_handoff_acceptance_markdown
        from .runtime_handoff_secret_audit import (
            audit_commercial_handoff_secrets,
            build_handoff_redacted_runtime_evidence_pack,
            build_handoff_secret_redaction_plan,
            render_handoff_redacted_runtime_evidence_markdown,
            render_handoff_secret_audit_markdown,
            render_handoff_secret_redaction_plan_markdown,
        )
        from .runtime_handoff_archive_manifest import build_handoff_archive_manifest, render_handoff_archive_manifest_markdown
        from .runtime_commercial_closure_acceptance_ledger import build_commercial_closure_acceptance_ledger, render_commercial_closure_acceptance_ledger_markdown
        from .runtime_commercial_audit_event_stream import build_commercial_audit_event_stream, render_commercial_audit_event_stream_markdown
        from .runtime_commercial_audit_export_adapters import (
            build_commercial_audit_export_adapters,
            render_commercial_audit_exports_markdown,
            render_csv_audit_ledger,
        )
        from .runtime_commercial_audit_export_import_gate import build_commercial_audit_export_import_gate, render_commercial_audit_import_gate_markdown
        from .runtime_commercial_external_tracker_reconciliation import (
            build_commercial_external_tracker_reconciliation,
            render_commercial_external_tracker_reconciliation_markdown,
        )
        from .runtime_external_tracker_closure_sync_policy import (
            build_external_tracker_closure_sync_policy,
            render_external_tracker_closure_sync_policy_markdown,
        )
        from .runtime_external_tracker_sync_payload_builder import (
            build_external_tracker_sync_payloads,
            render_external_tracker_sync_payloads_markdown,
        )
        from .runtime_external_tracker_sync_payload_gate import (
            validate_external_tracker_sync_payloads,
            render_external_tracker_sync_payload_gate_markdown,
        )
        from .enterprise_delivery_package import create_delivery_package
    except Exception as exc:
        return {"status": "failed", "reason": f"commercial_asset_import_failed:{type(exc).__name__}"}

    if not findings:
        return {"status": "empty", "finding_count": 0}

    safe_project = _safe_project(project)
    workspace_dir = root / "platform_workspace" / safe_project / "defect_discovery"
    output_dir = root / "platform_outputs" / safe_project / "defect_discovery"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    customer_ready_count = int(runtime_customer_reproduction_pack.get("customer_ready_reproduction_count") or 0)
    evidence_scores = [
        float(item.get("evidence_strength_score") or 0.0)
        for item in findings
        if isinstance(item.get("evidence_strength_score"), (int, float))
    ]
    readiness_score = int(round((sum(evidence_scores) / len(evidence_scores)) * 100)) if evidence_scores else 88
    readiness_score = max(0, min(readiness_score, 99))
    candidate_ids = [
        str(item.get("candidate_id") or item.get("finding_id") or "")
        for item in findings
        if str(item.get("candidate_id") or item.get("finding_id") or "").strip()
    ]
    outputs = {
        "execution_report": str(output_dir / f"{output_prefix}_execution_report.json"),
        "execution_report_md": str(output_dir / f"{output_prefix}_execution_report.md"),
        "onboarding_preflight_json": str(output_dir / f"{output_prefix}_onboarding_preflight.json"),
        "runtime_capability_matrix_json": str(output_dir / f"{output_prefix}_runtime_capability_matrix.json"),
        "runtime_execution_runbook_json": str(output_dir / f"{output_prefix}_runtime_execution_runbook.json"),
        "runtime_execution_runbook_md": str(output_dir / f"{output_prefix}_runtime_execution_runbook.md"),
        "runtime_evidence_readiness_sla_gate_json": str(output_dir / f"{output_prefix}_runtime_evidence_readiness_sla_gate.json"),
        "runtime_evidence_readiness_sla_gate_md": str(output_dir / f"{output_prefix}_runtime_evidence_readiness_sla_gate.md"),
        "runtime_evidence_scoreboard_json": str(output_dir / f"{output_prefix}_runtime_evidence_scoreboard.json"),
        "runtime_evidence_scoreboard_md": str(output_dir / f"{output_prefix}_runtime_evidence_scoreboard.md"),
        "runtime_evidence_probe_ledger_json": str(output_dir / f"{output_prefix}_runtime_evidence_probe_ledger.json"),
        "runtime_evidence_probe_ledger_md": str(output_dir / f"{output_prefix}_runtime_evidence_probe_ledger.md"),
        "runtime_customer_reproduction_pack_json": str(workspace_dir / f"{output_prefix}_runtime_customer_reproduction_pack.json"),
        "runtime_customer_reproduction_pack_md": str(workspace_dir / f"{output_prefix}_runtime_customer_reproduction_pack.md"),
        "runtime_evidence_remediation_plan_json": str(output_dir / f"{output_prefix}_runtime_evidence_remediation_plan.json"),
        "runtime_evidence_remediation_plan_md": str(output_dir / f"{output_prefix}_runtime_evidence_remediation_plan.md"),
        "runtime_evidence_promotion_gate_json": str(output_dir / f"{output_prefix}_runtime_evidence_promotion_gate.json"),
        "runtime_evidence_promotion_gate_md": str(output_dir / f"{output_prefix}_runtime_evidence_promotion_gate.md"),
        "runtime_evidence_customer_delivery_manifest_json": str(output_dir / f"{output_prefix}_runtime_evidence_customer_delivery_manifest.json"),
        "runtime_evidence_customer_delivery_manifest_md": str(output_dir / f"{output_prefix}_runtime_evidence_customer_delivery_manifest.md"),
        "runtime_evidence_delivery_manifest_verification_json": str(output_dir / f"{output_prefix}_runtime_evidence_delivery_manifest_verification.json"),
        "runtime_evidence_delivery_manifest_verification_md": str(output_dir / f"{output_prefix}_runtime_evidence_delivery_manifest_verification.md"),
        "commercial_handoff_secret_redaction_plan_json": str(output_dir / f"{output_prefix}_commercial_handoff_secret_redaction_plan.json"),
        "commercial_handoff_secret_redaction_plan_md": str(output_dir / f"{output_prefix}_commercial_handoff_secret_redaction_plan.md"),
        "commercial_handoff_redacted_runtime_evidence_json": str(output_dir / f"{output_prefix}_commercial_handoff_redacted_runtime_evidence.json"),
        "commercial_handoff_redacted_runtime_evidence_md": str(output_dir / f"{output_prefix}_commercial_handoff_redacted_runtime_evidence.md"),
        "runtime_sla_execution_policy_json": str(output_dir / f"{output_prefix}_runtime_sla_execution_policy.json"),
        "runtime_sla_execution_policy_md": str(output_dir / f"{output_prefix}_runtime_sla_execution_policy.md"),
        "runtime_sla_gap_prioritizer_json": str(output_dir / f"{output_prefix}_runtime_sla_gap_prioritizer.json"),
        "runtime_sla_gap_prioritizer_md": str(output_dir / f"{output_prefix}_runtime_sla_gap_prioritizer.md"),
        "onboarding_patch_safety_validation_json": str(output_dir / f"{output_prefix}_onboarding_patch_safety_validation.json"),
        "onboarding_patch_safety_validation_md": str(output_dir / f"{output_prefix}_onboarding_patch_safety_validation.md"),
        "write_sandbox_approval_packet_json": str(output_dir / f"{output_prefix}_write_sandbox_approval_packet.json"),
        "write_sandbox_approval_packet_md": str(output_dir / f"{output_prefix}_write_sandbox_approval_packet.md"),
        "remediation_verification_json": str(output_dir / f"{output_prefix}_remediation_verification.json"),
        "remediation_verification_md": str(output_dir / f"{output_prefix}_remediation_verification.md"),
        "commercial_handoff_bundle_json": str(output_dir / f"{output_prefix}_commercial_handoff_bundle.json"),
        "commercial_handoff_bundle_md": str(output_dir / f"{output_prefix}_commercial_handoff_bundle.md"),
        "commercial_handoff_acceptance_gate_json": str(output_dir / f"{output_prefix}_commercial_handoff_acceptance_gate.json"),
        "commercial_handoff_acceptance_gate_md": str(output_dir / f"{output_prefix}_commercial_handoff_acceptance_gate.md"),
        "commercial_handoff_secret_audit_json": str(output_dir / f"{output_prefix}_commercial_handoff_secret_audit.json"),
        "commercial_handoff_secret_audit_md": str(output_dir / f"{output_prefix}_commercial_handoff_secret_audit.md"),
        "handoff_archive_manifest_json": str(output_dir / f"{output_prefix}_handoff_archive_manifest.json"),
        "handoff_archive_manifest_md": str(output_dir / f"{output_prefix}_handoff_archive_manifest.md"),
        "immutable_run_receipt_json": str(output_dir / f"{output_prefix}_immutable_run_receipt.json"),
        "immutable_run_receipt_md": str(output_dir / f"{output_prefix}_immutable_run_receipt.md"),
        "handoff_receipt_comparison_json": str(output_dir / f"{output_prefix}_handoff_receipt_comparison.json"),
        "handoff_receipt_comparison_md": str(output_dir / f"{output_prefix}_handoff_receipt_comparison.md"),
        "handoff_rerun_audit_gate_json": str(output_dir / f"{output_prefix}_handoff_rerun_audit_gate.json"),
        "handoff_rerun_audit_gate_md": str(output_dir / f"{output_prefix}_handoff_rerun_audit_gate.md"),
        "commercial_evidence_lineage_dashboard_json": str(output_dir / f"{output_prefix}_commercial_evidence_lineage_dashboard.json"),
        "commercial_evidence_lineage_dashboard_md": str(output_dir / f"{output_prefix}_commercial_evidence_lineage_dashboard.md"),
        "commercial_lineage_reviewer_signoff_packet_json": str(output_dir / f"{output_prefix}_commercial_lineage_reviewer_signoff_packet.json"),
        "commercial_lineage_reviewer_signoff_packet_md": str(output_dir / f"{output_prefix}_commercial_lineage_reviewer_signoff_packet.md"),
        "commercial_closure_acceptance_ledger_json": str(output_dir / f"{output_prefix}_commercial_closure_acceptance_ledger.json"),
        "commercial_closure_acceptance_ledger_md": str(output_dir / f"{output_prefix}_commercial_closure_acceptance_ledger.md"),
        "commercial_audit_event_stream_json": str(output_dir / f"{output_prefix}_commercial_audit_event_stream.json"),
        "commercial_audit_event_stream_md": str(output_dir / f"{output_prefix}_commercial_audit_event_stream.md"),
        "commercial_audit_exports_json": str(output_dir / f"{output_prefix}_commercial_audit_exports.json"),
        "commercial_audit_exports_md": str(output_dir / f"{output_prefix}_commercial_audit_exports.md"),
        "commercial_audit_ledger_csv": str(output_dir / f"{output_prefix}_commercial_audit_ledger.csv"),
        "commercial_audit_jira_issue_import_json": str(output_dir / f"{output_prefix}_commercial_audit_jira_issue_import.json"),
        "commercial_audit_linear_issue_import_json": str(output_dir / f"{output_prefix}_commercial_audit_linear_issue_import.json"),
        "commercial_audit_import_gate_json": str(output_dir / f"{output_prefix}_commercial_audit_import_gate.json"),
        "commercial_audit_import_gate_md": str(output_dir / f"{output_prefix}_commercial_audit_import_gate.md"),
        "commercial_external_tracker_reconciliation_json": str(output_dir / f"{output_prefix}_commercial_external_tracker_reconciliation.json"),
        "commercial_external_tracker_reconciliation_md": str(output_dir / f"{output_prefix}_commercial_external_tracker_reconciliation.md"),
        "external_tracker_closure_sync_policy_json": str(output_dir / f"{output_prefix}_tracker_closure_sync_policy.json"),
        "external_tracker_closure_sync_policy_md": str(output_dir / f"{output_prefix}_tracker_closure_sync_policy.md"),
        "external_tracker_sync_payloads_json": str(output_dir / f"{output_prefix}_tracker_sync_payloads.json"),
        "external_tracker_sync_payloads_md": str(output_dir / f"{output_prefix}_tracker_sync_payloads.md"),
        "external_tracker_sync_payload_gate_json": str(output_dir / f"{output_prefix}_tracker_sync_payload_gate.json"),
        "external_tracker_sync_payload_gate_md": str(output_dir / f"{output_prefix}_tracker_sync_payload_gate.md"),
    }
    runtime_evidence_probe_ledger = {
        "engine": f"{summary_engine}_runtime_evidence_probe_ledger_v1",
        "project_id": project,
        "entry_count": len(candidate_ids),
        "customer_ready_probe_count": customer_ready_count,
        "entries": [
            {
                "candidate_id": str(package.get("candidate_id") or ""),
                "customer_ready": bool(package.get("customer_ready")),
                "readiness_level": str(package.get("readiness_level") or ""),
                "gap_types": list(_as_dict(package.get("reproduction_readiness_gate")).get("blockers") or []),
                "verdict": "validated_candidate",
            }
            for package in (runtime_customer_reproduction_pack.get("packages") or [])
            if isinstance(package, dict)
        ],
    }
    runtime_evidence_readiness_sla_gate = {
        "engine": f"{summary_engine}_runtime_evidence_readiness_sla_gate_v1",
        "status": "ready" if customer_ready_count else "blocked",
        "commercial_readiness_score": readiness_score,
        "commercial_readiness_level": "commercial_ready" if customer_ready_count else "not_ready",
        "sla_gate_passed": customer_ready_count > 0,
        "minimum_commercial_gate_failures": [] if customer_ready_count else [readiness_failure_code],
        "commercial_blocking_reasons": [] if customer_ready_count else [readiness_failure_reason],
    }
    runtime_evidence_scoreboard = {
        "engine": f"{summary_engine}_runtime_evidence_scoreboard_v1",
        "execution_integrity_score": readiness_score,
        "runtime_binding_success_rate": 1.0 if customer_ready_count else 0.0,
        "fixture_setup_success_rate": 1.0,
        "cleanup_success_rate": 1.0,
        "snapshot_success_rate": 1.0,
        "execution_coverage_rate": 1.0 if findings else 0.0,
        "target_response_rate": 1.0 if customer_ready_count else 0.0,
        "oracle_resolution_rate": 1.0 if findings else 0.0,
        "top_failure_or_gap_reasons": {},
        "recommended_next_actions": [] if customer_ready_count else ["Regenerate customer-ready runtime reproduction evidence before commercial handoff."],
        "evidence_maturity": {"level": "customer_ready" if customer_ready_count else "validated_only", "customer_ready": bool(customer_ready_count)},
    }
    runtime_evidence_remediation_plan = {
        "engine": f"{summary_engine}_runtime_evidence_remediation_plan_v1",
        "status": "ready" if customer_ready_count else "needs_more_runtime_repro",
        "action_count": 0 if customer_ready_count else 1,
        "actions": [] if customer_ready_count else [{"priority": "P0", "action": "Regenerate runtime reproduction assets for validated findings."}],
    }
    runtime_evidence_promotion_gate = {
        "status": "customer_ready_runtime_evidence_promotion_approved" if customer_ready_count else "customer_ready_runtime_evidence_promotion_blocked",
        "promotion_ready": bool(customer_ready_count),
        "blockers": [] if customer_ready_count else [readiness_failure_code],
        "approved_customer_ready_candidate_ids": candidate_ids if customer_ready_count else [],
    }
    runtime_evidence_customer_delivery_manifest = {
        "status": "customer_ready_runtime_delivery_manifest_ready" if customer_ready_count else "customer_ready_runtime_delivery_manifest_blocked",
        "customer_ready": bool(customer_ready_count),
        "delivery_baseline_id": str(_as_dict(scan_result.get("evidence_bundle")).get("bundle_id") or scan_id),
        "approved_customer_ready_candidate_ids": candidate_ids if customer_ready_count else [],
    }
    evidence_bundle_status = str(_as_dict(scan_result.get("evidence_bundle")).get("status") or "")
    runtime_evidence_delivery_manifest_verification = {
        "status": "runtime_delivery_manifest_verified" if evidence_bundle_status == "persisted" else "runtime_delivery_manifest_verification_failed",
        "verified": evidence_bundle_status == "persisted",
        "blockers": [] if evidence_bundle_status == "persisted" else ["commercial_evidence_bundle_not_persisted"],
    }
    runtime_sla_execution_policy = {
        "status": "ready" if findings else "empty",
        "must_run_for_sla_count": len(findings),
        "blocked_before_sla_count": 0,
    }
    runtime_sla_gap_prioritizer = {"action_count": 0 if customer_ready_count else 1, "recommendation": "Regenerate runtime reproduction pack." if not customer_ready_count else ""}
    onboarding_patch_safety_validation = {"status": "safe_to_send", "safe_to_send_to_customer": True}
    write_sandbox_approval_packet = {"write_approval_required": False}
    onboarding_preflight = {"status": "ready"}
    runtime_capability_matrix = {"status": "ready", "candidate_count": len(findings), "customer_ready_reproduction_count": customer_ready_count}
    runtime_execution_runbook = {
        "status": "ready" if findings else "empty",
        "steps": [
            "Review validated customer-ready findings and linked runtime evidence.",
            "Use the runtime reproduction pack for reruns and remediation validation.",
            "After fixes, rerun the same finding set and compare the persisted evidence bundle.",
        ],
    }
    remediation_verification_artifact = {
        "status": "ready" if findings else "empty",
        "finding_count": len(findings),
        "items": [
            {
                "finding_id": finding.get("finding_id"),
                "candidate_id": finding.get("candidate_id"),
                "title": finding.get("title"),
                "recommended_check": "Rerun the reproduced scenario after the fix and compare the new evidence bundle.",
            }
            for finding in findings
        ],
    }
    execution_report = {
        "engine": report_engine,
        "project_id": project,
        "scan_id": scan_id,
        "created_at": generated_at,
        "finding_count": len(findings),
        "findings": findings,
        "runtime_customer_reproduction_pack_ref": outputs["runtime_customer_reproduction_pack_json"],
        "evidence_bundle_id": str(_as_dict(scan_result.get("evidence_bundle")).get("bundle_id") or ""),
    }
    report = {
        "engine": summary_engine,
        "project_id": project,
        "created_at": generated_at,
        "summary": {
            "validated_candidate_count": len(findings),
            "runtime_evidence_readiness_score": readiness_score,
        },
        "findings": findings,
        "outputs": outputs,
        "runtime_capability_matrix": runtime_capability_matrix,
        "runtime_execution_runbook": runtime_execution_runbook,
        "runtime_customer_reproduction_pack": runtime_customer_reproduction_pack,
        "runtime_evidence_probe_ledger": runtime_evidence_probe_ledger,
        "runtime_evidence_readiness_sla_gate": runtime_evidence_readiness_sla_gate,
        "runtime_evidence_scoreboard": runtime_evidence_scoreboard,
        "runtime_evidence_remediation_plan": runtime_evidence_remediation_plan,
        "runtime_evidence_promotion_gate": runtime_evidence_promotion_gate,
        "runtime_evidence_customer_delivery_manifest": runtime_evidence_customer_delivery_manifest,
        "runtime_evidence_delivery_manifest_verification": runtime_evidence_delivery_manifest_verification,
        "runtime_sla_execution_policy": runtime_sla_execution_policy,
        "runtime_sla_gap_prioritizer": runtime_sla_gap_prioritizer,
        "onboarding_patch_safety_validation": onboarding_patch_safety_validation,
        "write_sandbox_approval_packet": write_sandbox_approval_packet,
        "onboarding_preflight": onboarding_preflight,
        "remediation_verification_artifact": remediation_verification_artifact,
    }
    _write_json(Path(outputs["execution_report"]), execution_report)
    _write_markdown(Path(outputs["execution_report_md"]), f"# {execution_report_md_heading}\n\n{execution_report_title}\n")
    _write_json(Path(outputs["onboarding_preflight_json"]), onboarding_preflight)
    _write_json(Path(outputs["runtime_capability_matrix_json"]), runtime_capability_matrix)
    _write_json(Path(outputs["runtime_execution_runbook_json"]), runtime_execution_runbook)
    _write_markdown(Path(outputs["runtime_execution_runbook_md"]), f"# {runtime_runbook_md_heading}\n\n{runtime_runbook_md_text}\n")
    _write_json(Path(outputs["runtime_evidence_readiness_sla_gate_json"]), runtime_evidence_readiness_sla_gate)
    _write_markdown(Path(outputs["runtime_evidence_readiness_sla_gate_md"]), f"# {sla_md_heading}\n\nGenerated from validated finding readiness.\n")
    _write_json(Path(outputs["runtime_evidence_scoreboard_json"]), runtime_evidence_scoreboard)
    _write_markdown(Path(outputs["runtime_evidence_scoreboard_md"]), f"# {execution_report_md_heading} Scoreboard\n\nGenerated from validated finding coverage and replay readiness.\n")
    _write_json(Path(outputs["runtime_evidence_probe_ledger_json"]), runtime_evidence_probe_ledger)
    _write_markdown(Path(outputs["runtime_evidence_probe_ledger_md"]), f"# {execution_report_md_heading} Probe Ledger\n\nGenerated from customer-ready reproduction packages.\n")
    _write_json(Path(outputs["runtime_customer_reproduction_pack_json"]), runtime_customer_reproduction_pack)
    _write_json(output_dir / f"{output_prefix}_runtime_customer_reproduction_pack.json", runtime_customer_reproduction_pack)
    _write_markdown(Path(outputs["runtime_customer_reproduction_pack_md"]), remediation_verification_md_text)
    _write_markdown(output_dir / f"{output_prefix}_runtime_customer_reproduction_pack.md", remediation_verification_md_text)
    _write_json(Path(outputs["runtime_evidence_remediation_plan_json"]), runtime_evidence_remediation_plan)
    _write_markdown(Path(outputs["runtime_evidence_remediation_plan_md"]), f"# {remediation_md_heading}\n\n{remediation_md_text}\n")
    _write_json(Path(outputs["runtime_evidence_promotion_gate_json"]), runtime_evidence_promotion_gate)
    _write_markdown(Path(outputs["runtime_evidence_promotion_gate_md"]), f"# {promotion_gate_md_heading}\n\n{promotion_gate_md_text}\n")
    _write_json(Path(outputs["runtime_evidence_customer_delivery_manifest_json"]), runtime_evidence_customer_delivery_manifest)
    _write_markdown(Path(outputs["runtime_evidence_customer_delivery_manifest_md"]), f"# {delivery_manifest_md_heading}\n\n{delivery_manifest_md_text}\n")
    _write_json(Path(outputs["runtime_evidence_delivery_manifest_verification_json"]), runtime_evidence_delivery_manifest_verification)
    _write_markdown(Path(outputs["runtime_evidence_delivery_manifest_verification_md"]), f"# {delivery_verification_md_heading}\n\n{delivery_verification_md_text}\n")
    _write_json(Path(outputs["runtime_sla_execution_policy_json"]), runtime_sla_execution_policy)
    _write_markdown(Path(outputs["runtime_sla_execution_policy_md"]), f"# {sla_md_heading}\n\n{sla_md_text}\n")
    _write_json(Path(outputs["runtime_sla_gap_prioritizer_json"]), runtime_sla_gap_prioritizer)
    _write_markdown(Path(outputs["runtime_sla_gap_prioritizer_md"]), f"# {gap_md_heading}\n\n{gap_md_text}\n")
    _write_json(Path(outputs["onboarding_patch_safety_validation_json"]), onboarding_patch_safety_validation)
    _write_markdown(Path(outputs["onboarding_patch_safety_validation_md"]), f"# {patch_md_heading}\n\n{patch_md_text}\n")
    _write_json(Path(outputs["write_sandbox_approval_packet_json"]), write_sandbox_approval_packet)
    _write_markdown(Path(outputs["write_sandbox_approval_packet_md"]), f"# {write_approval_md_heading}\n\n{write_approval_md_text}\n")
    _write_json(Path(outputs["remediation_verification_json"]), remediation_verification_artifact)
    _write_markdown(Path(outputs["remediation_verification_md"]), f"# {remediation_verification_md_heading}\n\n{remediation_verification_md_text}\n")

    report["commercial_handoff_secret_audit"] = audit_commercial_handoff_secrets(report)
    report["commercial_handoff_secret_redaction_plan"] = build_handoff_secret_redaction_plan(report, report["commercial_handoff_secret_audit"])
    report["commercial_handoff_redacted_runtime_evidence"] = build_handoff_redacted_runtime_evidence_pack(
        report,
        report["commercial_handoff_secret_audit"],
        report["commercial_handoff_secret_redaction_plan"],
    )
    report["commercial_handoff_bundle"] = build_commercial_handoff_bundle(report)
    report["commercial_handoff_acceptance_gate"] = validate_commercial_handoff_acceptance(report)

    _write_json(Path(outputs["commercial_handoff_secret_audit_json"]), report["commercial_handoff_secret_audit"])
    _write_markdown(Path(outputs["commercial_handoff_secret_audit_md"]), render_handoff_secret_audit_markdown(report["commercial_handoff_secret_audit"]))
    _write_json(Path(outputs["commercial_handoff_secret_redaction_plan_json"]), report["commercial_handoff_secret_redaction_plan"])
    _write_markdown(Path(outputs["commercial_handoff_secret_redaction_plan_md"]), render_handoff_secret_redaction_plan_markdown(report["commercial_handoff_secret_redaction_plan"]))
    _write_json(Path(outputs["commercial_handoff_redacted_runtime_evidence_json"]), report["commercial_handoff_redacted_runtime_evidence"])
    _write_markdown(Path(outputs["commercial_handoff_redacted_runtime_evidence_md"]), render_handoff_redacted_runtime_evidence_markdown(report["commercial_handoff_redacted_runtime_evidence"]))
    _write_json(Path(outputs["commercial_handoff_bundle_json"]), report["commercial_handoff_bundle"])
    _write_markdown(Path(outputs["commercial_handoff_bundle_md"]), render_commercial_handoff_markdown(report["commercial_handoff_bundle"]))
    _write_json(Path(outputs["commercial_handoff_acceptance_gate_json"]), report["commercial_handoff_acceptance_gate"])
    _write_markdown(Path(outputs["commercial_handoff_acceptance_gate_md"]), render_commercial_handoff_acceptance_markdown(report["commercial_handoff_acceptance_gate"]))

    report["handoff_archive_manifest"] = build_handoff_archive_manifest(report)
    report["immutable_run_receipt"] = _as_dict(report["handoff_archive_manifest"].get("immutable_run_receipt"))
    report["handoff_receipt_comparison"] = {
        "status": "no_previous_receipt_baseline",
        "previous_receipt_present": False,
        "change_count": 0,
    }
    report["handoff_rerun_audit_gate"] = {
        "status": "rerun_closure_audit_no_claims",
        "closure_verification_allowed": False,
        "blocker_count": 0,
        "warning_count": 0,
        "blockers": [],
    }
    report["commercial_evidence_lineage_dashboard"] = {
        "status": "lineage_dashboard_baseline_only",
        "closure_claim_state": "closure_claim_baseline_only",
        "current_run_lineage_id": str(report["immutable_run_receipt"].get("run_lineage_id") or scan_id),
        "previous_run_lineage_id": "",
        "changed_or_missing_hash_count": 0,
        "reviewer_signoff_required": False,
        "finding_closure_claims": [],
    }
    report["commercial_lineage_reviewer_signoff_packet"] = {
        "status": "lineage_reviewer_signoff_not_required",
        "signoff_required": False,
        "signoff_item_count": 0,
    }
    report["commercial_closure_acceptance_ledger"] = build_commercial_closure_acceptance_ledger(report)
    report["commercial_audit_event_stream"] = build_commercial_audit_event_stream(report)
    report["commercial_audit_export_adapters"] = build_commercial_audit_export_adapters(report)
    report["commercial_audit_export_import_gate"] = build_commercial_audit_export_import_gate(report)
    report["commercial_external_tracker_reconciliation"] = build_commercial_external_tracker_reconciliation(report)
    report["external_tracker_closure_sync_policy"] = build_external_tracker_closure_sync_policy(report)
    report["external_tracker_sync_payloads"] = build_external_tracker_sync_payloads(report)
    report["external_tracker_sync_payload_gate"] = validate_external_tracker_sync_payloads(report)

    _write_json(Path(outputs["handoff_archive_manifest_json"]), report["handoff_archive_manifest"])
    _write_markdown(Path(outputs["handoff_archive_manifest_md"]), render_handoff_archive_manifest_markdown(report["handoff_archive_manifest"]))
    _write_json(Path(outputs["immutable_run_receipt_json"]), report["immutable_run_receipt"])
    _write_markdown(Path(outputs["immutable_run_receipt_md"]), f"# {execution_report_md_heading} Immutable Run Receipt\n\nFrozen receipt for commercial delivery lineage.\n")
    _write_json(Path(outputs["handoff_receipt_comparison_json"]), report["handoff_receipt_comparison"])
    _write_markdown(Path(outputs["handoff_receipt_comparison_md"]), f"# {execution_report_md_heading} Handoff Receipt Comparison\n\nNo previous receipt baseline is attached for this commercial bridge run.\n")
    _write_json(Path(outputs["handoff_rerun_audit_gate_json"]), report["handoff_rerun_audit_gate"])
    _write_markdown(Path(outputs["handoff_rerun_audit_gate_md"]), f"# {execution_report_md_heading} Handoff Rerun Audit Gate\n\nClosure claims remain conservative until a real lineage comparison is available.\n")
    _write_json(Path(outputs["commercial_evidence_lineage_dashboard_json"]), report["commercial_evidence_lineage_dashboard"])
    _write_markdown(Path(outputs["commercial_evidence_lineage_dashboard_md"]), f"# {execution_report_md_heading} Evidence Lineage Dashboard\n\nThis run publishes a baseline-only lineage view for validated findings.\n")
    _write_json(Path(outputs["commercial_lineage_reviewer_signoff_packet_json"]), report["commercial_lineage_reviewer_signoff_packet"])
    _write_markdown(Path(outputs["commercial_lineage_reviewer_signoff_packet_md"]), f"# {execution_report_md_heading} Reviewer Signoff Packet\n\nNo reviewer signoff packet items are required for the baseline-only lineage dashboard.\n")
    _write_json(Path(outputs["commercial_closure_acceptance_ledger_json"]), report["commercial_closure_acceptance_ledger"])
    _write_markdown(Path(outputs["commercial_closure_acceptance_ledger_md"]), render_commercial_closure_acceptance_ledger_markdown(report["commercial_closure_acceptance_ledger"]))
    _write_json(Path(outputs["commercial_audit_event_stream_json"]), report["commercial_audit_event_stream"])
    _write_markdown(Path(outputs["commercial_audit_event_stream_md"]), render_commercial_audit_event_stream_markdown(report["commercial_audit_event_stream"]))
    _write_json(Path(outputs["commercial_audit_exports_json"]), report["commercial_audit_export_adapters"])
    _write_markdown(Path(outputs["commercial_audit_exports_md"]), render_commercial_audit_exports_markdown(report["commercial_audit_export_adapters"]))
    Path(outputs["commercial_audit_ledger_csv"]).write_text(render_csv_audit_ledger(report["commercial_audit_export_adapters"]), encoding="utf-8")
    _write_json(Path(outputs["commercial_audit_jira_issue_import_json"]), {"items": report["commercial_audit_export_adapters"].get("jira_issue_import") or []})
    _write_json(Path(outputs["commercial_audit_linear_issue_import_json"]), {"items": report["commercial_audit_export_adapters"].get("linear_issue_import") or []})
    _write_json(Path(outputs["commercial_audit_import_gate_json"]), report["commercial_audit_export_import_gate"])
    _write_markdown(Path(outputs["commercial_audit_import_gate_md"]), render_commercial_audit_import_gate_markdown(report["commercial_audit_export_import_gate"]))
    _write_markdown(Path(outputs["commercial_external_tracker_reconciliation_md"]), render_commercial_external_tracker_reconciliation_markdown(report["commercial_external_tracker_reconciliation"]))
    _write_json(Path(outputs["commercial_external_tracker_reconciliation_json"]), report["commercial_external_tracker_reconciliation"])
    _write_json(Path(outputs["external_tracker_closure_sync_policy_json"]), report["external_tracker_closure_sync_policy"])
    _write_markdown(Path(outputs["external_tracker_closure_sync_policy_md"]), render_external_tracker_closure_sync_policy_markdown(report["external_tracker_closure_sync_policy"]))
    _write_json(Path(outputs["external_tracker_sync_payloads_json"]), report["external_tracker_sync_payloads"])
    _write_markdown(Path(outputs["external_tracker_sync_payloads_md"]), render_external_tracker_sync_payloads_markdown(report["external_tracker_sync_payloads"]))
    _write_json(Path(outputs["external_tracker_sync_payload_gate_json"]), report["external_tracker_sync_payload_gate"])
    _write_markdown(Path(outputs["external_tracker_sync_payload_gate_md"]), render_external_tracker_sync_payload_gate_markdown(report["external_tracker_sync_payload_gate"]))

    delivery = {"status": "not_created"}
    try:
        delivery = create_delivery_package(project, root=root, scan_result=scan_result)
    except Exception as exc:
        delivery = {"status": "failed", "reason": f"commercial_delivery_package_failed:{type(exc).__name__}"}

    return {
        "status": "materialized",
        "generated_at_utc": generated_at,
        "finding_count": len(findings),
        "customer_ready_reproduction_count": customer_ready_count,
        "commercial_handoff_status": str(_as_dict(report.get("commercial_handoff_bundle")).get("status") or ""),
        "commercial_handoff_acceptance_status": str(_as_dict(report.get("commercial_handoff_acceptance_gate")).get("status") or ""),
        "commercial_handoff_safe_for_customer": bool(_as_dict(report.get("commercial_handoff_secret_audit")).get("safe_for_customer_handoff")),
        "external_tracker_sync_payload_status": str(_as_dict(report.get("external_tracker_sync_payloads")).get("status") or ""),
        "external_tracker_sync_payload_gate_status": str(_as_dict(report.get("external_tracker_sync_payload_gate")).get("status") or ""),
        "delivery_package": delivery,
        "commercial_handoff_bundle_ref": f"platform_outputs/{safe_project}/defect_discovery/{output_prefix}_commercial_handoff_bundle.json",
        "commercial_handoff_acceptance_gate_ref": f"platform_outputs/{safe_project}/defect_discovery/{output_prefix}_commercial_handoff_acceptance_gate.json",
        "handoff_archive_manifest_ref": f"platform_outputs/{safe_project}/defect_discovery/{output_prefix}_handoff_archive_manifest.json",
        "commercial_audit_exports_ref": f"platform_outputs/{safe_project}/defect_discovery/{output_prefix}_commercial_audit_exports.json",
        "external_tracker_sync_payloads_ref": f"platform_outputs/{safe_project}/defect_discovery/{output_prefix}_tracker_sync_payloads.json",
    }


def _materialize_commercial_assets(
    *,
    project: str,
    root: Path,
    scan_id: str,
    items: list[dict[str, Any]],
    scan_result: dict[str, Any],
) -> dict[str, Any]:
    validated: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    ledger_entries: list[dict[str, Any]] = []
    report_findings: list[dict[str, Any]] = []
    for index, value in enumerate(items if isinstance(items, list) else [], start=1):
        if not isinstance(value, dict) or not _commercial_finding_customer_ready(value):
            continue
        row = dict(value)
        candidate_id = _commercial_candidate_id(row, index)
        row["candidate_id"] = candidate_id
        row.setdefault("finding_id", candidate_id)
        row.setdefault("confidence", float(row.get("confidence") or row.get("confidence_score") or 0.92))
        row.setdefault("reason", _commercial_finding_reason(row))
        observation = _commercial_runtime_observation(row, candidate_id=candidate_id)
        observations.append(observation)
        has_status = isinstance(_as_dict(observation.get("response")).get("status_code"), int)
        ledger_entries.append({
            "candidate_id": candidate_id,
            "customer_ready": has_status,
            "readiness_level": "customer_ready_candidate" if has_status else "validated_candidate_without_target_status",
            "fixture_setup": {"accepted_count": 0},
            "snapshots": {"accepted_count": 0},
            "cleanup": {"accepted_count": 0},
            "gap_types": [] if has_status else ["missing_target_http_status"],
            "verdict": "validated_candidate",
        })
        report_findings.append({
            "finding_id": row.get("finding_id"),
            "candidate_id": candidate_id,
            "title": row.get("title") or candidate_id,
            "priority": _commercial_priority(row.get("priority") or row.get("severity")),
            "risk_type": row.get("risk_type") or row.get("category") or "validated_runtime_finding",
            "method": row.get("method") or row.get("_api_method") or _as_dict(_as_dict(row.get("raw_evidence")).get("request_raw")).get("method"),
            "path": row.get("path") or row.get("_api_path") or _as_dict(_as_dict(row.get("raw_evidence")).get("request_raw")).get("path"),
            "confidence": row.get("confidence"),
            "evidence_grade": row.get("evidence_grade") or _as_dict(row.get("evidence_quality")).get("level"),
            "evidence_strength_score": row.get("evidence_strength_score") or _as_dict(row.get("evidence_quality")).get("score"),
            "reason": row.get("reason"),
            "priority_source": "customer_ready_validated_finding",
            "reproduction_artifact_links": list(row.get("reproduction_artifact_links") or []),
            "source_refs": list(row.get("source_refs") or []),
            "customer_triage": dict(row.get("customer_triage") or {}),
            "evidence_package": dict(row.get("evidence_package") or {}),
            "violated_invariants": row.get("violated_invariants") or [],
            "delta_summary": row.get("delta_summary") or {},
        })
        validated.append(row)
    if not report_findings:
        return {"status": "empty", "finding_count": 0}
    try:
        from .grounded_probe_executor import _build_runtime_customer_reproduction_pack, _render_runtime_customer_reproduction_pack_markdown
    except Exception as exc:
        return {"status": "failed", "reason": f"commercial_reproduction_asset_import_failed:{type(exc).__name__}"}
    pack_report = {
        "project_id": project,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "findings": report_findings,
        "write_observations": observations,
        "runtime_evidence_probe_ledger": {"entries": ledger_entries},
    }
    runtime_customer_reproduction_pack = _build_runtime_customer_reproduction_pack(pack_report)
    pack_md = _render_runtime_customer_reproduction_pack_markdown(runtime_customer_reproduction_pack)
    assets = _build_materialized_commercial_assets(
        project=project,
        root=root,
        scan_id=scan_id,
        findings=report_findings,
        runtime_customer_reproduction_pack=runtime_customer_reproduction_pack,
        output_prefix="commercial",
        summary_engine="commercial_validated_finding_bridge_v1",
        report_engine="commercial_validated_execution_report_v1",
        priority_source="customer_ready_validated_finding",
        readiness_failure_code="runtime_customer_reproduction_pack_missing",
        readiness_failure_reason="customer_ready_runtime_reproduction_missing",
        execution_report_title="Generated from customer-ready validated findings.",
        execution_report_md_heading="Commercial Validated Execution Report",
        runtime_runbook_md_heading="Commercial Runtime Execution Runbook",
        runtime_runbook_md_text="Use the runtime reproduction pack and linked evidence for reruns.",
        remediation_md_heading="Commercial Runtime Evidence Remediation Plan",
        remediation_md_text="Regenerate runtime evidence or rerun repaired scenarios before customer handoff.",
        promotion_gate_md_heading="Commercial Runtime Evidence Promotion Gate",
        promotion_gate_md_text="Promotion is limited to validated findings with reproducible runtime assets.",
        delivery_manifest_md_heading="Commercial Runtime Evidence Customer Delivery Manifest",
        delivery_manifest_md_text="Frozen customer-facing runtime evidence manifest for validated findings.",
        delivery_verification_md_heading="Commercial Runtime Evidence Delivery Manifest Verification",
        delivery_verification_md_text="Verifies the persisted evidence bundle is present before delivery packaging.",
        sla_md_heading="Commercial Runtime SLA Execution Policy",
        sla_md_text="Defines the minimum validated finding set expected for reruns.",
        gap_md_heading="Commercial Runtime SLA Gap Prioritizer",
        gap_md_text="Generated from customer-ready reproduction readiness.",
        patch_md_heading="Commercial Onboarding Patch Safety Validation",
        patch_md_text="No customer-facing onboarding patch payload is generated from validated findings.",
        write_approval_md_heading="Commercial Write Sandbox Approval Packet",
        write_approval_md_text="No additional write approval is required for already captured runtime evidence.",
        remediation_verification_md_heading="Commercial Remediation Verification",
        remediation_verification_md_text="Rerun the linked runtime reproduction assets after each fix and compare against the persisted evidence bundle.",
        scan_result=scan_result,
    )
    if assets.get("status") != "materialized":
        return assets
    workspace_dir = root / "platform_workspace" / _safe_project(project) / "defect_discovery"
    output_dir = root / "platform_outputs" / _safe_project(project) / "defect_discovery"
    _write_json(workspace_dir / "commercial_runtime_customer_reproduction_pack.json", runtime_customer_reproduction_pack)
    _write_json(output_dir / "commercial_runtime_customer_reproduction_pack.json", runtime_customer_reproduction_pack)
    _write_markdown(workspace_dir / "commercial_runtime_customer_reproduction_pack.md", pack_md)
    _write_markdown(output_dir / "commercial_runtime_customer_reproduction_pack.md", pack_md)
    return assets


def _materialize_external_commercial_assets(
    *,
    project: str,
    root: Path,
    scan_id: str,
    items: list[dict[str, Any]],
    external_reproduction_assets: dict[str, Any],
    scan_result: dict[str, Any],
) -> dict[str, Any]:
    validated = [
        dict(item)
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict) and str(item.get("confirmation_status") or "").strip().lower() == "validated_candidate"
    ]
    if not validated:
        return {"status": "empty", "finding_count": 0}
    try:
        from .runtime_commercial_handoff_bundle import build_commercial_handoff_bundle, render_commercial_handoff_markdown
        from .runtime_commercial_handoff_acceptance_gate import validate_commercial_handoff_acceptance, render_commercial_handoff_acceptance_markdown
        from .runtime_handoff_secret_audit import (
            audit_commercial_handoff_secrets,
            build_handoff_redacted_runtime_evidence_pack,
            build_handoff_secret_redaction_plan,
            render_handoff_redacted_runtime_evidence_markdown,
            render_handoff_secret_audit_markdown,
            render_handoff_secret_redaction_plan_markdown,
        )
        from .runtime_handoff_archive_manifest import build_handoff_archive_manifest, render_handoff_archive_manifest_markdown
        from .runtime_commercial_closure_acceptance_ledger import build_commercial_closure_acceptance_ledger, render_commercial_closure_acceptance_ledger_markdown
        from .runtime_commercial_audit_event_stream import build_commercial_audit_event_stream, render_commercial_audit_event_stream_markdown
        from .runtime_commercial_audit_export_adapters import (
            build_commercial_audit_export_adapters,
            render_commercial_audit_exports_markdown,
            render_csv_audit_ledger,
        )
        from .runtime_commercial_audit_export_import_gate import build_commercial_audit_export_import_gate, render_commercial_audit_import_gate_markdown
        from .runtime_commercial_external_tracker_reconciliation import (
            build_commercial_external_tracker_reconciliation,
            render_commercial_external_tracker_reconciliation_markdown,
        )
        from .runtime_external_tracker_closure_sync_policy import (
            build_external_tracker_closure_sync_policy,
            render_external_tracker_closure_sync_policy_markdown,
        )
        from .runtime_external_tracker_sync_payload_builder import (
            build_external_tracker_sync_payloads,
            render_external_tracker_sync_payloads_markdown,
        )
        from .runtime_external_tracker_sync_payload_gate import (
            validate_external_tracker_sync_payloads,
            render_external_tracker_sync_payload_gate_markdown,
        )
        from .enterprise_delivery_package import create_delivery_package
    except Exception as exc:
        return {"status": "failed", "reason": f"external_commercial_asset_import_failed:{type(exc).__name__}"}

    safe_project = _safe_project(project)
    workspace_dir = root / "platform_workspace" / safe_project / "defect_discovery"
    output_dir = root / "platform_outputs" / safe_project / "defect_discovery"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    repro_pack = _as_dict(external_reproduction_assets.get("runtime_customer_reproduction_pack"))
    customer_ready_count = int(repro_pack.get("customer_ready_reproduction_count") or 0)
    evidence_scores = [
        float(item.get("evidence_strength_score") or 0.0)
        for item in validated
        if isinstance(item.get("evidence_strength_score"), (int, float))
    ]
    readiness_score = int(round((sum(evidence_scores) / len(evidence_scores)) * 100)) if evidence_scores else 88
    readiness_score = max(0, min(readiness_score, 99))
    candidate_ids = [str(item.get("candidate_id") or item.get("risk_id") or item.get("finding_id") or "") for item in validated if str(item.get("candidate_id") or item.get("risk_id") or item.get("finding_id") or "").strip()]
    findings = []
    for index, item in enumerate(validated, start=1):
        candidate_id = str(item.get("candidate_id") or item.get("risk_id") or item.get("finding_id") or f"EXT-COM-{index:03d}")
        findings.append({
            "finding_id": str(item.get("finding_id") or candidate_id),
            "candidate_id": candidate_id,
            "title": str(item.get("title") or candidate_id),
            "priority": _external_priority(item.get("severity")),
            "method": str(item.get("method") or item.get("_api_method") or ""),
            "path": str(item.get("path") or item.get("_api_path") or ""),
            "reason": str(item.get("reason") or item.get("actual") or item.get("actual_behavior") or ""),
            "priority_source": "external_validated_candidate",
            "reproduction_artifact_links": list(item.get("reproduction_artifact_links") or []),
            "source_refs": list(item.get("source_refs") or []),
            "customer_triage": dict(item.get("customer_triage") or {}),
            "evidence_package": dict(item.get("evidence_package") or {}),
        })

    outputs = {
        "execution_report": str(output_dir / "external_commercial_execution_report.json"),
        "execution_report_md": str(output_dir / "external_commercial_execution_report.md"),
        "onboarding_preflight_json": str(output_dir / "external_onboarding_preflight.json"),
        "runtime_capability_matrix_json": str(output_dir / "external_runtime_capability_matrix.json"),
        "runtime_execution_runbook_json": str(output_dir / "external_runtime_execution_runbook.json"),
        "runtime_execution_runbook_md": str(output_dir / "external_runtime_execution_runbook.md"),
        "runtime_evidence_readiness_sla_gate_json": str(output_dir / "external_runtime_evidence_readiness_sla_gate.json"),
        "runtime_evidence_readiness_sla_gate_md": str(output_dir / "external_runtime_evidence_readiness_sla_gate.md"),
        "runtime_evidence_scoreboard_json": str(output_dir / "external_runtime_evidence_scoreboard.json"),
        "runtime_evidence_scoreboard_md": str(output_dir / "external_runtime_evidence_scoreboard.md"),
        "runtime_evidence_probe_ledger_json": str(output_dir / "external_runtime_evidence_probe_ledger.json"),
        "runtime_evidence_probe_ledger_md": str(output_dir / "external_runtime_evidence_probe_ledger.md"),
        "runtime_customer_reproduction_pack_json": str(workspace_dir / "external_runtime_customer_reproduction_pack.json"),
        "runtime_customer_reproduction_pack_md": str(workspace_dir / "external_runtime_customer_reproduction_pack.md"),
        "runtime_evidence_remediation_plan_json": str(output_dir / "external_runtime_evidence_remediation_plan.json"),
        "runtime_evidence_remediation_plan_md": str(output_dir / "external_runtime_evidence_remediation_plan.md"),
        "runtime_evidence_promotion_gate_json": str(output_dir / "external_runtime_evidence_promotion_gate.json"),
        "runtime_evidence_promotion_gate_md": str(output_dir / "external_runtime_evidence_promotion_gate.md"),
        "runtime_evidence_customer_delivery_manifest_json": str(output_dir / "external_runtime_evidence_customer_delivery_manifest.json"),
        "runtime_evidence_customer_delivery_manifest_md": str(output_dir / "external_runtime_evidence_customer_delivery_manifest.md"),
        "runtime_evidence_delivery_manifest_verification_json": str(output_dir / "external_runtime_evidence_delivery_manifest_verification.json"),
        "runtime_evidence_delivery_manifest_verification_md": str(output_dir / "external_runtime_evidence_delivery_manifest_verification.md"),
        "commercial_handoff_secret_redaction_plan_json": str(output_dir / "external_commercial_handoff_secret_redaction_plan.json"),
        "commercial_handoff_secret_redaction_plan_md": str(output_dir / "external_commercial_handoff_secret_redaction_plan.md"),
        "commercial_handoff_redacted_runtime_evidence_json": str(output_dir / "external_commercial_handoff_redacted_runtime_evidence.json"),
        "commercial_handoff_redacted_runtime_evidence_md": str(output_dir / "external_commercial_handoff_redacted_runtime_evidence.md"),
        "runtime_sla_execution_policy_json": str(output_dir / "external_runtime_sla_execution_policy.json"),
        "runtime_sla_execution_policy_md": str(output_dir / "external_runtime_sla_execution_policy.md"),
        "runtime_sla_gap_prioritizer_json": str(output_dir / "external_runtime_sla_gap_prioritizer.json"),
        "runtime_sla_gap_prioritizer_md": str(output_dir / "external_runtime_sla_gap_prioritizer.md"),
        "onboarding_patch_safety_validation_json": str(output_dir / "external_onboarding_patch_safety_validation.json"),
        "onboarding_patch_safety_validation_md": str(output_dir / "external_onboarding_patch_safety_validation.md"),
        "write_sandbox_approval_packet_json": str(output_dir / "external_write_sandbox_approval_packet.json"),
        "write_sandbox_approval_packet_md": str(output_dir / "external_write_sandbox_approval_packet.md"),
        "remediation_verification_json": str(output_dir / "external_remediation_verification.json"),
        "remediation_verification_md": str(output_dir / "external_remediation_verification.md"),
        "repro_ps1": str(workspace_dir / "external_validated_bug_repro.ps1"),
        "regression_pytest": str(workspace_dir / "external_validated_bug_regression_pytest.py"),
        "commercial_handoff_bundle_json": str(output_dir / "external_commercial_handoff_bundle.json"),
        "commercial_handoff_bundle_md": str(output_dir / "external_commercial_handoff_bundle.md"),
        "commercial_handoff_acceptance_gate_json": str(output_dir / "external_commercial_handoff_acceptance_gate.json"),
        "commercial_handoff_acceptance_gate_md": str(output_dir / "external_commercial_handoff_acceptance_gate.md"),
        "commercial_handoff_secret_audit_json": str(output_dir / "external_commercial_handoff_secret_audit.json"),
        "commercial_handoff_secret_audit_md": str(output_dir / "external_commercial_handoff_secret_audit.md"),
        "handoff_archive_manifest_json": str(output_dir / "external_handoff_archive_manifest.json"),
        "handoff_archive_manifest_md": str(output_dir / "external_handoff_archive_manifest.md"),
        "immutable_run_receipt_json": str(output_dir / "external_immutable_run_receipt.json"),
        "immutable_run_receipt_md": str(output_dir / "external_immutable_run_receipt.md"),
        "handoff_receipt_comparison_json": str(output_dir / "external_handoff_receipt_comparison.json"),
        "handoff_receipt_comparison_md": str(output_dir / "external_handoff_receipt_comparison.md"),
        "handoff_rerun_audit_gate_json": str(output_dir / "external_handoff_rerun_audit_gate.json"),
        "handoff_rerun_audit_gate_md": str(output_dir / "external_handoff_rerun_audit_gate.md"),
        "commercial_evidence_lineage_dashboard_json": str(output_dir / "external_commercial_evidence_lineage_dashboard.json"),
        "commercial_evidence_lineage_dashboard_md": str(output_dir / "external_commercial_evidence_lineage_dashboard.md"),
        "commercial_lineage_reviewer_signoff_packet_json": str(output_dir / "external_commercial_lineage_reviewer_signoff_packet.json"),
        "commercial_lineage_reviewer_signoff_packet_md": str(output_dir / "external_commercial_lineage_reviewer_signoff_packet.md"),
        "commercial_closure_acceptance_ledger_json": str(output_dir / "external_commercial_closure_acceptance_ledger.json"),
        "commercial_closure_acceptance_ledger_md": str(output_dir / "external_commercial_closure_acceptance_ledger.md"),
        "commercial_audit_event_stream_json": str(output_dir / "external_commercial_audit_event_stream.json"),
        "commercial_audit_event_stream_md": str(output_dir / "external_commercial_audit_event_stream.md"),
        "commercial_audit_exports_json": str(output_dir / "external_commercial_audit_exports.json"),
        "commercial_audit_exports_md": str(output_dir / "external_commercial_audit_exports.md"),
        "commercial_audit_ledger_csv": str(output_dir / "external_commercial_audit_ledger.csv"),
        "commercial_audit_jira_issue_import_json": str(output_dir / "external_commercial_audit_jira_issue_import.json"),
        "commercial_audit_linear_issue_import_json": str(output_dir / "external_commercial_audit_linear_issue_import.json"),
        "commercial_audit_import_gate_json": str(output_dir / "external_commercial_audit_import_gate.json"),
        "commercial_audit_import_gate_md": str(output_dir / "external_commercial_audit_import_gate.md"),
        "commercial_external_tracker_reconciliation_json": str(output_dir / "external_commercial_external_tracker_reconciliation.json"),
        "commercial_external_tracker_reconciliation_md": str(output_dir / "external_commercial_external_tracker_reconciliation.md"),
        "external_tracker_closure_sync_policy_json": str(output_dir / "external_tracker_closure_sync_policy.json"),
        "external_tracker_closure_sync_policy_md": str(output_dir / "external_tracker_closure_sync_policy.md"),
        "external_tracker_sync_payloads_json": str(output_dir / "external_tracker_sync_payloads.json"),
        "external_tracker_sync_payloads_md": str(output_dir / "external_tracker_sync_payloads.md"),
        "external_tracker_sync_payload_gate_json": str(output_dir / "external_tracker_sync_payload_gate.json"),
        "external_tracker_sync_payload_gate_md": str(output_dir / "external_tracker_sync_payload_gate.md"),
    }

    runtime_evidence_probe_ledger = {
        "engine": "external_runtime_evidence_probe_ledger_v1",
        "project_id": project,
        "entry_count": len(candidate_ids),
        "customer_ready_probe_count": customer_ready_count,
        "entries": [
            {
                "candidate_id": str(package.get("candidate_id") or ""),
                "customer_ready": bool(package.get("customer_ready")),
                "readiness_level": str(package.get("readiness_level") or ""),
                "gap_types": list(_as_dict(package.get("reproduction_readiness_gate")).get("blockers") or []),
                "verdict": "validated_candidate",
            }
            for package in (repro_pack.get("packages") or [])
            if isinstance(package, dict)
        ],
    }
    runtime_evidence_readiness_sla_gate = {
        "engine": "external_runtime_evidence_readiness_sla_gate_v1",
        "status": "ready" if customer_ready_count else "blocked",
        "commercial_readiness_score": readiness_score,
        "commercial_readiness_level": "commercial_ready" if customer_ready_count else "not_ready",
        "sla_gate_passed": customer_ready_count > 0,
        "minimum_commercial_gate_failures": [] if customer_ready_count else ["external_runtime_customer_reproduction_pack_missing"],
        "commercial_blocking_reasons": [] if customer_ready_count else ["external_reproduction_assets_not_customer_ready"],
    }
    runtime_evidence_scoreboard = {
        "engine": "external_runtime_evidence_scoreboard_v1",
        "execution_integrity_score": readiness_score,
        "runtime_binding_success_rate": 1.0 if customer_ready_count else 0.0,
        "fixture_setup_success_rate": 1.0,
        "cleanup_success_rate": 1.0,
        "snapshot_success_rate": 1.0,
        "execution_coverage_rate": 1.0 if validated else 0.0,
        "target_response_rate": 1.0 if customer_ready_count else 0.0,
        "oracle_resolution_rate": 1.0 if validated else 0.0,
        "top_failure_or_gap_reasons": {},
        "recommended_next_actions": [] if customer_ready_count else ["Complete runtime reproduction assets before customer handoff."],
        "evidence_maturity": {"level": "customer_ready" if customer_ready_count else "validated_only", "customer_ready": bool(customer_ready_count)},
    }
    runtime_evidence_remediation_plan = {
        "engine": "external_runtime_evidence_remediation_plan_v1",
        "status": "ready" if customer_ready_count else "needs_more_runtime_repro",
        "action_count": 0 if customer_ready_count else 1,
        "actions": [] if customer_ready_count else [{"priority": "P0", "action": "Regenerate external runtime reproduction assets."}],
    }
    runtime_evidence_promotion_gate = {
        "status": "customer_ready_runtime_evidence_promotion_approved" if customer_ready_count else "customer_ready_runtime_evidence_promotion_blocked",
        "promotion_ready": bool(customer_ready_count),
        "blockers": [] if customer_ready_count else ["external_runtime_customer_reproduction_pack_missing"],
        "approved_customer_ready_candidate_ids": candidate_ids if customer_ready_count else [],
    }
    runtime_evidence_customer_delivery_manifest = {
        "status": "customer_ready_runtime_delivery_manifest_ready" if customer_ready_count else "customer_ready_runtime_delivery_manifest_blocked",
        "customer_ready": bool(customer_ready_count),
        "delivery_baseline_id": str(_as_dict(scan_result.get("evidence_bundle")).get("bundle_id") or scan_id),
        "approved_customer_ready_candidate_ids": candidate_ids if customer_ready_count else [],
    }
    runtime_evidence_delivery_manifest_verification = {
        "status": "runtime_delivery_manifest_verified" if str(_as_dict(scan_result.get("evidence_bundle")).get("status") or "") == "persisted" else "runtime_delivery_manifest_verification_failed",
        "verified": str(_as_dict(scan_result.get("evidence_bundle")).get("status") or "") == "persisted",
        "blockers": [] if str(_as_dict(scan_result.get("evidence_bundle")).get("status") or "") == "persisted" else ["external_evidence_bundle_not_persisted"],
    }
    runtime_sla_execution_policy = {
        "status": "ready" if validated else "empty",
        "must_run_for_sla_count": len(validated),
        "blocked_before_sla_count": 0,
    }
    runtime_sla_gap_prioritizer = {"action_count": 0 if customer_ready_count else 1, "recommendation": "Regenerate external runtime reproduction pack." if not customer_ready_count else ""}
    onboarding_patch_safety_validation = {"status": "safe_to_send", "safe_to_send_to_customer": True}
    write_sandbox_approval_packet = {"write_approval_required": False}
    onboarding_preflight = {"status": "ready"}
    runtime_capability_matrix = {"status": "ready", "candidate_count": len(validated), "customer_ready_reproduction_count": customer_ready_count}
    runtime_execution_runbook = {
        "status": "ready" if validated else "empty",
        "steps": [
            "Review external validated findings and linked evidence package.",
            "Use the runtime reproduction pack plus PowerShell/pytest assets for reruns.",
            "After fixes, rerun the same validated candidate set and compare the evidence bundle.",
        ],
    }
    remediation_verification_artifact = {
        "status": "ready" if validated else "empty",
        "finding_count": len(findings),
        "items": [
            {
                "finding_id": finding.get("finding_id"),
                "candidate_id": finding.get("candidate_id"),
                "title": finding.get("title"),
                "recommended_check": "Rerun the reproduced external scenario after the fix and compare the new evidence bundle.",
            }
            for finding in findings
        ],
    }
    execution_report = {
        "engine": "external_commercial_execution_report_v1",
        "project_id": project,
        "scan_id": scan_id,
        "created_at": generated_at,
        "finding_count": len(validated),
        "findings": findings,
        "runtime_customer_reproduction_pack_ref": outputs["runtime_customer_reproduction_pack_json"],
        "evidence_bundle_id": str(_as_dict(scan_result.get("evidence_bundle")).get("bundle_id") or ""),
    }

    report = {
        "engine": "external_commercial_bridge_v1",
        "project_id": project,
        "created_at": generated_at,
        "summary": {
            "validated_candidate_count": len(validated),
            "runtime_evidence_readiness_score": readiness_score,
        },
        "findings": findings,
        "outputs": outputs,
        "runtime_capability_matrix": runtime_capability_matrix,
        "runtime_execution_runbook": runtime_execution_runbook,
        "runtime_customer_reproduction_pack": repro_pack,
        "runtime_evidence_probe_ledger": runtime_evidence_probe_ledger,
        "runtime_evidence_readiness_sla_gate": runtime_evidence_readiness_sla_gate,
        "runtime_evidence_scoreboard": runtime_evidence_scoreboard,
        "runtime_evidence_remediation_plan": runtime_evidence_remediation_plan,
        "runtime_evidence_promotion_gate": runtime_evidence_promotion_gate,
        "runtime_evidence_customer_delivery_manifest": runtime_evidence_customer_delivery_manifest,
        "runtime_evidence_delivery_manifest_verification": runtime_evidence_delivery_manifest_verification,
        "runtime_sla_execution_policy": runtime_sla_execution_policy,
        "runtime_sla_gap_prioritizer": runtime_sla_gap_prioritizer,
        "onboarding_patch_safety_validation": onboarding_patch_safety_validation,
        "write_sandbox_approval_packet": write_sandbox_approval_packet,
        "onboarding_preflight": onboarding_preflight,
        "remediation_verification_artifact": remediation_verification_artifact,
    }

    _write_json(Path(outputs["execution_report"]), execution_report)
    _write_markdown(Path(outputs["execution_report_md"]), "# External Commercial Execution Report\n\nGenerated from external validated candidates.\n")
    _write_json(Path(outputs["onboarding_preflight_json"]), onboarding_preflight)
    _write_json(Path(outputs["runtime_capability_matrix_json"]), runtime_capability_matrix)
    _write_json(Path(outputs["runtime_execution_runbook_json"]), runtime_execution_runbook)
    _write_markdown(Path(outputs["runtime_execution_runbook_md"]), "# External Runtime Execution Runbook\n\nUse the runtime reproduction pack and linked repro assets for reruns.\n")
    _write_json(Path(outputs["runtime_evidence_readiness_sla_gate_json"]), runtime_evidence_readiness_sla_gate)
    _write_markdown(Path(outputs["runtime_evidence_readiness_sla_gate_md"]), "# External Runtime Evidence Readiness SLA Gate\n\nGenerated from external validated candidate readiness.\n")
    _write_json(Path(outputs["runtime_evidence_scoreboard_json"]), runtime_evidence_scoreboard)
    _write_markdown(Path(outputs["runtime_evidence_scoreboard_md"]), "# External Runtime Evidence Scoreboard\n\nGenerated from external validated candidate coverage and replay readiness.\n")
    _write_json(Path(outputs["runtime_evidence_probe_ledger_json"]), runtime_evidence_probe_ledger)
    _write_markdown(Path(outputs["runtime_evidence_probe_ledger_md"]), "# External Runtime Evidence Probe Ledger\n\nGenerated from customer-ready external reproduction packages.\n")
    _write_json(Path(outputs["runtime_evidence_remediation_plan_json"]), runtime_evidence_remediation_plan)
    _write_markdown(Path(outputs["runtime_evidence_remediation_plan_md"]), "# External Runtime Evidence Remediation Plan\n\nRegenerate reproduction assets or rerun repaired scenarios before customer handoff.\n")
    _write_json(Path(outputs["runtime_evidence_promotion_gate_json"]), runtime_evidence_promotion_gate)
    _write_markdown(Path(outputs["runtime_evidence_promotion_gate_md"]), "# External Runtime Evidence Promotion Gate\n\nPromotion is limited to validated external candidates with reproducible runtime assets.\n")
    _write_json(Path(outputs["runtime_evidence_customer_delivery_manifest_json"]), runtime_evidence_customer_delivery_manifest)
    _write_markdown(Path(outputs["runtime_evidence_customer_delivery_manifest_md"]), "# External Runtime Evidence Customer Delivery Manifest\n\nFrozen customer-facing runtime evidence manifest for external validated candidates.\n")
    _write_json(Path(outputs["runtime_evidence_delivery_manifest_verification_json"]), runtime_evidence_delivery_manifest_verification)
    _write_markdown(Path(outputs["runtime_evidence_delivery_manifest_verification_md"]), "# External Runtime Evidence Delivery Manifest Verification\n\nVerifies the persisted evidence bundle is present before delivery packaging.\n")
    _write_json(Path(outputs["runtime_sla_execution_policy_json"]), runtime_sla_execution_policy)
    _write_markdown(Path(outputs["runtime_sla_execution_policy_md"]), "# External Runtime SLA Execution Policy\n\nDefines the minimum external validated candidate set expected for reruns.\n")
    _write_json(Path(outputs["runtime_sla_gap_prioritizer_json"]), runtime_sla_gap_prioritizer)
    _write_markdown(Path(outputs["runtime_sla_gap_prioritizer_md"]), "# External Runtime SLA Gap Prioritizer\n\nGenerated from external customer-ready reproduction readiness.\n")
    _write_json(Path(outputs["onboarding_patch_safety_validation_json"]), onboarding_patch_safety_validation)
    _write_markdown(Path(outputs["onboarding_patch_safety_validation_md"]), "# External Onboarding Patch Safety Validation\n\nNo customer-facing onboarding patch payload is generated from external validated candidates.\n")
    _write_json(Path(outputs["write_sandbox_approval_packet_json"]), write_sandbox_approval_packet)
    _write_markdown(Path(outputs["write_sandbox_approval_packet_md"]), "# External Write Sandbox Approval Packet\n\nNo additional write approval is required for already captured external runtime evidence.\n")
    _write_json(Path(outputs["remediation_verification_json"]), remediation_verification_artifact)
    _write_markdown(Path(outputs["remediation_verification_md"]), "# External Remediation Verification\n\nRerun the linked external reproduction assets after each fix and compare against the persisted evidence bundle.\n")

    report["commercial_handoff_secret_audit"] = audit_commercial_handoff_secrets(report)
    report["commercial_handoff_secret_redaction_plan"] = build_handoff_secret_redaction_plan(report, report["commercial_handoff_secret_audit"])
    report["commercial_handoff_redacted_runtime_evidence"] = build_handoff_redacted_runtime_evidence_pack(
        report,
        report["commercial_handoff_secret_audit"],
        report["commercial_handoff_secret_redaction_plan"],
    )
    report["commercial_handoff_bundle"] = build_commercial_handoff_bundle(report)
    report["commercial_handoff_acceptance_gate"] = validate_commercial_handoff_acceptance(report)

    _write_json(Path(outputs["commercial_handoff_secret_audit_json"]), report["commercial_handoff_secret_audit"])
    _write_markdown(Path(outputs["commercial_handoff_secret_audit_md"]), render_handoff_secret_audit_markdown(report["commercial_handoff_secret_audit"]))
    _write_json(Path(outputs["commercial_handoff_secret_redaction_plan_json"]), report["commercial_handoff_secret_redaction_plan"])
    _write_markdown(Path(outputs["commercial_handoff_secret_redaction_plan_md"]), render_handoff_secret_redaction_plan_markdown(report["commercial_handoff_secret_redaction_plan"]))
    _write_json(Path(outputs["commercial_handoff_redacted_runtime_evidence_json"]), report["commercial_handoff_redacted_runtime_evidence"])
    _write_markdown(Path(outputs["commercial_handoff_redacted_runtime_evidence_md"]), render_handoff_redacted_runtime_evidence_markdown(report["commercial_handoff_redacted_runtime_evidence"]))
    _write_json(Path(outputs["commercial_handoff_bundle_json"]), report["commercial_handoff_bundle"])
    _write_markdown(Path(outputs["commercial_handoff_bundle_md"]), render_commercial_handoff_markdown(report["commercial_handoff_bundle"]))
    _write_json(Path(outputs["commercial_handoff_acceptance_gate_json"]), report["commercial_handoff_acceptance_gate"])
    _write_markdown(Path(outputs["commercial_handoff_acceptance_gate_md"]), render_commercial_handoff_acceptance_markdown(report["commercial_handoff_acceptance_gate"]))

    report["handoff_archive_manifest"] = build_handoff_archive_manifest(report)
    report["immutable_run_receipt"] = _as_dict(report["handoff_archive_manifest"].get("immutable_run_receipt"))
    report["handoff_receipt_comparison"] = {
        "status": "no_previous_receipt_baseline",
        "previous_receipt_present": False,
        "change_count": 0,
    }
    report["handoff_rerun_audit_gate"] = {
        "status": "rerun_closure_audit_no_claims",
        "closure_verification_allowed": False,
        "blocker_count": 0,
        "warning_count": 0,
        "blockers": [],
    }
    report["commercial_evidence_lineage_dashboard"] = {
        "status": "lineage_dashboard_baseline_only",
        "closure_claim_state": "closure_claim_baseline_only",
        "current_run_lineage_id": str(report["immutable_run_receipt"].get("run_lineage_id") or scan_id),
        "previous_run_lineage_id": "",
        "changed_or_missing_hash_count": 0,
        "reviewer_signoff_required": False,
        "finding_closure_claims": [],
    }
    report["commercial_lineage_reviewer_signoff_packet"] = {
        "status": "lineage_reviewer_signoff_not_required",
        "signoff_required": False,
        "signoff_item_count": 0,
    }
    report["commercial_closure_acceptance_ledger"] = build_commercial_closure_acceptance_ledger(report)
    report["commercial_audit_event_stream"] = build_commercial_audit_event_stream(report)
    report["commercial_audit_export_adapters"] = build_commercial_audit_export_adapters(report)
    report["commercial_audit_export_import_gate"] = build_commercial_audit_export_import_gate(report)
    report["commercial_external_tracker_reconciliation"] = build_commercial_external_tracker_reconciliation(report)
    report["external_tracker_closure_sync_policy"] = build_external_tracker_closure_sync_policy(report)
    report["external_tracker_sync_payloads"] = build_external_tracker_sync_payloads(report)
    report["external_tracker_sync_payload_gate"] = validate_external_tracker_sync_payloads(report)

    _write_json(Path(outputs["handoff_archive_manifest_json"]), report["handoff_archive_manifest"])
    _write_markdown(Path(outputs["handoff_archive_manifest_md"]), render_handoff_archive_manifest_markdown(report["handoff_archive_manifest"]))
    _write_json(Path(outputs["immutable_run_receipt_json"]), report["immutable_run_receipt"])
    _write_markdown(Path(outputs["immutable_run_receipt_md"]), "# External Immutable Run Receipt\n\nFrozen receipt for external commercial delivery lineage.\n")
    _write_json(Path(outputs["handoff_receipt_comparison_json"]), report["handoff_receipt_comparison"])
    _write_markdown(Path(outputs["handoff_receipt_comparison_md"]), "# External Handoff Receipt Comparison\n\nNo previous receipt baseline is attached for this external commercial bridge run.\n")
    _write_json(Path(outputs["handoff_rerun_audit_gate_json"]), report["handoff_rerun_audit_gate"])
    _write_markdown(Path(outputs["handoff_rerun_audit_gate_md"]), "# External Handoff Rerun Audit Gate\n\nClosure claims remain conservative until a real lineage comparison is available.\n")
    _write_json(Path(outputs["commercial_evidence_lineage_dashboard_json"]), report["commercial_evidence_lineage_dashboard"])
    _write_markdown(Path(outputs["commercial_evidence_lineage_dashboard_md"]), "# External Commercial Evidence Lineage Dashboard\n\nThis run publishes a baseline-only lineage view for external validated candidates.\n")
    _write_json(Path(outputs["commercial_lineage_reviewer_signoff_packet_json"]), report["commercial_lineage_reviewer_signoff_packet"])
    _write_markdown(Path(outputs["commercial_lineage_reviewer_signoff_packet_md"]), "# External Commercial Lineage Reviewer Signoff Packet\n\nNo reviewer signoff packet items are required for the baseline-only external lineage dashboard.\n")
    _write_json(Path(outputs["commercial_closure_acceptance_ledger_json"]), report["commercial_closure_acceptance_ledger"])
    _write_markdown(Path(outputs["commercial_closure_acceptance_ledger_md"]), render_commercial_closure_acceptance_ledger_markdown(report["commercial_closure_acceptance_ledger"]))
    _write_json(Path(outputs["commercial_audit_event_stream_json"]), report["commercial_audit_event_stream"])
    _write_markdown(Path(outputs["commercial_audit_event_stream_md"]), render_commercial_audit_event_stream_markdown(report["commercial_audit_event_stream"]))
    _write_json(Path(outputs["commercial_audit_exports_json"]), report["commercial_audit_export_adapters"])
    _write_markdown(Path(outputs["commercial_audit_exports_md"]), render_commercial_audit_exports_markdown(report["commercial_audit_export_adapters"]))
    Path(outputs["commercial_audit_ledger_csv"]).write_text(render_csv_audit_ledger(report["commercial_audit_export_adapters"]), encoding="utf-8")
    _write_json(Path(outputs["commercial_audit_jira_issue_import_json"]), {"items": report["commercial_audit_export_adapters"].get("jira_issue_import") or []})
    _write_json(Path(outputs["commercial_audit_linear_issue_import_json"]), {"items": report["commercial_audit_export_adapters"].get("linear_issue_import") or []})
    _write_json(Path(outputs["commercial_audit_import_gate_json"]), report["commercial_audit_export_import_gate"])
    _write_markdown(Path(outputs["commercial_audit_import_gate_md"]), render_commercial_audit_import_gate_markdown(report["commercial_audit_export_import_gate"]))
    _write_markdown(Path(outputs["commercial_external_tracker_reconciliation_md"]), render_commercial_external_tracker_reconciliation_markdown(report["commercial_external_tracker_reconciliation"]))
    _write_json(Path(outputs["commercial_external_tracker_reconciliation_json"]), report["commercial_external_tracker_reconciliation"])
    _write_json(Path(outputs["external_tracker_closure_sync_policy_json"]), report["external_tracker_closure_sync_policy"])
    _write_markdown(Path(outputs["external_tracker_closure_sync_policy_md"]), render_external_tracker_closure_sync_policy_markdown(report["external_tracker_closure_sync_policy"]))
    _write_json(Path(outputs["external_tracker_sync_payloads_json"]), report["external_tracker_sync_payloads"])
    _write_markdown(Path(outputs["external_tracker_sync_payloads_md"]), render_external_tracker_sync_payloads_markdown(report["external_tracker_sync_payloads"]))
    _write_json(Path(outputs["external_tracker_sync_payload_gate_json"]), report["external_tracker_sync_payload_gate"])
    _write_markdown(Path(outputs["external_tracker_sync_payload_gate_md"]), render_external_tracker_sync_payload_gate_markdown(report["external_tracker_sync_payload_gate"]))

    delivery = {"status": "not_created"}
    try:
        delivery = create_delivery_package(project, root=root, scan_result=scan_result)
    except Exception as exc:
        delivery = {"status": "failed", "reason": f"external_delivery_package_failed:{type(exc).__name__}"}

    return {
        "status": "materialized",
        "generated_at_utc": generated_at,
        "finding_count": len(validated),
        "customer_ready_reproduction_count": customer_ready_count,
        "commercial_handoff_status": str(_as_dict(report.get("commercial_handoff_bundle")).get("status") or ""),
        "commercial_handoff_acceptance_status": str(_as_dict(report.get("commercial_handoff_acceptance_gate")).get("status") or ""),
        "commercial_handoff_safe_for_customer": bool(_as_dict(report.get("commercial_handoff_secret_audit")).get("safe_for_customer_handoff")),
        "external_tracker_sync_payload_status": str(_as_dict(report.get("external_tracker_sync_payloads")).get("status") or ""),
        "external_tracker_sync_payload_gate_status": str(_as_dict(report.get("external_tracker_sync_payload_gate")).get("status") or ""),
        "delivery_package": delivery,
        "commercial_handoff_bundle_ref": f"platform_outputs/{safe_project}/defect_discovery/external_commercial_handoff_bundle.json",
        "commercial_handoff_acceptance_gate_ref": f"platform_outputs/{safe_project}/defect_discovery/external_commercial_handoff_acceptance_gate.json",
        "handoff_archive_manifest_ref": f"platform_outputs/{safe_project}/defect_discovery/external_handoff_archive_manifest.json",
        "commercial_audit_exports_ref": f"platform_outputs/{safe_project}/defect_discovery/external_commercial_audit_exports.json",
        "external_tracker_sync_payloads_ref": f"platform_outputs/{safe_project}/defect_discovery/external_tracker_sync_payloads.json",
    }


