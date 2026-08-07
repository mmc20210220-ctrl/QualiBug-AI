"""Chinese Semantic Frame enrichment — clause structure, list inheritance,
table coordinates (SPEC: QUALIBUG-CHINESE-SEMANTIC-ROOT-FIX-V1, P0-B).

Contract:
- This stage enriches EXISTING frames (qualibug.chinese-semantic-frame.v1)
  with the clause structure the parser found for their source block: list
  parent conditions are inherited by list children, table row/column headers
  become actor/condition mention candidates for table cells, enumeration
  action candidates are added to action mentions, and exception nodes are
  merged. Nothing here re-parses raw text and nothing here binds semantics to
  technical objects (that is P0-D grounding).
- The frame's fact-derived slots stay authoritative: enrichment only ADDS
  mentions/conditions the fact missed ("不丢"), never removes or overrides.
  When the enumeration interpretation is AMBIGUOUS the raw text is kept and
  no mention is selected.
- The semantic signature is recomputed after enrichment (conditions and actor
  mentions are typed slots), so frames stay fail-closed valid; P0-A signature
  stability is preserved because the quote/evidence never enters the
  signature.
- The enrichment is idempotent: re-running on an enriched ledger deduplicates
  by raw text and produces identical frames.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .chinese_clause_parser import parse_block_text
from .chinese_context_envelope import (
    block_context_for,
    locate_unique_block,
)
from .chinese_semantic_ledger_adapter import frames_from_asset
from .chinese_semantic_schema import (
    semantic_signature,
    validate_semantic_frame,
)

CHINESE_SEMANTIC_FRAME_CLAUSE_STRUCTURE_SCHEMA = (
    "qualibug.chinese-semantic-frame-clause-structure.v1"
)
CHINESE_SEMANTIC_FRAME_ENRICHMENT_RECEIPT_SCHEMA = (
    "qualibug.chinese-semantic-frame-enrichment-receipt.v1"
)

# Frame types whose semantics may carry state conditions from table column
# headers; structural frames (relations, formulas, cardinality) do not.
_TABLE_CONDITION_FRAME_TYPES = frozenset(
    {
        "PERMISSION_RULE",
        "OWNERSHIP_RULE",
        "SCOPE_RULE",
        "STATE_GUARD",
        "STATE_TRANSITION",
        "VALIDATION_RULE",
        "DATA_VISIBILITY_RULE",
    }
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return " ".join(_text(value).split()).strip()


def _resolve_frame_block(
    asset: dict[str, Any],
    frame: dict[str, Any],
) -> dict[str, Any]:
    """Locate the frame's source block via block id, then unique quote match."""
    span = _dict(frame.get("source_span"))
    source_id = _text(span.get("source_id"))
    block_id = _text(span.get("document_block_id"))
    if block_id:
        entry = block_context_for(asset, source_id, block_id)
        if entry:
            return entry
    return locate_unique_block(
        asset,
        source_id=source_id,
        quote=_text(span.get("quote")),
    )


def _conditions_of_block(
    asset: dict[str, Any],
    block: dict[str, Any],
    *,
    origin: str,
) -> list[dict[str, Any]]:
    """Conditions from a block's clause tree (fallback: parse block text)."""
    from .chinese_clause_parser import clause_tree_for_block

    tree = clause_tree_for_block(asset, _text(block.get("block_id")))
    if not tree:
        try:
            tree = parse_block_text(
                _text(block.get("text")),
                source_id=_text(block.get("source_id")),
                block_id=_text(block.get("block_id")),
                block_type=_text(block.get("block_type")),
                locator=_text(block.get("locator")),
            )
        except ValueError:
            return []
    return [
        {
            "raw": _norm(row.get("raw")),
            "logic_group": _norm(row.get("logic_group")) or "main",
        }
        for row in _list(tree.get("conditions"))
        if isinstance(row, dict) and _norm(row.get("raw"))
    ]


def _next_condition_id(conditions: list[dict[str, Any]]) -> str:
    highest = 0
    for row in conditions:
        if not isinstance(row, dict):
            continue
        condition_id = _text(row.get("condition_id"))
        if condition_id.startswith("condition:"):
            try:
                highest = max(highest, int(condition_id[len("condition:") :]))
            except ValueError:
                continue
    return f"condition:{highest + 1}"


