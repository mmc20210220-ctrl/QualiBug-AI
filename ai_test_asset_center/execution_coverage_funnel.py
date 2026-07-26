"""Execution Coverage Funnel — track every obligation from creation to unique root cause.

SPEC v1.2 §5: Unified Coverage Funnel

This module builds a complete per-obligation funnel that records the first
terminal stage and reason for each obligation, enabling precise blocker
attribution and coverage recovery measurement.

Output: qualibug.execution-coverage-funnel.v1
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ─── Funnel Stage Definitions ────────────────────────────────────────────────

FUNNEL_STAGES = (
    "OBLIGATION_CREATED",
    "ELIGIBILITY_CONFIRMED",
    "COMPILER_ENTERED",
    "EXPERIMENT_COMPILED",
    "WRITE_PROOF_PROVEN",
    "OBSERVER_RESOLVED",
    "FIXTURE_RESOLVED",
    "BINDINGS_RESOLVED",
    "RUNTIME_PROOF_VALID",
    "TARGET_TRANSPORT_REACHED",
    "ORACLE_INPUT_COMPLETE",
    "ORACLE_EVALUATED",
    "CLEANUP_EQUIVALENT",
    "FINDING_CANDIDATE",
    "FORMAL_DELIVERABLE",
    "UNIQUE_ROOT_CAUSE",
    "DEEP_UNIQUE_ROOT_CAUSE",
)

_STAGE_INDEX = {stage: i for i, stage in enumerate(FUNNEL_STAGES)}


# ─── Per-Obligation Stage Resolution ─────────────────────────────────────────


def _resolve_obligation_stages(
    obligation: dict[str, Any],
    experiment: dict[str, Any] | None,
    execution_result: dict[str, Any] | None,
    finding: dict[str, Any] | None,
) -> dict[str, Any]:
    """Determine the terminal stage and reason for a single obligation.

    Returns:
        {
            "obligation_id": str,
            "risk_family": str,
            "reached_stage": str,
            "terminal_stage": str,
            "terminal_reason": str,
            "is_terminal": bool,
        }
    """
    oid = _text(obligation.get("obligation_id"))
    family = _text(obligation.get("risk_family"))
    reached = "OBLIGATION_CREATED"
    terminal_reason = ""

    # Stage: ELIGIBILITY_CONFIRMED — obligation has required fields
    if oid and family:
        reached = "ELIGIBILITY_CONFIRMED"

    # Stage: COMPILER_ENTERED — experiment exists (even if BLOCKED)
    exp = _dict(experiment)
    if exp:
        reached = "COMPILER_ENTERED"
        compile_receipt = _dict(exp.get("compile_receipt"))
        compile_status = _text(compile_receipt.get("status")).upper()

        # Stage: EXPERIMENT_COMPILED
        if compile_status == "COMPILED":
            reached = "EXPERIMENT_COMPILED"

            # Stage: WRITE_PROOF_PROVEN
            proof = _dict(exp.get("write_reversibility_proof"))
            safety = _dict(exp.get("safety_contract"))
            if not safety.get("governed_write") or _text(proof.get("proof_status")) == "PROVEN":
                reached = "WRITE_PROOF_PROVEN"

            # Stage: OBSERVER_RESOLVED
            observers = _list(exp.get("observers"))
            if observers or not safety.get("governed_write"):
                reached = "OBSERVER_RESOLVED"

            # Stage: FIXTURE_RESOLVED
            binding_plan = _list(exp.get("binding_plan"))
            unresolved_bindings = [
                b for b in binding_plan
                if isinstance(b, dict) and _text(b.get("status")) == "unresolved"
            ]
            if not unresolved_bindings:
                reached = "FIXTURE_RESOLVED"

            # Stage: BINDINGS_RESOLVED
            reached = "BINDINGS_RESOLVED"

            # Stage: RUNTIME_PROOF_VALID (checked at execution time)
            reached = "RUNTIME_PROOF_VALID"

        elif compile_status == "BLOCKED":
            terminal_reason = _text(compile_receipt.get("reason_code")) or "BLOCKED_COMPILE"
        elif compile_status == "DEFERRED":
            terminal_reason = _text(compile_receipt.get("reason_code")) or "DEFERRED"

    # Execution-level stages
    result = _dict(execution_result)
    if result and reached == "RUNTIME_PROOF_VALID":
        exec_status = _text(result.get("status")).upper()
        exec_reason = _text(result.get("reason_code"))

        if exec_status == "BLOCKED":
            terminal_reason = exec_reason or "BLOCKED_RUNTIME"
        elif exec_status in ("EXECUTED", "DELIVERABLE", "HARNESS_FAILURE"):
            reached = "TARGET_TRANSPORT_REACHED"

            # Stage: ORACLE_INPUT_COMPLETE
            observations = _dict(result.get("observations"))
            oracle_receipt = _dict(result.get("oracle_receipt"))
            if oracle_receipt or _dict(result.get("delivery_gate_receipt")):
                reached = "ORACLE_INPUT_COMPLETE"

            # Stage: ORACLE_EVALUATED
            if oracle_receipt:
                reached = "ORACLE_EVALUATED"

            # Stage: CLEANUP_EQUIVALENT
            cleanup_equiv = _dict(observations.get("cleanup_equivalence_receipt"))
            equiv_status = _text(cleanup_equiv.get("equivalence_status")).upper()
            safety = _dict(exp.get("safety_contract"))
            if not safety.get("governed_write") or equiv_status == "EQUIVALENT":
                reached = "CLEANUP_EQUIVALENT"
            elif equiv_status == "NOT_APPLICABLE":
                reached = "CLEANUP_EQUIVALENT"

            # Stage: FINDING_CANDIDATE
            f = _dict(finding) or _dict(result.get("finding"))
            if f and _text(f.get("title")):
                reached = "FINDING_CANDIDATE"

                # Stage: FORMAL_DELIVERABLE
                if f.get("customer_deliverable") is True or _text(
                    f.get("delivery_status")
                ) == "formal_deliverable":
                    reached = "FORMAL_DELIVERABLE"

                    # Stage: UNIQUE_ROOT_CAUSE
                    root_cause_id = _text(f.get("root_cause_id") or f.get("unique_root_cause_id"))
                    if root_cause_id:
                        reached = "UNIQUE_ROOT_CAUSE"

                        # Stage: DEEP_UNIQUE_ROOT_CAUSE
                        if f.get("is_deep_root_cause") is True:
                            reached = "DEEP_UNIQUE_ROOT_CAUSE"

        if exec_status == "HARNESS_FAILURE" and not terminal_reason:
            terminal_reason = exec_reason or "HARNESS_FAILURE"

    # Determine terminal
    is_terminal = bool(terminal_reason) or reached in (
        "DEEP_UNIQUE_ROOT_CAUSE", "UNIQUE_ROOT_CAUSE", "FORMAL_DELIVERABLE",
    )
    terminal_stage = reached if terminal_reason else (
        reached if reached in ("DEEP_UNIQUE_ROOT_CAUSE", "UNIQUE_ROOT_CAUSE", "FORMAL_DELIVERABLE") else ""
    )

    return {
        "obligation_id": oid,
        "risk_family": family,
        "reached_stage": reached,
        "terminal_stage": terminal_stage or reached,
        "terminal_reason": terminal_reason,
        "is_terminal": is_terminal,
    }


# ─── Main Funnel Builder ──────────────────────────────────────────────────────


def build_execution_coverage_funnel(
    *,
    obligations: list[dict[str, Any]],
    experiments: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    campaign_id: str = "",
) -> dict[str, Any]:
    """Build the complete execution coverage funnel.

    Args:
        obligations: All test obligations from the campaign.
        experiments: All compiled experiments (including BLOCKED).
        execution_results: All execution results.
        findings: All findings (candidate and formal).
        campaign_id: Campaign identifier.

    Returns:
        qualibug.execution-coverage-funnel.v1 receipt.
    """
    # Index by obligation_id
    exp_by_oid: dict[str, dict[str, Any]] = {}
    for exp in _list(experiments):
        if isinstance(exp, dict):
            oid = _text(exp.get("obligation_id"))
            if oid:
                exp_by_oid[oid] = exp

    result_by_oid: dict[str, dict[str, Any]] = {}
    for res in _list(execution_results):
        if isinstance(res, dict):
            oid = _text(res.get("obligation_id"))
            if oid:
                result_by_oid[oid] = res

    finding_by_oid: dict[str, dict[str, Any]] = {}
    for f in _list(findings):
        if isinstance(f, dict):
            oid = _text(f.get("obligation_id"))
            if oid:
                finding_by_oid[oid] = f

    # Resolve per-obligation stages
    per_obligation: list[dict[str, Any]] = []
    seen_oids: set[str] = set()
    for obl in _list(obligations):
        if not isinstance(obl, dict):
            continue
        oid = _text(obl.get("obligation_id"))
        if not oid or oid in seen_oids:
            continue
        seen_oids.add(oid)
        stages = _resolve_obligation_stages(
            obligation=obl,
            experiment=exp_by_oid.get(oid),
            execution_result=result_by_oid.get(oid),
            finding=finding_by_oid.get(oid),
        )
        per_obligation.append(stages)

    total = len(per_obligation)

    # Count per stage
    stage_counts: dict[str, int] = {stage: 0 for stage in FUNNEL_STAGES}
    for entry in per_obligation:
        reached = _text(entry.get("reached_stage"))
        reached_idx = _STAGE_INDEX.get(reached, 0)
        for i, stage in enumerate(FUNNEL_STAGES):
            if i <= reached_idx:
                stage_counts[stage] += 1

    # Terminal reasons
    terminal_reasons: dict[str, int] = {}
    for entry in per_obligation:
        reason = _text(entry.get("terminal_reason"))
        if reason:
            terminal_reasons[reason] = terminal_reasons.get(reason, 0) + 1

    # Risk family breakdown
    risk_family_breakdown: dict[str, dict[str, int]] = {}
    for entry in per_obligation:
        family = _text(entry.get("risk_family")) or "unknown"
        if family not in risk_family_breakdown:
            risk_family_breakdown[family] = {"total": 0, "compiled": 0, "transport": 0, "deliverable": 0}
        risk_family_breakdown[family]["total"] += 1
        reached = _text(entry.get("reached_stage"))
        reached_idx = _STAGE_INDEX.get(reached, 0)
        if reached_idx >= _STAGE_INDEX["EXPERIMENT_COMPILED"]:
            risk_family_breakdown[family]["compiled"] += 1
        if reached_idx >= _STAGE_INDEX["TARGET_TRANSPORT_REACHED"]:
            risk_family_breakdown[family]["transport"] += 1
        if reached_idx >= _STAGE_INDEX["FORMAL_DELIVERABLE"]:
            risk_family_breakdown[family]["deliverable"] += 1

    # Build stages output
    stages_out: dict[str, dict[str, Any]] = {}
    for stage in FUNNEL_STAGES:
        count = stage_counts[stage]
        stages_out[stage] = {
            "count": count,
            "rate": round(count / total, 4) if total > 0 else 0.0,
        }

    # Fingerprint
    fingerprint_content = {
        "obligations_total": total,
        "stages": {k: v["count"] for k, v in stages_out.items()},
        "terminal_reasons": terminal_reasons,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]

    return {
        "schema_version": "qualibug.execution-coverage-funnel.v1",
        "campaign_id": _text(campaign_id),
        "input_fingerprint": fingerprint,
        "obligations_total": total,
        "stages": stages_out,
        "terminal_reasons": terminal_reasons,
        "risk_family_breakdown": risk_family_breakdown,
        "operation_breakdown": {},
        "depth_breakdown": {},
        "per_obligation": per_obligation,
        "fingerprint": fingerprint,
    }
