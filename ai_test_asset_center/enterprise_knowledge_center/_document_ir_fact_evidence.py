"""Align extracted business facts back to exact source-preserving Document IR blocks.

Chinese-first extraction operates on a merged text projection.  This stage restores the
more precise page/cell/OCR locator whenever one and only one IR block contains the fact's
original statement.  It never changes fact meaning or resolves ambiguous block matches.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable


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
    allowed = {"HEADING", "PARAGRAPH", "LIST_ITEM", "TABLE_CELL", "KEY_VALUE", "NOTE"}
    return [
        row
        for row in _list(structure.get("blocks"))
        if isinstance(row, dict)
        and _text(row.get("type")) in allowed
        and _text(row.get("region")) in {"", "body"}
        and not row.get("excluded_from_main_flow")
        and not row.get("excluded_from_plain_text_projection")
        and _text(row.get("text"))
        and _text(row.get("source_locator"))
    ]


def _candidate_blocks(statement: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = _normalized(statement)
    if not target:
        return []
    exact = [row for row in blocks if _normalized(row.get("text")) == target]
    if exact:
        return exact
    return [row for row in blocks if target in _normalized(row.get("text"))]


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
    for source_id, source_facts in facts_by_source.items():
        source = source_map.get(source_id)
        if not source:
            continue
        structure = _dict(source.get("document_structure"))
        blocks = _eligible_blocks(structure)
        if not blocks:
            continue
        for fact in source_facts:
            statement = _text(fact.get("raw_statement") or fact.get("statement"))
            candidates = _candidate_blocks(statement, blocks)
            if len(candidates) != 1:
                unresolved.append(
                    {
                        "fact_id": fact.get("fact_id"),
                        "source_id": source_id,
                        "reason": (
                            "DOCUMENT_IR_FACT_BLOCK_NOT_FOUND"
                            if not candidates
                            else "DOCUMENT_IR_FACT_BLOCK_NOT_UNIQUE"
                        ),
                        "candidate_block_ids": [row.get("block_id") for row in candidates],
                    }
                )
                continue
            block = candidates[0]
            locator = _text(block.get("source_locator"))
            spans = [dict(row) for row in _list(fact.get("source_spans")) if isinstance(row, dict)]
            if not any(_text(row.get("locator")) == locator for row in spans):
                quote_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()
                spans.append(
                    {
                        "source_id": source_id,
                        "locator": locator,
                        "quote": statement,
                        "quote_hash": quote_hash,
                        "document_block_id": block.get("block_id"),
                        "derivation": "document_ir_exact_statement_alignment",
                    }
                )
            fact["source_spans"] = spans
            fact["document_structure_alignment"] = {
                "source_backed": True,
                "source_id": source_id,
                "block_id": block.get("block_id"),
                "block_type": block.get("type"),
                "source_locator": locator,
                "page": block.get("page"),
                "bbox": block.get("bbox"),
                "observed_by_adapters": block.get("observed_by_adapters") or [],
                "automatic_business_inference_used": False,
            }
            aligned.append(
                {
                    "fact_id": fact.get("fact_id"),
                    "source_id": source_id,
                    "block_id": block.get("block_id"),
                    "source_locator": locator,
                }
            )

    ledger["items"] = facts
    ledger["document_ir_fact_evidence_contract"] = {
        "exact_or_contained_statement_match_required": True,
        "unique_block_required": True,
        "business_semantics_changed": False,
        "ambiguous_matches_are_not_selected": True,
        "blocks_excluded_from_plain_text_projection_are_not_fact_authority": True,
    }
    asset["business_fact_ledger"] = ledger
    asset["document_ir_fact_evidence_receipt"] = {
        "schema": "qualibug.document-ir-fact-evidence-receipt.v1",
        "aligned_fact_count": len(aligned),
        "unresolved_fact_count": len(unresolved),
        "aligned": aligned,
        "unresolved": unresolved,
    }
    return asset


__all__ = ["align_business_facts_to_document_ir"]
