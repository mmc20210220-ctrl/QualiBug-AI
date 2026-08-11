"""Align extracted business facts back to exact source-preserving Document IR blocks.

Chinese-first extraction operates on a merged text projection.  This stage restores the
more precise page/cell/OCR locator whenever one unique block or one unique contiguous
block span contains the fact's original statement.  It never changes fact meaning or
selects an ambiguous structural match.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable

_MAX_CONTIGUOUS_BLOCK_SPAN = 12


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value))


def _fact_source_id(fact: dict[str, Any]) -> str:
    spans = _list(fact.get("source_spans"))
    first = _dict(spans[0]) if spans else {}
    return _text(first.get("source_id") or fact.get("source_id"))


def _eligible_blocks(structure: dict[str, Any]) -> list[dict[str, Any]]:
    # HEADING blocks are structural context only. Aligning facts to heading titles
    # would silently promote section labels into business fact authority.
    allowed = {"PARAGRAPH", "LIST_ITEM", "TABLE_CELL", "KEY_VALUE", "NOTE", "FORMULA"}
    return sorted(
        [
            row
            for row in _list(structure.get("blocks"))
            if isinstance(row, dict)
            and _text(row.get("type")) in allowed
            and _text(row.get("region")) in {"", "body"}
            and not row.get("excluded_from_main_flow")
            and not row.get("excluded_from_plain_text_projection")
            and _text(row.get("text"))
            and _text(row.get("source_locator"))
        ],
        key=lambda row: (
            int(row.get("page") or row.get("slide") or 0),
            int(row.get("order") or row.get("page_reading_order") or 0),
            _text(row.get("source_locator")),
            _text(row.get("block_id")),
        ),
    )


def _prefer_specific_blocks(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specificity = {
        "TABLE_CELL": 50,
        "LIST_ITEM": 40,
        "KEY_VALUE": 40,
        "FORMULA": 35,
        "NOTE": 30,
        "PARAGRAPH": 20,
        "TABLE": 0,
    }
    if len(candidates) <= 1:
        return candidates
    best = max(specificity.get(_text(row.get("type")), 0) for row in candidates)
    refined = [
        row
        for row in candidates
        if specificity.get(_text(row.get("type")), 0) == best
    ]
    return refined or candidates


def _single_block_candidates(
    statement: str,
    blocks: list[dict[str, Any]],
    normalized_blocks: dict[int, str] | None = None,
) -> list[list[dict[str, Any]]]:
    target = _normalized(statement)
    if not target:
        return []
    normalized = normalized_blocks or {
        id(row): _normalized(row.get("text")) for row in blocks
    }
    exact = [row for row in blocks if normalized[id(row)] == target]
    if exact:
        return [[row] for row in _prefer_specific_blocks(exact)]
    contained = [row for row in blocks if target in normalized[id(row)]]
    return [[row] for row in _prefer_specific_blocks(contained)]


def _same_structural_stream(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Reject spans that jump between unrelated pages/sheets/slides."""
    for field in ("page", "sheet", "slide"):
        left_value = _text(left.get(field))
        right_value = _text(right.get(field))
        if left_value and right_value and left_value != right_value:
            return False
    return True


def _contiguous_block_candidates(
    statement: str,
    blocks: list[dict[str, Any]],
    normalized_blocks: dict[int, str] | None = None,
) -> list[list[dict[str, Any]]]:
    target = _normalized(statement)
    if not target or len(blocks) < 2:
        return []
    candidates: list[list[dict[str, Any]]] = []
    normalized = normalized_blocks or {
        id(row): _normalized(row.get("text")) for row in blocks
    }
    shortest: int | None = None
    for start in range(len(blocks)):
        combined = ""
        span: list[dict[str, Any]] = []
        for end in range(start, min(len(blocks), start + _MAX_CONTIGUOUS_BLOCK_SPAN)):
            block = blocks[end]
            if span and not _same_structural_stream(span[-1], block):
                break
            combined += normalized[id(block)]
            span.append(block)
            if len(combined) > max(len(target) * 4, len(target) + 400):
                break
            if target not in combined:
                continue
            length = len(span)
            if shortest is None or length < shortest:
                shortest = length
                candidates = [list(span)]
            elif length == shortest:
                candidates.append(list(span))
            break
    unique: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for span in candidates:
        key = tuple(_text(row.get("block_id")) for row in span)
        if key and all(key):
            unique[key] = span
    return list(unique.values())


def _candidate_spans(
    statement: str,
    blocks: list[dict[str, Any]],
    normalized_blocks: dict[int, str] | None = None,
) -> list[list[dict[str, Any]]]:
    singles = _single_block_candidates(statement, blocks, normalized_blocks)
    if singles:
        return singles
    return _contiguous_block_candidates(statement, blocks, normalized_blocks)


def _candidate_block_payload(
    candidates: list[list[dict[str, Any]]],
) -> tuple[list[str], list[list[str]]]:
    """Keep the legacy flat ID list while exposing span groupings additively."""
    spans = [
        [_text(row.get("block_id")) for row in span if _text(row.get("block_id"))]
        for span in candidates
    ]
    flat = sorted({block_id for span in spans for block_id in span})
    return flat, spans


def _span_locator(span: list[dict[str, Any]]) -> str:
    locators = [
        _text(row.get("source_locator"))
        for row in span
        if _text(row.get("source_locator"))
    ]
    if not locators:
        return ""
    if len(locators) == 1:
        return locators[0]
    return f"{locators[0]}..{locators[-1]}"


