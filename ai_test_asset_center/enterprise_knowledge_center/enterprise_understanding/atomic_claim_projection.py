"""Project accepted atomic effect claims into the existing business-fact ledger.

The structure-first compiler atomizes one source statement into claims. The current
understanding builder consumes facts as its operation authority. This module closes that
single contract by materializing source-backed DATA_EFFECT claims as child facts in the
same ledger. It never parses text, invents an object, or creates a parallel behavior IR.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

RECEIPT_SCHEMA = "qualibug.atomic-claim-fact-projection.v1"
_DERIVATION = "accepted_atomic_claim_projection"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _stable_id(*parts: Any) -> str:
    raw = "\x1f".join(
        json.dumps(part, ensure_ascii=False, sort_keys=True, default=str)
        if isinstance(part, (dict, list, tuple))
        else _text(part)
        for part in parts
    )
    return "fact:atomic:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _claim_statement(claim: dict[str, Any], parent: dict[str, Any]) -> str:
    value = claim.get("value")
    if isinstance(value, dict):
        statement = _text(value.get("statement") or value.get("raw"))
        if statement:
            return statement
    elif _text(value):
        return _text(value)
    predicate = _text(claim.get("predicate"))
    objects = [_text(value) for value in _list(claim.get("object_refs")) if _text(value)]
    if predicate and objects:
        return predicate + "".join(objects)
    return _text(parent.get("raw_statement"))


def _project_data_effect(parent: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any] | None:
    if _text(claim.get("claim_type")).upper() != "DATA_EFFECT":
        return None
    if claim.get("source_backed") is False:
        return None
    predicate = _text(claim.get("predicate"))
    objects = sorted({_text(value) for value in _list(claim.get("object_refs")) if _text(value)})
    spans = [dict(row) for row in _list(parent.get("source_spans")) if isinstance(row, dict)]
    parent_id = _text(parent.get("fact_id"))
    claim_id = _text(claim.get("claim_id"))
    if not parent_id or not claim_id or not predicate or not objects or not spans:
        return None
    subject = _dict(parent.get("subject"))
    actors = sorted({_text(value) for value in _list(subject.get("actor_refs")) if _text(value)})
    statement = _claim_statement(claim, parent)
    effect = _dict(claim.get("value"))
    if not effect:
        effect = {
            "statement": statement,
            "action": predicate,
            "entity": objects[0] if len(objects) == 1 else "",
            "source_backed": True,
        }
    fact_id = _stable_id(parent_id, claim_id, predicate, objects)
    return {
        "fact_id": fact_id,
        "kind": "RULE",
        "fact_type": "DATA_EFFECT",
        "language": parent.get("language"),
        "statement_frame_id": parent.get("statement_frame_id"),
        "parent_fact_ref": parent_id,
        "atomic_claim_ref": claim_id,
        "subject": {
            "actor_refs": actors,
            "entity_refs": objects,
            "resolution_evidence": [
                {
                    "method": "accepted_atomic_claim_object_refs",
                    "source_backed": True,
                    "parent_fact_ref": parent_id,
                    "atomic_claim_ref": claim_id,
                }
            ],
        },
        "object": {"entity_refs": objects},
        "predicate": predicate,
        "action": {"canonical": predicate, "raw": predicate},
        "conditions": list(_list(parent.get("conditions"))),
        "condition_combinator": parent.get("condition_combinator"),
        "condition_frame": dict(_dict(parent.get("condition_frame"))),
        "scope": dict(_dict(parent.get("scope"))),
        "modality": "ASSERTS",
        "polarity": "POSITIVE",
        "exceptions": list(_list(parent.get("exceptions"))),
        "exception_scope": list(_list(parent.get("exception_scope"))),
        "postconditions": [statement] if statement else [],
        "state_effects": [],
        "data_effects": [dict(effect)],
        "temporal_constraints": list(_list(parent.get("temporal_constraints"))),
        "quantity_constraints": [],
        "time_window_constraints": [
            dict(row)
            for row in _list(parent.get("time_window_constraints"))
            if isinstance(row, dict)
        ],
        "formula_constraints": [],
        "authorization_delegation": {},
        "compensation": [],
        "compensations": [],
        "raw_statement": statement,
        "normalized_statement": "".join(statement.split()),
        "source_spans": spans,
        "confidence": parent.get("confidence"),
        "status": "ACCEPTED",
        "ambiguities": [],
        "critical": bool(parent.get("critical")),
        "derivation": _DERIVATION,
        "claims": [dict(claim)],
        "formal_promotion_allowed": True,
        "source_backed": True,
    }


def project_atomic_claim_facts(asset: dict[str, Any]) -> dict[str, Any]:
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    existing_ids = {_text(row.get("fact_id")) for row in facts if _text(row.get("fact_id"))}
    existing_claim_refs = {
        _text(row.get("atomic_claim_ref"))
        for row in facts
        if _text(row.get("derivation")) == _DERIVATION and _text(row.get("atomic_claim_ref"))
    }
    projected: list[dict[str, Any]] = []
    blocked_claims: list[dict[str, Any]] = []
    for parent in facts:
        if _text(parent.get("status")) != "ACCEPTED":
            continue
        if _text(parent.get("derivation")) == _DERIVATION:
            continue
        for claim in _list(parent.get("claims")):
            if not isinstance(claim, dict):
                continue
            if _text(claim.get("claim_type")).upper() != "DATA_EFFECT":
                continue
            claim_id = _text(claim.get("claim_id"))
            if claim_id and claim_id in existing_claim_refs:
                continue
            child = _project_data_effect(parent, claim)
            if child is None:
                blocked_claims.append(
                    {
                        "parent_fact_ref": parent.get("fact_id"),
                        "claim_id": claim.get("claim_id"),
                        "claim_type": claim.get("claim_type"),
                        "reason": "ATOMIC_DATA_EFFECT_BINDING_INCOMPLETE",
                    }
                )
                continue
            if _text(child.get("fact_id")) in existing_ids:
                continue
            existing_ids.add(_text(child.get("fact_id")))
            existing_claim_refs.add(_text(child.get("atomic_claim_ref")))
            projected.append(child)
    ledger["items"] = [*facts, *projected]
    ledger["atomic_claim_projected_fact_count"] = len(projected)
    asset["business_fact_ledger"] = ledger
    asset["atomic_claim_fact_projection_receipt"] = {
        "schema": RECEIPT_SCHEMA,
        "status": "BLOCKED" if blocked_claims else "PASS",
        "projected_fact_count": len(projected),
        "blocked_claim_count": len(blocked_claims),
        "blocked_claims": blocked_claims,
        "projection_scope": ["DATA_EFFECT"],
        "source_text_reparsed": False,
        "parallel_behavior_ir_created": False,
        "automatic_object_inference_allowed": False,
    }
    if blocked_claims:
        gate = _dict(asset.get("enterprise_comprehension_gate"))
        gate["status"] = "BLOCKED_ATOMIC_CLAIM_FACT_PROJECTION_INCOMPLETE"
        gate["entry_allowed"] = False
        gate["required_operator_action"] = (
            "resolve source-backed atomic data-effect predicate/object/evidence binding"
        )
        asset["enterprise_comprehension_gate"] = gate
        gaps = [
            dict(row)
            for row in _list(asset.get("coverage_gaps"))
            if isinstance(row, dict)
            and _text(row.get("kind")) != "BLOCKED_ATOMIC_CLAIM_FACT_PROJECTION_INCOMPLETE"
        ]
        gaps.append(
            {
                "kind": "BLOCKED_ATOMIC_CLAIM_FACT_PROJECTION_INCOMPLETE",
                "gap_type": "atomic_data_effect_claim_not_materialized",
                "source_id": "*",
                "blocked_claims": blocked_claims,
                "operator_action": gate["required_operator_action"],
            }
        )
        asset["coverage_gaps"] = gaps
    summary = _dict(asset.get("summary"))
    summary["atomic_claim_projected_fact_count"] = len(projected)
    asset["summary"] = summary
    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "atomic_data_effect_claims_project_into_existing_fact_ledger": True,
            "atomic_claim_projection_reparses_source_text": False,
            "atomic_claim_projection_invents_object_binding": False,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["RECEIPT_SCHEMA", "project_atomic_claim_facts"]
