"""Receipt-driven business evidence enrichment.

This existing bridge turns normalized execution receipts into the typed business
contract.  It never invents a tenant, actor, entity, snapshot, invariant,
cleanup or reproduction step when the runtime receipt did not observe one.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .evidence_models import BusinessEvidenceDraft, MISSING_REQUIREMENTS

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sourced(runtime: dict[str, Any], name: str) -> dict[str, Any]:
    value = runtime.get(name)
    return value if isinstance(value, dict) else {}


def _observed(value: dict[str, Any]) -> Any:
    return value.get("value") if value and value.get("confidence") != "missing" else None


def _add_missing(draft: BusinessEvidenceDraft, value: str) -> None:
    if value in MISSING_REQUIREMENTS and value not in draft.missing_requirements:
        draft.missing_requirements.append(value)


def _snapshot_ref(snapshot: Any) -> str:
    if snapshot in (None, "", [], {}):
        return ""
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return "snap:" + hashlib.sha256(raw).hexdigest()[:20]


def _finding_value(finding: Any, key: str) -> Any:
    return finding.get(key) if isinstance(finding, dict) else getattr(finding, key, None)


class BusinessEvidenceEnricher:
    def _compute_business_evidence_status(self, draft: BusinessEvidenceDraft, semantic_verdict: str) -> str:
        missing = set(draft.missing_requirements)
        if not missing:
            return "VALIDATED"
        if "ENTITY_BINDING_MISSING" in missing:
            return "PENDING_ENTITY_BINDING"
        if "BEFORE_SNAPSHOT_MISSING" in missing:
            return "PENDING_BEFORE_SNAPSHOT"
        if "AFTER_SNAPSHOT_MISSING" in missing:
            return "PENDING_AFTER_SNAPSHOT"
        if "CLEANUP_PENDING" in missing or "CLEANUP_FAILED" in missing:
            return "PENDING_CLEANUP_EVIDENCE"
        if "OBSERVER_CONFLICT" in missing:
            return "PENDING_OBSERVER_CONSENSUS"
        if "ASYNC_WINDOW_OPEN" in missing:
            return "PENDING_ASYNC_WINDOW"
        if "REPRODUCTION_INCOMPLETE" in missing:
            return "PENDING_REPRODUCTION"
        if "INVARIANT_REF_MISSING" in missing:
            return "PENDING_INVARIANT"
        return "PENDING_ENTITY_BINDING"

    def _compute_final_review_status(self, business_status: str, semantic_verdict: str) -> str:
        return "VALIDATED_CANDIDATE" if business_status == "VALIDATED" else "NEEDS_MORE_EVIDENCE"

    def enrich(self, normalized: dict[str, Any], semantic: dict[str, Any], finding: Any,
               calls: list[dict] | None = None, project_id: str = "", policy_version: str = "baseline") -> BusinessEvidenceDraft:
        normalized = _dict(normalized)
        runtime = _dict(normalized.get("runtime")) or normalized
        semantic = _dict(semantic) or _dict(normalized.get("semantic"))
        draft = BusinessEvidenceDraft(
            project_id=project_id or _text(_finding_value(finding, "project_id")),
            environment_id=_text(_finding_value(finding, "environment_id")),
            run_id=_text(_finding_value(finding, "run_id")),
            hypothesis_id=_text(_finding_value(finding, "hypothesis_id")),
            flow_id=_text(_finding_value(finding, "flow_id")),
            policy_version=policy_version,
        )
        entity_id, entity_type = _sourced(runtime, "entity_id"), _sourced(runtime, "entity_type")
        if _observed(entity_id) not in (None, ""):
            draft.entity_binding = {
                "entity_id": _observed(entity_id), "entity_type": _observed(entity_type) or "",
                "source": entity_id.get("source", ""), "confidence": entity_id.get("confidence", "evidenced"),
            }
        else:
            draft.entity_binding = {"entity_id": "", "entity_type": _observed(entity_type) or "", "source": entity_id.get("source", "entity_not_observed"), "confidence": "missing"}
            _add_missing(draft, "ENTITY_BINDING_MISSING")
        tenant, owner, correlation = _sourced(runtime, "tenant_id"), _sourced(runtime, "owner_id"), _sourced(runtime, "correlation_id")
        draft.tenant_binding = {"tenant_id": _observed(tenant) or "", "source": tenant.get("source", "scope_not_observed"), "confidence": tenant.get("confidence", "missing")}
        draft.owner_binding = {"owner_id": _observed(owner) or "", "source": owner.get("source", "owner_not_observed"), "confidence": owner.get("confidence", "missing")}
        draft.correlation_binding = {"correlation_id": _observed(correlation) or "", "source": correlation.get("source", "correlation_not_observed"), "confidence": correlation.get("confidence", "missing")}

        action = _sourced(runtime, "action_ref")
        draft.action_evidence_ref = _text(_observed(action))
        before = next((item for item in runtime.get("before_candidates") or [] if isinstance(item, dict) and item.get("body_snapshot") not in (None, "", [], {})), None)
        after = next((item for item in runtime.get("after_candidates") or [] if isinstance(item, dict) and item.get("body_snapshot") not in (None, "", [], {})), None)
        if before:
            draft.before_snapshot_data = _dict(before.get("body_snapshot"))
            draft.before_snapshot_ref = _snapshot_ref(draft.before_snapshot_data)
        if after:
            draft.after_snapshot_data = _dict(after.get("body_snapshot"))
            draft.after_snapshot_ref = _snapshot_ref(draft.after_snapshot_data)
        method = _text(_observed(_sourced(runtime, "method"))).upper()
        if method in _WRITE_METHODS:
            if not draft.before_snapshot_ref:
                _add_missing(draft, "BEFORE_SNAPSHOT_MISSING")
            if not draft.after_snapshot_ref:
                _add_missing(draft, "AFTER_SNAPSHOT_MISSING")
            raw_evidence = _dict(_finding_value(finding, "evidence"))
            cleanup = _dict(raw_evidence.get("cleanup"))
            cleanup_status = _text(cleanup.get("status")).lower()
            if cleanup_status in {"completed", "verified"}:
                draft.cleanup_status = "COMPLETED"
                draft.cleanup_evidence_ref = _text(cleanup.get("receipt_ref") or cleanup.get("evidence_ref"))
            else:
                draft.cleanup_status = "PENDING"
                _add_missing(draft, "CLEANUP_PENDING")
        else:
            draft.cleanup_status = "NOT_APPLICABLE"

        draft.observer_refs = [json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)[:500] for item in runtime.get("observer_refs") or [] if isinstance(item, dict)]
        draft.runtime_evidence_refs = [_text(value) for value in runtime.get("call_chain_refs") or runtime.get("raw_evidence_refs") or [] if _text(value)]
        if draft.runtime_evidence_refs:
            draft.reproduction_flow_ref = "receipt_chain::" + " -> ".join(draft.runtime_evidence_refs[:12])
        else:
            _add_missing(draft, "REPRODUCTION_INCOMPLETE")
        draft.invariant_ref = _text(semantic.get("violated_invariant") or semantic.get("verifier_rule"))
        if not draft.invariant_ref:
            _add_missing(draft, "INVARIANT_REF_MISSING")
        draft.semantic_evidence_ref = _text(semantic.get("verifier_trace_ref"))
        title = _text(_finding_value(finding, "title"))
        severity = _text(_finding_value(finding, "severity")) or "P2"
        draft.impact_assessment = f"[{severity}] {title}"[:300]
        if not title:
            _add_missing(draft, "IMPACT_ASSESSMENT_MISSING")
        for item in runtime.get("missing_requirements") or []:
            _add_missing(draft, _text(item))
        draft._fabricated = False
        return draft


def enrich_finding_evidence(finding: Any, calls: list[dict] | None = None,
                             normalized: dict[str, Any] | None = None,
                             semantic: dict[str, Any] | None = None,
                             project_id: str = "", policy_version: str = "baseline") -> dict[str, Any]:
    enricher = BusinessEvidenceEnricher()
    normalized, semantic = normalized or {}, semantic or {}
    draft = enricher.enrich(normalized, semantic, finding, calls, project_id, policy_version)
    raw = _text(_finding_value(finding, "verdict")) or "inconclusive"
    semantic_verdict = _text(semantic.get("semantic_verdict") or _dict(normalized.get("semantic")).get("semantic_verdict")) or "SEMANTIC_INCONCLUSIVE"
    business_status = enricher._compute_business_evidence_status(draft, semantic_verdict)
    result = draft.to_dict()
    result.update({
        "raw_runtime_verdict": _text(semantic.get("_original_verdict")) or raw,
        "semantic_verdict": semantic_verdict,
        "business_evidence_status": business_status,
        "final_review_status": enricher._compute_final_review_status(business_status, semantic_verdict),
    })
    result["compound_status"] = "VALIDATED_CANDIDATE" if semantic_verdict == "SEMANTIC_CONFIRMED" and business_status == "VALIDATED" else ("SEMANTIC_CONFIRMED_PENDING_EVIDENCE" if semantic_verdict == "SEMANTIC_CONFIRMED" else result["final_review_status"])
    return result
