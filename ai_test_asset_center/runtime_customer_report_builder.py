from __future__ import annotations

"""Customer delivery index built from the single runtime finding contract.

This module is the existing reporting exit used by ``grounded_probe_executor``.
It does not create a second report model: it adds an explicit proof card to each
already-produced finding and separates customer-ready defects from internal
validation leads and capability gaps.
"""

import hashlib
import json
from typing import Any, Iterable

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in (value or []) if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sort_key(finding: dict[str, Any]) -> tuple[int, int, float, str]:
    priority = PRIORITY_ORDER.get(_text(finding.get("priority")), 9)
    severity = SEVERITY_ORDER.get(_text(finding.get("severity")).lower(), 9)
    score = finding.get("evidence_strength_score")
    try:
        numeric_score = float(score)
    except (TypeError, ValueError):
        numeric_score = 0.0
    return priority, severity, -numeric_score, _text(finding.get("finding_id"))


def _count_by(items: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = _text(item.get(key)) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _risk_family(finding: dict[str, Any]) -> str:
    triage = _as_dict(finding.get("customer_triage"))
    return _text(triage.get("risk_family")) or _text(finding.get("defect_family")) or _text(finding.get("risk_type")) or "unknown"


def _top_invariant_kinds(findings: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        for invariant in _as_rows(finding.get("violated_invariants")):
            key = _text(invariant.get("kind"))
            if key:
                counts[key] = counts.get(key, 0) + 1
        contract = _as_dict(finding.get("finding_contract"))
        invariant = _as_dict(contract.get("violated_invariant"))
        key = _text(invariant.get("kind"))
        if key:
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:20])


def _evidence_coverage(findings: Iterable[dict[str, Any]]) -> dict[str, Any]:
    with_before_after = 0
    with_semantic_graph = 0
    with_cross_observer_failure = 0
    observer_kinds: set[str] = set()
    proof_status_counts: dict[str, int] = {}
    for finding in findings:
        proof = _proof_card(finding)
        proof_status = _text(proof.get("proof_status")) or "unknown"
        proof_status_counts[proof_status] = proof_status_counts.get(proof_status, 0) + 1
        if not proof.get("evidence_gaps") and proof.get("has_before_after"):
            with_before_after += 1
        package = _as_dict(finding.get("evidence_package"))
        chain = _as_dict(package.get("evidence_chain"))
        delta = _as_dict(package.get("delta_summary"))
        semantic = _as_dict(delta.get("semantic_graph"))
        if semantic.get("present"):
            with_semantic_graph += 1
        if delta.get("cross_observer_failures"):
            with_cross_observer_failure += 1
        for kind in chain.get("observer_kinds") or []:
            if _text(kind):
                observer_kinds.add(_text(kind))
        for observer in _as_rows(_as_dict(finding.get("evidence")).get("observer_refs")):
            if _text(observer.get("observer")):
                observer_kinds.add(_text(observer.get("observer")))
    return {
        "before_after_snapshot_finding_count": with_before_after,
        "semantic_graph_finding_count": with_semantic_graph,
        "cross_observer_failure_finding_count": with_cross_observer_failure,
        "observer_kinds": sorted(observer_kinds),
        "proof_status_distribution": dict(sorted(proof_status_counts.items())),
    }


def _finding_evidence(finding: dict[str, Any]) -> dict[str, Any]:
    direct = _as_dict(finding.get("evidence"))
    if direct:
        return direct
    contract = _as_dict(finding.get("finding_contract"))
    return _as_dict(contract.get("evidence"))


def _method(finding: dict[str, Any], evidence: dict[str, Any]) -> str:
    direct = _text(finding.get("method") or finding.get("_api_method"))
    if direct:
        return direct.upper()
    method = _as_dict(evidence.get("method"))
    if _text(method.get("value")):
        return _text(method.get("value")).upper()
    calls = _as_rows(evidence.get("calls"))
    for call in calls:
        text = _text(call.get("call"))
        if text:
            return text.split(None, 1)[0].upper()
    return ""