def _enrich_frame_structure(
    asset: dict[str, Any],
    frame: dict[str, Any],
) -> dict[str, Any]:
    """Enrich one frame in place; returns its clause_structure block."""
    block = _resolve_frame_block(asset, frame)
    if not block:
        return {
            "schema": CHINESE_SEMANTIC_FRAME_CLAUSE_STRUCTURE_SCHEMA,
            "status": "UNLOCATED",
            "reason_codes": ["DOCUMENT_STRUCTURE_MISSING"],
        }

    structure: dict[str, Any] = {
        "schema": CHINESE_SEMANTIC_FRAME_CLAUSE_STRUCTURE_SCHEMA,
        "status": "ENRICHED",
        "source_block_id": _text(block.get("block_id")),
        "source_block_type": _text(block.get("block_type")),
        "reason_codes": [],
    }
    block_type = _text(block.get("block_type"))
    frame_type = _text(frame.get("frame_type"))

    # ── 1. list parent condition inheritance (SPEC §7.3) ──
    list_parent_conditions: list[dict[str, Any]] = []
    if block_type == "LIST_ITEM":
        list_context = _dict(block.get("list_context"))
        ancestor_chain = [
            _text(row) for row in _list(list_context.get("list_ancestor_chain"))
        ]
        for ancestor_id in ancestor_chain:
            ancestor = block_context_for(
                asset, _text(block.get("source_id")), ancestor_id
            )
            if not ancestor:
                continue
            for raw in _conditions_of_block(
                asset, ancestor, origin="list_parent_inheritance"
            ):
                if raw["raw"] not in {
                    _norm(row.get("raw")) for row in list_parent_conditions
                }:
                    list_parent_conditions.append(raw)
        if list_parent_conditions:
            conditions = frame["conditions"]
            existing_raws = {_norm(row.get("raw")) for row in conditions}
            for inherited in list_parent_conditions:
                if inherited["raw"] in existing_raws:
                    continue
                conditions.append(
                    {
                        "condition_id": _next_condition_id(conditions),
                        "raw": inherited["raw"],
                        "subject_concept_ref": "",
                        "field_concept_ref": "",
                        "operator": "",
                        "value_concept_ref": "",
                        "logic_group": inherited["logic_group"] or "main",
                        "origin": "list_parent_inheritance",
                        "resolution_status": "RESOLVED",
                        "evidence": [],
                    }
                )
                existing_raws.add(inherited["raw"])
            frame["conditions"] = conditions

    # ── 2. table row/column header context (SPEC §7.2) ──
    table_context = _dict(block.get("table_context"))
    if block_type == "TABLE_CELL" and table_context:
        row_header = _norm(table_context.get("row_header"))
        column_header = _norm(table_context.get("column_header"))
        structure["table_context_used"] = {
            "table_id": _text(table_context.get("table_id")),
            "row_header": row_header,
            "column_header": column_header,
            "row_index": table_context.get("row_index"),
            "column_index": table_context.get("column_index"),
        }
        # Row header → actor mention candidate when the source omitted it.
        actor = frame["actor"]
        if row_header and actor.get("resolution_status") == "OMITTED":
            mentions = [_norm(item) for item in _list(actor.get("mentions"))]
            if row_header not in mentions:
                actor["mentions"] = mentions + [row_header]
                actor["resolution_status"] = "RESOLVED"
                actor["evidence"] = _list(actor.get("evidence")) + [
                    {
                        "origin": "table_row_header",
                        "table_id": _text(table_context.get("table_id")),
                        "row_index": table_context.get("row_index"),
                    }
                ]
                frame["resolution"]["reason_codes"] = [
                    code
                    for code in _list(frame["resolution"].get("reason_codes"))
                    if _text(code) != "OMITTED_ACTOR_UNRESOLVED"
                ]
                frame["actor"] = actor
        # Column header → state condition mention.
        if column_header and frame_type in _TABLE_CONDITION_FRAME_TYPES:
            conditions = frame["conditions"]
            existing_raws = {_norm(row.get("raw")) for row in conditions}
            if column_header not in existing_raws:
                conditions.append(
                    {
                        "condition_id": _next_condition_id(conditions),
                        "raw": column_header,
                        "subject_concept_ref": "",
                        "field_concept_ref": "",
                        "operator": "",
                        "value_concept_ref": "",
                        "logic_group": "main",
                        "origin": "table_column_header",
                        "resolution_status": "RESOLVED",
                        "evidence": [
                            {
                                "origin": "table_column_header",
                                "table_id": _text(table_context.get("table_id")),
                                "column_index": table_context.get("column_index"),
                            }
                        ],
                    }
                )
            frame["conditions"] = conditions

    # ── 3. clause tree structure: enumeration mentions + exceptions ──
    from .chinese_clause_parser import clause_tree_for_block

    tree = clause_tree_for_block(asset, _text(block.get("block_id")))
    if tree:
        enumeration = dict(_dict(tree.get("enumeration")))
        structure["enumeration"] = {
            "joiner": _text(enumeration.get("joiner")),
            "part_count": enumeration.get("part_count", 0),
            "interpretation": _text(enumeration.get("interpretation")),
        }
        structure["negation_scope"] = dict(_dict(tree.get("negation_scope")))
        tree_modality = _text(_dict(tree.get("modality")).get("type"))
        structure["modality_cross_check"] = {
            "tree_modality": tree_modality,
            "frame_modality": _text(_dict(frame.get("modality")).get("type")),
            "matches": tree_modality
            == _text(_dict(frame.get("modality")).get("type")),
        }
        if (
            enumeration.get("part_count", 0) > 1
            and "CLAUSE_SEGMENTATION_AMBIGUOUS" not in _list(tree.get("reason_codes"))
        ):
            action = frame["action"]
            mentions = [_norm(item) for item in _list(action.get("mentions"))]
            added = False
            for clause in _list(tree.get("clauses")):
                if not isinstance(clause, dict):
                    continue
                mention = _norm(clause.get("action_mention"))
                if mention and mention not in mentions:
                    mentions.append(mention)
                    added = True
            if added:
                action["mentions"] = mentions
                frame["action"] = action
        # Exception nodes merge (never override fact-derived exceptions).
        tree_exceptions = [
            row for row in _list(tree.get("exceptions")) if isinstance(row, dict)
        ]
        if tree_exceptions:
            frame_exceptions = frame["exceptions"]
            existing_raws = {
                _norm(row.get("raw")) for row in frame_exceptions
            }
            for row in tree_exceptions:
                raw = _norm(row.get("raw"))
                if not raw or raw in existing_raws:
                    continue
                frame_exceptions.append(
                    {
                        "exception_id": _text(row.get("exception_id")) or f"exception:{len(frame_exceptions) + 1}",
                        "raw": raw,
                        "logic": "AND",
                        "clauses": [
                            dict(clause)
                            for clause in _list(row.get("clauses"))
                            if isinstance(clause, dict)
                        ],
                        "kind": _text(row.get("kind")),
                        "origin": "clause_parser",
                        "resolution_status": "RESOLVED",
                        "evidence": [],
                    }
                )
                existing_raws.add(raw)
            frame["exceptions"] = frame_exceptions

    # ── signature recompute keeps the frame fail-closed valid ──
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)
    return structure


