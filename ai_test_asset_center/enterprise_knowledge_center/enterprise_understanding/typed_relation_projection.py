"""Project typed object-relation facts into the existing object-graph input."""
from __future__ import annotations

import hashlib
import json
from typing import Any

RECEIPT_SCHEMA = "qualibug.typed-object-relation-projection.v1"
_DERIVATION = "typed_fact_object_relation"


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
    return "edge:typed-relation:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _relation_endpoints(fact: dict[str, Any]) -> tuple[list[str], list[str]]:
    subject = _dict(fact.get("subject"))
    obj = _dict(fact.get("object"))
    sources = sorted({_text(value) for value in _list(subject.get("entity_refs")) if _text(value)})
    targets = sorted({_text(value) for value in _list(obj.get("entity_refs")) if _text(value)})
    return sources, targets


def _evidence(fact: dict[str, Any]) -> dict[str, Any]:
    spans = [dict(row) for row in _list(fact.get("source_spans")) if isinstance(row, dict)]
    if not spans:
        return {}
    span = spans[0]
    return {
        "source_id": span.get("source_id"),
        "source_locator": span.get("locator") or span.get("source_locator"),
        "quote": span.get("quote") or fact.get("raw_statement"),
        "quote_hash": span.get("quote_hash"),
        "document_block_id": span.get("document_block_id"),
        "address_kind": span.get("address_kind"),
        "fact_id": fact.get("fact_id"),
        "derivation": _DERIVATION,
    }


def project_typed_object_relations(asset: dict[str, Any]) -> dict[str, Any]:
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [
        dict(row)
        for row in _list(ledger.get("items"))
        if isinstance(row, dict)
        and _text(row.get("status")) == "ACCEPTED"
        and _text(row.get("fact_type")).upper() == "OBJECT_RELATION"
    ]
    existing = [
        dict(row) for row in _list(asset.get("entity_relations")) if isinstance(row, dict)
    ]
    identities = {
        (
            _text(row.get("from_entity") or row.get("from") or row.get("source")),
            _text(row.get("relation") or row.get("relation_type")).upper(),
            _text(row.get("to_entity") or row.get("to") or row.get("target")),
            _text(row.get("fact_ref")),
        )
        for row in existing
    }
    projected: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for fact in facts:
        sources, targets = _relation_endpoints(fact)
        relation = _text(fact.get("predicate") or fact.get("relation_type")).upper()
        evidence = _evidence(fact)
        if len(sources) != 1 or len(targets) != 1 or not relation or not evidence:
            blocked.append(
                {
                    "fact_id": fact.get("fact_id"),
                    "source_candidates": sources,
                    "target_candidates": targets,
                    "relation": relation,
                    "reason": "TYPED_OBJECT_RELATION_BINDING_INCOMPLETE",
                }
            )
            continue
        source, target = sources[0], targets[0]
        if source == target:
            blocked.append(
                {
                    "fact_id": fact.get("fact_id"),
                    "source_candidates": sources,
                    "target_candidates": targets,
                    "relation": relation,
                    "reason": "TYPED_OBJECT_RELATION_SELF_EDGE_REQUIRES_EXPLICIT_MODEL",
                }
            )
            continue
        identity = (source, relation, target, _text(fact.get("fact_id")))
        if identity in identities:
            continue
        identities.add(identity)
        projected.append(
            {
                "edge_id": _stable_id(source, relation, target, fact.get("fact_id")),
                "from_entity": source,
                "to_entity": target,
                "relation": relation,
                "status": "accepted",
                "source_id": evidence.get("source_id"),
                "fact_ref": fact.get("fact_id"),
                "conditions": list(_list(fact.get("conditions"))),
                "exceptions": list(_list(fact.get("exception_scope") or fact.get("exceptions"))),
                "evidence": evidence,
                "derivation": _DERIVATION,
                "automatic_endpoint_inference_allowed": False,
            }
        )
    asset["entity_relations"] = [*existing, *projected]
    asset["typed_object_relation_projection_receipt"] = {
        "schema": RECEIPT_SCHEMA,
        "status": "BLOCKED" if blocked else "PASS",
        "typed_relation_fact_count": len(facts),
        "projected_relation_count": len(projected),
        "blocked_relation_count": len(blocked),
        "blocked_relations": blocked,
        "raw_statement_reparsed": False,
        "automatic_endpoint_inference_allowed": False,
        "existing_object_graph_input_reused": True,
    }
    if blocked:
        gate = _dict(asset.get("enterprise_comprehension_gate"))
        gate["status"] = "BLOCKED_TYPED_OBJECT_RELATION_BINDING_INCOMPLETE"
        gate["entry_allowed"] = False
        gate["required_operator_action"] = (
            "resolve typed object relation source/target/relation/evidence slots"
        )
        asset["enterprise_comprehension_gate"] = gate
        gaps = [
            dict(row)
            for row in _list(asset.get("coverage_gaps"))
            if isinstance(row, dict)
            and _text(row.get("kind"))
            != "BLOCKED_TYPED_OBJECT_RELATION_BINDING_INCOMPLETE"
        ]
        gaps.append(
            {
                "kind": "BLOCKED_TYPED_OBJECT_RELATION_BINDING_INCOMPLETE",
                "gap_type": "typed_object_relation_binding_incomplete",
                "source_id": "*",
                "blocked_relations": blocked,
                "operator_action": gate["required_operator_action"],
            }
        )
        asset["coverage_gaps"] = gaps
    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "typed_object_relations_feed_existing_object_graph": True,
            "typed_object_relation_projection_reparses_text": False,
            "typed_object_relation_projection_infers_endpoints": False,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["RECEIPT_SCHEMA", "project_typed_object_relations"]
