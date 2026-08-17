"""Chinese Semantic Frame enrichment — clause structure, list-scope inheritance,
table coordinates (SPEC: QUALIBUG-CHINESE-SEMANTIC-ROOT-FIX-V1, P0-B).

Contract:
- This stage enriches EXISTING frames (qualibug.chinese-semantic-frame.v1)
  with the clause structure the parser found for their source block: list
  parent conditions, exception scopes and explicit time windows are inherited
  by list children; table row/column headers become actor/condition mention
  candidates and typed time-header constraints for table cells; enumeration
  action candidates are added to action mentions; and own-block time/exception
  nodes are merged. Nothing here binds semantics to technical objects (that is
  P0-D grounding).
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

from .chinese_clause_parser import (
    extract_explicit_time_constraints,
    parse_block_text,
)
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


def _exceptions_of_block(
    asset: dict[str, Any],
    block: dict[str, Any],
) -> list[dict[str, Any]]:
    """Source-bound exceptions declared by one structural ancestor block."""
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
            "exception_id": _text(row.get("exception_id")),
            "raw": _norm(row.get("raw")),
            "kind": _text(row.get("kind")),
            "clauses": [
                dict(clause)
                for clause in _list(row.get("clauses"))
                if isinstance(clause, dict)
            ],
        }
        for row in _list(tree.get("exceptions"))
        if isinstance(row, dict) and _norm(row.get("raw"))
    ]


def _time_constraints_of_block(
    asset: dict[str, Any],
    block: dict[str, Any],
) -> list[dict[str, Any]]:
    """Explicit typed time windows declared by one source block."""
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
        dict(row)
        for row in _list(tree.get("time_constraints"))
        if isinstance(row, dict)
    ]


def _time_constraint_identity(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        _norm(row.get(key))
        for key in (
            "anchor",
            "relation",
            "duration",
            "operator",
            "value",
            "unit",
            "deadline",
        )
    )


def _lineaged_time_constraint(
    row: dict[str, Any],
    *,
    origin: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        **dict(row),
        "origin": origin,
        "evidence": [{**dict(evidence), "origin": origin}],
    }


def _merge_time_constraints(
    frame: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    constraints = frame["time_constraints"]
    existing = {
        _time_constraint_identity(row)
        for row in constraints
        if isinstance(row, dict)
    }
    for candidate in candidates:
        identity = _time_constraint_identity(candidate)
        if not any(identity) or identity in existing:
            continue
        constraints.append(dict(candidate))
        existing.add(identity)
    frame["time_constraints"] = constraints


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
        "list_parent_time_constraint_count": 0,
        "table_time_constraint_count": 0,
        "own_time_constraint_count": 0,
    }
    block_type = _text(block.get("block_type"))
    frame_type = _text(frame.get("frame_type"))

    # ── 1. list parent condition/exception scope inheritance (SPEC §7.3) ──
    list_parent_conditions: list[dict[str, Any]] = []
    list_parent_exceptions: list[dict[str, Any]] = []
    list_parent_time_constraints: list[dict[str, Any]] = []
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
            for exception in _exceptions_of_block(asset, ancestor):
                identity = (exception["raw"], exception["kind"])
                if identity not in {
                    (row["raw"], row["kind"])
                    for row in list_parent_exceptions
                }:
                    list_parent_exceptions.append(
                        {
                            **exception,
                            "source_id": _text(ancestor.get("source_id")),
                            "document_block_id": _text(ancestor.get("block_id")),
                            "locator": _text(ancestor.get("locator")),
                        }
                    )
            for time_constraint in _time_constraints_of_block(asset, ancestor):
                identity = _time_constraint_identity(time_constraint)
                if not any(identity) or identity in {
                    _time_constraint_identity(row)
                    for row in list_parent_time_constraints
                }:
                    continue
                list_parent_time_constraints.append(
                    _lineaged_time_constraint(
                        time_constraint,
                        origin="list_parent_time_inheritance",
                        evidence={
                            "source_id": _text(ancestor.get("source_id")),
                            "document_block_id": _text(ancestor.get("block_id")),
                            "locator": _text(ancestor.get("locator")),
                        },
                    )
                )
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
        if list_parent_exceptions:
            frame_exceptions = frame["exceptions"]
            existing_identities = {
                (_norm(row.get("raw")), _text(row.get("kind")))
                for row in frame_exceptions
                if isinstance(row, dict)
            }
            for inherited in list_parent_exceptions:
                identity = (inherited["raw"], inherited["kind"])
                if identity in existing_identities:
                    continue
                frame_exceptions.append(
                    {
                        "exception_id": inherited["exception_id"]
                        or f"exception:{len(frame_exceptions) + 1}",
                        "raw": inherited["raw"],
                        "logic": "AND",
                        "clauses": [dict(row) for row in inherited["clauses"]],
                        "kind": inherited["kind"],
                        "origin": "list_parent_exception_inheritance",
                        "resolution_status": "RESOLVED",
                        "evidence": [
                            {
                                "origin": "list_parent_exception_inheritance",
                                "source_id": inherited["source_id"],
                                "document_block_id": inherited["document_block_id"],
                                "locator": inherited["locator"],
                            }
                        ],
                    }
                )
                existing_identities.add(identity)
            frame["exceptions"] = frame_exceptions
        structure["list_parent_exception_count"] = len(list_parent_exceptions)
        _merge_time_constraints(frame, list_parent_time_constraints)
        structure["list_parent_time_constraint_count"] = len(
            list_parent_time_constraints
        )

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
            "row_header_block_id": _text(
                table_context.get("row_header_block_id")
            ),
            "row_header_locator": _text(table_context.get("row_header_locator")),
            "column_header_block_id": _text(
                table_context.get("column_header_block_id")
            ),
            "column_header_locator": _text(
                table_context.get("column_header_locator")
            ),
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

        table_time_constraints = [
            _lineaged_time_constraint(
                row,
                origin="table_column_time_header",
                evidence={
                    "source_id": _text(block.get("source_id")),
                    "table_id": _text(table_context.get("table_id")),
                    "column_index": table_context.get("column_index"),
                    "document_block_id": _text(
                        table_context.get("column_header_block_id")
                    ),
                    "locator": _text(
                        table_context.get("column_header_locator")
                    ),
                },
            )
            for row in extract_explicit_time_constraints(column_header)
        ]
        _merge_time_constraints(frame, table_time_constraints)
        structure["table_time_constraint_count"] = len(table_time_constraints)

    # ── 3. clause tree structure: conditions, enumeration, exceptions ──
    from .chinese_clause_parser import clause_tree_for_block

    tree = clause_tree_for_block(asset, _text(block.get("block_id")))
    if tree:
        own_time_constraints = [
            _lineaged_time_constraint(
                row,
                origin="clause_parser_time_constraint",
                evidence={
                    "source_id": _text(block.get("source_id")),
                    "document_block_id": _text(block.get("block_id")),
                    "locator": _text(block.get("locator")),
                },
            )
            for row in _list(tree.get("time_constraints"))
            if isinstance(row, dict)
        ]
        _merge_time_constraints(frame, own_time_constraints)
        structure["own_time_constraint_count"] = len(own_time_constraints)
        # The frame's own block conditions merge (never override fact-derived
        # conditions; the parser's leaf split only ADDS what the fact missed).
        conditions = frame["conditions"]
        existing_raws = {_norm(row.get("raw")) for row in conditions}
        for row in _list(tree.get("conditions")):
            if not isinstance(row, dict):
                continue
            raw = _norm(row.get("raw"))
            if not raw or raw in existing_raws:
                continue
            conditions.append(
                {
                    "condition_id": _next_condition_id(conditions),
                    "raw": raw,
                    "subject_concept_ref": "",
                    "field_concept_ref": "",
                    "operator": "",
                    "value_concept_ref": "",
                    "logic_group": _norm(row.get("logic_group")) or "main",
                    "origin": "clause_parser",
                    "resolution_status": "RESOLVED",
                    "evidence": [],
                }
            )
            existing_raws.add(raw)
        frame["conditions"] = conditions
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
    list_parent_exception_count = 0
    list_parent_time_constraint_count = 0
    table_time_constraint_count = 0
    own_time_constraint_count = 0
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
        list_parent_exception_count += int(
            structure.get("list_parent_exception_count", 0) or 0
        )
        list_parent_time_constraint_count += int(
            structure.get("list_parent_time_constraint_count", 0) or 0
        )
        table_time_constraint_count += int(
            structure.get("table_time_constraint_count", 0) or 0
        )
        own_time_constraint_count += int(
            structure.get("own_time_constraint_count", 0) or 0
        )
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
            "list_parent_exception_count": list_parent_exception_count,
            "list_parent_time_constraint_count": (
                list_parent_time_constraint_count
            ),
            "table_time_constraint_count": table_time_constraint_count,
            "own_time_constraint_count": own_time_constraint_count,
            "reason_code_counts": dict(sorted(reason_counts.items())),
            "signature_recomputed": True,
        }
        closure = dict(_dict(ledger.get("closure")))
        closure["enriched_frame_count"] = enriched
        closure["unlocated_frame_count"] = unlocated
        ledger["closure"] = closure
        asset["chinese_semantic_frame_ledger"] = ledger
    return asset
