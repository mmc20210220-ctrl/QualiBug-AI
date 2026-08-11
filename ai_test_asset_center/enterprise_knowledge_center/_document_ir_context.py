"""Resolve pending Chinese references from source-preserving document structure IR.

This stage is mainline comprehension support, not a second fact extractor.  It uses
DOCX-native heading, list and table structure to identify the section containing an
already extracted source statement.  It never treats filename, block order or style
similarity as a business fact, and it promotes a pending fact only when the section
context identifies one unique object and/or actor.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable

CONTEXT_RECEIPT_SCHEMA = "qualibug.document-ir-context-resolution.v1"
_REFERENCE_PREFIXES = (
    "COREFERENCE_",
    "BUSINESS_SUBJECT_",
    "DOCUMENT_CONTEXT_",
)
_REFERENCE_SIGNAL_RE = re.compile(
    r"该|本|此|其|上述|前述|对应|相关|当前|该人员|该角色|由其|完成后|通过后|退回后"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique(values: Iterable[Any]) -> list[str]:
    return sorted({_text(value) for value in values if _text(value)})


def _alias_map(facts: list[dict[str, Any]]) -> dict[str, str]:
    from ._chinese_business_comprehension import _term_alias_map

    alias_map, _conflicts = _term_alias_map(facts)
    return alias_map


def _canonicalize(values: Iterable[Any], alias_map: dict[str, str]) -> list[str]:
    from ._chinese_business_comprehension import _canonicalize_names

    return _canonicalize_names((_text(value) for value in values), alias_map)


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value))


_BLOCK_SPECIFICITY = {
    "TABLE_CELL": 40,
    "LIST_ITEM": 30,
    "KEY_VALUE": 30,
    "NOTE": 20,
    "PARAGRAPH": 10,
    "TABLE": 0,
}


def _prefer_specific_blocks(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer cell/list spans over containing TABLE aggregates."""
    if len(candidates) <= 1:
        return candidates
    best = max(_BLOCK_SPECIFICITY.get(_text(row.get("type")), 0) for row in candidates)
    refined = [
        row
        for row in candidates
        if _BLOCK_SPECIFICITY.get(_text(row.get("type")), 0) == best
    ]
    return refined or candidates


def _fact_source_id(fact: dict[str, Any]) -> str:
    spans = _list(fact.get("source_spans"))
    span = _dict(spans[0]) if spans else {}
    return _text(span.get("source_id") or fact.get("source_id"))


