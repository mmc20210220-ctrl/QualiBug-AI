"""Candidate validation and promotion state machine.

Phase 4 of SPEC_FORMAT_AGNOSTIC_ENTERPRISE_MATERIAL_COMPREHENSION.

Candidates are model suggestions, never business facts. Promotion requires
independent source evidence. Repetition inside one document is not multi-source
consistency, and only validated entity candidates may enter entity space.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Iterable

logger = logging.getLogger(__name__)

__all__ = [
    "validate_and_promote_candidates",
    "candidates_to_behavior_ir_entries",
    "CandidateValidationReceipt",
    "CANDIDATE_STATES",
]

CANDIDATE_STATES = {
    "CANDIDATE",
    "PENDING_VALIDATION",
    "VALIDATED",
    "CONFLICTED",
    "STALE",
    "REJECTED",
}
_ALLOWED_KINDS = {"entity", "field", "relation", "state", "actor"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _source_identity(row: dict[str, Any], *, prefix: str = "") -> str:
    source_id = _text(row.get("source_id"))
    if source_id:
        return source_id
    locator = _text(row.get("source_locator"))
    return f"{prefix}:{locator}" if locator else ""


def _independent_match_sources(
    name: str,
    rows: Iterable[dict[str, Any]],
    *,
    text_fields: tuple[str, ...],
    candidate_source: str,
    exact_fields: tuple[str, ...] = (),
) -> list[str]:
    name_lower = name.lower()
    sources: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        exact_match = any(
            _text(row.get(field)).lower() == name_lower for field in exact_fields
        )
        text_match = any(
            name_lower in _text(row.get(field)).lower() for field in text_fields
        )
        if not exact_match and not text_match:
            continue
        source = _source_identity(row, prefix=f"asset-{index}")
        if source and source != candidate_source:
            sources.add(source)
    return sorted(sources)


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
            "schema_version": "qualibug.candidate-validation-receipt.v2",
            "promotion_contract": (
                "independent source identities required; same-source repetition "
                "never satisfies multi-source consistency"
            ),
            "total_candidates": self.total_candidates,
            "validated_count": len(self.validated),
            "conflicted_count": len(self.conflicted),
            "pending_count": len(self.pending),
            "rejected_count": len(self.rejected),
            "stale_count": len(self.stale),
            "validated": self.validated,
            "conflicted": self.conflicted,
            "pending": self.pending,
            "rejected": self.rejected,
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
    """Validate candidates using independent, source-identifiable evidence."""
    receipt = CandidateValidationReceipt()
    receipt.total_candidates = len(candidates)

    interfaces = [row for row in (interfaces or []) if isinstance(row, dict)]
    tables = [row for row in (tables or []) if isinstance(row, dict)]
    rules = [row for row in (rules or []) if isinstance(row, dict)]
    state_machines = [
        row for row in (state_machines or []) if isinstance(row, dict)
    ]
    other_candidates = [
        row for row in (other_candidates or []) if isinstance(row, dict)
    ]

    candidate_sources: dict[tuple[str, str], set[str]] = defaultdict(set)
    all_candidates = [
        row for row in [*candidates, *other_candidates] if isinstance(row, dict)
    ]
    for candidate in all_candidates:
        name = _text(candidate.get("name")).lower()
        kind = _text(candidate.get("kind")).lower()
        source = _source_identity(candidate, prefix="candidate")
        if name and kind and source:
            candidate_sources[(name, kind)].add(source)

    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            receipt.rejected.append(
                {"raw": str(candidate)[:100], "reason": "not_a_dict"}
            )
            continue
        name = _text(candidate.get("name"))
        kind = _text(candidate.get("kind")).lower()
        if not name:
            receipt.rejected.append(
                {"candidate": candidate, "reason": "empty_name"}
            )
            continue
        if kind not in _ALLOWED_KINDS:
            receipt.rejected.append(
                {
                    "candidate": candidate,
                    "reason": "unsupported_candidate_kind",
                    "allowed_kinds": sorted(_ALLOWED_KINDS),
                }
            )
            continue
        row = dict(candidate)
        row["kind"] = kind
        normalized.append(row)

    conflicted_keys: dict[tuple[str, str], set[str]] = defaultdict(set)
    for candidate in [*normalized, *other_candidates]:
        name = _text(candidate.get("name")).lower()
        kind = _text(candidate.get("kind")).lower()
        source = _source_identity(candidate, prefix="candidate")
        if name and kind and source:
            conflicted_keys[(name, source)].add(kind)

    for candidate in normalized:
        name = _text(candidate.get("name"))
        name_lower = name.lower()
        kind = _text(candidate.get("kind")).lower()
        source = _source_identity(candidate, prefix="candidate")
        source_kinds = conflicted_keys.get((name_lower, source), set())
        if len(source_kinds) > 1:
            conflicted = dict(candidate)
            conflicted["status"] = "CONFLICTED"
            conflicted["conflict_reason"] = (
                "same_source_same_name_multiple_kinds:"
                + ",".join(sorted(source_kinds))
            )
            receipt.conflicted.append(conflicted)
            continue

        evidence: list[str] = []
        details: dict[str, list[str]] = {}

        api_sources = _independent_match_sources(
            name,
            interfaces,
            text_fields=("path", "summary", "source_excerpt"),
            candidate_source=source,
        )
        if api_sources:
            evidence.append("cross_ref_api_path")
            details["cross_ref_api_path"] = api_sources

        table_sources = _independent_match_sources(
            name,
            tables,
            text_fields=("description",),
            exact_fields=("name",),
            candidate_source=source,
        )
        if table_sources:
            evidence.append("cross_ref_table_name")
            details["cross_ref_table_name"] = table_sources

        rule_sources = _independent_match_sources(
            name,
            rules,
            text_fields=("statement", "expected"),
            candidate_source=source,
        )
        if rule_sources:
            evidence.append("cross_ref_rule_text")
            details["cross_ref_rule_text"] = rule_sources

        state_rows: list[dict[str, Any]] = []
        for state_machine in state_machines:
            state_rows.append(
                {
                    **state_machine,
                    "_state_text": " ".join(
                        [
                            _text(state_machine.get("name")),
                            *[
                                _text(state)
                                for state in state_machine.get("states") or []
                            ],
                        ]
                    ),
                }
            )
        state_sources = _independent_match_sources(
            name,
            state_rows,
            text_fields=("_state_text",),
            candidate_source=source,
        )
        if state_sources:
            evidence.append("cross_ref_state_machine")
            details["cross_ref_state_machine"] = state_sources

        multi_sources = sorted(candidate_sources.get((name_lower, kind), set()))
        independent_candidate_sources = [
            value for value in multi_sources if value and value != source
        ]
        if independent_candidate_sources:
            evidence.append("multi_source_consistency")
            details["multi_source_consistency"] = independent_candidate_sources

        if evidence:
            promoted = dict(candidate)
            promoted["status"] = "VALIDATED"
            promoted["promotion_evidence"] = evidence
            promoted["promotion_evidence_sources"] = details
            promoted["confidence"] = min(
                1.0,
                float(candidate.get("confidence") or 0.5) + 0.2,
            )
            receipt.validated.append(promoted)
        else:
            pending = dict(candidate)
            pending["status"] = "PENDING_VALIDATION"
            pending["promotion_evidence"] = []
            pending["promotion_evidence_sources"] = {}
            pending["pending_reason"] = "no_independent_source_evidence"
            receipt.pending.append(pending)

    # Cross-source same-name/different-kind contradictions remain visible and
    # remove the affected row from the validated set without mutating while iterating.
    other_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for other in other_candidates:
        name = _text(other.get("name")).lower()
        if name:
            other_by_name[name].append(other)

    still_validated: list[dict[str, Any]] = []
    for validated in receipt.validated:
        name_lower = _text(validated.get("name")).lower()
        kind = _text(validated.get("kind")).lower()
        source = _source_identity(validated, prefix="candidate")
        conflicts = [
            other
            for other in other_by_name.get(name_lower, [])
            if _source_identity(other, prefix="candidate") != source
            and _text(other.get("kind")).lower() != kind
        ]
        if conflicts:
            conflicted = dict(validated)
            conflicted["status"] = "CONFLICTED"
            conflicted["conflict_reason"] = (
                f"same_name_different_kind:{kind}_vs_"
                + ",".join(
                    sorted({_text(other.get("kind")).lower() for other in conflicts})
                )
            )
            conflicted["conflict_sources"] = sorted(
                {
                    _source_identity(other, prefix="candidate")
                    for other in conflicts
                    if _source_identity(other, prefix="candidate")
                }
            )
            receipt.conflicted.append(conflicted)
        else:
            still_validated.append(validated)
    receipt.validated = still_validated

    logger.info(
        "Candidate validation: %d total → %d validated, %d pending, %d conflicted, %d rejected",
        receipt.total_candidates,
        len(receipt.validated),
        len(receipt.pending),
        len(receipt.conflicted),
        len(receipt.rejected),
    )
    return receipt


def _diagnostic_entry(
    candidate: dict[str, Any],
    *,
    source: str,
    derivation: str,
    confidence: float,
) -> dict[str, Any]:
    kind = _text(candidate.get("kind")).lower()
    entry: dict[str, Any] = {
        "candidate_name": candidate.get("name"),
        "semantic_candidate_kind": kind,
        "source": source,
        "evidence": [
            {
                "source_id": candidate.get("source_id"),
                "verbatim_quote": _text(candidate.get("verbatim_quote"))[:200],
                "source_locator": candidate.get("source_locator"),
            }
        ],
        "confidence": confidence,
        "derivation": derivation,
        "promotion_evidence": list(candidate.get("promotion_evidence") or []),
        "promotion_evidence_sources": dict(
            candidate.get("promotion_evidence_sources") or {}
        ),
    }
    return entry


def candidates_to_behavior_ir_entries(
    validated: list[dict[str, Any]],
    pending: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Convert candidates without collapsing every semantic kind into an entity.

    Only VALIDATED ``entity`` candidates receive ``object`` and can enter the
    current entity-space caller. Validated fields, relations, states and actors
    remain typed diagnostics until a matching typed Behavior IR adapter exists.
    Pending candidates never receive ``object`` and therefore cannot silently
    enter entity space.
    """
    entries: list[dict[str, Any]] = []

    for candidate in validated:
        if not isinstance(candidate, dict):
            continue
        entry = _diagnostic_entry(
            candidate,
            source="semantic_extraction_validated",
            derivation="llm_semantic_validated",
            confidence=float(candidate.get("confidence") or 0.7),
        )
        if _text(candidate.get("kind")).lower() == "entity":
            entry["object"] = candidate.get("name")
            entry["behavior_ir_promotion_status"] = "ENTITY_SPACE_ACCEPTED"
        else:
            entry["behavior_ir_promotion_status"] = (
                "TYPED_ADAPTER_REQUIRED_NOT_ENTITY"
            )
            entry["_cannot_enter_entity_space"] = True
        entries.append(entry)

    for candidate in pending or []:
        if not isinstance(candidate, dict):
            continue
        entry = _diagnostic_entry(
            candidate,
            source="semantic_extraction_pending",
            derivation="llm_semantic_pending_validation",
            confidence=min(0.4, float(candidate.get("confidence") or 0.3)),
        )
        entry.update(
            {
                "behavior_ir_promotion_status": "PENDING_DIAGNOSTIC_ONLY",
                "_low_confidence_marker": True,
                "_cannot_solely_support_finding": True,
                "_cannot_enter_entity_space": True,
            }
        )
        entries.append(entry)

    return entries
