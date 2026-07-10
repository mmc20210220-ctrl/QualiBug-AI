"""Mandatory receipt-backed gate for DiscoveryEngine findings.

The gate is the existing promotion point between discovery and customer-facing
results. It consumes the normalized receipt and business evidence contract; it
does not re-parse calls with default admin, tenant, role, or call-order rules.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

from .business_evidence_enricher import enrich_finding_evidence
from .evidence_normalizer import normalize_finding_evidence

GATED_VERDICTS = {
    "VALIDATED_CANDIDATE": "validated_candidate",
    "NEEDS_MORE_EVIDENCE": "needs_more_evidence",
    "BLOCKED_BY_RUNTIME_EVIDENCE": "blocked_by_runtime_evidence",
    "BLOCKED_BY_SAFETY": "blocked_by_safety",
    "REJECTED": "rejected",
}

_VERDICT_TO_SEMANTIC = {
    "confirmed": "SEMANTIC_CONFIRMED",
    "validated_candidate": "SEMANTIC_CONFIRMED",
    "falsified": "SEMANTIC_FALSIFIED",
    "rejected": "SEMANTIC_FALSIFIED",
    "execution_error": "SEMANTIC_ERROR",
    "needs_more_evidence": "SEMANTIC_PENDING",
    "inconclusive": "SEMANTIC_INCONCLUSIVE",
}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _fields(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        return asdict(item)
    return {
        "hypothesis_id": getattr(item, "hypothesis_id", ""),
        "title": getattr(item, "title", ""),
        "severity": getattr(item, "severity", "P2"),
        "verdict": getattr(item, "verdict", "inconclusive"),
        "expected": getattr(item, "expected", ""),
        "actual": getattr(item, "actual", ""),
        "evidence": getattr(item, "evidence", {}) or {},
        "confidence": getattr(item, "confidence", 0.0),
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "")[:limit]


def _calls(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (evidence.get("calls") or []) if isinstance(item, dict)]


def _method(runtime: dict[str, Any]) -> str:
    """Use the observed mutation receipt when a flow begins with a read.

    A common proof shape is GET(before) -> PATCH(action) -> GET(after). The
    resource method alone therefore cannot decide whether before/after and
    cleanup evidence are mandatory.
    """
    action = _dict(runtime.get("action_ref"))
    action_text = _text(action.get("value"))
    action_method = action_text.split(None, 1)[0].upper() if action_text else ""
    if action_method in _WRITE_METHODS:
        return action_method
    field = _dict(runtime.get("method"))
    return _text(field.get("value")).upper()


def _has_real_result(call: dict[str, Any]) -> bool:
    results = _dict(call.get("results"))
    if any(isinstance(row, dict) and ("status" in row or "status_code" in row or "body" in row) for row in results.values()):
        return True
    response = _dict(call.get("response"))
    return bool(response and ("status" in response or "status_code" in response or "body" in response))


def _source_refs(row: dict[str, Any], evidence: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    for candidate in (
        row.get("source_refs"), row.get("document_refs"), row.get("evidence_refs"),
        evidence.get("source_refs"), evidence.get("document_refs"),
        evidence.get("obligation_id"), row.get("context_artifact_id"),
    ):
        if isinstance(candidate, list):
            values.extend(candidate)
        elif candidate:
            values.append(candidate)
    return values


class RuntimeEvidenceGate:
    """Validate raw receipt traceability without assigning business meaning."""

    @staticmethod
    def check(contract: dict[str, Any]) -> tuple[str, list[str]]:
        evidence = _dict(contract.get("evidence"))
        calls = _calls(evidence)
        refs = evidence.get("raw_evidence_refs") or evidence.get("call_chain_refs") or []
        reasons: list[str] = []
        if not calls and not refs:
            return "FAILED_MISSING_CALLS", ["RUNTIME_RECEIPT_MISSING"]
        if calls and not any(_has_real_result(call) for call in calls):
            reasons.append("RUNTIME_RESPONSE_UNTRACEABLE")
        if calls and all("QUALIBUG_UNRESOLVED_ID" in _text(call.get("call")) for call in calls):
            reasons.append("UNRESOLVED_RUNTIME_BINDING")
        if not _text(evidence.get("semantic_evidence_ref")) and not _text(contract.get("semantic_verdict")):
            reasons.append("SEMANTIC_TRACE_MISSING")
        return ("PASSED", []) if not reasons else ("FAILED_UNTRACEABLE", reasons)


class BusinessEvidenceGate:
    """Validate whether a receipt-backed finding may enter human review."""

    @staticmethod
    def check(contract: dict[str, Any], semantic_verdict: str = "") -> tuple[str, list[str]]:
        evidence = _dict(contract.get("evidence"))
        method = _method(_dict(evidence.get("normalized_runtime"))) or _text(evidence.get("method")).upper()
        binding = _dict(evidence.get("entity_binding"))
        missing: list[str] = []
        if method in _WRITE_METHODS and not _text(binding.get("entity_id")):
            missing.append("ENTITY_BINDING_MISSING")
        if not method and not _text(binding.get("entity_type")):
            missing.append("RESOURCE_BINDING_MISSING")
        if method in _WRITE_METHODS and not _text(evidence.get("before_snapshot_ref")):
            missing.append("BEFORE_SNAPSHOT_MISSING")
        if method in _WRITE_METHODS and not _text(evidence.get("after_snapshot_ref")):
            missing.append("AFTER_SNAPSHOT_MISSING")
        cleanup = _dict(evidence.get("cleanup"))
        cleanup_status = _text(cleanup.get("status")).lower()
        # Prefer enriched top-level cleanup when present (NOT_APPLICABLE for
        # action-style writes that honestly cannot be rolled back).
        if not cleanup_status:
            cleanup_status = _text(contract.get("cleanup", {}).get("status") if isinstance(contract.get("cleanup"), dict) else "").lower()
        if not cleanup_status:
            cleanup_status = _text(evidence.get("cleanup_status")).lower()
        if method in _WRITE_METHODS and cleanup_status not in {
            "completed", "verified", "not_reversible", "not_applicable", "n/a", "not_required",
        }:
            missing.append("CLEANUP_PENDING")
        if not _text(evidence.get("invariant_ref")):
            missing.append("INVARIANT_REF_MISSING")
        if not _text(evidence.get("reproduction_flow_ref")):
            missing.append("REPRODUCTION_INCOMPLETE")
        if not _source_refs(contract, evidence):
            missing.append("SOURCE_GROUNDING_MISSING")
        if semantic_verdict != "SEMANTIC_CONFIRMED":
            missing.append("SEMANTIC_VERDICT_NOT_CONFIRMED")
        return ("PASSED", []) if not missing else ("PENDING", missing)


def discovery_finding_to_contract(item: Any, *, project_id: str, policy_version: str, context_artifact_id: str) -> dict[str, Any]:
    row = _fields(item)
    raw_evidence = _dict(row.get("evidence"))
    calls = _calls(raw_evidence)
    bundle = normalize_finding_evidence(row, calls)
    runtime = _dict(bundle.get("runtime"))
    semantic = _dict(bundle.get("semantic"))
    enriched = enrich_finding_evidence(
        row, calls=calls, normalized=runtime, semantic=semantic,
        project_id=project_id, policy_version=policy_version,
    )
    hypothesis_id = _text(row.get("hypothesis_id"), 160)
    digest = hashlib.sha256(json.dumps({"project": project_id, "hypothesis": hypothesis_id, "title": row.get("title", "")}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    evidence = {
        **raw_evidence,
        **enriched,
        "calls": calls,
        "raw_evidence_refs": runtime.get("raw_evidence_refs") or [],
        "call_chain_refs": runtime.get("call_chain_refs") or [],
        "normalized_runtime": runtime,
        "semantic_evidence_ref": enriched.get("semantic_evidence_ref") or semantic.get("verifier_trace_ref") or "",
        "source_refs": _source_refs(row, raw_evidence),
    }
    enriched_cleanup_status = _text(enriched.get("cleanup_status")).lower()
    if enriched_cleanup_status in {"completed", "verified", "not_applicable", "n/a", "not_reversible"}:
        raw_cleanup = _dict(raw_evidence.get("cleanup"))
        evidence["cleanup"] = {
            "status": (
                "not_applicable"
                if enriched_cleanup_status in {"not_applicable", "n/a"}
                else enriched_cleanup_status
            ),
            "receipt_ref": _text(
                enriched.get("cleanup_evidence_ref")
                or raw_cleanup.get("receipt_ref")
                or raw_cleanup.get("evidence_ref")
            ),
        }
    return {
        "finding_id": f"DISC_{digest}",
        "hypothesis_id": hypothesis_id,
        "project_id": project_id,
        "policy_version": policy_version,
        "context_artifact_id": context_artifact_id,
        "title": _text(row.get("title"), 300),
        "severity": _text(row.get("severity"), 32) or "P2",
        "expected": _text(row.get("expected") or row.get("expected_behavior"), 800),
        "actual": _text(row.get("actual") or row.get("actual_behavior"), 800),
        "confidence": row.get("confidence", 0.0),
        "raw_runtime_verdict": enriched.get("raw_runtime_verdict") or _text(row.get("verdict")) or "inconclusive",
        "semantic_verdict": enriched.get("semantic_verdict") or _VERDICT_TO_SEMANTIC.get(_text(row.get("verdict")).lower(), "SEMANTIC_INCONCLUSIVE"),
        "business_evidence_status": enriched.get("business_evidence_status", "NOT_ENRICHED"),
        "final_review_status": enriched.get("final_review_status", "NEEDS_MORE_EVIDENCE"),
        "entity_binding": enriched.get("entity_binding", {}),
        "before_snapshot_ref": enriched.get("before_snapshot_ref", ""),
        "action_evidence_ref": enriched.get("action_evidence_ref", ""),
        "after_snapshot_ref": enriched.get("after_snapshot_ref", ""),
        "observer_refs": enriched.get("observer_refs", []),
        "violated_invariant": {"definition": enriched.get("invariant_ref", ""), "evidence_ref": enriched.get("semantic_evidence_ref", "")},
        "reproduction": {"flow_id": enriched.get("reproduction_flow_ref", ""), "required_inputs": runtime.get("call_chain_refs") or []},
        "cleanup": {"status": enriched.get("cleanup_status", "NOT_APPLICABLE"), "evidence_ref": enriched.get("cleanup_evidence_ref", "")},
        "evidence": evidence,
        "calls": calls,
    }


def _pending_status(missing: list[str]) -> str:
    values = set(missing)
    if "ENTITY_BINDING_MISSING" in values or "RESOURCE_BINDING_MISSING" in values:
        return "PENDING_ENTITY_BINDING"
    if "BEFORE_SNAPSHOT_MISSING" in values:
        return "PENDING_BEFORE_SNAPSHOT"
    if "AFTER_SNAPSHOT_MISSING" in values:
        return "PENDING_AFTER_SNAPSHOT"
    if "CLEANUP_PENDING" in values:
        return "PENDING_CLEANUP_EVIDENCE"
    if "REPRODUCTION_INCOMPLETE" in values:
        return "PENDING_REPRODUCTION"
    if "INVARIANT_REF_MISSING" in values:
        return "PENDING_INVARIANT"
    return "PENDING_ENTITY_BINDING"


def gate_discovery_findings(findings: Iterable[Any], *, project_id: str | None = None, policy_version: str = "", context_artifact_id: str = "", enable_llm_disprover: bool | None = None, write_human_review_ledger: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved_project = project_id or os.environ.get("QUALIBUG_PROJECT", "real_project_demo")
    contracts = [
        discovery_finding_to_contract(item, project_id=resolved_project,
                                      policy_version=policy_version,
                                      context_artifact_id=context_artifact_id)
        for item in findings
    ]
    runtime_gate, business_gate = RuntimeEvidenceGate(), BusinessEvidenceGate()
    summary = {
        "input_count": len(contracts), "validated_candidate_count": 0,
        "needs_more_evidence_count": 0, "blocked_runtime_count": 0,
        "by_missing_requirement": {},
    }
    for contract in contracts:
        runtime_status, runtime_reasons = runtime_gate.check(contract)
        contract["runtime_gate_status"] = runtime_status
        if runtime_status != "PASSED":
            contract.update({
                "verdict": "NEEDS_MORE_EVIDENCE",
                "business_evidence_status": "BLOCKED_BY_RUNTIME_EVIDENCE",
                "final_review_status": "BLOCKED",
                "business_gate_status": "NOT_RUN",
                "business_gate_missing": runtime_reasons,
            })
            summary["blocked_runtime_count"] += 1
            continue
        business_status, missing = business_gate.check(contract, contract.get("semantic_verdict", ""))
        contract["business_gate_status"] = business_status
        contract["business_gate_missing"] = missing
        if business_status == "PASSED":
            contract.update({
                "verdict": "VALIDATED_CANDIDATE",
                "business_evidence_status": "VALIDATED",
                "final_review_status": "PENDING_REVIEW",
            })
            summary["validated_candidate_count"] += 1
        else:
            contract.update({
                "verdict": "NEEDS_MORE_EVIDENCE",
                "business_evidence_status": _pending_status(missing),
                "final_review_status": "NEEDS_MORE_EVIDENCE",
            })
            summary["needs_more_evidence_count"] += 1
            for reason in missing:
                summary["by_missing_requirement"][reason] = summary["by_missing_requirement"].get(reason, 0) + 1
    summary["by_missing_requirement"] = dict(sorted(summary["by_missing_requirement"].items(), key=lambda item: (-item[1], item[0])))
    summary["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary["engine"] = "discovery_finding_gate_v2_phase108"
    return contracts, summary
