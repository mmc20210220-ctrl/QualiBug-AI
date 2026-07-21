"""Customer reproduction pack and remediation plan."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from ._common import *  # noqa: F401,F403
from ._common import _redact, _safe_payload_summary  # explicit import for underscore-prefixed helpers
from ._evidence_scoreboard import _runtime_evidence_probe_binding_events, _runtime_evidence_target_statuses  # noqa: F401

def _shell_single_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _runtime_repro_curl_template(method: str, path: str, body: Any = None) -> str:
    """Return a secret-free curl template using BASE_URL instead of raw credentials."""
    method = str(method or "GET").upper()
    path = str(path or "/")
    base = f"curl -X {method} \"$BASE_URL{path}\""
    if body is not None and body != {}:
        payload = json.dumps(_redact(body), ensure_ascii=False, sort_keys=True)
        base += " -H \"Content-Type: application/json\" --data-raw " + _shell_single_quote(payload)
    return base


def _runtime_response_status(item: dict[str, Any]) -> Any:
    response = item.get("response") if isinstance(item.get("response"), dict) else item
    return response.get("status_code") if isinstance(response, dict) else None


def _runtime_response_summary(item: dict[str, Any]) -> dict[str, Any]:
    response = item.get("response") if isinstance(item.get("response"), dict) else item
    payload = response.get("payload") if isinstance(response, dict) else None
    return _safe_payload_summary(payload)


def _runtime_repro_steps_for_observation(obs: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a deterministic, customer-shareable reproduction trace for one observation."""
    steps: list[dict[str, Any]] = []
    seq = 1
    for receipt in obs.get("fixture_receipts") or []:
        if not isinstance(receipt, dict):
            continue
        method = str(receipt.get("method") or "POST").upper()
        path = str(receipt.get("path") or "")
        body_binding = receipt.get("body_runtime_binding") if isinstance(receipt.get("body_runtime_binding"), dict) else {}
        steps.append({
            "sequence": seq,
            "phase": "setup",
            "purpose": receipt.get("purpose") or "disposable_fixture_setup",
            "method": method,
            "path": path,
            "curl_template": _runtime_repro_curl_template(method, path),
            "status_code": _runtime_response_status(receipt),
            "accepted": bool(receipt.get("accepted")),
            "runtime_binding": _redact(receipt.get("runtime_binding") or {}),
            "body_runtime_binding": _redact(body_binding),
            "response_summary": _runtime_response_summary(receipt),
        })
        seq += 1

    snapshots = obs.get("snapshots") if isinstance(obs.get("snapshots"), dict) else {}
    for phase in ("before", "after"):
        for snap in (snapshots.get(phase) or []):
            if not isinstance(snap, dict):
                continue
            method = str(snap.get("method") or "GET").upper()
            path = str(snap.get("path") or "")
            response = snap.get("response") if isinstance(snap.get("response"), dict) else {}
            steps.append({
                "sequence": seq,
                "phase": f"snapshot_{phase}",
                "purpose": snap.get("observer_kind") or snap.get("evidence_goal") or f"{phase}_snapshot",
                "method": method,
                "path": path,
                "curl_template": _runtime_repro_curl_template(method, path),
                "status_code": response.get("status_code"),
                "accepted": isinstance(response.get("status_code"), int) and 200 <= int(response.get("status_code")) < 300,
                "response_summary": _safe_payload_summary(response.get("payload")),
            })
            seq += 1

    request = obs.get("request") if isinstance(obs.get("request"), dict) else {}
    if obs.get("response"):
        method = str(obs.get("method") or request.get("method") or "GET").upper()
        path = str(request.get("path") or obs.get("path") or "")
        body = request.get("body") if isinstance(request, dict) else None
        response = obs.get("response") if isinstance(obs.get("response"), dict) else {}
        steps.append({
            "sequence": seq,
            "phase": "target",
            "purpose": "main_probe_request",
            "method": method,
            "path": path,
            "curl_template": _runtime_repro_curl_template(method, path, body),
            "status_code": response.get("status_code"),
            "accepted": isinstance(response.get("status_code"), int) and 200 <= int(response.get("status_code")) < 300,
            "body_runtime_binding": _redact(request.get("body_runtime_binding") or {}),
            "response_summary": _safe_payload_summary(response.get("payload")),
        })
        seq += 1
    for response in obs.get("responses") or []:
        if not isinstance(response, dict):
            continue
        method = str(response.get("method") or obs.get("method") or "POST").upper()
        path = str(response.get("flow_path") or response.get("path") or request.get("path") or obs.get("path") or "")
        steps.append({
            "sequence": seq,
            "phase": "target_flow_step",
            "purpose": response.get("flow_action") or f"flow_step_{response.get('step') or response.get('attempt') or seq}",
            "step": response.get("step"),
            "attempt": response.get("attempt"),
            "method": method,
            "path": path,
            "curl_template": _runtime_repro_curl_template(method, path),
            "status_code": response.get("status_code"),
            "accepted": isinstance(response.get("status_code"), int) and 200 <= int(response.get("status_code")) < 300,
            "runtime_binding": _redact(response.get("runtime_binding") or {}),
            "body_runtime_binding": _redact(response.get("request_body_runtime_binding") or {}),
            "response_summary": _safe_payload_summary(response.get("payload")),
        })
        seq += 1

    for receipt in obs.get("cleanup_receipts") or []:
        if not isinstance(receipt, dict):
            continue
        method = str(receipt.get("method") or "DELETE").upper()
        path = str(receipt.get("path") or "")
        steps.append({
            "sequence": seq,
            "phase": "cleanup",
            "purpose": receipt.get("purpose") or "disposable_fixture_cleanup",
            "method": method,
            "path": path,
            "curl_template": _runtime_repro_curl_template(method, path),
            "status_code": _runtime_response_status(receipt),
            "accepted": bool(receipt.get("accepted")),
            "runtime_binding": _redact(receipt.get("runtime_binding") or {}),
            "body_runtime_binding": _redact(receipt.get("body_runtime_binding") or {}),
            "response_summary": _runtime_response_summary(receipt),
        })
        seq += 1
    return steps



