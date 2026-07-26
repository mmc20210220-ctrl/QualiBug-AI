"""Compensation Relation Resolver — recover explicit source-declared compensations.

SPEC v1.2 §10: Explicit Compensation Relation Recovery

This module finds real compensation relations from source evidence only.
It never infers compensation from name antonyms or path similarity alone.

Output: qualibug.relation-evidence.v1
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


# ─── Evidence Levels ──────────────────────────────────────────────────────────

EVIDENCE_LEVELS = (
    "SOURCE_EXPLICIT",       # Directly declared in source documentation
    "SOURCE_IMPLIED",        # Strongly implied by source (link, relation)
    "NAME_HEURISTIC",        # Only name-based (NOT sufficient for proof)
    "PATH_HEURISTIC",        # Only path-based (NOT sufficient for proof)
)

# Minimum level required for WriteReversibilityProof
MINIMUM_PROOF_LEVEL = "SOURCE_EXPLICIT"


# ─── Relation Resolver ────────────────────────────────────────────────────────


def resolve_compensation_relation(
    *,
    primary_operation: dict[str, Any],
    candidate_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate whether a candidate operation compensates the primary.

    Only SOURCE_EXPLICIT evidence allows entry into WriteReversibilityProof.

    Args:
        primary_operation: The write operation needing compensation.
        candidate_operation: The candidate compensating operation.
        behavior_ir: The Behavior IR graph.
        source_refs: Additional source references.

    Returns:
        qualibug.relation-evidence.v1
    """
    primary = _dict(primary_operation)
    candidate = _dict(candidate_operation)
    ir = _dict(behavior_ir)

    primary_id = _text(primary.get("id"))
    candidate_id = _text(candidate.get("id"))
    primary_method = _text(primary.get("method")).upper()
    candidate_method = _text(candidate.get("method")).upper()
    primary_path = _text(primary.get("path") or primary.get("raw_path"))
    candidate_path = _text(candidate.get("path") or candidate.get("raw_path"))

    # Check for explicit relation in Behavior IR
    relations = _list(ir.get("relations"))
    explicit_relation = None
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        rel_kind = _text(rel.get("relation_type") or rel.get("kind")).lower()
        if rel_kind not in ("compensates", "inverse", "compensation", "reverses"):
            continue
        # Check both directions
        from_ref = _text(rel.get("from_ref") or rel.get("from") or rel.get("source"))
        to_ref = _text(rel.get("to_ref") or rel.get("to") or rel.get("target"))
        op_ref = _text(rel.get("operation_ref"))
        if (
            (from_ref == candidate_id and to_ref == primary_id)
            or (from_ref == primary_id and to_ref == candidate_id)
            or (op_ref == candidate_id and to_ref == primary_id)
        ):
            explicit_relation = rel
            break

    # Determine evidence level
    evidence_level = "NAME_HEURISTIC"  # Default: weakest
    confidence = 0.2

    if explicit_relation:
        evidence_level = "SOURCE_EXPLICIT"
        confidence = 0.95
    else:
        # Check source_refs for explicit documentation
        all_source_refs = list(source_refs or []) + list(primary.get("source_refs") or [])
        has_source_doc = any(
            isinstance(sr, dict)
            and _text(sr.get("kind")) in ("api_doc", "prd", "test_spec", "openapi_link")
            and (
                candidate_id in _text(sr.get("locator"))
                or candidate_path in _text(sr.get("locator"))
            )
            for sr in all_source_refs
        )
        if has_source_doc:
            evidence_level = "SOURCE_EXPLICIT"
            confidence = 0.85

    # Entity match validation
    primary_entity = _text(primary.get("entity_ref") or primary.get("entity"))
    candidate_entity = _text(candidate.get("entity_ref") or candidate.get("entity"))
    entity_match = bool(
        primary_entity and candidate_entity and primary_entity == candidate_entity
    )
    if not entity_match and primary_path and candidate_path:
        # Collection-level match
        primary_coll = primary_path.split("{")[0].rstrip("/")
        candidate_coll = candidate_path.split("{")[0].rstrip("/")
        entity_match = bool(primary_coll and candidate_coll and primary_coll == candidate_coll)

    # Identity contract check
    primary_id_fields = _list(primary.get("identity_fields") or primary.get("path_params"))
    candidate_id_fields = _list(candidate.get("identity_fields") or candidate.get("path_params"))
    identity_match = bool(
        primary_id_fields and candidate_id_fields
        and set(str(f) for f in primary_id_fields) & set(str(f) for f in candidate_id_fields)
    )

    # Rejection conditions
    rejected = False
    rejection_reason = ""

    if not entity_match:
        rejected = True
        rejection_reason = "entity_mismatch"
    elif evidence_level in ("NAME_HEURISTIC", "PATH_HEURISTIC"):
        rejected = True
        rejection_reason = f"insufficient_evidence_level:{evidence_level}"
    elif not _list(source_refs) and not explicit_relation and not list(primary.get("source_refs") or []):
        rejected = True
        rejection_reason = "source_refs_missing"

    # Fingerprint
    fp_content = {
        "source_operation_ref": primary_id,
        "target_operation_ref": candidate_id,
        "evidence_level": evidence_level,
        "entity_match": entity_match,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fp_content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]

    return {
        "schema_version": "qualibug.relation-evidence.v1",
        "relation_id": f"rel_{fingerprint[:16]}",
        "relation_kind": "compensates",
        "source_operation_ref": primary_id,
        "target_operation_ref": candidate_id,
        "entity_match": entity_match,
        "identity_contract": {
            "primary_fields": [str(f) for f in primary_id_fields],
            "candidate_fields": [str(f) for f in candidate_id_fields],
            "match": identity_match,
        },
        "body_contract": {},
        "state_semantics": {},
        "source_refs": list(source_refs or [])[:5],
        "evidence_level": evidence_level,
        "confidence": round(confidence, 4),
        "accepted": not rejected and evidence_level == "SOURCE_EXPLICIT",
        "rejection_reason": rejection_reason,
        "fingerprint": fingerprint,
    }


# ─── Batch Resolution ─────────────────────────────────────────────────────────


def resolve_all_compensation_relations(
    *,
    behavior_ir: dict[str, Any],
    unresolved_operations: list[str] | None = None,
) -> dict[str, Any]:
    """Find all source-explicit compensation relations in Behavior IR.

    Returns only relations with evidence_level >= SOURCE_EXPLICIT.
    """
    ir = _dict(behavior_ir)
    ops = {
        _text(op.get("id")): op
        for op in _list(ir.get("operations"))
        if isinstance(op, dict) and _text(op.get("id"))
    }
    relations = _list(ir.get("relations"))

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for rel in relations:
        if not isinstance(rel, dict):
            continue
        rel_kind = _text(rel.get("relation_type") or rel.get("kind")).lower()
        if rel_kind not in ("compensates", "inverse", "compensation", "reverses"):
            continue

        from_ref = _text(rel.get("from_ref") or rel.get("from") or rel.get("source") or rel.get("operation_ref"))
        to_ref = _text(rel.get("to_ref") or rel.get("to") or rel.get("target"))

        if from_ref not in ops or to_ref not in ops:
            continue

        evidence = resolve_compensation_relation(
            primary_operation=ops[to_ref],
            candidate_operation=ops[from_ref],
            behavior_ir=ir,
            source_refs=list(rel.get("source_refs") or []),
        )
        if evidence.get("accepted"):
            accepted.append(evidence)
        else:
            rejected.append(evidence)

    return {
        "schema_version": "qualibug.compensation-resolution-batch.v1",
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "accepted": accepted,
        "rejected": rejected,
    }
