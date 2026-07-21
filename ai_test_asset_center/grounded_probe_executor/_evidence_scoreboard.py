"""Runtime evidence scoreboard, probe ledger, gap recommendations."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from ._common import *  # noqa: F401,F403

def _runtime_evidence_gap_recommendations(scoreboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert factual scoreboard counters into deterministic next actions.

    The scoreboard itself is a ledger.  This helper does not invent runtime
    evidence; it only turns observed rates/counts into an ordered remediation
    plan so operators know which runtime gap blocks stronger bug proof.
    """
    actions: list[dict[str, Any]] = []

    def add(priority: str, gap_type: str, metric: str, observed: Any, threshold: Any, action: str) -> None:
        actions.append({
            "priority": priority,
            "gap_type": gap_type,
            "metric": metric,
            "observed": observed,
            "threshold": threshold,
            "action": action,
        })

    probe_count = int(scoreboard.get("probe_count") or 0)
    executed = int(scoreboard.get("executed_probe_count") or 0)
    execution_rate = float(scoreboard.get("execution_coverage_rate") or _safe_rate(executed, probe_count))
    response_rate = float(scoreboard.get("target_response_rate") or 0.0)
    fixture_rate = float(scoreboard.get("fixture_setup_success_rate") or 0.0)
    binding_rate = float(scoreboard.get("runtime_binding_success_rate") or 0.0)
    snapshot_count = int(scoreboard.get("snapshot_request_count") or 0)
    snapshot_rate = float(scoreboard.get("snapshot_success_rate") or 0.0)
    cleanup_executed = int(scoreboard.get("cleanup_executed_count") or 0)
    cleanup_rate = float(scoreboard.get("cleanup_success_rate") or 0.0)
    oracle_rate = float(scoreboard.get("oracle_resolution_rate") or 0.0)
    needs_more = int(scoreboard.get("needs_more_evidence_count") or 0)
    inconclusive = int(scoreboard.get("inconclusive_count") or 0)
    finding_count = int(scoreboard.get("finding_count") or 0)
    validated = int(scoreboard.get("validated_candidate_count") or 0)

    if probe_count and execution_rate < 70.0:
        add(
            "P0",
            "low_execution_coverage",
            "execution_coverage_rate",
            execution_rate,
            ">=70.0",
            "Resolve blocked probe decisions first: missing path params, unsafe write policy, base URL/auth config, or read_only_safe flags.",
        )
    if executed and response_rate < 90.0:
        add(
            "P0",
            "low_target_response_rate",
            "target_response_rate",
            response_rate,
            ">=90.0",
            "Stabilize sandbox reachability and endpoint rendering so executed probes produce HTTP evidence instead of transport/runtime gaps.",
        )
    if int(scoreboard.get("fixture_setup_executed_count") or 0) and fixture_rate < 85.0:
        add(
            "P0",
            "fixture_setup_instability",
            "fixture_setup_success_rate",
            fixture_rate,
            ">=85.0",
            "Fix disposable fixture setup plans, required request bodies, credential profile, and parent-resource ordering before trusting write/auth findings.",
        )
    if int(scoreboard.get("runtime_binding_event_count") or 0) and binding_rate < 95.0:
        add(
            "P0",
            "runtime_binding_instability",
            "runtime_binding_success_rate",
            binding_rate,
            ">=95.0",
            "Improve route-aware response ID extraction and bind observed IDs into path, query, target body, flow body, snapshots, and cleanup.",
        )
    if executed and snapshot_count == 0:
        add(
            "P0",
            "missing_before_after_snapshots",
            "snapshot_request_count",
            snapshot_count,
            ">0",
            "Configure or auto-plan before/after resource observers so accepted writes can be proven by business-state deltas.",
        )
    elif snapshot_count and snapshot_rate < 80.0:
        add(
            "P0",
            "snapshot_observer_instability",
            "snapshot_success_rate",
            snapshot_rate,
            ">=80.0",
            "Repair snapshot observer paths, query binding, and auth headers; weak snapshots turn accepted writes into needs_more_evidence.",
        )
    if cleanup_executed and cleanup_rate < 90.0:
        add(
            "P1",
            "cleanup_instability",
            "cleanup_success_rate",
            cleanup_rate,
            ">=90.0",
            "Fix cleanup ordering and runtime ID binding so disposable sandbox evidence does not leave residue.",
        )
    if executed and oracle_rate < 65.0:
        add(
            "P1",
            "weak_runtime_oracle_resolution",
            "oracle_resolution_rate",
            oracle_rate,
            ">=65.0",
            "Add stronger before/after invariants, fixture evidence anchors, and response semantic joins to reduce inconclusive/needs_more_evidence outcomes.",
        )
    if needs_more > 0:
        add(
            "P1",
            "needs_more_evidence_backlog",
            "needs_more_evidence_count",
            needs_more,
            "0 preferred",
            "Promote needs_more_evidence items with missing observers, control-actor baselines, or fixture ID anchors before reporting as customer-ready.",
        )
    if inconclusive > 0:
        add(
            "P2",
            "inconclusive_runtime_backlog",
            "inconclusive_count",
            inconclusive,
            "0 preferred",
            "Classify network/config failures separately from true protected behavior so the next run focuses on actionable runtime gaps.",
        )
    if validated > finding_count:
        add(
            "P1",
            "validated_finding_packaging_gap",
            "validated_candidate_count_minus_finding_count",
            validated - finding_count,
            "0",
            "Package every validated candidate into customer-ready reproduction evidence or explicitly mark why it is held back.",
        )

    order = {"P0": 0, "P1": 1, "P2": 2}
    return sorted(actions, key=lambda item: (order.get(str(item.get("priority")), 9), str(item.get("gap_type") or "")))[:12]