def _runtime_reproduction_readiness_gate(
    ledger_entry: dict[str, Any],
    verification: dict[str, Any],
    steps: list[dict[str, Any]],
    binding_events: list[dict[str, Any]],
    obs: dict[str, Any] | None,
) -> dict[str, Any]:
    """Decide whether a packaged finding is safe to call customer-reproducible.

    A validated runtime verdict alone is not enough for a customer handoff.  The
    package also needs an actual redacted reproduction trace and no known
    per-probe execution gaps from the ledger.  This gate prevents over-claiming
    customer readiness when the finding exists but its setup/binding/snapshot or
    cleanup evidence is incomplete.
    """
    blockers: list[str] = []
    gap_types = [str(g) for g in (ledger_entry.get("gap_types") or []) if g]
    verdict = str(verification.get("verdict") or "")
    target_steps = [
        step for step in steps
        if isinstance(step, dict) and step.get("phase") in {"target", "target_flow_step"}
    ]
    target_http_statuses = [
        int(step.get("status_code")) for step in target_steps
        if isinstance(step.get("status_code"), int)
    ]
    failed_support_steps = [
        str(step.get("phase") or "unknown")
        for step in steps
        if isinstance(step, dict)
        and step.get("phase") in {"setup", "snapshot_before", "snapshot_after", "cleanup"}
        and step.get("accepted") is False
    ]
    unbound_events = [
        event for event in binding_events
        if isinstance(event, dict) and event.get("bound") is not True
    ]

    if not isinstance(obs, dict) or not obs:
        blockers.append("missing_runtime_observation")
    if verdict != "validated_candidate":
        blockers.append("runtime_verdict_not_validated")
    if not steps:
        blockers.append("missing_reproduction_trace")
    if not target_steps:
        blockers.append("missing_target_reproduction_step")
    if not target_http_statuses:
        blockers.append("missing_target_http_status")
    if gap_types:
        blockers.append("probe_ledger_has_evidence_gaps")
    if unbound_events:
        blockers.append("runtime_binding_not_fully_bound")
    if failed_support_steps:
        blockers.append("supporting_setup_snapshot_or_cleanup_failed")

    blockers = sorted(dict.fromkeys(blockers))
    customer_ready = not blockers
    if customer_ready:
        level = "customer_ready_reproduction"
    elif verdict == "validated_candidate":
        level = "validated_but_reproduction_gap"
    else:
        level = "not_validated_runtime_finding"

    return {
        "engine": "runtime_customer_reproduction_readiness_gate_v1_phase95",
        "customer_ready": customer_ready,
        "level": level,
        "blockers": blockers,
        "blocker_count": len(blockers),
        "checks": {
            "validated_runtime_verdict": verdict == "validated_candidate",
            "has_runtime_observation": isinstance(obs, dict) and bool(obs),
            "has_reproduction_trace": bool(steps),
            "has_target_reproduction_step": bool(target_steps),
            "target_http_statuses": target_http_statuses,
            "ledger_gap_types": gap_types,
            "runtime_binding_event_count": len(binding_events),
            "runtime_binding_unbound_count": len(unbound_events),
            "failed_support_step_phases": failed_support_steps,
        },
    }

