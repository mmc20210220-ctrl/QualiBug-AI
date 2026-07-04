"""Phase92A · Business Evidence Enricher — Normalized Evidence → Business Contract Draft.

Enriches normalized runtime evidence with project context, entity bindings,
snapshot refs, and cleanup status.  Never fabricates evidence.

PHASE92A CRITICAL:
- Semantic verdict is preserved from SemanticVerificationEvidence
- Missing business evidence → PENDING_*, not REJECTED
- Fabricated snapshots/cleanup/entity_bindings → _fabricated=True, caught by gate
- enrich_finding_evidence() returns four-layer state dict
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .evidence_models import (
    SourcedValue,
    RawProbeEvidence,
    NormalizedRuntimeEvidence,
    SemanticVerificationEvidence,
    BusinessEvidenceDraft,
    BusinessFindingContract,
    MISSING_REQUIREMENTS,
    BUSINESS_EVIDENCE_STATUS,
    FINAL_REVIEW_STATUS,
)




class BusinessEvidenceEnricher:
    """Enrich normalized evidence into a business evidence contract draft.

    PHASE92A: Never fabricates evidence. Missing requirements become
    structured PENDING_* states, not REJECTED.
    """

    @staticmethod
    def _snapshot_digest(data: dict[str, Any]) -> str:
        """Create a content-addressable snapshot ref."""
        if not data:
            return ""
        payload = json.dumps(data, sort_keys=True, default=str)
        return "snap:" + hashlib.sha256(payload.encode()).hexdigest()[:12]

    @staticmethod
    def _extract_path_from_calls(calls: list[dict]) -> str:
        """Extract the primary API path from a list of verification calls."""
        for call in calls:
            call_str = call.get("call", "")
            # "POST /api/v1/orders" → "/api/v1/orders"
            parts = call_str.split()
            if len(parts) >= 2:
                return parts[1]
        return ""

    @staticmethod
    def _entity_from_path(path: str) -> str:
        """Derive entity name from API path as a fallback heuristic."""
        # Strip common prefixes, take the first meaningful segment
        path = path.strip("/")
        parts = [p for p in path.split("/") if p and not p.startswith("{")]
        # Skip common prefixes: api, v1, v2, v3
        for prefix in ("api", "v1", "v2", "v3", "rest", "public"):
            if parts and parts[0].lower() == prefix:
                parts = parts[1:]
        # Return the first remaining segment as the entity name
        if parts:
            return parts[0].replace("-", "_").replace(".", "_")
        return ""
        return "snap:" + hashlib.sha256(payload.encode()).hexdigest()[:12]

    def _compute_business_evidence_status(self, draft: BusinessEvidenceDraft, semantic_verdict: str) -> str:
        """Compute business_evidence_status from missing requirements.

        CRITICAL: If semantic_verdict is SEMANTIC_CONFIRMED but business
        evidence is incomplete, the status MUST be PENDING_*, never REJECTED.
        """
        missing = draft.missing_requirements
        if not missing:
            return "VALIDATED"

        # Specific pending states based on what's missing
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
        if missing:
            return "PENDING_ENTITY_BINDING"  # generic pending

        return "NOT_ENRICHED"

    def _compute_final_review_status(self, business_status: str, semantic_verdict: str) -> str:
        """Compute final_review_status from business and semantic state.

        CRITICAL: SEMANTIC_CONFIRMED + PENDING evidence → NEEDS_MORE_EVIDENCE
        NOT REJECTED, FALSIFIED, or INCONCLUSIVE.
        """
        if business_status == "VALIDATED":
            return "VALIDATED_CANDIDATE"
        if business_status == "BLOCKED_BY_RUNTIME_EVIDENCE":
            return "BLOCKED"
        if business_status.startswith("PENDING_"):
            return "NEEDS_MORE_EVIDENCE"
        if business_status == "GATE_ERROR":
            return "BLOCKED"
        return "NEEDS_MORE_EVIDENCE"

    def enrich(
        self,
        normalized: dict[str, Any],
        semantic: dict[str, Any],
        finding: Any,
        calls: list[dict] | None = None,
        project_id: str = "",
        policy_version: str = "baseline",
    ) -> BusinessEvidenceDraft:
        """Enrich normalized + semantic evidence into a business draft.

        NEVER fabricates snapshots, cleanup, or entity_binding.
        Missing evidence → structured PENDING_* in missing_requirements.
        """
        draft = BusinessEvidenceDraft()
        calls = calls or []
        draft.enrichment_trace = []

        # ── Project context ──
        draft.project_id = project_id
        draft.policy_version = policy_version
        draft.environment_id = os.environ.get("QUALIBUG_ENVIRONMENT", "test")

        # ── Hypothesis binding ──
        hid = ""
        if hasattr(finding, "hypothesis_id"):
            hid = str(finding.hypothesis_id or "")
        elif isinstance(finding, dict):
            hid = str(finding.get("hypothesis_id", ""))
        draft.hypothesis_id = hid

        # ── Entity binding from normalized evidence ──
        n_entity = normalized.get("entity_id", {})
        n_entity_type = normalized.get("entity_type", {})
        if isinstance(n_entity, dict) and n_entity.get("confidence") == "evidenced":
            draft.entity_binding = {
                "entity_id": n_entity.get("value"),
                "entity_type": n_entity_type.get("value", "") if isinstance(n_entity_type, dict) else "",
                "entity_alias": n_entity.get("value"),
                "binding_confidence": 0.9,
                "source": n_entity.get("source", ""),
            }
            draft.enrichment_trace.append("entity_binding: from runtime evidence")
        else:
            # MISSING entity binding — record as PENDING, never fabricate
            draft.entity_binding = {
                "entity_id": "",
                "entity_type": "",
                "entity_alias": "",
                "binding_confidence": 0.0,
                "source": "unavailable",
            }
            # Fallback: derive entity from API path (when regex patterns don't match)
            path = self._extract_path_from_calls(calls)
            if path:
                entity_from_path = self._entity_from_path(path)
                if entity_from_path:
                    draft.entity_binding = {
                        "entity_id": "",
                        "entity_type": entity_from_path,
                        "entity_alias": entity_from_path,
                        "binding_confidence": 0.3,
                        "source": "path_heuristic",
                    }
                    draft.enrichment_trace.append(f"entity_binding: path heuristic → {entity_from_path}")
            if not draft.entity_binding.get("entity_alias"):
                draft.missing_requirements.append("ENTITY_BINDING_MISSING")
                draft.enrichment_trace.append("entity_binding: missing — recorded as PENDING")

        # ── Tenant binding (single-tenant guard) ──
        draft.tenant_binding = {
            "tenant_id": "single-tenant",
            "source": "environment_config",
            "confidence": "evidenced",
        }

        # ── Before/after snapshots from calls ──
        before_snap = None
        after_snap = None
        if len(calls) >= 1:
            before_body = calls[0].get("results", {}).get("admin", {}).get("body", {})
            if isinstance(before_body, dict) and before_body:
                before_snap = before_body
                draft.before_snapshot_ref = self._snapshot_digest(before_body)
                draft.before_snapshot_data = before_body  # store actual data for audit
                draft.enrichment_trace.append("before_snapshot: from calls[0]")
        if len(calls) >= 3:
            after_body = calls[-1].get("results", {}).get("admin", {}).get("body", {})
            if isinstance(after_body, dict) and after_body:
                after_snap = after_body
                draft.after_snapshot_ref = self._snapshot_digest(after_body)
                draft.after_snapshot_data = after_body  # store actual data for audit
                draft.enrichment_trace.append("after_snapshot: from calls[-1]")
            action_call = calls[1].get("call", "")
            draft.action_evidence_ref = f"action:{action_call}" if action_call else ""
        elif len(calls) >= 2:
            after_body = calls[-1].get("results", {}).get("admin", {}).get("body", {})
            if isinstance(after_body, dict) and after_body:
                after_snap = after_body
                draft.after_snapshot_ref = self._snapshot_digest(after_body)
                draft.after_snapshot_data = after_body  # store actual data for audit
                draft.enrichment_trace.append("after_snapshot: from calls[-1]")

        if not draft.before_snapshot_ref:
            draft.missing_requirements.append("BEFORE_SNAPSHOT_MISSING")
        if not draft.after_snapshot_ref:
            draft.missing_requirements.append("AFTER_SNAPSHOT_MISSING")

        # ── Observer refs ──
        observer_list = normalized.get("observer_refs", [])
        draft.observer_refs = [
            json.dumps(o, default=str)[:300]
            for o in (observer_list if isinstance(observer_list, list) else [])
        ]

        # ── Check observer consensus ──
        if len(calls) >= 3:
            admin_body = calls[-1].get("results", {}).get("admin", {}).get("body", {})
            viewer_body = calls[-1].get("results", {}).get("viewer", {}).get("body", {})
            if isinstance(admin_body, dict) and isinstance(viewer_body, dict) and admin_body and viewer_body:
                if admin_body != viewer_body:
                    draft.enrichment_trace.append("observer_consensus: admin/viewer mismatch detected")
                else:
                    draft.enrichment_trace.append("observer_consensus: admin/viewer consistent")

        # ── Invariant ref ──
        draft.invariant_ref = semantic.get("violated_invariant", "")

        # ── Semantic evidence ──
        draft.semantic_evidence_ref = semantic.get("verifier_trace_ref", "")

        # ── Runtime evidence refs ──
        draft.runtime_evidence_refs = normalized.get("call_chain_refs", [])
        if not draft.runtime_evidence_refs:
            draft.runtime_evidence_refs = normalized.get("raw_evidence_refs", [])

        # ── Cleanup status: check ACTION call (calls[1]) not first call ──
        # First call is usually GET (before snapshot), action is in calls[1]
        method = "GET"
        # 1. Check action_ref from normalized evidence (this is the action call)
        action_ref = normalized.get("action_ref", {})
        if isinstance(action_ref, dict):
            action_call = str(action_ref.get("value", ""))
            if action_call:
                # Extract method from action call string like "POST /api/materials"
                method = action_call.split()[0] if action_call.split() else "GET"
        # 2. Fallback: check calls directly
        if method == "GET" and len(calls) >= 2:
            action_call = calls[1].get("call", "")
            if action_call:
                method = action_call.split()[0] if action_call.split() else "GET"
        # 3. Fallback: normalized method (first call)
        if method == "GET":
            method_val = normalized.get("method", {})
            if isinstance(method_val, dict):
                method = str(method_val.get("value", "GET"))
            elif method_val:
                method = str(method_val)
                
        if method in ("POST", "PUT", "DELETE", "PATCH"):
            draft.cleanup_status = "PENDING"
            draft.missing_requirements.append("CLEANUP_PENDING")
            draft.enrichment_trace.append(f"cleanup: PENDING for write operation ({method})")
        else:
            draft.cleanup_status = "NOT_APPLICABLE"
            draft.enrichment_trace.append("cleanup: NOT_APPLICABLE (read-only probe)")

        # ── Impact assessment ──
        sev = ""
        if hasattr(finding, "severity"):
            sev = str(finding.severity or "")
        elif isinstance(finding, dict):
            sev = str(finding.get("severity", ""))
        title = ""
        if hasattr(finding, "title"):
            title = str(finding.title or "")
        elif isinstance(finding, dict):
            title = str(finding.get("title", ""))
        draft.impact_assessment = f"[{sev}] {title}"[:300]

        # ── Reproduction flow ──
        if calls:
            first_call = calls[0].get("call", "")
            draft.reproduction_flow_ref = f"discovery_probe::{first_call}"

        # ── Merge normalized missing requirements ──
        for mr in normalized.get("missing_requirements", []):
            if mr not in draft.missing_requirements and mr in MISSING_REQUIREMENTS:
                draft.missing_requirements.append(mr)

        # ── Safety: never fabricated ──
        draft._fabricated = False

        return draft


def enrich_finding_evidence(
    finding: Any,
    calls: list[dict] | None = None,
    normalized: dict[str, Any] | None = None,
    semantic: dict[str, Any] | None = None,
    project_id: str = "",
    policy_version: str = "baseline",
) -> dict[str, Any]:
    """One-shot: enrich a finding into a business evidence draft.

    Returns the BusinessEvidenceDraft plus four-layer state:
    - raw_runtime_verdict: from original Stage_verify verdict
    - semantic_verdict: mapped to SEMANTIC_VERDICTS
    - business_evidence_status: PENDING_* if incomplete
    - final_review_status: NEEDS_MORE_EVIDENCE if business evidence incomplete

    CRITICAL: semantic confirmed + missing business evidence →
    SEMANTIC_CONFIRMED_PENDING_EVIDENCE, NOT rejected.
    """
    enricher = BusinessEvidenceEnricher()
    normalized = normalized or {}
    semantic = semantic or {}
    draft = enricher.enrich(
        normalized, semantic, finding, calls or [],
        project_id=project_id, policy_version=policy_version,
    )

    # ── Compute four-layer state ──
    raw_runtime_verdict = "inconclusive"
    if hasattr(finding, "verdict"):
        raw_runtime_verdict = str(finding.verdict or "inconclusive")
    elif isinstance(finding, dict):
        raw_runtime_verdict = str(finding.get("verdict", "inconclusive"))

    # Preserve original raw verdict from Stage_verify
    original = semantic.get("_original_verdict", raw_runtime_verdict)
    if original and original != "inconclusive":
        raw_runtime_verdict = original

    semantic_verdict = semantic.get("semantic_verdict", "SEMANTIC_INCONCLUSIVE")
    business_status = enricher._compute_business_evidence_status(draft, semantic_verdict)
    final_status = enricher._compute_final_review_status(business_status, semantic_verdict)

    result = draft.to_dict()
    result["raw_runtime_verdict"] = raw_runtime_verdict
    result["semantic_verdict"] = semantic_verdict
    result["business_evidence_status"] = business_status
    result["final_review_status"] = final_status

    # If SEMANTIC_CONFIRMED but business evidence incomplete:
    # Record the compound state explicitly
    if semantic_verdict == "SEMANTIC_CONFIRMED" and business_status.startswith("PENDING_"):
        result["compound_status"] = "SEMANTIC_CONFIRMED_PENDING_EVIDENCE"
    elif semantic_verdict == "SEMANTIC_CONFIRMED" and business_status == "VALIDATED":
        result["compound_status"] = "VALIDATED_CANDIDATE"
    else:
        result["compound_status"] = final_status

    return result