def align_business_facts_to_document_ir(
    asset: dict[str, Any],
    structured_sources: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    source_map = {
        _text(row.get("source_id")): row
        for row in structured_sources
        if isinstance(row, dict) and _text(row.get("source_id"))
    }
    facts_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        facts_by_source[_fact_source_id(fact)].append(fact)

    aligned: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    single_block_count = 0
    contiguous_span_count = 0
    for source_id, source_facts in facts_by_source.items():
        source = source_map.get(source_id)
        if not source:
            for fact in source_facts:
                unresolved.append(
                    {
                        "fact_id": fact.get("fact_id"),
                        "source_id": source_id,
                        "reason": "DOCUMENT_IR_SOURCE_STRUCTURE_UNAVAILABLE",
                        "candidate_block_ids": [],
                        "candidate_block_spans": [],
                    }
                )
            continue
        structure = _dict(source.get("document_structure"))
        blocks = _eligible_blocks(structure)
        if not blocks:
            for fact in source_facts:
                unresolved.append(
                    {
                        "fact_id": fact.get("fact_id"),
                        "source_id": source_id,
                        "reason": "DOCUMENT_IR_ELIGIBLE_BLOCKS_EMPTY",
                        "candidate_block_ids": [],
                        "candidate_block_spans": [],
                    }
                )
            continue
        normalized_blocks = {
            id(row): _normalized(row.get("text")) for row in blocks
        }
        for fact in source_facts:
            statement = _text(fact.get("raw_statement") or fact.get("statement"))
            candidates = _candidate_spans(statement, blocks, normalized_blocks)
            if len(candidates) != 1:
                candidate_block_ids, candidate_block_spans = _candidate_block_payload(
                    candidates
                )
                unresolved.append(
                    {
                        "fact_id": fact.get("fact_id"),
                        "source_id": source_id,
                        "reason": (
                            "DOCUMENT_IR_FACT_BLOCK_NOT_FOUND"
                            if not candidates
                            else "DOCUMENT_IR_FACT_BLOCK_NOT_UNIQUE"
                        ),
                        "candidate_block_ids": candidate_block_ids,
                        "candidate_block_spans": candidate_block_spans,
                    }
                )
                continue
            span = candidates[0]
            locator = _span_locator(span)
            block_ids = [_text(row.get("block_id")) for row in span]
            primary = span[0]
            match_kind = (
                "SINGLE_BLOCK" if len(span) == 1 else "CONTIGUOUS_BLOCK_SPAN"
            )
            if len(span) == 1:
                single_block_count += 1
            else:
                contiguous_span_count += 1
            spans = [
                dict(row)
                for row in _list(fact.get("source_spans"))
                if isinstance(row, dict)
            ]
            if not any(
                _text(row.get("locator")) == locator
                and [
                    _text(value) for value in _list(row.get("document_block_ids"))
                ]
                == block_ids
                for row in spans
            ):
                quote_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()
                spans.append(
                    {
                        "source_id": source_id,
                        "locator": locator,
                        "quote": statement,
                        "quote_hash": quote_hash,
                        "document_block_id": primary.get("block_id"),
                        "document_block_ids": block_ids,
                        "derivation": (
                            "document_ir_exact_statement_alignment"
                            if len(span) == 1
                            else "document_ir_contiguous_statement_alignment"
                        ),
                    }
                )
            fact["source_spans"] = spans
            fact["document_structure_alignment"] = {
                "source_backed": True,
                "source_id": source_id,
                "block_id": primary.get("block_id"),
                "block_ids": block_ids,
                "block_type": primary.get("type"),
                "block_types": [_text(row.get("type")) for row in span],
                "source_locator": locator,
                "source_locator_start": span[0].get("source_locator"),
                "source_locator_end": span[-1].get("source_locator"),
                "page": primary.get("page"),
                "bbox": primary.get("bbox"),
                "sheet": primary.get("sheet"),
                "cell_ref": primary.get("cell_ref"),
                "slide": primary.get("slide"),
                "shape_id": primary.get("shape_id"),
                "observed_by_adapters": sorted(
                    {
                        _text(value)
                        for row in span
                        for value in _list(row.get("observed_by_adapters"))
                        if _text(value)
                    }
                ),
                "match_kind": match_kind,
                "automatic_business_inference_used": False,
            }
            aligned.append(
                {
                    "fact_id": fact.get("fact_id"),
                    "source_id": source_id,
                    "block_id": primary.get("block_id"),
                    "block_ids": block_ids,
                    "source_locator": locator,
                    "match_kind": match_kind,
                }
            )

    ledger["items"] = facts
    ledger["document_ir_fact_evidence_contract"] = {
        "exact_or_contained_statement_match_required": True,
        "unique_block_or_contiguous_span_required": True,
        "maximum_contiguous_block_span": _MAX_CONTIGUOUS_BLOCK_SPAN,
        "business_semantics_changed": False,
        "ambiguous_matches_are_not_selected": True,
        "blocks_excluded_from_plain_text_projection_are_not_fact_authority": True,
        "cross_page_sheet_or_slide_spans_forbidden": True,
        "candidate_block_ids_remain_flat_for_compatibility": True,
        "candidate_block_spans_are_additive": True,
    }
    asset["business_fact_ledger"] = ledger
    asset["document_ir_fact_evidence_receipt"] = {
        "schema": "qualibug.document-ir-fact-evidence-receipt.v1",
        "aligned_fact_count": len(aligned),
        "single_block_aligned_fact_count": single_block_count,
        "contiguous_span_aligned_fact_count": contiguous_span_count,
        "unresolved_fact_count": len(unresolved),
        "aligned": aligned,
        "unresolved": unresolved,
    }
    return asset


__all__ = ["align_business_facts_to_document_ir"]
