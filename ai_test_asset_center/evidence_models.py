"""Phase92A · Evidence Models — Strong-typed evidence contract models.

Defines the canonical evidence schema for the Runtime Probe → Business Contract bridge.
All findings MUST pass through these typed models before entering gates.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any


# ── Phase92A: Four-layer state constants ─────────────────────────────────────
RAW_RUNTIME_VERDICTS = {
    "confirmed", "falsified", "inconclusive", "execution_error",
    "duplicate_mutation_observed", "auth_bypass_observed",
    "state_change_observed", "cross_view_mismatch", "needs_more_evidence",
}

SEMANTIC_VERDICTS = {
    "SEMANTIC_CONFIRMED", "SEMANTIC_FALSIFIED", "SEMANTIC_INCONCLUSIVE",
    "SEMANTIC_ERROR", "SEMANTIC_PENDING",
}

BUSINESS_EVIDENCE_STATUS = {
    "VALIDATED", "PENDING_ENTITY_BINDING", "PENDING_BEFORE_SNAPSHOT",
    "PENDING_AFTER_SNAPSHOT", "PENDING_CLEANUP_EVIDENCE",
    "PENDING_OBSERVER_CONSENSUS", "PENDING_ASYNC_WINDOW",
    "PENDING_REPRODUCTION", "PENDING_INVARIANT", "PENDING_IMPACT",
    "GATE_ERROR", "LEGACY_EVIDENCE_INCOMPLETE", "NOT_ENRICHED",
    "BLOCKED_BY_RUNTIME_EVIDENCE", "BLOCKED_BY_SAFETY",
}

FINAL_REVIEW_STATUS = {
    "NEEDS_MORE_EVIDENCE", "PENDING_REVIEW", "VALIDATED_CANDIDATE",
    "REJECTED_BY_COUNTERFACTUAL", "REJECTED_BY_SCHEMA",
    "CONFIRMED_BY_HUMAN", "BLOCKED",
}


# ── Value with source tracking (from evidence_normalizer.py) ─────────────────
@dataclass
class SourcedValue:
    """A value with explicit source trace for auditability."""
    value: Any
    source: str
    confidence: str  # evidenced | inferred | ambiguous | missing

    @staticmethod
    def missing(source: str = "") -> "SourcedValue":
        return SourcedValue(None, source, "missing")

    @staticmethod
    def from_dict(d: dict, key: str, source_prefix: str) -> "SourcedValue":
        if not isinstance(d, dict):
            return SourcedValue.missing(source_prefix)
        v = d.get(key)
        if v is None:
            return SourcedValue.missing(f"{source_prefix}.{key}")
        return SourcedValue(v, f"{source_prefix}.{key}", "evidenced")

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "source": self.source, "confidence": self.confidence}


# ── 2.1 RawProbeEvidence ──────────────────────────────────────────────────────
@dataclass
class RawProbeEvidence:
    """Phase92A §2.1: Original runtime probe evidence from Discovery Engine.

    Preserves raw execution facts without interpretation.
    MUST NOT be overwritten or lost.
    """
    run_id: str = ""
    hypothesis_id: str = ""
    flow_id: str = ""
    probe_id: str = ""
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    status_code: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    duration_ms: float = 0.0
    error: str = ""
    raw_call_refs: list[str] = field(default_factory=list)
    _preserved: bool = True  # Safety flag: must never be dropped

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "hypothesis_id": self.hypothesis_id,
            "flow_id": self.flow_id,
            "probe_id": self.probe_id,
            "request": self.request,
            "response": self.response,
            "status_code": self.status_code,
            "headers": self.headers,
            "body": self.body,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "raw_call_refs": self.raw_call_refs,
            "_preserved": self._preserved,
        }

    @staticmethod
    def from_call(call: dict[str, Any], run_id: str = "", hypothesis_id: str = "") -> "RawProbeEvidence":
        """Create RawProbeEvidence from a single discovery engine call."""
        results = call.get("results", {})
        admin_result = results.get("admin", {})
        return RawProbeEvidence(
            run_id=run_id,
            hypothesis_id=hypothesis_id,
            probe_id=call.get("call", "").replace(" ", "_").replace("/", "_")[:40],
            request={"call": call.get("call", "")},
            response=admin_result.get("body", {}),
            status_code=admin_result.get("status", 0),
            headers={},
            body=admin_result.get("body", {}),
            timestamp=time.time(),
            duration_ms=0.0,
            error=admin_result.get("_error", ""),
            raw_call_refs=[call.get("call", "")],
        )


# ── 2.2 NormalizedRuntimeEvidence ────────────────────────────────────────────
@dataclass
class NormalizedRuntimeEvidence:
    """Phase92A §2.2: Normalized runtime evidence with explicit field sources.

    Extracts traceable IDs and references from raw probe calls.
    """
    request_id: SourcedValue = field(default_factory=SourcedValue.missing)
    trace_id: SourcedValue = field(default_factory=SourcedValue.missing)
    correlation_id: SourcedValue = field(default_factory=SourcedValue.missing)
    idempotency_key: SourcedValue = field(default_factory=SourcedValue.missing)
    entity_id: SourcedValue = field(default_factory=SourcedValue.missing)
    entity_type: SourcedValue = field(default_factory=SourcedValue.missing)
    tenant_id: SourcedValue = field(default_factory=SourcedValue.missing)
    owner_id: SourcedValue = field(default_factory=SourcedValue.missing)
    actor_id: SourcedValue = field(default_factory=SourcedValue.missing)
    resource_path: SourcedValue = field(default_factory=SourcedValue.missing)
    method: SourcedValue = field(default_factory=SourcedValue.missing)
    before_candidates: list[dict] = field(default_factory=list)
    action_ref: SourcedValue = field(default_factory=SourcedValue.missing)
    after_candidates: list[dict] = field(default_factory=list)
    event_refs: list[str] = field(default_factory=list)
    observer_refs: list[dict] = field(default_factory=list)
    call_chain_refs: list[str] = field(default_factory=list)
    timing_window: dict[str, Any] = field(default_factory=dict)
    missing_requirements: list[str] = field(default_factory=list)
    raw_evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id.to_dict(),
            "trace_id": self.trace_id.to_dict(),
            "correlation_id": self.correlation_id.to_dict(),
            "idempotency_key": self.idempotency_key.to_dict(),
            "entity_id": self.entity_id.to_dict(),
            "entity_type": self.entity_type.to_dict(),
            "tenant_id": self.tenant_id.to_dict(),
            "owner_id": self.owner_id.to_dict(),
            "actor_id": self.actor_id.to_dict(),
            "resource_path": self.resource_path.to_dict(),
            "method": self.method.to_dict(),
            "before_candidates": self.before_candidates,
            "action_ref": self.action_ref.to_dict(),
            "after_candidates": self.after_candidates,
            "event_refs": self.event_refs,
            "observer_refs": self.observer_refs,
            "call_chain_refs": self.call_chain_refs,
            "timing_window": self.timing_window,
            "missing_requirements": self.missing_requirements,
            "raw_evidence_refs": self.raw_evidence_refs,
        }


# ── 2.3 SemanticVerificationEvidence ─────────────────────────────────────────
@dataclass
class SemanticVerificationEvidence:
    """Phase92A §2.3: Semantic verdict evidence from Stage_verify.

    MUST preserve Stage_verify's verdict without Gate override.
    """
    semantic_verdict: str = "SEMANTIC_INCONCLUSIVE"
    violated_invariant: str = ""
    expected_behavior: str = ""
    observed_behavior: str = ""
    verifier_rule: str = ""
    before_observation: dict[str, Any] = field(default_factory=dict)
    action_observation: dict[str, Any] = field(default_factory=dict)
    after_observation: dict[str, Any] = field(default_factory=dict)
    observer_consensus: str = ""
    counterfactual_result: str = ""
    semantic_confidence: float = 0.0
    verifier_trace_ref: str = ""
    _original_verdict: str = ""  # Safety: preserve original verdict

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_verdict": self.semantic_verdict,
            "violated_invariant": self.violated_invariant,
            "expected_behavior": self.expected_behavior,
            "observed_behavior": self.observed_behavior,
            "verifier_rule": self.verifier_rule,
            "before_observation": self.before_observation,
            "action_observation": self.action_observation,
            "after_observation": self.after_observation,
            "observer_consensus": self.observer_consensus,
            "counterfactual_result": self.counterfactual_result,
            "semantic_confidence": self.semantic_confidence,
            "verifier_trace_ref": self.verifier_trace_ref,
            "_original_verdict": self._original_verdict,
        }


# ── 2.4 BusinessEvidenceDraft ────────────────────────────────────────────────
@dataclass
class BusinessEvidenceDraft:
    """Phase92A §2.4: Business evidence contract draft.

    Enriched evidence ready for gate validation.
    NEVER fabricates snapshots or cleanup.
    """
    project_id: str = ""
    environment_id: str = ""
    run_id: str = ""
    hypothesis_id: str = ""
    flow_id: str = ""
    policy_version: str = "baseline"
    entity_binding: dict[str, Any] = field(default_factory=dict)
    tenant_binding: dict[str, Any] = field(default_factory=dict)
    owner_binding: dict[str, Any] = field(default_factory=dict)
    correlation_binding: dict[str, Any] = field(default_factory=dict)
    before_snapshot_ref: str = ""
    action_evidence_ref: str = ""
    after_snapshot_ref: str = ""
    observer_refs: list[str] = field(default_factory=list)
    invariant_ref: str = ""
    impact_assessment: str = ""
    reproduction_flow_ref: str = ""
    cleanup_status: str = "NOT_APPLICABLE"
    cleanup_evidence_ref: str = ""
    runtime_evidence_refs: list[str] = field(default_factory=list)
    semantic_evidence_ref: str = ""
    missing_requirements: list[str] = field(default_factory=list)
    enrichment_trace: list[str] = field(default_factory=list)
    _fabricated: bool = False  # Safety: must always be False

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "environment_id": self.environment_id,
            "run_id": self.run_id,
            "hypothesis_id": self.hypothesis_id,
            "flow_id": self.flow_id,
            "policy_version": self.policy_version,
            "entity_binding": self.entity_binding,
            "tenant_binding": self.tenant_binding,
            "owner_binding": self.owner_binding,
            "correlation_binding": self.correlation_binding,
            "before_snapshot_ref": self.before_snapshot_ref,
            "action_evidence_ref": self.action_evidence_ref,
            "after_snapshot_ref": self.after_snapshot_ref,
            "observer_refs": self.observer_refs,
            "invariant_ref": self.invariant_ref,
            "impact_assessment": self.impact_assessment,
            "reproduction_flow_ref": self.reproduction_flow_ref,
            "cleanup_status": self.cleanup_status,
            "cleanup_evidence_ref": self.cleanup_evidence_ref,
            "runtime_evidence_refs": self.runtime_evidence_refs,
            "semantic_evidence_ref": self.semantic_evidence_ref,
            "missing_requirements": self.missing_requirements,
            "enrichment_trace": self.enrichment_trace,
            "_fabricated": self._fabricated,
        }


# ── BusinessFindingContract ──────────────────────────────────────────────────
@dataclass
class BusinessFindingContract:
    """Phase92A: Full business finding contract with four-layer state.

    This is the final contract that goes through gates and registry.
    """
    finding_id: str = ""
    hypothesis_id: str = ""
    project_id: str = ""
    environment_id: str = ""
    policy_version: str = ""
    context_artifact_id: str = ""
    
    # ── Four-layer state (Phase92A §1) ──
    raw_runtime_verdict: str = "inconclusive"
    semantic_verdict: str = "SEMANTIC_INCONCLUSIVE"
    business_evidence_status: str = "NOT_ENRICHED"
    final_review_status: str = "NEEDS_MORE_EVIDENCE"
    
    # ── Finding metadata ──
    title: str = ""
    severity: str = "P2"
    business_intent: str = ""
    root_cause_candidate: str = ""
    
    # ── Evidence refs ──
    entity_binding: dict[str, Any] = field(default_factory=dict)
    before_snapshot_ref: str = ""
    action_evidence_ref: str = ""
    after_snapshot_ref: str = ""
    observer_refs: list[str] = field(default_factory=list)
    violated_invariant: dict[str, Any] = field(default_factory=dict)
    cleanup: dict[str, Any] = field(default_factory=dict)
    reproduction: dict[str, Any] = field(default_factory=dict)
    
    # ── Evidence trace ──
    raw_evidence_refs: list[str] = field(default_factory=list)
    normalized_evidence_ref: str = ""
    semantic_trace_ref: str = ""
    business_contract_ref: str = ""
    
    # ── Gate results ──
    runtime_gate_status: str = "NOT_RUN"
    business_gate_status: str = "NOT_RUN"
    adversarial_status: str = "NOT_RUN"
    schema_status: str = "NOT_RUN"
    
    # ── State history ──
    state_history: list[dict[str, Any]] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "hypothesis_id": self.hypothesis_id,
            "project_id": self.project_id,
            "environment_id": self.environment_id,
            "policy_version": self.policy_version,
            "context_artifact_id": self.context_artifact_id,
            "raw_runtime_verdict": self.raw_runtime_verdict,
            "semantic_verdict": self.semantic_verdict,
            "business_evidence_status": self.business_evidence_status,
            "final_review_status": self.final_review_status,
            "title": self.title,
            "severity": self.severity,
            "business_intent": self.business_intent,
            "root_cause_candidate": self.root_cause_candidate,
            "entity_binding": self.entity_binding,
            "before_snapshot_ref": self.before_snapshot_ref,
            "action_evidence_ref": self.action_evidence_ref,
            "after_snapshot_ref": self.after_snapshot_ref,
            "observer_refs": self.observer_refs,
            "violated_invariant": self.violated_invariant,
            "cleanup": self.cleanup,
            "reproduction": self.reproduction,
            "raw_evidence_refs": self.raw_evidence_refs,
            "normalized_evidence_ref": self.normalized_evidence_ref,
            "semantic_trace_ref": self.semantic_trace_ref,
            "business_contract_ref": self.business_contract_ref,
            "runtime_gate_status": self.runtime_gate_status,
            "business_gate_status": self.business_gate_status,
            "adversarial_status": self.adversarial_status,
            "schema_status": self.schema_status,
            "state_history": self.state_history,
            "missing_requirements": self.missing_requirements,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def record_state_transition(self, from_state: str, to_state: str, reason: str):
        """Record state transition for audit trail."""
        self.state_history.append({
            "from": from_state,
            "to": to_state,
            "reason": reason,
            "timestamp": time.time(),
        })
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── Missing requirement constants ────────────────────────────────────────────
MISSING_REQUIREMENTS = {
    "ENTITY_BINDING_MISSING",
    "BEFORE_SNAPSHOT_MISSING",
    "AFTER_SNAPSHOT_MISSING",
    "CLEANUP_PENDING",
    "CLEANUP_FAILED",
    "OBSERVER_CONFLICT",
    "FIXTURE_AMBIGUOUS",
    "ASYNC_WINDOW_OPEN",
    "REPRODUCTION_INCOMPLETE",
    "INVARIANT_REF_MISSING",
    "IMPACT_ASSESSMENT_MISSING",
    "TRACE_CORRUPTED",
    "SCHEMA_INVALID",
    "ADVERSARIAL_COUNTERFACTUAL",
}


# ── Gate status constants ─────────────────────────────────────────────────────
RUNTIME_GATE_STATUS = {
    "PASSED", "FAILED_UNTRACEABLE", "FAILED_CROSS_PROJECT",
    "FAILED_MISSING_CALLS", "FAILED_TIMING_ERROR", "NOT_RUN",
}

BUSINESS_GATE_STATUS = {
    "PASSED", "FAILED_ENTITY_BINDING", "FAILED_BEFORE_SNAPSHOT",
    "FAILED_AFTER_SNAPSHOT", "FAILED_CLEANUP", "FAILED_INVARIANT",
    "FAILED_OBSERVER_CONFLICT", "FAILED_REPRODUCTION",
    "NOT_RUN", "PENDING",
}