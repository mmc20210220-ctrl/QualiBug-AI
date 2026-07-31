"""Project typed object-relation facts into the existing object-graph input."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

RECEIPT_SCHEMA = "qualibug.typed-object-relation-projection.v1"
_DERIVATION = "typed_fact_object_relation"
_COORDINATE_GRAMMAR_RE = re.compile(
    r"如果|若|一旦|则|否则|只有|仅当|必须|应当|务必|不得|严禁|禁止|"
    r"不允许|不可|不能|可以|允许|有权|无权|并且|同时满足|以及|或者|"
    r"其中之一|每个|每张|每条|每份|一个|多个|多条|若干|(?<![并])且(?![不])"
)
_CONNECTOR_ONLY_RE = re.compile(r"^(?:并|且|和|与|或|及|以及|则|否则)$")


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


def _coordinate_is_grammar_fragment(value: str) -> bool:
    coordinate = _text(value)
    return bool(
        not coordinate
        or _CONNECTOR_ONLY_RE.fullmatch(coordinate)
        or _COORDINATE_GRAMMAR_RE.search(coordinate)
    )


def _evidence(fact: dict[str, Any]) -> dict[str, Any]:
    spans = [dict(row) for row in _list(fact.get("source_spans")) if isinstance(row, dict)]
    if not spans:
        return {}
    exact = next(
        (
            span
            for span in spans
            if _text(span.get("document_block_id"))
            or _text(span.get("address_kind")) in {"EXACT_SOURCE_LOCATOR", "PAGE_BBOX"}
        ),
        spans[0],
    )
    return {
        "source_id": exact.get("source_id"),
        "source_locator": exact.get("locator") or exact.get("source_locator"),
        "quote": exact.get("quote") or fact.get("raw_statement"),
        "quote_hash": exact.get("quote_hash"),
        "document_block_id": exact.get("document_block_id"),
        "address_kind": exact.get("address_kind"),
        "fact_id": fact.get("fact_id"),
        "derivation": _DERIVATION,
    }


def _reject_grammar_fragment_relation(
    fact: dict[str, Any],
    *,
    sources: list[str],
    targets: list[str],
    relation: str,
    invalid_coordinates: list[str],
) -> dict[str, Any]:
    reason = "TYPED_OBJECT_RELATION_COORDINATE_GRAMMAR_FRAGMENT"
    fact["status"] = "REJECTED"
    fact["formal_promotion_allowed"] = False
    ambiguities = [_text(value) for value in _list(fact.get("ambiguities")) if _text(value)]
    if reason not in ambiguities:
        ambiguities.append(reason)
    fact["ambiguities"] = ambiguities
    fact["typed_relation_coordinate_validation"] = {
        "status": "REJECTED",
        "reason": reason,
        "invalid_coordinates": invalid_coordinates,
        "automatic_endpoint_repair_allowed": False,
        "operator_selection_required": False,
    }
    return {
        "fact_id": fact.get("fact_id"),
        "source_candidates": sources,
        "target_candidates": targets,
        "relation": relation,
        "invalid_coordinates": invalid_coordinates,
        "reason": reason,
    }


def project_typed_object_relations(asset: dict[str, Any]) -> dict[str, Any]:
    ledger = _dict(asset.get("business_fact_ledger"))
    all_facts = [
        dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)
    ]
    typed_facts = [
        fact
        for fact in all_facts
        if _text(fact.get("status")) == "ACCEPTED"
        and _text(fact.get("fact_type")).upper() == "OBJECT_RELATION"
    ]
    existing = [
        dict(row) for row in _list(asset.get("entity_relations")) if isinstance(row, dict)
    ]
    identity_to_edge = {
        (
            _text(row.get("from_entity") or row.get("from") or row.get("source")),
            _text(row.get("relation") or row.get("relation_type")).upper(),
            _text(row.get("to_entity") or row.get("to") or row.get("target")),
            _text(row.get("fact_ref")),
        ): _text(row.get("edge_id") or row.get("relation_id"))
        for row in existing
    }
    projected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    marked = 0
    for fact in typed_facts:
        sources, targets = _relation_endpoints(fact)
        relation = _text(fact.get("predicate") or fact.get("relation_type")).upper()
        evidence = _evidence(fact)
        invalid_coordinates = sorted(
            {
                coordinate
                for coordinate in [*sources, *targets]
                if _coordinate_is_grammar_fragment(coordinate)
            }
        )
        if invalid_coordinates:
            rejected.append(
                _reject_grammar_fragment_relation(
                    fact,
                    sources=sources,
                    targets=targets,
                    relation=relation,
                    invalid_coordinates=invalid_coordinates,
                )
            )
            continue
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
        edge_id = identity_to_edge.get(identity) or _stable_id(
            source, relation, target, fact.get("fact_id")
        )
        if identity not in identity_to_edge:
            identity_to_edge[identity] = edge_id
            projected.append(
                {
                    "edge_id": edge_id,
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
        fact["object_graph_projection_authority"] = "EXISTING_ENTITY_RELATIONS"
        fact["object_graph_projection_ref"] = edge_id
        fact["object_graph_text_reparse_allowed"] = False
        fact["typed_relation_coordinate_validation"] = {
            "status": "PASS",
            "automatic_endpoint_repair_allowed": False,
        }
        marked += 1
    ledger["items"] = all_facts
    ledger["typed_object_relation_projection_authority_marked_count"] = marked
    ledger["typed_object_relation_grammar_rejected_count"] = len(rejected)
    asset["business_fact_ledger"] = ledger
    asset["entity_relations"] = [*existing, *projected]
    asset["typed_object_relation_projection_receipt"] = {
        "schema": RECEIPT_SCHEMA,
        "status": "BLOCKED" if blocked else "PASS",
        "typed_relation_fact_count": len(typed_facts),
        "projected_relation_count": len(projected),
        "authority_marked_fact_count": marked,
        "grammar_fragment_rejected_count": len(rejected),
        "grammar_fragment_rejected_relations": rejected,
        "blocked_relation_count": len(blocked),
        "blocked_relations": blocked,
        "raw_statement_reparsed": False,
        "automatic_endpoint_inference_allowed": False,
        "automatic_endpoint_repair_allowed": False,
        "deterministic_grammar_fragment_rejection_blocks_gate": False,
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
            "typed_object_relation_projection_repairs_endpoints": False,
            "typed_object_relation_grammar_fragments_are_rejected": True,
            "typed_object_relation_fact_marks_projection_authority": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["RECEIPT_SCHEMA", "project_typed_object_relations"]