def _known_names(asset: dict[str, Any], facts: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    objects: list[str] = []
    actors: list[str] = []
    for row in _list(asset.get("business_objects")):
        if isinstance(row, dict):
            objects.append(_text(row.get("object") or row.get("name")))
    for row in _list(asset.get("data_tables")):
        if isinstance(row, dict):
            objects.append(_text(row.get("name")))
    for row in _list(asset.get("roles")):
        if isinstance(row, dict):
            actors.append(_text(row.get("role") or row.get("name")))
    for row in _list(asset.get("permission_matrix")):
        if isinstance(row, dict):
            actors.append(_text(row.get("role") or row.get("actor")))
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        subject = _dict(fact.get("subject"))
        objects.extend(_list(subject.get("entity_refs")))
        actors.extend(_list(subject.get("actor_refs")))
        if _text(fact.get("kind")) == "TERM_ALIAS":
            objects.extend([fact.get("canonical_term"), fact.get("alias")])
    return sorted(_unique(objects), key=lambda item: (-len(item), item)), sorted(
        _unique(actors), key=lambda item: (-len(item), item)
    )


def _block_index(document_structure: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    rows = [
        row
        for row in _list(document_structure.get("blocks"))
        if isinstance(row, dict) and _text(row.get("block_id"))
    ]
    return {_text(row.get("block_id")): row for row in rows}, rows


def _statement_blocks(
    statement: str,
    blocks: list[dict[str, Any]],
    normalized_blocks: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    target = _normalized(statement)
    if not target:
        return []
    eligible = {
        "PARAGRAPH",
        "LIST_ITEM",
        "TABLE_CELL",
        "TABLE",
    }
    exact: list[dict[str, Any]] = []
    contained: list[dict[str, Any]] = []
    normalized = normalized_blocks or {
        id(row): _normalized(row.get("text")) for row in blocks
    }
    for block in blocks:
        if _text(block.get("region")) not in {"", "body"}:
            continue
        if _text(block.get("type")) not in eligible:
            continue
        block_text = normalized[id(block)]
        if not block_text:
            continue
        if block_text == target:
            exact.append(block)
        elif target in block_text:
            contained.append(block)
    return _prefer_specific_blocks(exact or contained)


def _heading_chain(block: dict[str, Any], index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    parent_id = _text(block.get("parent_id"))
    visited: set[str] = set()
    while parent_id and parent_id not in visited:
        visited.add(parent_id)
        parent = index.get(parent_id)
        if not parent:
            break
        if _text(parent.get("type")) == "HEADING":
            chain.append(parent)
        parent_id = _text(parent.get("parent_id"))
    chain.reverse()
    return chain


def _heading_mentions(chain: list[dict[str, Any]], names: list[str]) -> list[str]:
    # Filename and document root are intentionally absent: only explicit body heading
    # blocks may contribute formal context.
    text = " ".join(_text(row.get("text")) for row in chain)
    return [name for name in names if name and name in text]


def _section_parent_id(block: dict[str, Any], index: dict[str, dict[str, Any]]) -> str:
    parent_id = _text(block.get("parent_id"))
    while parent_id:
        parent = index.get(parent_id)
        if not parent:
            return ""
        if _text(parent.get("type")) == "HEADING":
            return parent_id
        parent_id = _text(parent.get("parent_id"))
    return ""


def _fact_block_map(
    facts: list[dict[str, Any]],
    source_id: str,
    blocks: list[dict[str, Any]],
    normalized_blocks: dict[int, str] | None = None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for fact in facts:
        if _fact_source_id(fact) != source_id:
            continue
        candidates = _statement_blocks(
            _text(fact.get("raw_statement")), blocks, normalized_blocks
        )
        if len(candidates) == 1:
            result[_text(fact.get("fact_id"))] = candidates[0]
    return result


def _prior_candidates(
    fact: dict[str, Any],
    current_block: dict[str, Any],
    facts: list[dict[str, Any]],
    fact_blocks: dict[str, dict[str, Any]],
    block_index: dict[str, dict[str, Any]],
    *,
    kind: str,
) -> list[str]:
    current_order = int(current_block.get("order") or 0)
    section_parent = _section_parent_id(current_block, block_index)
    rows: list[tuple[int, list[str]]] = []
    for other in facts:
        if other is fact or _text(other.get("status")) != "ACCEPTED":
            continue
        other_block = fact_blocks.get(_text(other.get("fact_id")))
        if not other_block:
            continue
        if int(other_block.get("order") or 0) >= current_order:
            continue
        if _section_parent_id(other_block, block_index) != section_parent:
            continue
        subject = _dict(other.get("subject"))
        values = (
            _list(subject.get("entity_refs"))
            if kind == "object"
            else _list(subject.get("actor_refs"))
        )
        if values:
            rows.append((int(other_block.get("order") or 0), _unique(values)))
    rows.sort(key=lambda row: row[0], reverse=True)
    return _unique(value for _, values in rows[:3] for value in values)


def _unique_candidate(
    heading_values: list[str],
    prior_values: list[str],
    *,
    alias_map: dict[str, str] | None = None,
) -> tuple[str, str, list[str]]:
    alias_map = alias_map or {}
    headings = _canonicalize(heading_values, alias_map)
    priors = _canonicalize(prior_values, alias_map)
    if len(headings) == 1 and len(priors) == 1:
        if headings[0] == priors[0]:
            return headings[0], "document_ir_heading_and_same_section_prior_fact", []
        return "", "", [f"DOCUMENT_IR_CONTEXT_CONFLICT:{headings[0]}_vs_{priors[0]}"]
    if len(headings) == 1:
        return headings[0], "unique_document_ir_heading_context", []
    if len(headings) > 1:
        return "", "", ["DOCUMENT_IR_HEADING_AMBIGUOUS:" + ",".join(headings)]
    if len(priors) == 1:
        return priors[0], "unique_document_ir_prior_fact_in_same_section", []
    if len(priors) > 1:
        return "", "", ["DOCUMENT_IR_PRIOR_FACT_AMBIGUOUS:" + ",".join(priors)]
    return "", "", []


def apply_document_ir_context(
    asset: dict[str, Any], structured_sources: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Use source-preserving structure blocks to resolve pending Chinese references."""
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    object_names, actor_names = _known_names(asset, facts)
    alias_map = _alias_map(facts)
    sources = [row for row in structured_sources if isinstance(row, dict)]
    source_map = {_text(row.get("source_id")): row for row in sources if _text(row.get("source_id"))}
    resolutions: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    facts_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        facts_by_source[_fact_source_id(fact)].append(fact)

    for source_id, source_facts in facts_by_source.items():
        source = source_map.get(source_id)
        if not source:
            continue
        structure = _dict(source.get("document_structure"))
        block_index, blocks = _block_index(structure)
        if not blocks:
            continue
        normalized_blocks = {
            id(row): _normalized(row.get("text")) for row in blocks
        }
        fact_blocks = _fact_block_map(
            source_facts, source_id, blocks, normalized_blocks
        )
        for fact in source_facts:
            ambiguities = _unique(_list(fact.get("ambiguities")))
            statement = _text(fact.get("raw_statement"))
            if _text(fact.get("status")) != "PENDING":
                continue
            if not any(value.startswith(_REFERENCE_PREFIXES) for value in ambiguities):
                continue
            if not _REFERENCE_SIGNAL_RE.search(statement):
                continue
            candidates = _statement_blocks(statement, blocks, normalized_blocks)
            if len(candidates) != 1:
                unresolved.append(
                    {
                        "fact_id": fact.get("fact_id"),
                        "reason": "DOCUMENT_IR_STATEMENT_BLOCK_NOT_UNIQUE",
                        "candidate_block_ids": [row.get("block_id") for row in candidates],
                    }
                )
                continue
            block = candidates[0]
            chain = _heading_chain(block, block_index)
            object_value, object_method, object_errors = _unique_candidate(
                _heading_mentions(chain, object_names),
                _prior_candidates(
                    fact,
                    block,
                    source_facts,
                    fact_blocks,
                    block_index,
                    kind="object",
                ),
                alias_map=alias_map,
            )
            actor_value, actor_method, actor_errors = _unique_candidate(
                _heading_mentions(chain, actor_names),
                _prior_candidates(
                    fact,
                    block,
                    source_facts,
                    fact_blocks,
                    block_index,
                    kind="actor",
                ),
                alias_map=alias_map,
            )
            errors = [*object_errors, *actor_errors]
            if errors:
                fact["ambiguities"] = _unique([*ambiguities, *errors])
                unresolved.append(
                    {
                        "fact_id": fact.get("fact_id"),
                        "reason": errors,
                        "block_id": block.get("block_id"),
                    }
                )
                continue
            if not object_value and not actor_value:
                unresolved.append(
                    {
                        "fact_id": fact.get("fact_id"),
                        "reason": "DOCUMENT_IR_NO_UNIQUE_REFERENCE",
                        "block_id": block.get("block_id"),
                    }
                )
                continue

            subject = _dict(fact.get("subject"))
            entity_refs = _unique([*_list(subject.get("entity_refs")), object_value])
            actor_refs = _unique([*_list(subject.get("actor_refs")), actor_value])
            evidence = list(_list(subject.get("resolution_evidence")))
            heading_evidence = [
                {
                    "block_id": row.get("block_id"),
                    "text": row.get("text"),
                    "source_locator": row.get("source_locator"),
                    "style": row.get("style"),
                    "structure_evidence": row.get("structure_evidence"),
                }
                for row in chain
            ]
            if object_value:
                evidence.append(
                    {
                        "mention": "中文省略/指代对象",
                        "resolved_ref": object_value,
                        "method": object_method,
                        "document_block_id": block.get("block_id"),
                        "heading_evidence": heading_evidence,
                        "confidence": 0.93 if object_method.startswith("unique_document_ir_heading") else 0.86,
                    }
                )
            if actor_value:
                evidence.append(
                    {
                        "mention": "中文省略/指代角色",
                        "resolved_ref": actor_value,
                        "method": actor_method,
                        "document_block_id": block.get("block_id"),
                        "heading_evidence": heading_evidence,
                        "confidence": 0.93 if actor_method.startswith("unique_document_ir_heading") else 0.86,
                    }
                )
            fact["subject"] = {
                **subject,
                "entity_refs": entity_refs,
                "actor_refs": actor_refs,
                "resolution_evidence": evidence,
            }
            fact["object"] = {
                **_dict(fact.get("object")),
                "entity_refs": entity_refs,
            }
            remaining = [
                value
                for value in ambiguities
                if not value.startswith(_REFERENCE_PREFIXES)
            ]
            fact["ambiguities"] = remaining
            fact["status"] = "ACCEPTED" if not remaining else "PENDING"
            fact["document_structure_context"] = {
                "schema": CONTEXT_RECEIPT_SCHEMA,
                "source_backed": True,
                "source_id": source_id,
                "block_id": block.get("block_id"),
                "block_type": block.get("type"),
                "source_locator": block.get("source_locator"),
                "heading_block_ids": [row.get("block_id") for row in chain],
                "filename_context_used": False,
                "document_order_as_business_flow_used": False,
                "cross_document_resolution_used": False,
            }
            resolutions.append(
                {
                    "resolution_id": _stable_id(
                        "document_ir_resolution", fact.get("fact_id"), block.get("block_id")
                    ),
                    "fact_id": fact.get("fact_id"),
                    "resolved_object": object_value,
                    "resolved_actor": actor_value,
                    "block_id": block.get("block_id"),
                    "heading_block_ids": [row.get("block_id") for row in chain],
                    "status": fact.get("status"),
                }
            )

    ledger["items"] = facts
    ledger["document_structure_context_contract"] = {
        "source_structure_required": True,
        "same_source_only": True,
        "unique_statement_block_required": True,
        "heading_or_same_section_prior_fact_required": True,
        "filename_context_forbidden": True,
        "document_order_is_not_business_flow": True,
        "cross_document_proximity_forbidden": True,
    }
    asset["business_fact_ledger"] = ledger
    asset["document_ir_context_resolution_receipt"] = {
        "schema": CONTEXT_RECEIPT_SCHEMA,
        "resolved_fact_count": len(resolutions),
        "unresolved_fact_count": len(unresolved),
        "resolutions": resolutions,
        "unresolved": unresolved,
        "filename_context_used": False,
        "cross_document_proximity_resolution_allowed": False,
        "document_order_is_business_flow": False,
    }
    return asset


__all__ = ["CONTEXT_RECEIPT_SCHEMA", "apply_document_ir_context"]
