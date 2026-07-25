"""Candidate validation and promotion state machine.

Phase 4 of SPEC_FORMAT_AGNOSTIC_ENTERPRISE_MATERIAL_COMPREHENSION.

State machine:
    CANDIDATE → PENDING_VALIDATION → VALIDATED / CONFLICTED / STALE / REJECTED

Promotion evidence (at least one required for VALIDATED):
1. Multi-source cross-consistency: candidate name appears independently in
   API paths / other documents / state machines.
2. Runtime read-only probe confirmation (contract-gated).

Rules for Behavior IR entry:
- VALIDATED: normal entry into entity space
- CANDIDATE / PENDING_VALIDATION: may enter with low-confidence marker,
  cannot solely support a formal finding
- CONFLICTED: must be visible as explicit conflict, never silently resolved
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "validate_and_promote_candidates",
    "CandidateValidationReceipt",
    "CANDIDATE_STATES",
]

# Valid state transitions
CANDIDATE_STATES = {
    "CANDIDATE",
    "PENDING_VALIDATION",
    "VALIDATED",
    "CONFLICTED",
    "STALE",
    "REJECTED",
}


class CandidateValidationReceipt:
    """Receipt for candidate validation and promotion."""

    def __init__(self) -> None:
        self.total_candidates: int = 0
        self.validated: list[dict[str, Any]] = []
        self.conflicted: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []
        self.stale: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "qualibug.candidate-validation-receipt.v1",
            "total_candidates": self.total_candidates,
            "validated_count": len(self.validated),
            "conflicted_count": len(self.conflicted),
            "pending_count": len(self.pending),
            "rejected_count": len(self.rejected),
            "stale_count": len(self.stale),
            "validated": self.validated,
            "conflicted": self.conflicted,
        }


def validate_and_promote_candidates(
    candidates: list[dict[str, Any]],
    *,
    interfaces: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    rules: list[dict[str, Any]] | None = None,
    state_machines: list[dict[str, Any]] | None = None,
    other_candidates: list[dict[str, Any]] | None = None,
) -> CandidateValidationReceipt:
    """Validate and promote semantic candidates.

    Promotion logic:
    1. Cross-source consistency: candidate name appears in API paths, table names,
       rule text, or state machine definitions independently.
    2. Multiple candidates from different sources referencing the same entity.

    Args:
        candidates: List of validated semantic candidates (status=CANDIDATE).
        interfaces: Extracted API interfaces for cross-reference.
        tables: Extracted data tables for cross-reference.
        rules: Extracted business rules for cross-reference.
        state_machines: Extracted state machines for cross-reference.
        other_candidates: Other candidates for cross-source consistency.

    Returns:
        CandidateValidationReceipt with promotion results.
    """
    receipt = CandidateValidationReceipt()
    receipt.total_candidates = len(candidates)

    interfaces = interfaces or []
    tables = tables or []
    rules = rules or []
    state_machines = state_machines or []
    other_candidates = other_candidates or []

    # Build cross-reference indices
    api_path_text = " ".join(
        str(row.get("path") or "") + " " + str(row.get("summary") or "")
        for row in interfaces
        if isinstance(row, dict)
    ).lower()

    table_names = {
        str(row.get("name") or "").lower()
        for row in tables
        if isinstance(row, dict) and row.get("name")
    }

    rule_text = " ".join(
        str(row.get("statement") or "") + " " + str(row.get("expected") or "")
        for row in rules
        if isinstance(row, dict)
    ).lower()

    state_text = " ".join(
        str(sm.get("name") or "") + " " + " ".join(str(s) for s in sm.get("states") or [])
        for sm in state_machines
        if isinstance(sm, dict)
    ).lower()

    # Cross-source candidate name index
    candidate_name_counts: dict[str, int] = {}
    for cand in candidates + other_candidates:
        if isinstance(cand, dict):
            name = str(cand.get("name") or "").strip().lower()
            if name:
                candidate_name_counts[name] = candidate_name_counts.get(name, 0) + 1

    for candidate in candidates:
        if not isinstance(candidate, dict):
            receipt.rejected.append({"raw": str(candidate)[:100], "reason": "not_a_dict"})
            continue

        name = str(candidate.get("name") or "").strip()
        kind = str(candidate.get("kind") or "").strip()
        name_lower = name.lower()

        if not name:
            receipt.rejected.append({"candidate": candidate, "reason": "empty_name"})
            continue

        # ── Check promotion evidence ──
        evidence: list[str] = []

        # Evidence 1: name appears in API paths
        if name_lower in api_path_text:
            evidence.append("cross_ref_api_path")

        # Evidence 2: name matches a table name
        if name_lower in table_names:
            evidence.append("cross_ref_table_name")

        # Evidence 3: name appears in rule text
        if name_lower in rule_text:
            evidence.append("cross_ref_rule_text")

        # Evidence 4: name appears in state machine definitions
        if name_lower in state_text:
            evidence.append("cross_ref_state_machine")

        # Evidence 5: multiple candidates from different sources reference same name
        if candidate_name_counts.get(name_lower, 0) >= 2:
            evidence.append("multi_source_consistency")

        # ── State transition ──
        if evidence:
            # Has promotion evidence → VALIDATED
            promoted = dict(candidate)
            promoted["status"] = "VALIDATED"
            promoted["promotion_evidence"] = evidence
            promoted["confidence"] = min(1.0, float(candidate.get("confidence") or 0.5) + 0.2)
            receipt.validated.append(promoted)
        else:
            # No evidence yet → remains CANDIDATE (pending future validation)
            pending = dict(candidate)
            pending["status"] = "PENDING_VALIDATION"
            pending["promotion_evidence"] = []
            receipt.pending.append(pending)

    # ── Conflict detection ──
    # Check if any VALIDATED candidate conflicts with existing tables/entities
    for validated in receipt.validated:
        name_lower = str(validated.get("name") or "").lower()
        kind = str(validated.get("kind") or "")
        # Conflict: same name but different kind in existing assets
        if kind == "entity" and name_lower in table_names:
            # Entity name matches table name — this is actually consistent, not a conflict
            pass
        # Check for conflicting candidates (same name, different source, contradictory)
        for other in other_candidates:
            if not isinstance(other, dict):
                continue
            if str(other.get("name") or "").lower() == name_lower and other.get("source_id") != validated.get("source_id"):
                # Same name from different source — check for contradictions
                if str(other.get("kind") or "") != kind:
                    validated["status"] = "CONFLICTED"
                    validated["conflict_reason"] = f"same_name_different_kind:{kind}_vs_{other.get('kind')}"
                    receipt.conflicted.append(validated)
                    receipt.validated.remove(validated)
                    break

    logger.info(
        "Candidate validation: %d total → %d validated, %d pending, %d conflicted, %d rejected",
        receipt.total_candidates,
        len(receipt.validated),
        len(receipt.pending),
        len(receipt.conflicted),
        len(receipt.rejected),
    )
    return receipt


def candidates_to_behavior_ir_entries(
    validated: list[dict[str, Any]],
    pending: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Convert validated/pending candidates to Behavior IR compatible entries.

    Rules:
    - VALIDATED: normal entry, confidence as-is
    - PENDING_VALIDATION: entry with low-confidence marker, cannot solely
      support a formal finding
    - CONFLICTED: NOT included (must be resolved first)

    Returns:
        List of entity-like dicts compatible with Behavior IR entity space.
    """
    entries: list[dict[str, Any]] = []

    for candidate in validated:
        if not isinstance(candidate, dict):
            continue
        entries.append({
            "object": candidate.get("name"),
            "source": "semantic_extraction_validated",
            "evidence": [{
                "source_id": candidate.get("source_id"),
                "verbatim_quote": candidate.get("verbatim_quote", "")[:200],
                "source_locator": candidate.get("source_locator"),
            }],
            "confidence": float(candidate.get("confidence") or 0.7),
            "derivation": "llm_semantic_validated",
        })

    for candidate in (pending or []):
        if not isinstance(candidate, dict):
            continue
        entries.append({
            "object": candidate.get("name"),
            "source": "semantic_extraction_pending",
            "evidence": [{
                "source_id": candidate.get("source_id"),
                "verbatim_quote": candidate.get("verbatim_quote", "")[:200],
                "source_locator": candidate.get("source_locator"),
            }],
            "confidence": min(0.4, float(candidate.get("confidence") or 0.3)),
            "derivation": "llm_semantic_pending_validation",
            "_low_confidence_marker": True,
            "_cannot_solely_support_finding": True,
        })

    return entries