def _has_execution_receipt(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if _as_rows(evidence.get("calls")) or _as_rows(finding.get("raw_probes")):
        return True
    package = _as_dict(finding.get("evidence_package"))
    chain = _as_dict(package.get("evidence_chain"))
    return bool(chain.get("request_count") or chain.get("call_count") or finding.get("action_evidence_ref"))


def _has_source_grounding(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    candidates = [
        finding.get("source_refs"),
        finding.get("document_refs"),
        finding.get("evidence_refs"),
        evidence.get("source_refs"),
        evidence.get("document_refs"),
        evidence.get("obligation_id"),
        finding.get("context_artifact_id"),
    ]
    return any(bool(value) for value in candidates)


def _has_assertion(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if finding.get("failed_assertions") or finding.get("expected") or finding.get("expected_behavior"):
        return True
    if finding.get("actual") or finding.get("actual_behavior"):
        return True
    if finding.get("violated_invariants"):
        return True
    semantic = _as_dict(finding.get("semantic"))
    return bool(semantic.get("verifier_rule") or semantic.get("violated_invariant") or evidence.get("invariant_evidence_ref"))


def _has_reproduction(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    reproduction = _as_dict(finding.get("reproduction"))
    if reproduction.get("flow_id") or reproduction.get("steps") or reproduction.get("required_inputs"):
        return True
    if finding.get("reproduction_steps") or finding.get("is_reproducible"):
        return True
    return bool(evidence.get("reproduction_flow_ref") or evidence.get("reproduction"))


def _before_after(finding: dict[str, Any], evidence: dict[str, Any]) -> tuple[bool, bool, bool]:
    before = bool(finding.get("before_snapshot_ref") or evidence.get("before_snapshot_ref") or evidence.get("before_snapshot"))
    after = bool(finding.get("after_snapshot_ref") or evidence.get("after_snapshot_ref") or evidence.get("after_snapshot"))
    package = _as_dict(finding.get("evidence_package"))
    chain = _as_dict(package.get("evidence_chain"))
    before = before or int(chain.get("before_snapshot_count") or 0) > 0
    after = after or int(chain.get("after_snapshot_count") or 0) > 0
    return before, after, before and after


def _cleanup_is_complete(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    cleanup = _as_dict(finding.get("cleanup")) or _as_dict(evidence.get("cleanup"))
    status = _text(cleanup.get("status")).lower()
    return status in {"completed", "verified", "not_applicable", "not applicable"}


def _semantic_verdict(finding: dict[str, Any], evidence: dict[str, Any]) -> str:
    return _text(finding.get("semantic_verdict") or evidence.get("semantic_verdict") or _as_dict(finding.get("semantic")).get("semantic_verdict"))


def _approved_status(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    final = _text(finding.get("final_review_status") or evidence.get("final_review_status")).upper()
    verdict = _text(finding.get("verdict") or finding.get("verification", {}).get("verdict") if isinstance(finding.get("verification"), dict) else finding.get("verdict")).upper()
    return final in {"PENDING_REVIEW", "CONFIRMED_BY_HUMAN"} or verdict in {"VALIDATED_CANDIDATE", "VALIDATED_BUG", "CONFIRMED"}


def _proof_card(finding: dict[str, Any]) -> dict[str, Any]:
    evidence = _finding_evidence(finding)
    method = _method(finding, evidence)
    write = method in _WRITE_METHODS
    before, after, has_before_after = _before_after(finding, evidence)
    gaps: list[str] = []
    if not _has_source_grounding(finding, evidence):
        gaps.append("SOURCE_GROUNDING_MISSING")
    if not _has_execution_receipt(finding, evidence):
        gaps.append("EXECUTION_RECEIPT_MISSING")
    if not _has_assertion(finding, evidence):
        gaps.append("FAILED_ASSERTION_MISSING")
    if not _has_reproduction(finding, evidence):
        gaps.append("REPRODUCTION_MISSING")
    if write and not before:
        gaps.append("BEFORE_SNAPSHOT_MISSING")
    if write and not after:
        gaps.append("AFTER_SNAPSHOT_MISSING")
    if write and not _cleanup_is_complete(finding, evidence):
        gaps.append("CLEANUP_RECEIPT_MISSING")
    semantic = _semantic_verdict(finding, evidence)
    if semantic and semantic not in {"SEMANTIC_CONFIRMED", "confirmed", "CONFIRMED"}:
        gaps.append("SEMANTIC_VERDICT_NOT_CONFIRMED")
    if not _approved_status(finding, evidence):
        gaps.append("FINAL_GATE_NOT_PASSED")
    proof_status = "customer_ready" if not gaps else ("needs_more_evidence" if _has_execution_receipt(finding, evidence) else "not_executed")
    lineage_payload = {
        "finding_id": finding.get("finding_id"),
        "method": method,
        "path": finding.get("path") or finding.get("_api_path"),
        "source_refs": finding.get("source_refs") or evidence.get("source_refs") or evidence.get("obligation_id"),
        "receipts": evidence.get("calls") or finding.get("raw_probes"),
        "assertion": finding.get("expected") or finding.get("failed_assertions") or evidence.get("invariant_evidence_ref"),
    }
    lineage_digest = hashlib.sha256(json.dumps(lineage_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {
        "proof_status": proof_status,
        "evidence_gaps": gaps,
        "method": method,
        "is_write": write,
        "has_before_snapshot": before,
        "has_after_snapshot": after,
        "has_before_after": has_before_after,
        "has_source_grounding": _has_source_grounding(finding, evidence),
        "has_execution_receipt": _has_execution_receipt(finding, evidence),
        "has_assertion": _has_assertion(finding, evidence),
        "has_reproduction": _has_reproduction(finding, evidence),
        "lineage_digest": lineage_digest,
    }


def _top_item(finding: dict[str, Any]) -> dict[str, Any]:
    proof = _proof_card(finding)
    invariants = [
        _text(item.get("kind"))
        for item in _as_rows(finding.get("violated_invariants"))
        if _text(item.get("kind"))
    ][:8]
    return {
        "finding_id": finding.get("finding_id"),
        "priority": finding.get("priority"),
        "severity": finding.get("severity"),
        "risk_type": finding.get("risk_type"),
        "endpoint": f"{_text(finding.get('method'))} {_text(finding.get('path'))}".strip(),
        "evidence_grade": finding.get("evidence_grade"),
        "evidence_strength_score": finding.get("evidence_strength_score"),
        "customer_impact_summary": finding.get("customer_impact_summary"),
        "violated_invariant_kinds": invariants,
        "proof_status": proof["proof_status"],
        "evidence_gaps": proof["evidence_gaps"],
        "lineage_digest": proof["lineage_digest"],
        "proof_receipt": {
            "source_grounded": proof["has_source_grounding"],
            "executed": proof["has_execution_receipt"],
            "asserted": proof["has_assertion"],
            "reproducible": proof["has_reproduction"],
            "before_after": proof["has_before_after"],
        },
    }


def build_customer_delivery_index(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the existing report exit enriched with proof completeness.

    ``top_customer_actions`` keeps every candidate for operational visibility,
    but only ``customer_ready_findings`` are eligible for customer-facing defect
    counts and commercial delivery claims.
    """
    ordered = sorted([item for item in findings if isinstance(item, dict)], key=_sort_key)
    ready = [item for item in ordered if _proof_card(item)["proof_status"] == "customer_ready"]
    risk_families = _count_by(ready, "risk_type")
    top_items = [_top_item(item) for item in ordered[:25]]
    return {
        "engine": "runtime_customer_report_builder_v2_phase108",
        "customer_delivery_ready": bool(ready),
        "input_finding_count": len(ordered),
        "validated_finding_count": len(ready),
        "customer_ready_finding_count": len(ready),
        "internal_validation_lead_count": len([item for item in ordered if _proof_card(item)["proof_status"] == "needs_more_evidence"]),
        "unexecuted_or_untraceable_count": len([item for item in ordered if _proof_card(item)["proof_status"] == "not_executed"]),
        "by_priority": _count_by(ready, "priority"),
        "by_severity": _count_by(ready, "severity"),
        "by_risk_family": risk_families,
        "top_violated_invariant_kinds": _top_invariant_kinds(ready),
        "evidence_coverage": _evidence_coverage(ordered),
        "top_customer_actions": top_items,
        "customer_ready_findings": [_top_item(item) for item in ready[:25]],
        "recommended_report_usage": "Only customer_ready findings may be presented as confirmed customer defects. needs_more_evidence items are internal validation leads and must show their explicit evidence gaps.",
    }