def _build_runtime_customer_reproduction_pack(report: dict[str, Any]) -> dict[str, Any]:
    """Package customer-ready findings with exact, redacted runtime reproduction traces."""
    observations = [o for o in (report.get("observations") or []) if isinstance(o, dict)]
    write_observations = [o for o in (report.get("write_observations") or []) if isinstance(o, dict)]
    obs_by_id = {str(o.get("candidate_id") or ""): o for o in observations + write_observations if o.get("candidate_id")}
    ledger_entries = {}
    ledger = report.get("runtime_evidence_probe_ledger") if isinstance(report.get("runtime_evidence_probe_ledger"), dict) else {}
    for entry in ledger.get("entries") or []:
        if isinstance(entry, dict) and entry.get("candidate_id"):
            ledger_entries[str(entry.get("candidate_id"))] = entry

    findings = [f for f in (report.get("findings") or []) if isinstance(f, dict)]
    packages: list[dict[str, Any]] = []
    for finding in findings:
        cid = str(finding.get("candidate_id") or "")
        obs = obs_by_id.get(cid) or {}
        ledger_entry = ledger_entries.get(cid) or {}
        verification = obs.get("verification") if isinstance(obs.get("verification"), dict) else {}
        steps = _runtime_repro_steps_for_observation(obs) if obs else []
        binding_events = _runtime_evidence_probe_binding_events(obs) if obs else []
        readiness_gate = _runtime_reproduction_readiness_gate(ledger_entry, verification, steps, binding_events, obs)
        packages.append({
            "finding_id": finding.get("finding_id"),
            "candidate_id": cid,
            "title": finding.get("title"),
            "risk_type": finding.get("risk_type"),
            "method": finding.get("method"),
            "path": finding.get("path"),
            "confidence": finding.get("confidence"),
            "evidence_grade": finding.get("evidence_grade"),
            "evidence_strength_score": finding.get("evidence_strength_score"),
            "customer_ready": bool(readiness_gate.get("customer_ready")),
            "readiness_level": readiness_gate.get("level") or ledger_entry.get("readiness_level") or "validated_candidate_without_probe_ledger",
            "reproduction_readiness_gate": readiness_gate,
            "reason": finding.get("reason") or verification.get("reason"),
            "runtime_evidence": {
                "target_http_statuses": _runtime_evidence_target_statuses(obs) if obs else [],
                "runtime_binding_event_count": len(binding_events),
                "runtime_binding_bound_count": sum(1 for event in binding_events if event.get("bound") is True),
                "fixture_setup": ledger_entry.get("fixture_setup") or {},
                "snapshots": ledger_entry.get("snapshots") or {},
                "cleanup": ledger_entry.get("cleanup") or {},
                "gap_types": ledger_entry.get("gap_types") or [],
            },
            "reproduction_trace": steps,
            "violated_invariants": _redact(finding.get("violated_invariants") or []),
            "delta_summary": _redact(finding.get("delta_summary") or {}),
            "source_refs": _redact(finding.get("source_refs") or []),
            "customer_triage": _redact(finding.get("customer_triage") or {}),
        })

    carry_forward = report.get("runtime_evidence_carry_forward") if isinstance(report.get("runtime_evidence_carry_forward"), dict) else {}
    current_package_ids = {str(item.get("candidate_id") or "") for item in packages if item.get("candidate_id")}
    carried_forward_count = 0
    for carried_package in carry_forward.get("packages") or []:
        if not isinstance(carried_package, dict):
            continue
        cid = str(carried_package.get("candidate_id") or "")
        if not cid or cid in current_package_ids:
            continue
        packages.append(carried_package)
        current_package_ids.add(cid)
        carried_forward_count += 1

    customer_ready_count = sum(1 for item in packages if item.get("customer_ready") is True)
    blocker_counts: dict[str, int] = {}
    for item in packages:
        gate = item.get("reproduction_readiness_gate") if isinstance(item.get("reproduction_readiness_gate"), dict) else {}
        for blocker in gate.get("blockers") or []:
            blocker_counts[str(blocker)] = blocker_counts.get(str(blocker), 0) + 1
    return {
        "engine": "runtime_customer_reproduction_pack_v1_phase95",
        "created_at": report.get("created_at"),
        "project_id": report.get("project_id"),
        "finding_count": len(packages),
        "customer_ready_reproduction_count": customer_ready_count,
        "carried_forward_reproduction_count": carried_forward_count,
        "blocked_reproduction_count": len(packages) - customer_ready_count,
        "status": "ready" if customer_ready_count else ("blocked_reproduction_evidence_gap" if packages else "empty_no_validated_runtime_findings"),
        "reproduction_readiness_blocker_counts": dict(sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))),
        "redaction_policy": "uses BASE_URL templates and redacts secret-bearing fields; no raw Authorization/Cookie values are emitted",
        "packages": packages,
    }




