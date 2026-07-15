from __future__ import annotations

"""Customer delivery index built from the existing runtime finding contract.

``grounded_probe_executor`` already calls ``build_customer_delivery_index``.
This file is therefore the single delivery exit: it evaluates proof completeness
in place and keeps customer defects, internal leads and capability gaps separate.
"""

import hashlib
import json
import time
from typing import Any, Iterable

_PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_WRITE = {"POST", "PUT", "PATCH", "DELETE"}


def _dict(value: Any) -> dict[str, Any]: return value if isinstance(value, dict) else {}
def _rows(value: Any) -> list[dict[str, Any]]: return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
def _text(value: Any) -> str: return str(value or "").strip()
def _count(items: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        value = _text(item.get(key)) or "unknown"; result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))
def _sort(item: dict[str, Any]) -> tuple[int, int, float, str]:
    try: score = float(item.get("evidence_strength_score") or 0)
    except (TypeError, ValueError): score = 0.0
    return _PRIORITY.get(_text(item.get("priority")), 9), _SEVERITY.get(_text(item.get("severity")).lower(), 9), -score, _text(item.get("finding_id"))


def _evidence(finding: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(finding.get("evidence"))
    if direct: return direct
    return _dict(_dict(finding.get("finding_contract")).get("evidence"))

def _runtime(evidence: dict[str, Any]) -> dict[str, Any]:
    return _dict(evidence.get("normalized_runtime")) or _dict(evidence.get("runtime"))

def _action_method(evidence: dict[str, Any]) -> str:
    runtime = _runtime(evidence)
    action = _dict(runtime.get("action_ref"))
    text = _text(action.get("value") or evidence.get("action_evidence_ref"))
    method = text.split(None, 1)[0].upper() if text else ""
    return method if method in _WRITE else ""

def _method(finding: dict[str, Any], evidence: dict[str, Any]) -> str:
    direct = _text(finding.get("method") or finding.get("_api_method")).upper()
    if direct in _WRITE: return direct
    action = _action_method(evidence)
    if action: return action
    runtime = _runtime(evidence)
    runtime_method = _text(_dict(runtime.get("method")).get("value")).upper()
    if runtime_method: return runtime_method
    declared = _text(_dict(evidence.get("method")).get("value") or evidence.get("method")).upper()
    if declared: return declared
    for call in _rows(evidence.get("calls")):
        text = _text(call.get("call"))
        if text: return text.split(None, 1)[0].upper()
    return direct

def _source_grounded(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    values = (finding.get("source_refs"), finding.get("document_refs"), finding.get("evidence_refs"), evidence.get("source_refs"), evidence.get("document_refs"), evidence.get("obligation_id"), finding.get("context_artifact_id"))
    return any(bool(value) for value in values)

def _executed(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if _rows(evidence.get("calls")) or _rows(finding.get("raw_probes")): return True
    package = _dict(finding.get("evidence_package")); chain = _dict(package.get("evidence_chain"))
    return bool(chain.get("request_count") or chain.get("call_count") or finding.get("action_evidence_ref") or evidence.get("action_evidence_ref"))

def _asserted(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if finding.get("failed_assertions") or finding.get("expected") or finding.get("expected_behavior") or finding.get("actual") or finding.get("actual_behavior") or finding.get("violated_invariants"): return True
    semantic = _dict(finding.get("semantic"))
    return bool(semantic.get("verifier_rule") or semantic.get("violated_invariant") or evidence.get("invariant_ref") or evidence.get("invariant_evidence_ref"))

def _reproducible(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    reproduction = _dict(finding.get("reproduction"))
    if reproduction.get("flow_id") or reproduction.get("steps") or reproduction.get("required_inputs") or finding.get("reproduction_steps") or finding.get("is_reproducible"): return True
    return bool(evidence.get("reproduction_flow_ref") or evidence.get("reproduction"))

def _before_after(finding: dict[str, Any], evidence: dict[str, Any]) -> tuple[bool, bool]:
    before = bool(finding.get("before_snapshot_ref") or evidence.get("before_snapshot_ref") or evidence.get("before_snapshot"))
    after = bool(finding.get("after_snapshot_ref") or evidence.get("after_snapshot_ref") or evidence.get("after_snapshot"))
    package = _dict(finding.get("evidence_package")); chain = _dict(package.get("evidence_chain"))
    return before or int(chain.get("before_snapshot_count") or 0) > 0, after or int(chain.get("after_snapshot_count") or 0) > 0

def _cleanup_ok(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    cleanup = _dict(finding.get("cleanup")) or _dict(evidence.get("cleanup"))
    status = _text(cleanup.get("status") or evidence.get("cleanup_status")).lower()
    return status in {"completed", "verified", "not_applicable", "not applicable"}

def _semantic_confirmed(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    verification = _dict(finding.get("verification"))
    semantic = _text(finding.get("semantic_verdict") or evidence.get("semantic_verdict") or _dict(finding.get("semantic")).get("semantic_verdict") or verification.get("verdict") or finding.get("verdict")).upper()
    return semantic in {"SEMANTIC_CONFIRMED", "CONFIRMED", "VALIDATED_CANDIDATE", "VALIDATED_BUG", "VALIDATED"}

def _reviewed(finding: dict[str, Any], evidence: dict[str, Any]) -> bool:
    final = _text(finding.get("final_review_status") or evidence.get("final_review_status")).upper()
    verification = _dict(finding.get("verification"))
    verdict = _text(finding.get("verdict") or verification.get("verdict")).upper()
    return final in {"PENDING_REVIEW", "CONFIRMED_BY_HUMAN"} or verdict in {"VALIDATED_CANDIDATE", "VALIDATED_BUG", "CONFIRMED"}

def _family(finding: dict[str, Any]) -> str:
    triage = _dict(finding.get("customer_triage"))
    return _text(triage.get("risk_family") or finding.get("defect_family") or finding.get("risk_type")) or "unknown"


def _proof(finding: dict[str, Any]) -> dict[str, Any]:
    evidence = _evidence(finding); method = _method(finding, evidence); write = method in _WRITE
    before, after = _before_after(finding, evidence); gaps: list[str] = []
    if not _source_grounded(finding, evidence): gaps.append("SOURCE_GROUNDING_MISSING")
    if not _executed(finding, evidence): gaps.append("EXECUTION_RECEIPT_MISSING")
    if not _asserted(finding, evidence): gaps.append("FAILED_ASSERTION_MISSING")
    if not _reproducible(finding, evidence): gaps.append("REPRODUCTION_MISSING")
    if write and not before: gaps.append("BEFORE_SNAPSHOT_MISSING")
    if write and not after: gaps.append("AFTER_SNAPSHOT_MISSING")
    if write and not _cleanup_ok(finding, evidence): gaps.append("CLEANUP_RECEIPT_MISSING")
    if not _semantic_confirmed(finding, evidence): gaps.append("SEMANTIC_VERDICT_NOT_CONFIRMED")
    if not _reviewed(finding, evidence): gaps.append("FINAL_GATE_NOT_PASSED")
    lineage = {"finding_id": finding.get("finding_id"), "method": method, "path": finding.get("path") or finding.get("_api_path"), "sources": finding.get("source_refs") or evidence.get("source_refs") or evidence.get("obligation_id"), "receipts": evidence.get("calls") or finding.get("raw_probes"), "assertion": finding.get("expected") or finding.get("failed_assertions") or evidence.get("invariant_ref")}
    return {"proof_status": "customer_ready" if not gaps else ("needs_more_evidence" if _executed(finding, evidence) else "not_executed"), "evidence_gaps": gaps, "method": method, "is_write": write, "has_before_snapshot": before, "has_after_snapshot": after, "has_before_after": before and after, "has_source_grounding": _source_grounded(finding, evidence), "has_execution_receipt": _executed(finding, evidence), "has_assertion": _asserted(finding, evidence), "has_reproduction": _reproducible(finding, evidence), "lineage_digest": hashlib.sha256(json.dumps(lineage, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()}


def _item(finding: dict[str, Any]) -> dict[str, Any]:
    proof = _proof(finding); invariants = [_text(item.get("kind")) for item in _rows(finding.get("violated_invariants")) if _text(item.get("kind"))][:8]
    return {"finding_id": finding.get("finding_id"), "priority": finding.get("priority"), "severity": finding.get("severity"), "risk_type": finding.get("risk_type"), "endpoint": f"{_text(finding.get('method'))} {_text(finding.get('path'))}".strip(), "evidence_grade": finding.get("evidence_grade"), "evidence_strength_score": finding.get("evidence_strength_score"), "customer_impact_summary": finding.get("customer_impact_summary"), "violated_invariant_kinds": invariants, "proof_status": proof["proof_status"], "evidence_gaps": proof["evidence_gaps"], "lineage_digest": proof["lineage_digest"], "proof_receipt": {"source_grounded": proof["has_source_grounding"], "executed": proof["has_execution_receipt"], "asserted": proof["has_assertion"], "reproducible": proof["has_reproduction"], "before_after": proof["has_before_after"]}}


def _coverage(findings: Iterable[dict[str, Any]]) -> dict[str, Any]:
    proof_counts: dict[str, int] = {}; observers: set[str] = set(); before_after = semantic = cross_observer = 0
    for finding in findings:
        proof = _proof(finding); status = proof["proof_status"]; proof_counts[status] = proof_counts.get(status, 0) + 1
        if proof["has_before_after"]: before_after += 1
        package = _dict(finding.get("evidence_package")); chain = _dict(package.get("evidence_chain")); delta = _dict(package.get("delta_summary"))
        if _dict(delta.get("semantic_graph")).get("present"): semantic += 1
        if delta.get("cross_observer_failures"): cross_observer += 1
        for value in chain.get("observer_kinds") or []:
            if _text(value): observers.add(_text(value))
        for value in _rows(_evidence(finding).get("observer_refs")):
            if _text(value.get("observer")): observers.add(_text(value.get("observer")))
    return {"before_after_snapshot_finding_count": before_after, "semantic_graph_finding_count": semantic, "cross_observer_failure_finding_count": cross_observer, "observer_kinds": sorted(observers), "proof_status_distribution": dict(sorted(proof_counts.items()))}


def build_customer_delivery_index(findings: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted([item for item in findings if isinstance(item, dict)], key=_sort)
    ready = [item for item in ordered if _proof(item)["proof_status"] == "customer_ready"]
    top = [_item(item) for item in ordered[:25]]
    invariant_counts: dict[str, int] = {}
    for item in ready:
        for name in _item(item)["violated_invariant_kinds"]: invariant_counts[name] = invariant_counts.get(name, 0) + 1
    return {"engine": "runtime_customer_report_builder_v3_phase108", "customer_delivery_ready": bool(ready), "input_finding_count": len(ordered), "validated_finding_count": len(ready), "customer_ready_finding_count": len(ready), "internal_validation_lead_count": sum(1 for item in ordered if _proof(item)["proof_status"] == "needs_more_evidence"), "unexecuted_or_untraceable_count": sum(1 for item in ordered if _proof(item)["proof_status"] == "not_executed"), "by_priority": _count(ready, "priority"), "by_severity": _count(ready, "severity"), "by_risk_family": dict(sorted((_family(item), sum(1 for row in ready if _family(row) == _family(item))) for item in ready)), "top_violated_invariant_kinds": dict(sorted(invariant_counts.items(), key=lambda row: (-row[1], row[0]))[:20]), "evidence_coverage": _coverage(ordered), "top_customer_actions": top, "customer_ready_findings": [_item(item) for item in ready[:25]], "recommended_report_usage": "Only customer_ready findings may be presented as customer defects. needs_more_evidence items are internal validation leads and must retain their explicit proof gaps."}


def build_customer_regression_verification_report(
    project_id: str,
    findings: list[dict[str, Any]],
    *,
    root: str | None = None,
    regression_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """P5: Build a customer-readable regression verification report.

    Combines:
      - Regression suite execution results (passed/failed per probe)
      - Lifecycle regression detection (bugs that came back)
      - Stability declaration (≥2 runs all green)
      - Per-defect fix verification status

    Returns a structured dict suitable for rendering as HTML/PDF customer report.
    """
    from pathlib import Path as _Path
    root_path = _Path(root or "platform_outputs")

    # ── Load regression stability ──
    stability: dict[str, Any] = {}
    try:
        from .regression_runner import evaluate_regression_stability
        stability = evaluate_regression_stability(project_id, root_path)
    except Exception:
        stability = {"stable": False, "reason": "stability_check_unavailable"}

    # ── Load regression history ──
    history: list[dict[str, Any]] = []
    try:
        import json as _json
        hist_path = root_path / "platform_outputs" / project_id / "regression_run" / "regression_run_history.json"
        if hist_path.exists():
            raw = _json.loads(hist_path.read_text(encoding="utf-8", errors="replace") or "[]")
            if isinstance(raw, list):
                history = raw
    except Exception:
        pass

    # ── Per-defect fix verification ──
    defect_verifications: list[dict[str, Any]] = []
    for finding in (findings or []):
        if not isinstance(finding, dict):
            continue
        finding_id = finding.get("finding_id") or finding.get("issue_id") or ""
        defect_verifications.append({
            "finding_id": finding_id,
            "title": str(finding.get("title") or "")[:200],
            "severity": str(finding.get("severity") or "P2"),
            "risk_type": str(finding.get("risk_type") or ""),
            "fix_status": _resolve_fix_status(finding, history),
            "last_regression_run": _last_run_summary(history),
            "reproduction_assets": _repro_asset_summary(finding),
            "evidence_chain_available": bool(finding.get("evidence_package") or finding.get("evidence")),
        })

    return {
        "engine": "runtime_customer_regression_verification_v1_phase92",
        "project_id": project_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "regression_stability": stability,
        "regression_run_count": len(history),
        "last_regression_run": _last_run_summary(history),
        "defect_verifications": defect_verifications,
        "summary": {
            "total_defects_tracked": len(defect_verifications),
            "defects_verified_fixed": sum(1 for d in defect_verifications if d.get("fix_status") == "verified_fixed"),
            "defects_still_present": sum(1 for d in defect_verifications if d.get("fix_status") == "still_present"),
            "defects_unknown": sum(1 for d in defect_verifications if d.get("fix_status") == "unknown"),
        },
        "recommended_actions": (
            ["所有已追踪缺陷已通过回归验证，系统可安全发布。"]
            if stability.get("stable")
            else ["存在未通过回归验证的缺陷或回归不稳定，建议修复后再发布。"]
        ),
    }


def _resolve_fix_status(finding: dict[str, Any], history: list[dict[str, Any]]) -> str:
    """Determine fix verification status from regression history."""
    finding_id = _text(finding.get("finding_id") or finding.get("issue_id"))
    if not finding_id or not history:
        return "unknown"
    # Check last 2 runs for this finding
    for run in reversed(history[-2:]):
        for item in run.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("issue_id") or item.get("regression_probe_id") or "")
            if item_id == finding_id:
                status = str(item.get("status") or "")
                if status == "passed":
                    return "verified_fixed"
                if status == "failed":
                    return "still_present"
    return "unknown"


def _last_run_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history:
        return {"available": False}
    last = history[-1]
    summary = last.get("summary", {})
    return {
        "available": True,
        "generated_at": last.get("generated_at"),
        "gate_status": last.get("gate_status"),
        "passed": summary.get("passed_count", 0),
        "failed": summary.get("failed_count", 0),
        "needs_review": summary.get("needs_review_count", 0),
    }


def _repro_asset_summary(finding: dict[str, Any]) -> dict[str, Any]:
    repro = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
    package = finding.get("evidence_package") if isinstance(finding.get("evidence_package"), dict) else {}
    assets = package.get("reproduction_assets") if isinstance(package.get("reproduction_assets"), dict) else {}
    return {
        "has_repro_steps": bool(repro.get("steps") or finding.get("reproduction_steps")),
        "has_har_evidence": bool(finding.get("har_evidence")),
        "has_artifact_link": bool(assets.get("artifact_links")),
        "artifact_count": len(assets.get("artifact_links") or []),
    }