def enrich_frames_with_clause_structure(asset: dict[str, Any]) -> dict[str, Any]:
    """Enrich every frame in the frame ledger with clause structure (in place)."""
    ledger = _dict(asset.get("chinese_semantic_frame_ledger"))
    frames = [row for row in _list(ledger.get("items")) if isinstance(row, dict)]
    enriched = 0
    unlocated = 0
    no_signal = 0
    reason_counts: Counter = Counter()
    for frame in frames:
        structure = _enrich_frame_structure(asset, frame)
        status = _text(structure.get("status"))
        if status == "ENRICHED":
            enriched += 1
        elif status == "UNLOCATED":
            unlocated += 1
        else:
            no_signal += 1
        for code in _list(structure.get("reason_codes")):
            reason_counts[_text(code)] += 1
        frame["clause_structure"] = structure
        errors = validate_semantic_frame(frame)
        if errors:
            raise ValueError(
                "chinese_semantic_frame_invalid_after_enrichment:"
                + ",".join(sorted(errors))
            )
    if ledger:
        ledger["enrichment_receipt"] = {
            "schema": CHINESE_SEMANTIC_FRAME_ENRICHMENT_RECEIPT_SCHEMA,
            "status": "PASS" if not reason_counts else "PARTIAL",
            "enriched_count": enriched,
            "unlocated_count": unlocated,
            "no_signal_count": no_signal,
            "reason_code_counts": dict(sorted(reason_counts.items())),
            "signature_recomputed": True,
        }
        closure = dict(_dict(ledger.get("closure")))
        closure["enriched_frame_count"] = enriched
        closure["unlocated_frame_count"] = unlocated
        ledger["closure"] = closure
        asset["chinese_semantic_frame_ledger"] = ledger
    return asset