def _runtime_remediation_priority(gap_type: str) -> str:
    gap = str(gap_type or "")
    p0 = {
        "blocked_decision",
        "missing_runtime_observation",
        "missing_target_http_response",
        "fixture_setup_not_fully_accepted",
        "runtime_binding_not_fully_bound",
        "snapshot_not_fully_accepted",
        "missing_runtime_observation",
        "missing_reproduction_trace",
        "missing_target_reproduction_step",
        "target_http_status_missing",
        "validated_runtime_verdict_missing",
        "probe_ledger_has_evidence_gaps",
    }
    p1 = {
        "needs_more_evidence",
        "inconclusive_runtime_oracle",
        "cleanup_not_fully_accepted",
        "support_step_not_fully_accepted",
        "runtime_binding_not_fully_bound",
    }
    if gap.startswith("blocked:"):
        return "P0"
    if gap in p0:
        return "P0"
    if gap in p1:
        return "P1"
    return "P2"


def _runtime_remediation_instruction(gap_type: str) -> str:
    gap = str(gap_type or "")
    instructions = {
        "blocked_decision": "Fix the decision blocker first, then rerun the candidate with the same runtime configuration.",
        "missing_runtime_observation": "Enable the required readonly/write execution mode or repair scheduling so this probe produces a runtime observation.",
        "missing_target_http_response": "Stabilize URL rendering, auth headers, tenant headers, base URL, and timeout settings until the target request returns an HTTP response.",
        "fixture_setup_not_fully_accepted": "Repair disposable fixture setup endpoint mapping and generated request data before trusting downstream evidence.",
        "runtime_binding_not_fully_bound": "Bind observed runtime IDs into path, query, target body, flow body, snapshots, and cleanup until every binding event is marked bound.",
        "snapshot_not_fully_accepted": "Repair before/after observer requests so the runtime oracle can compare accepted business-state snapshots.",
        "cleanup_not_fully_accepted": "Fix cleanup path/body binding or cleanup ordering so sandbox data is reliably removed after reruns.",
        "needs_more_evidence": "Add stronger oracle evidence such as fixture anchors, control actor baseline reads, or richer observer deltas.",
        "inconclusive_runtime_oracle": "Strengthen invariant classification so runtime responses resolve to validated, protected, or falsified outcomes.",
        "probe_ledger_has_evidence_gaps": "Do not hand this finding to customers yet; clear the probe ledger gaps and regenerate the reproduction pack.",
        "missing_reproduction_trace": "Capture setup, target, snapshot, and cleanup steps before calling the finding reproducible.",
        "missing_target_reproduction_step": "Ensure the reproduction trace includes the target request or target flow step that triggered the finding.",
        "target_http_status_missing": "Regenerate the reproduction trace only after the target step records a concrete HTTP status.",
        "support_step_not_fully_accepted": "Fix setup, snapshot, or cleanup support steps so the reproduction package is deterministic.",
        "validated_runtime_verdict_missing": "Only package findings whose latest runtime observation is explicitly validated_candidate.",
    }
    if gap.startswith("blocked:"):
        return f"Resolve decision blocker `{gap.split(':', 1)[1]}` and rerun this candidate."
    return instructions.get(gap, "Inspect this evidence gap in the probe ledger and add a targeted repair before the next customer-ready run.")