def _runtime_evidence_maturity(scoreboard: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic gate over factual runtime scoreboard metrics."""
    execution_rate = float(scoreboard.get("execution_coverage_rate") or 0.0)
    response_rate = float(scoreboard.get("target_response_rate") or 0.0)
    fixture_rate = float(scoreboard.get("fixture_setup_success_rate") or 0.0)
    binding_rate = float(scoreboard.get("runtime_binding_success_rate") or 0.0)
    snapshot_count = int(scoreboard.get("snapshot_request_count") or 0)
    snapshot_rate = float(scoreboard.get("snapshot_success_rate") or 0.0)
    cleanup_executed = int(scoreboard.get("cleanup_executed_count") or 0)
    cleanup_rate = float(scoreboard.get("cleanup_success_rate") or 0.0)
    oracle_rate = float(scoreboard.get("oracle_resolution_rate") or 0.0)
    integrity = float(scoreboard.get("execution_integrity_score") or 0.0)
    p0_gaps = [a for a in (scoreboard.get("recommended_next_actions") or []) if isinstance(a, dict) and a.get("priority") == "P0"]

    gates = {
        "execution_coverage_gate": execution_rate >= 70.0,
        "target_response_gate": response_rate >= 90.0 or int(scoreboard.get("executed_probe_count") or 0) == 0,
        "fixture_setup_gate": fixture_rate >= 85.0 or int(scoreboard.get("fixture_setup_executed_count") or 0) == 0,
        "runtime_binding_gate": binding_rate >= 95.0 or int(scoreboard.get("runtime_binding_event_count") or 0) == 0,
        "snapshot_gate": snapshot_count > 0 and snapshot_rate >= 80.0,
        "cleanup_gate": cleanup_rate >= 90.0 or cleanup_executed == 0,
        "oracle_resolution_gate": oracle_rate >= 65.0 or int(scoreboard.get("executed_probe_count") or 0) == 0,
        "integrity_gate": integrity >= 75.0,
    }
    if not scoreboard.get("executed_probe_count"):
        level = "not_executed"
        customer_ready = False
        reason = "no probes executed against the runtime target"
    elif p0_gaps:
        level = "runtime_evidence_blocked"
        customer_ready = False
        reason = f"{len(p0_gaps)} P0 runtime evidence gap(s) must be resolved first"
    elif all(gates.values()) and integrity >= 85.0:
        level = "customer_ready_runtime_evidence"
        customer_ready = True
        reason = "runtime coverage, binding, snapshots, cleanup, and oracle resolution passed customer-ready gates"
    elif integrity >= 65.0:
        level = "runtime_evidence_needs_hardening"
        customer_ready = False
        reason = "runtime run produced useful evidence but still needs hardening before customer-ready claims"
    else:
        level = "runtime_evidence_early_stage"
        customer_ready = False
        reason = "runtime execution is present but evidence integrity remains below the hardening threshold"

    return {
        "level": level,
        "customer_ready": customer_ready,
        "reason": reason,
        "gates": gates,
        "p0_gap_count": len(p0_gaps),
    }


def _build_runtime_evidence_scoreboard(report: dict[str, Any]) -> dict[str, Any]:
    """Build a factual run ledger from the actual runtime report.

    This deliberately avoids extrapolation: every count is derived from observed
    decisions, HTTP responses, fixture receipts, snapshots and findings already
    present in the execution report.  It gives customers a concrete answer to
    "how much of this run really executed against runtime evidence?"
    """
    decisions = [d for d in (report.get("decisions") or []) if isinstance(d, dict)]
    observations = [o for o in (report.get("observations") or []) if isinstance(o, dict)]
    write_observations = [o for o in (report.get("write_observations") or []) if isinstance(o, dict)]
    all_obs = observations + write_observations
    findings = [f for f in (report.get("findings") or []) if isinstance(f, dict)]

    decision_counts: dict[str, int] = {}
    for decision in decisions:
        key = str(decision.get("decision") or "unknown")
        decision_counts[key] = decision_counts.get(key, 0) + 1

    verdict_counts: dict[str, int] = {}
    for obs in all_obs:
        verification = obs.get("verification") if isinstance(obs.get("verification"), dict) else {}
        verdict = str(verification.get("verdict") or "unknown")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    fixture_receipts = [r for obs in all_obs for r in (obs.get("fixture_receipts") or []) if isinstance(r, dict)]
    cleanup_receipts = [r for obs in all_obs for r in (obs.get("cleanup_receipts") or []) if isinstance(r, dict)]
    snapshots = [
        s
        for obs in write_observations
        for phase in ("before", "after")
        for s in (((obs.get("snapshots") or {}).get(phase)) or [])
        if isinstance(s, dict)
    ]
    snapshot_accepted = sum(1 for s in snapshots if isinstance(_snapshot_status_code(s), int) and 200 <= int(_snapshot_status_code(s) or 0) < 300)
    target_responses = sum(1 for obs in observations if isinstance((obs.get("response") or {}).get("status_code"), int))
    write_target_responses = sum(1 for obs in write_observations for r in (obs.get("responses") or []) if isinstance(r, dict) and isinstance(r.get("status_code"), int))
    runtime_binding_events = _collect_runtime_binding_events(all_obs)
    bound_events = [e for e in runtime_binding_events if e.get("bound") is True]
    binding_sources: dict[str, int] = {}
    for event in bound_events:
        source = str(event.get("source") or "unknown")
        binding_sources[source] = binding_sources.get(source, 0) + 1

    query_bound_request_count = sum(
        1
        for obs in all_obs
        if isinstance(obs.get("request"), dict) and "?" in str((obs.get("request") or {}).get("path") or "")
    ) + sum(
        1
        for obs in write_observations
        for response in (obs.get("responses") or [])
        if isinstance(response, dict) and "?" in str(response.get("flow_path") or "")
    )

    fixture_setup_executed = _count_status(fixture_receipts, status="executed")
    fixture_setup_accepted = _count_status(fixture_receipts, status="executed", accepted=True)
    cleanup_executed = _count_status(cleanup_receipts, status="executed")
    cleanup_accepted = _count_status(cleanup_receipts, status="executed", accepted=True)
    observations_total = len(all_obs)
    executed_probe_count = len(observations) + len(write_observations)
    validated_count = verdict_counts.get("validated_candidate", 0)
    protected_count = verdict_counts.get("falsified_or_protected", 0)
    needs_more_count = verdict_counts.get("needs_more_evidence", 0)
    inconclusive_count = verdict_counts.get("inconclusive", 0)

    execution_integrity_score = round(
        min(100.0,
            _safe_rate(executed_probe_count, max(1, len(decisions))) * 0.30
            + _safe_rate(fixture_setup_accepted, max(1, fixture_setup_executed)) * 0.20
            + _safe_rate(len(bound_events), max(1, len(runtime_binding_events))) * 0.20
            + _safe_rate(cleanup_accepted, max(1, cleanup_executed)) * 0.15
            + _safe_rate(validated_count + protected_count, max(1, observations_total)) * 0.15
        ),
        2,
    )

    target_http_response_count = target_responses + write_target_responses
    oracle_resolved_count = validated_count + protected_count
    scoreboard = {
        "engine": "runtime_evidence_scoreboard_v2_phase95_gap_plan",
        "created_at": report.get("created_at"),
        "project_id": report.get("project_id"),
        "probe_count": len(decisions),
        "executed_probe_count": executed_probe_count,
        "executed_readonly_count": len(observations),
        "executed_write_sandbox_count": len(write_observations),
        "execution_coverage_rate": _safe_rate(executed_probe_count, len(decisions)),
        "target_http_response_count": target_http_response_count,
        "target_response_rate": _safe_rate(target_http_response_count, executed_probe_count),
        "decision_counts": decision_counts,
        "verdict_counts": verdict_counts,
        "validated_candidate_count": validated_count,
        "protected_or_falsified_count": protected_count,
        "oracle_resolved_count": oracle_resolved_count,
        "oracle_resolution_rate": _safe_rate(oracle_resolved_count, observations_total),
        "needs_more_evidence_count": needs_more_count,
        "inconclusive_count": inconclusive_count,
        "finding_count": len(findings),
        "fixture_setup_request_count": len(fixture_receipts),
        "fixture_setup_executed_count": fixture_setup_executed,
        "fixture_setup_accepted_count": fixture_setup_accepted,
        "fixture_setup_success_rate": _safe_rate(fixture_setup_accepted, fixture_setup_executed),
        "cleanup_request_count": len(cleanup_receipts),
        "cleanup_executed_count": cleanup_executed,
        "cleanup_accepted_count": cleanup_accepted,
        "cleanup_success_rate": _safe_rate(cleanup_accepted, cleanup_executed),
        "snapshot_request_count": len(snapshots),
        "snapshot_accepted_count": snapshot_accepted,
        "snapshot_success_rate": _safe_rate(snapshot_accepted, len(snapshots)),
        "runtime_binding_event_count": len(runtime_binding_events),
        "runtime_binding_success_count": len(bound_events),
        "runtime_binding_success_rate": _safe_rate(len(bound_events), len(runtime_binding_events)),
        "runtime_binding_sources": dict(sorted(binding_sources.items())),
        "query_bound_request_count": query_bound_request_count,
        "execution_integrity_score": execution_integrity_score,
        "top_failure_or_gap_reasons": _execution_failure_reasons(decisions, all_obs),
    }
    scoreboard["recommended_next_actions"] = _runtime_evidence_gap_recommendations(scoreboard)
    scoreboard["evidence_maturity"] = _runtime_evidence_maturity(scoreboard)
    return scoreboard



def _runtime_evidence_probe_binding_events(obs: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect binding events for one probe observation without relying on global state."""
    events: list[dict[str, Any]] = []
    req = obs.get("request") if isinstance(obs.get("request"), dict) else {}
    body_binding = req.get("body_runtime_binding")
    if _meaningful_runtime_binding(body_binding):
        events.append({
            "source": body_binding.get("source") or "request_body",
            "bound": bool(body_binding.get("bound")),
            "kind": "target_request_body",
        })
    for bucket_name in ("fixture_receipts", "cleanup_receipts"):
        for receipt in obs.get(bucket_name) or []:
            if not isinstance(receipt, dict):
                continue
            binding = receipt.get("runtime_binding")
            if _meaningful_runtime_binding(binding):
                events.append({
                    "source": binding.get("source") or bucket_name,
                    "bound": bool(binding.get("bound")),
                    "kind": bucket_name,
                    "path": receipt.get("path"),
                })
            body_binding = receipt.get("body_runtime_binding")
            if _meaningful_runtime_binding(body_binding):
                events.append({
                    "source": body_binding.get("source") or f"{bucket_name}_body",
                    "bound": bool(body_binding.get("bound")),
                    "kind": f"{bucket_name}_body",
                    "path": receipt.get("path"),
                })
    for response in obs.get("responses") or []:
        if not isinstance(response, dict):
            continue
        binding = response.get("runtime_binding")
        if _meaningful_runtime_binding(binding):
            events.append({
                "source": binding.get("source") or "flow_response",
                "bound": bool(binding.get("bound")),
                "kind": "flow_response",
                "step": response.get("step"),
            })
        body_binding = response.get("request_body_runtime_binding")
        if _meaningful_runtime_binding(body_binding):
            events.append({
                "source": body_binding.get("source") or "flow_step_body",
                "bound": bool(body_binding.get("bound")),
                "kind": "flow_step_body",
                "step": response.get("step"),
            })
    return events


def _runtime_evidence_target_statuses(obs: dict[str, Any]) -> list[int]:
    statuses: list[int] = []
    response = obs.get("response") if isinstance(obs.get("response"), dict) else {}
    if isinstance(response.get("status_code"), int):
        statuses.append(int(response.get("status_code")))
    for response in obs.get("responses") or []:
        if isinstance(response, dict) and isinstance(response.get("status_code"), int):
            statuses.append(int(response.get("status_code")))
    return statuses


def _runtime_evidence_probe_gap_types(decision: dict[str, Any], obs: dict[str, Any] | None) -> list[str]:
    """Return deterministic per-probe blockers/gaps from actual run evidence."""
    gaps: list[str] = []
    if decision.get("decision") == "blocked":
        gaps.append("blocked_decision")
        reason = str(decision.get("reason") or "")
        if reason:
            gaps.append(f"blocked:{reason}")
        return gaps
    if not obs:
        return ["missing_runtime_observation"]

    statuses = _runtime_evidence_target_statuses(obs)
    if not statuses:
        gaps.append("missing_target_http_response")

    fixture_receipts = [r for r in (obs.get("fixture_receipts") or []) if isinstance(r, dict)]
    fixture_executed = _count_status(fixture_receipts, status="executed")
    fixture_accepted = _count_status(fixture_receipts, status="executed", accepted=True)
    if fixture_executed and fixture_accepted < fixture_executed:
        gaps.append("fixture_setup_not_fully_accepted")

    binding_events = _runtime_evidence_probe_binding_events(obs)
    if binding_events and any(event.get("bound") is not True for event in binding_events):
        gaps.append("runtime_binding_not_fully_bound")

    cleanup_receipts = [r for r in (obs.get("cleanup_receipts") or []) if isinstance(r, dict)]
    cleanup_executed = _count_status(cleanup_receipts, status="executed")
    cleanup_accepted = _count_status(cleanup_receipts, status="executed", accepted=True)
    if cleanup_executed and cleanup_accepted < cleanup_executed:
        gaps.append("cleanup_not_fully_accepted")

    snapshots_obj = obs.get("snapshots") if isinstance(obs.get("snapshots"), dict) else {}
    snapshot_items = [
        s
        for phase in ("before", "after")
        for s in ((snapshots_obj.get(phase) or []) if isinstance(snapshots_obj.get(phase), list) else [])
        if isinstance(s, dict)
    ]
    if snapshot_items:
        accepted = sum(1 for s in snapshot_items if isinstance(_snapshot_status_code(s), int) and 200 <= int(_snapshot_status_code(s) or 0) < 300)
        if accepted < len(snapshot_items):
            gaps.append("snapshot_not_fully_accepted")

    verification = obs.get("verification") if isinstance(obs.get("verification"), dict) else {}
    verdict = str(verification.get("verdict") or "")
    if verdict == "needs_more_evidence":
        gaps.append("needs_more_evidence")
    elif verdict == "inconclusive":
        gaps.append("inconclusive_runtime_oracle")
    return gaps


def _runtime_evidence_probe_next_action(gap_types: list[str], obs: dict[str, Any] | None) -> str:
    gap_set = set(gap_types)
    if "blocked_decision" in gap_set:
        return "Resolve the blocker reason in the decision ledger, then rerun this probe against the same candidate."
    if "missing_runtime_observation" in gap_set:
        return "Rerun with readonly/write execution enabled as appropriate so this candidate produces an observation."
    if "missing_target_http_response" in gap_set:
        return "Stabilize target reachability, URL rendering, auth headers, and timeout settings for this probe."
    if "fixture_setup_not_fully_accepted" in gap_set:
        return "Fix disposable fixture setup data or endpoint mapping before trusting downstream target evidence."
    if "runtime_binding_not_fully_bound" in gap_set:
        return "Improve response ID extraction and bind observed IDs into path, query, request body, snapshots, and cleanup."
    if "snapshot_not_fully_accepted" in gap_set:
        return "Repair before/after observer requests so the runtime oracle can compare business state deltas."
    if "needs_more_evidence" in gap_set:
        return "Add fixture-anchor checks, control actor baseline reads, or richer observer deltas for this candidate."
    if "inconclusive_runtime_oracle" in gap_set:
        return "Strengthen the oracle rule or invariant evidence that classifies this runtime response."
    if "cleanup_not_fully_accepted" in gap_set:
        return "Fix cleanup path/body binding or cleanup ordering so the disposable sandbox remains reusable."
    verification = (obs or {}).get("verification") if isinstance((obs or {}).get("verification"), dict) else {}
    verdict = str(verification.get("verdict") or "")
    if verdict == "validated_candidate":
        return "Package the reproduction trace, evidence snapshots, and fix verification plan for customer review."
    if verdict == "falsified_or_protected":
        return "Keep as protected baseline evidence and prioritize unresolved candidates first."
    return "No immediate action; keep this probe in the runtime evidence ledger for trend analysis."


def _runtime_evidence_probe_readiness_level(decision: dict[str, Any], obs: dict[str, Any] | None, gap_types: list[str]) -> str:
    if decision.get("decision") == "blocked":
        return "blocked_before_execution"
    if not obs:
        return "not_observed"
    if "missing_target_http_response" in set(gap_types):
        return "transport_or_runtime_gap"
    verification = obs.get("verification") if isinstance(obs.get("verification"), dict) else {}
    verdict = str(verification.get("verdict") or "unknown")
    if verdict == "validated_candidate" and not ({"runtime_binding_not_fully_bound", "fixture_setup_not_fully_accepted", "snapshot_not_fully_accepted"} & set(gap_types)):
        return "customer_ready_candidate"
    if verdict == "falsified_or_protected":
        return "protected_or_falsified"
    if verdict in {"needs_more_evidence", "inconclusive"}:
        return "evidence_gap"
    return "executed_unclassified"


def _build_runtime_evidence_probe_ledger(report: dict[str, Any]) -> dict[str, Any]:
    """Build an actionable per-probe ledger from the same factual runtime report.

    Scoreboard metrics identify global weak points; this ledger maps those weak
    points back to concrete candidate IDs so the next optimization cycle can act
    on the exact probes that blocked customer-ready evidence.
    """
    decisions = [d for d in (report.get("decisions") or []) if isinstance(d, dict)]
    observations = [o for o in (report.get("observations") or []) if isinstance(o, dict)]
    write_observations = [o for o in (report.get("write_observations") or []) if isinstance(o, dict)]
    obs_by_id: dict[str, dict[str, Any]] = {}
    for obs in observations + write_observations:
        cid = str(obs.get("candidate_id") or "")
        if cid and cid not in obs_by_id:
            obs_by_id[cid] = obs

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for decision in decisions:
        cid = str(decision.get("candidate_id") or "")
        obs = obs_by_id.get(cid)
        seen.add(cid)
        binding_events = _runtime_evidence_probe_binding_events(obs or {}) if obs else []
        fixture_receipts = [r for r in ((obs or {}).get("fixture_receipts") or []) if isinstance(r, dict)]
        cleanup_receipts = [r for r in ((obs or {}).get("cleanup_receipts") or []) if isinstance(r, dict)]
        snapshots_obj = (obs or {}).get("snapshots") if isinstance((obs or {}).get("snapshots"), dict) else {}
        snapshot_items = [
            s
            for phase in ("before", "after")
            for s in ((snapshots_obj.get(phase) or []) if isinstance(snapshots_obj.get(phase), list) else [])
            if isinstance(s, dict)
        ]
        verification = (obs or {}).get("verification") if isinstance((obs or {}).get("verification"), dict) else {}
        statuses = _runtime_evidence_target_statuses(obs or {}) if obs else []
        gap_types = _runtime_evidence_probe_gap_types(decision, obs)
        entry = {
            "candidate_id": cid,
            "risk_type": (obs or decision).get("risk_type"),
            "method": (obs or decision).get("method"),
            "path": (obs or decision).get("path") or (((obs or {}).get("request") or {}).get("path") if isinstance((obs or {}).get("request"), dict) else None),
            "decision": decision.get("decision") or "unknown",
            "decision_reason": decision.get("reason"),
            "observed": bool(obs),
            "target_http_statuses": statuses,
            "verdict": verification.get("verdict"),
            "confidence": verification.get("confidence"),
            "verification_reason": verification.get("reason"),
            "fixture_setup": {
                "request_count": len(fixture_receipts),
                "executed_count": _count_status(fixture_receipts, status="executed"),
                "accepted_count": _count_status(fixture_receipts, status="executed", accepted=True),
            },
            "runtime_binding": {
                "event_count": len(binding_events),
                "bound_count": sum(1 for event in binding_events if event.get("bound") is True),
                "success_rate": _safe_rate(sum(1 for event in binding_events if event.get("bound") is True), len(binding_events)),
                "sources": sorted({str(event.get("source") or "unknown") for event in binding_events}),
            },
            "snapshots": {
                "request_count": len(snapshot_items),
                "accepted_count": sum(1 for item in snapshot_items if isinstance(item.get("status_code"), int) and 200 <= int(item.get("status_code")) < 300),
            },
            "cleanup": {
                "request_count": len(cleanup_receipts),
                "executed_count": _count_status(cleanup_receipts, status="executed"),
                "accepted_count": _count_status(cleanup_receipts, status="executed", accepted=True),
            },
            "gap_types": gap_types,
            "readiness_level": _runtime_evidence_probe_readiness_level(decision, obs, gap_types),
            "customer_ready": _runtime_evidence_probe_readiness_level(decision, obs, gap_types) == "customer_ready_candidate",
            "next_action": _runtime_evidence_probe_next_action(gap_types, obs),
        }
        entries.append(entry)

    for cid, obs in sorted(obs_by_id.items()):
        if cid in seen:
            continue
        pseudo_decision = {"candidate_id": cid, "decision": "observed_without_decision"}
        gap_types = _runtime_evidence_probe_gap_types(pseudo_decision, obs)
        verification = obs.get("verification") if isinstance(obs.get("verification"), dict) else {}
        entries.append({
            "candidate_id": cid,
            "risk_type": obs.get("risk_type"),
            "method": obs.get("method"),
            "path": obs.get("path") or ((obs.get("request") or {}).get("path") if isinstance(obs.get("request"), dict) else None),
            "decision": "observed_without_decision",
            "decision_reason": None,
            "observed": True,
            "target_http_statuses": _runtime_evidence_target_statuses(obs),
            "verdict": verification.get("verdict"),
            "confidence": verification.get("confidence"),
            "verification_reason": verification.get("reason"),
            "gap_types": gap_types,
            "readiness_level": _runtime_evidence_probe_readiness_level(pseudo_decision, obs, gap_types),
            "customer_ready": _runtime_evidence_probe_readiness_level(pseudo_decision, obs, gap_types) == "customer_ready_candidate",
            "next_action": _runtime_evidence_probe_next_action(gap_types, obs),
        })

    carry_forward = report.get("runtime_evidence_carry_forward") if isinstance(report.get("runtime_evidence_carry_forward"), dict) else {}
    current_ids = {str(entry.get("candidate_id") or "") for entry in entries if entry.get("candidate_id")}
    for carried_entry in carry_forward.get("probe_ledger_entries") or []:
        if not isinstance(carried_entry, dict):
            continue
        cid = str(carried_entry.get("candidate_id") or "")
        if not cid or cid in current_ids:
            continue
        entries.append(carried_entry)
        current_ids.add(cid)

    gap_counts: dict[str, int] = {}
    readiness_counts: dict[str, int] = {}
    for entry in entries:
        readiness = str(entry.get("readiness_level") or "unknown")
        readiness_counts[readiness] = readiness_counts.get(readiness, 0) + 1
        for gap in entry.get("gap_types") or []:
            gap_counts[str(gap)] = gap_counts.get(str(gap), 0) + 1

    return {
        "engine": "runtime_evidence_probe_ledger_v1_phase95",
        "created_at": report.get("created_at"),
        "project_id": report.get("project_id"),
        "probe_count": len(decisions),
        "entry_count": len(entries),
        "customer_ready_probe_count": sum(1 for entry in entries if entry.get("customer_ready") is True),
        "carried_forward_probe_count": sum(1 for entry in entries if entry.get("carried_forward") is True),
        "blocked_probe_count": readiness_counts.get("blocked_before_execution", 0),
        "evidence_gap_probe_count": readiness_counts.get("evidence_gap", 0),
        "validated_probe_count": sum(1 for entry in entries if entry.get("verdict") == "validated_candidate"),
        "protected_probe_count": sum(1 for entry in entries if entry.get("verdict") == "falsified_or_protected"),
        "readiness_counts": dict(sorted(readiness_counts.items())),
        "top_probe_gap_types": dict(sorted(gap_counts.items(), key=lambda item: (-item[1], item[0]))[:20]),
        "entries": entries,
    }


def _render_runtime_evidence_probe_ledger_markdown(ledger: dict[str, Any]) -> str:
    lines = [
        "# Runtime Evidence Probe Ledger",
        "",
        f"- engine: `{ledger.get('engine')}`",
        f"- project: `{ledger.get('project_id')}`",
        f"- probes total: {ledger.get('probe_count')}",
        f"- ledger entries: {ledger.get('entry_count')}",
        f"- customer-ready probes: {ledger.get('customer_ready_probe_count')}",
        f"- blocked probes: {ledger.get('blocked_probe_count')}",
        f"- evidence-gap probes: {ledger.get('evidence_gap_probe_count')}",
        f"- readiness counts: `{json.dumps(ledger.get('readiness_counts') or {}, ensure_ascii=False)}`",
        "",
    ]
    gaps = ledger.get("top_probe_gap_types") if isinstance(ledger.get("top_probe_gap_types"), dict) else {}
    if gaps:
        lines.extend(["## Top probe gap types", ""])
        for gap, count in gaps.items():
            lines.append(f"- {gap}: {count}")
        lines.append("")
    entries = [e for e in (ledger.get("entries") or []) if isinstance(e, dict)]
    if entries:
        lines.extend(["## Probe actions", "", "| Candidate | Decision | Readiness | Verdict | HTTP | Gaps | Next action |", "|---|---|---|---|---|---|---|"])
        for entry in entries[:50]:
            gaps_text = ", ".join(str(g) for g in (entry.get("gap_types") or [])) or "-"
            statuses = ", ".join(str(s) for s in (entry.get("target_http_statuses") or [])) or "-"
            lines.append(
                "| "
                + " | ".join([
                    str(entry.get("candidate_id") or "-"),
                    str(entry.get("decision") or "-"),
                    str(entry.get("readiness_level") or "-"),
                    str(entry.get("verdict") or "-"),
                    statuses,
                    gaps_text,
                    str(entry.get("next_action") or "-"),
                ]).replace("\n", " ")
                + " |"
            )
        if len(entries) > 50:
            lines.append(f"\n_Only the first 50 entries are shown; see JSON for all {len(entries)} probes._")
        lines.append("")
    return "\n".join(lines)


