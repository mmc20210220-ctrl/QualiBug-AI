from __future__ import annotations

"""Phase92T: customer-ready runtime finding evidence packaging.

This module does not decide whether something is a bug.  Earlier runtime gates
(HTTP evidence, before/after invariant evaluation, semantic observer join and
cross-observer reconciliation) make that decision.  Phase92T turns a validated
observation into a compact, reviewable evidence package that a customer can use
to understand why QualiBug raised the finding and how to reproduce it.
"""

from typing import Any


FAILED_VERDICTS = {"failed"}
STRONG_OBSERVER_KINDS = {
    "primary_resource_detail",
    "inventory_projection",
    "account_projection",
    "business_ledger_projection",
    "workflow_history_projection",
    "tenant_scope_projection",
}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, {}, ""):
        return []
    return [value]


def _first_status(obs: dict[str, Any]) -> int | None:
    if isinstance(obs.get("response"), dict):
        code = (obs.get("response") or {}).get("status_code")
        return int(code) if isinstance(code, int) else None
    for item in _as_list(obs.get("responses")):
        if isinstance(item, dict) and isinstance(item.get("status_code"), int):
            return int(item.get("status_code"))
    return None


def _snapshot_items(obs: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    snapshots = obs.get("snapshots") if isinstance(obs.get("snapshots"), dict) else {}
    raw = snapshots.get(phase) if isinstance(snapshots, dict) else []
    return [x for x in _as_list(raw) if isinstance(x, dict)]


def _snapshot_statuses(items: list[dict[str, Any]]) -> list[int]:
    statuses: list[int] = []
    for item in items:
        response = item.get("response") if isinstance(item.get("response"), dict) else {}
        code = response.get("status_code")
        if isinstance(code, int):
            statuses.append(int(code))
    return statuses


def _observer_kinds(obs: dict[str, Any]) -> list[str]:
    kinds: list[str] = []
    for phase in ("before", "after"):
        for item in _snapshot_items(obs, phase):
            kind = str(item.get("observer_kind") or "").strip()
            if kind:
                kinds.append(kind)
    return sorted(dict.fromkeys(kinds))


def _evidence_goals(obs: dict[str, Any]) -> list[str]:
    goals: list[str] = []
    for phase in ("before", "after"):
        for item in _snapshot_items(obs, phase):
            goal = str(item.get("evidence_goal") or "").strip()
            if goal:
                goals.append(goal)
    return sorted(dict.fromkeys(goals))[:20]


def _failed_invariants(invariant_eval: dict[str, Any]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for result in invariant_eval.get("results") or []:
        if not isinstance(result, dict) or result.get("verdict") not in FAILED_VERDICTS:
            continue
        failed.append({
            "invariant_id": result.get("invariant_id"),
            "kind": result.get("kind"),
            "reason": result.get("reason"),
            "confidence": result.get("confidence"),
            "failed_fields": list(result.get("failed_fields") or [])[:20],
        })
    return failed[:20]


def _cross_observer_failures(invariant_eval: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for result in invariant_eval.get("results") or []:
        if not isinstance(result, dict) or result.get("kind") != "cross_observer_conservation_reconciliation":
            continue
        computed = result.get("computed") if isinstance(result.get("computed"), dict) else {}
        for failure in computed.get("failures") or []:
            if isinstance(failure, dict):
                out.append({
                    "kind": failure.get("kind"),
                    "reason": failure.get("reason"),
                    "state_delta": failure.get("state_delta"),
                    "ledger_delta": failure.get("ledger_delta"),
                })
    return out[:20]


def _semantic_graph_summary(invariant_eval: dict[str, Any]) -> dict[str, Any]:
    graph = invariant_eval.get("semantic_observer_graph") if isinstance(invariant_eval.get("semantic_observer_graph"), dict) else {}
    if not graph:
        return {"present": False}
    return {
        "present": True,
        "engine": graph.get("engine"),
        "entity_counts": graph.get("entity_counts") or {},
        "cluster_count": graph.get("cluster_count"),
        "join_key_fields": list(graph.get("join_key_fields") or [])[:20],
        "coverage": list(graph.get("coverage") or [])[:20],
        "changed_entity_count": len(graph.get("changed_entity_fingerprints") or []),
        "added_entity_count": len(graph.get("added_entity_fingerprints") or []),
        "deleted_entity_count": len(graph.get("deleted_entity_fingerprints") or []),
    }


def _delta_summary(verification: dict[str, Any]) -> dict[str, Any]:
    invariant_eval = verification.get("business_invariant_evaluation") if isinstance(verification.get("business_invariant_evaluation"), dict) else {}
    failed = _failed_invariants(invariant_eval)
    failed_fields: list[str] = []
    for item in failed:
        failed_fields.extend(str(x) for x in item.get("failed_fields") or [])
    return {
        "primary_reason": verification.get("reason"),
        "negative_values": list(verification.get("negative_values") or [])[:20],
        "replay_ids": list(verification.get("replay_ids") or [])[:20],
        "failed_invariant_kinds": sorted({str(x.get("kind")) for x in failed if x.get("kind")}),
        "failed_fields": list(dict.fromkeys(failed_fields))[:30],
        "cross_observer_failures": _cross_observer_failures(invariant_eval),
        "semantic_graph": _semantic_graph_summary(invariant_eval),
    }


def _score_evidence(obs: dict[str, Any], verification: dict[str, Any], invariant_eval: dict[str, Any]) -> float:
    score = 0.0
    if verification.get("verdict") == "validated_candidate":
        score += 0.30
    if _first_status(obs) is not None:
        score += 0.12
    if obs.get("responses"):
        score += 0.08
    before = _snapshot_items(obs, "before")
    after = _snapshot_items(obs, "after")
    if before and after:
        score += 0.16
    if _failed_invariants(invariant_eval):
        score += 0.16
    graph_summary = _semantic_graph_summary(invariant_eval)
    if graph_summary.get("present"):
        score += 0.10
        if graph_summary.get("cluster_count"):
            score += 0.03
    if _cross_observer_failures(invariant_eval):
        score += 0.10
    kinds = set(_observer_kinds(obs))
    if kinds & STRONG_OBSERVER_KINDS:
        score += 0.04
    if obs.get("source_refs"):
        score += 0.06
    if obs.get("grounding_basis"):
        score += 0.03
    confidence = verification.get("confidence")
    if isinstance(confidence, (int, float)):
        score = max(score, min(float(confidence), 0.95))
    return round(min(score, 0.99), 2)


def _grade(score: float) -> str:
    if score >= 0.85:
        return "strong"
    if score >= 0.7:
        return "moderate"
    if score >= 0.5:
        return "partial"
    return "weak"


def package_runtime_finding_evidence(obs: dict[str, Any], *, source: str = "") -> dict[str, Any]:
    """Build a customer-facing evidence package for one runtime finding."""
    verification = obs.get("verification") if isinstance(obs.get("verification"), dict) else {}
    invariant_eval = verification.get("business_invariant_evaluation") if isinstance(verification.get("business_invariant_evaluation"), dict) else {}
    before = _snapshot_items(obs, "before")
    after = _snapshot_items(obs, "after")
    observer_kinds = _observer_kinds(obs)
    score = _score_evidence(obs, verification, invariant_eval)
    request = obs.get("request") if isinstance(obs.get("request"), dict) else {}
    method = obs.get("method") or request.get("method")
    path = obs.get("path") or request.get("path")
    response_count = len(_as_list(obs.get("responses"))) or (1 if obs.get("response") else 0)

    return {
        "engine": "runtime_finding_evidence_packager_v1_phase92t",
        "evidence_strength_score": score,
        "evidence_grade": _grade(score),
        "customer_ready_summary": {
            "claim": verification.get("reason"),
            "endpoint": f"{method} {path}",
            "risk_type": obs.get("risk_type"),
            "why_this_is_not_static_rule": "Finding is backed by observed HTTP execution evidence plus before/after snapshots when available.",
        },
        "evidence_chain": {
            "source_refs_present": bool(obs.get("source_refs")),
            "grounding_basis_present": bool(obs.get("grounding_basis")),
            "http_response_present": _first_status(obs) is not None,
            "http_status_code": _first_status(obs),
            "response_count": response_count,
            "before_snapshot_count": len(before),
            "after_snapshot_count": len(after),
            "before_snapshot_status_codes": _snapshot_statuses(before),
            "after_snapshot_status_codes": _snapshot_statuses(after),
            "observer_kinds": observer_kinds,
            "evidence_goals": _evidence_goals(obs),
        },
        "violated_invariants": _failed_invariants(invariant_eval),
        "delta_summary": _delta_summary(verification),
        "reproduction_assets": {
            "request_available": bool(request),
            "replay_attempt_count": response_count,
            "generated_assets": [
                "grounded_probe_execution_report.json",
                "grounded_probe_execution_report.md",
                "grounded_probe_repro.ps1",
                "grounded_probe_regression_pytest.py",
            ],
            "source": source,
        },
    }