def _build_runtime_evidence_remediation_plan(report: dict[str, Any]) -> dict[str, Any]:
    """Build a concrete remediation/rerun queue from scoreboard, ledger, and reproduction gates.

    Scoreboards identify global weak points and the probe ledger names the exact
    candidates.  This plan converts both into an ordered action queue so a future
    run can focus on the smallest set of probes blocking customer-ready evidence.
    """
    ledger = report.get("runtime_evidence_probe_ledger") if isinstance(report.get("runtime_evidence_probe_ledger"), dict) else {}
    repro_pack = report.get("runtime_customer_reproduction_pack") if isinstance(report.get("runtime_customer_reproduction_pack"), dict) else {}
    scoreboard = report.get("runtime_evidence_scoreboard") if isinstance(report.get("runtime_evidence_scoreboard"), dict) else {}
    entries = [entry for entry in (ledger.get("entries") or []) if isinstance(entry, dict)]
    packages = [item for item in (repro_pack.get("packages") or []) if isinstance(item, dict)]

    groups: dict[str, dict[str, Any]] = {}

    def ensure_group(gap_type: str) -> dict[str, Any]:
        gap = str(gap_type or "unknown_gap")
        if gap not in groups:
            groups[gap] = {
                "priority": _runtime_remediation_priority(gap),
                "gap_type": gap,
                "candidate_ids": [],
                "finding_ids": [],
                "readiness_levels": {},
                "verdicts": {},
                "source_counts": {},
                "recommended_fix": _runtime_remediation_instruction(gap),
            }
        return groups[gap]

    def add_unique(items: list[Any], value: Any) -> None:
        if value is None or value == "":
            return
        if value not in items:
            items.append(value)

    for entry in entries:
        cid = str(entry.get("candidate_id") or "")
        gaps = [str(gap) for gap in (entry.get("gap_types") or []) if str(gap)]
        if not gaps and entry.get("customer_ready") is not True and entry.get("readiness_level") not in {"protected_or_falsified", "customer_ready_candidate"}:
            gaps = ["executed_unclassified"]
        for gap in gaps:
            group = ensure_group(gap)
            add_unique(group["candidate_ids"], cid)
            readiness = str(entry.get("readiness_level") or "unknown")
            verdict = str(entry.get("verdict") or "unknown")
            group["readiness_levels"][readiness] = group["readiness_levels"].get(readiness, 0) + 1
            group["verdicts"][verdict] = group["verdicts"].get(verdict, 0) + 1
            group["source_counts"]["probe_ledger"] = group["source_counts"].get("probe_ledger", 0) + 1

    for item in packages:
        cid = str(item.get("candidate_id") or "")
        finding_id = str(item.get("finding_id") or "")
        gate = item.get("reproduction_readiness_gate") if isinstance(item.get("reproduction_readiness_gate"), dict) else {}
        blockers = [str(blocker) for blocker in (gate.get("blockers") or []) if str(blocker)]
        if item.get("customer_ready") is True:
            continue
        if not blockers and item.get("readiness_level") not in {"customer_ready_candidate", "protected_or_falsified"}:
            blockers = [str(item.get("readiness_level") or "blocked_reproduction_evidence_gap")]
        for blocker in blockers:
            group = ensure_group(blocker)
            add_unique(group["candidate_ids"], cid)
            add_unique(group["finding_ids"], finding_id)
            readiness = str(item.get("readiness_level") or "unknown")
            group["readiness_levels"][readiness] = group["readiness_levels"].get(readiness, 0) + 1
            group["source_counts"]["reproduction_readiness_gate"] = group["source_counts"].get("reproduction_readiness_gate", 0) + 1

    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    priority_groups = sorted(
        groups.values(),
        key=lambda item: (priority_order.get(str(item.get("priority")), 9), -len(item.get("candidate_ids") or []), str(item.get("gap_type") or "")),
    )
    for group in priority_groups:
        group["candidate_count"] = len(group.get("candidate_ids") or [])
        group["finding_count"] = len(group.get("finding_ids") or [])
        group["readiness_levels"] = dict(sorted(group.get("readiness_levels", {}).items()))
        group["verdicts"] = dict(sorted(group.get("verdicts", {}).items()))
        group["source_counts"] = dict(sorted(group.get("source_counts", {}).items()))
        group["rerun_scope"] = {
            "candidate_ids": group.get("candidate_ids") or [],
            "after_fix": group.get("recommended_fix"),
            "regenerate_outputs": [
                "grounded_probe_runtime_evidence_scoreboard.json",
                "grounded_probe_runtime_evidence_probe_ledger.json",
                "grounded_probe_runtime_customer_reproduction_pack.json",
            ],
        }

    queued_candidates: list[str] = []
    for group in priority_groups:
        for cid in group.get("candidate_ids") or []:
            add_unique(queued_candidates, cid)
    ready_candidates = [str(entry.get("candidate_id")) for entry in entries if entry.get("customer_ready") is True and entry.get("candidate_id")]

    p0_count = sum(1 for group in priority_groups if group.get("priority") == "P0")
    p1_count = sum(1 for group in priority_groups if group.get("priority") == "P1")
    if queued_candidates:
        status = "runtime_remediation_required" if p0_count else "runtime_hardening_recommended"
    elif entries or packages:
        status = "customer_ready_no_runtime_remediation_needed"
    else:
        status = "empty_no_runtime_evidence"

    return {
        "engine": "runtime_evidence_remediation_plan_v1_phase95",
        "created_at": report.get("created_at"),
        "project_id": report.get("project_id"),
        "status": status,
        "scoreboard_maturity_level": ((scoreboard.get("evidence_maturity") or {}).get("level") if isinstance(scoreboard.get("evidence_maturity"), dict) else None),
        "source_counts": {
            "probe_ledger_entries": len(entries),
            "reproduction_packages": len(packages),
            "scoreboard_recommended_actions": len(scoreboard.get("recommended_next_actions") or []),
        },
        "p0_group_count": p0_count,
        "p1_group_count": p1_count,
        "remediation_group_count": len(priority_groups),
        "queued_candidate_count": len(queued_candidates),
        "customer_ready_candidate_count": len(ready_candidates),
        "priority_groups": priority_groups,
        "rerun_manifest": {
            "selection_policy": "fix P0 groups first, rerun only queued candidate_ids, then regenerate scoreboard, probe ledger, and reproduction pack",
            "candidate_ids": queued_candidates,
            "customer_ready_candidate_ids_excluded": ready_candidates,
            "max_candidates": len(queued_candidates),
            "requires_full_rerun_when": [
                "auth account matrix changed",
                "OpenAPI endpoint mapping changed",
                "fixture data factory changed",
                "runtime oracle semantics changed",
            ],
        },
    }


def _render_runtime_evidence_remediation_plan_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Remediation Plan",
        "",
        f"- engine: `{plan.get('engine')}`",
        f"- project: `{plan.get('project_id')}`",
        f"- status: `{plan.get('status')}`",
        f"- scoreboard maturity: `{plan.get('scoreboard_maturity_level')}`",
        f"- remediation groups: {plan.get('remediation_group_count')}",
        f"- P0 groups: {plan.get('p0_group_count')}",
        f"- queued candidates: {plan.get('queued_candidate_count')}",
        "",
    ]
    manifest = plan.get("rerun_manifest") if isinstance(plan.get("rerun_manifest"), dict) else {}
    if manifest.get("candidate_ids"):
        lines.extend([
            "## Rerun manifest",
            "",
            f"- selection policy: {manifest.get('selection_policy')}",
            f"- candidate ids: `{json.dumps(manifest.get('candidate_ids') or [], ensure_ascii=False)}`",
            "",
        ])
    groups = [group for group in (plan.get("priority_groups") or []) if isinstance(group, dict)]
    if groups:
        lines.extend([
            "## Remediation groups",
            "",
            "| Priority | Gap | Candidates | Findings | Recommended fix |",
            "|---|---|---:|---:|---|",
        ])
        for group in groups[:50]:
            lines.append(
                "| "
                + " | ".join([
                    str(group.get("priority") or "-"),
                    str(group.get("gap_type") or "-").replace("|", "\\|"),
                    str(group.get("candidate_count") or 0),
                    str(group.get("finding_count") or 0),
                    str(group.get("recommended_fix") or "-").replace("|", "\\|"),
                ])
                + " |"
            )
        lines.append("")
        for group in groups[:20]:
            lines.extend([
                f"### {group.get('priority')} — {group.get('gap_type')}",
                "",
                f"- candidate ids: `{json.dumps(group.get('candidate_ids') or [], ensure_ascii=False)}`",
                f"- finding ids: `{json.dumps(group.get('finding_ids') or [], ensure_ascii=False)}`",
                f"- sources: `{json.dumps(group.get('source_counts') or {}, ensure_ascii=False)}`",
                f"- fix: {group.get('recommended_fix')}",
                "",
            ])
        if len(groups) > 50:
            lines.append(f"_Only the first 50 remediation groups are shown; see JSON for all {len(groups)} groups._")
            lines.append("")
    else:
        lines.append("No runtime evidence remediation groups were produced.")
        lines.append("")
    return "\n".join(lines)
def _render_runtime_customer_reproduction_pack_markdown(pack: dict[str, Any]) -> str:
    lines = [
        "# Runtime Customer Reproduction Pack",
        "",
        f"- engine: `{pack.get('engine')}`",
        f"- project: `{pack.get('project_id')}`",
        f"- status: `{pack.get('status')}`",
        f"- findings packaged: {pack.get('finding_count')}",
        f"- customer-ready reproductions: {pack.get('customer_ready_reproduction_count')}",
        f"- blocked reproductions: {pack.get('blocked_reproduction_count', 0)}",
        f"- readiness blockers: `{json.dumps(pack.get('reproduction_readiness_blocker_counts') or {}, ensure_ascii=False)}`",
        f"- redaction policy: {pack.get('redaction_policy')}",
        "",
    ]
    packages = [p for p in (pack.get("packages") or []) if isinstance(p, dict)]
    if not packages:
        lines.append("No validated runtime findings were available for customer reproduction packaging.")
        lines.append("")
        return "\n".join(lines)
    for item in packages[:50]:
        lines.extend([
            f"## {item.get('finding_id')} — {item.get('title')}",
            "",
            f"- candidate: `{item.get('candidate_id')}`",
            f"- endpoint: `{item.get('method')} {item.get('path')}`",
            f"- readiness: `{item.get('readiness_level')}` / customer-ready `{item.get('customer_ready')}`",
            f"- readiness blockers: `{json.dumps(((item.get('reproduction_readiness_gate') or {}).get('blockers') or []), ensure_ascii=False)}`",
            f"- evidence: grade `{item.get('evidence_grade')}`, score `{item.get('evidence_strength_score')}`, confidence `{item.get('confidence')}`",
            f"- reason: {item.get('reason')}",
            "",
            "### Reproduction trace",
            "",
            "| # | Phase | Method | Path | HTTP | Accepted | Command template |",
            "|---:|---|---|---|---:|---|---|",
        ])
        for step in item.get("reproduction_trace") or []:
            if not isinstance(step, dict):
                continue
            lines.append(
                "| "
                + " | ".join([
                    str(step.get("sequence") or "-"),
                    str(step.get("phase") or "-"),
                    str(step.get("method") or "-"),
                    str(step.get("path") or "-").replace("|", "\\|"),
                    str(step.get("status_code") if step.get("status_code") is not None else "-"),
                    str(step.get("accepted")),
                    f"`{str(step.get('curl_template') or '-').replace('`', '')}`",
                ])
                + " |"
            )
        lines.append("")
    if len(packages) > 50:
        lines.append(f"_Only the first 50 packages are shown; see JSON for all {len(packages)} findings._")
        lines.append("")
    return "\n".join(lines)

def _render_runtime_evidence_scoreboard_markdown(scoreboard: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Scoreboard",
        "",
        f"- engine: `{scoreboard.get('engine')}`",
        f"- project: `{scoreboard.get('project_id')}`",
        f"- execution integrity score: `{scoreboard.get('execution_integrity_score')}`",
        f"- evidence maturity: `{((scoreboard.get('evidence_maturity') or {}).get('level'))}` / customer-ready `{((scoreboard.get('evidence_maturity') or {}).get('customer_ready'))}`",
        f"- maturity reason: {((scoreboard.get('evidence_maturity') or {}).get('reason'))}",
        "",
        "## Execution coverage",
        "",
        f"- probes total: {scoreboard.get('probe_count')}",
        f"- probes executed: {scoreboard.get('executed_probe_count')} ({scoreboard.get('execution_coverage_rate')}%)",
        f"- target HTTP responses: {scoreboard.get('target_http_response_count')} ({scoreboard.get('target_response_rate')}%)",
        f"- decisions: `{json.dumps(scoreboard.get('decision_counts') or {}, ensure_ascii=False)}`",
        f"- verdicts: `{json.dumps(scoreboard.get('verdict_counts') or {}, ensure_ascii=False)}`",
        "",
        "## Runtime evidence health",
        "",
        f"- fixture setup accepted/executed: {scoreboard.get('fixture_setup_accepted_count')}/{scoreboard.get('fixture_setup_executed_count')} ({scoreboard.get('fixture_setup_success_rate')}%)",
        f"- runtime id/body binding success: {scoreboard.get('runtime_binding_success_count')}/{scoreboard.get('runtime_binding_event_count')} ({scoreboard.get('runtime_binding_success_rate')}%)",
        f"- snapshots accepted/total: {scoreboard.get('snapshot_accepted_count')}/{scoreboard.get('snapshot_request_count')} ({scoreboard.get('snapshot_success_rate')}%)",
        f"- cleanup accepted/executed: {scoreboard.get('cleanup_accepted_count')}/{scoreboard.get('cleanup_executed_count')} ({scoreboard.get('cleanup_success_rate')}%)",
        f"- query-bound target or flow requests: {scoreboard.get('query_bound_request_count')}",
        f"- binding sources: `{json.dumps(scoreboard.get('runtime_binding_sources') or {}, ensure_ascii=False)}`",
        "",
        "## Findings",
        "",
        f"- validated candidates: {scoreboard.get('validated_candidate_count')}",
        f"- protected/falsified: {scoreboard.get('protected_or_falsified_count')}",
        f"- runtime oracle resolved: {scoreboard.get('oracle_resolved_count')} ({scoreboard.get('oracle_resolution_rate')}%)",
        f"- needs more evidence: {scoreboard.get('needs_more_evidence_count')}",
        f"- inconclusive: {scoreboard.get('inconclusive_count')}",
        f"- customer-ready finding count: {scoreboard.get('finding_count')}",
        "",
    ]
    maturity = scoreboard.get("evidence_maturity") if isinstance(scoreboard.get("evidence_maturity"), dict) else {}
    gates = maturity.get("gates") if isinstance(maturity.get("gates"), dict) else {}
    if gates:
        lines.extend(["## Evidence maturity gates", ""])
        for name, passed in gates.items():
            marker = "pass" if passed else "needs work"
            lines.append(f"- {name}: `{marker}`")
        lines.append("")
    actions = scoreboard.get("recommended_next_actions") or []
    if actions:
        lines.extend(["## Recommended next actions", ""])
        for item in actions:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {item.get('priority')} / {item.get('gap_type')}: "
                f"{item.get('metric')}={item.get('observed')} "
                f"(target {item.get('threshold')}) — {item.get('action')}"
            )
        lines.append("")
    gaps = scoreboard.get("top_failure_or_gap_reasons") or {}
    if gaps:
        lines.extend(["## Top failure or evidence-gap reasons", ""])
        for reason, count in gaps.items():
            lines.append(f"- {reason}: {count}")
        lines.append("")
    return "\n".join(lines)


