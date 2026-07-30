"""Close the immutable source -> Document IR block evidence chain.

This stage adds no business semantics.  It binds every formal text-bearing block to the
source fingerprint and verifies that plain-text authority cannot exist without exact,
addressable structural evidence.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from .contract import DocumentSource, text

EVIDENCE_CLOSURE_SCHEMA = "qualibug.document-evidence-closure-receipt.v1"

_FORMAL_TEXT_BLOCK_TYPES = {
    "HEADING",
    "PARAGRAPH",
    "LIST_ITEM",
    "TABLE_CELL",
    "KEY_VALUE",
    "NOTE",
    "CAPTION",
    "FORMULA",
}
_EXACT_LOCATOR_MARKERS = (
    "#line=",
    "#page=",
    "#paragraph=",
    "#table=",
    "#sheet=",
    "#slide=",
    "#section=",
    "#interface=",
    "#defined-name=",
    "#whole-file",
    ";cell=",
    ";table-cell=",
    ";shape=",
    ";speaker-notes",
    "chars=",
)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", text(value))


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def _address_kind(block: dict[str, Any]) -> str:
    if block.get("page") and block.get("bbox"):
        return "PAGE_BBOX"
    if text(block.get("sheet")) and text(block.get("cell_ref")):
        return "SPREADSHEET_CELL"
    if block.get("slide") and text(block.get("shape_id")):
        return "PRESENTATION_SHAPE"
    locator = text(block.get("source_locator"))
    if any(marker in locator for marker in _EXACT_LOCATOR_MARKERS):
        return "EXACT_SOURCE_LOCATOR"
    return "SOURCE_LOCATOR" if locator else "UNADDRESSED"


def _dedupe_gaps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        key = (
            text(row.get("reason_code") or row.get("kind")),
            text(row.get("source_locator")),
        )
        if key not in result:
            result[key] = row
            continue
        existing = result[key]
        existing["count"] = int(existing.get("count") or 0) + int(row.get("count") or 0)
        existing["block_ids"] = sorted(
            {
                text(value)
                for value in [
                    *_list(existing.get("block_ids")),
                    *_list(row.get("block_ids")),
                ]
                if text(value)
            }
        )
        existing["blocks_formal_understanding"] = bool(
            existing.get("blocks_formal_understanding")
        ) or bool(row.get("blocks_formal_understanding"))
    return list(result.values())


def apply_document_evidence_closure(
    document_ir: dict[str, Any], source: DocumentSource
) -> dict[str, Any]:
    """Attach immutable source identity and fail closed on untraceable authority blocks."""
    result = dict(document_ir or {})
    blocks = [dict(row) for row in _list(result.get("blocks")) if isinstance(row, dict)]
    gaps: list[dict[str, Any]] = []
    formal_blocks: list[dict[str, Any]] = []
    traceable_blocks: list[dict[str, Any]] = []
    exact_blocks: list[dict[str, Any]] = []
    locator_texts: dict[str, set[str]] = defaultdict(set)
    locator_block_ids: dict[str, list[str]] = defaultdict(list)

    for block in blocks:
        block_id = text(block.get("block_id"))
        locator = text(block.get("source_locator"))
        value = text(block.get("text"))
        block["source_id"] = source.source_id
        block["source_hash"] = source.content_hash
        block["source_filename"] = source.filename
        block["text_hash"] = hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""
        address_kind = _address_kind(block)
        block["evidence_address"] = {
            "source_id": source.source_id,
            "source_hash": source.content_hash,
            "filename": source.filename,
            "block_id": block_id,
            "source_locator": locator,
            "address_kind": address_kind,
            "page": block.get("page"),
            "bbox": block.get("bbox"),
            "sheet": block.get("sheet"),
            "cell_ref": block.get("cell_ref"),
            "slide": block.get("slide"),
            "shape_id": block.get("shape_id"),
            "business_semantics_added": False,
        }
        if locator:
            locator_texts[locator].add(_normalized(value))
            if block_id:
                locator_block_ids[locator].append(block_id)
        if (
            text(block.get("type")) in _FORMAL_TEXT_BLOCK_TYPES
            and value
            and text(block.get("region")) in {"", "body"}
            and not block.get("excluded_from_main_flow")
            and not block.get("excluded_from_plain_text_projection")
        ):
            formal_blocks.append(block)
            if block_id and locator and source.content_hash:
                traceable_blocks.append(block)
            else:
                gaps.append(
                    {
                        "kind": "DOCUMENT_EVIDENCE_CHAIN_INCOMPLETE",
                        "reason_code": "DOCUMENT_EVIDENCE_CHAIN_INCOMPLETE",
                        "count": 1,
                        "status": "FORMAL_AUTHORITY_BLOCK_NOT_SOURCE_ADDRESSABLE",
                        "severity": "P0",
                        "blocks_formal_understanding": True,
                        "included_in_plain_text_authority": False,
                        "source_locator": locator,
                        "block_ids": [block_id] if block_id else [],
                        "missing": [
                            name
                            for name, present in (
                                ("block_id", bool(block_id)),
                                ("source_locator", bool(locator)),
                                ("source_hash", bool(source.content_hash)),
                            )
                            if not present
                        ],
                    }
                )
            if address_kind != "UNADDRESSED":
                exact_blocks.append(block)

    for locator, normalized_values in locator_texts.items():
        meaningful = {value for value in normalized_values if value}
        if len(meaningful) <= 1:
            continue
        gaps.append(
            {
                "kind": "DOCUMENT_EVIDENCE_LOCATOR_CONFLICT",
                "reason_code": "DOCUMENT_EVIDENCE_LOCATOR_CONFLICT",
                "count": 1,
                "status": "ONE_SOURCE_ADDRESS_HAS_DIFFERENT_FORMAL_TEXT",
                "severity": "P0",
                "blocks_formal_understanding": True,
                "included_in_plain_text_authority": False,
                "source_locator": locator,
                "block_ids": locator_block_ids.get(locator, []),
                "distinct_text_count": len(meaningful),
            }
        )

    plain_text = text(result.get("plain_text"))
    if plain_text and not formal_blocks:
        gaps.append(
            {
                "kind": "PLAIN_TEXT_WITHOUT_BLOCK_EVIDENCE",
                "reason_code": "PLAIN_TEXT_WITHOUT_BLOCK_EVIDENCE",
                "count": 1,
                "status": "PLAIN_TEXT_AUTHORITY_HAS_NO_ADDRESSABLE_BLOCKS",
                "severity": "P0",
                "blocks_formal_understanding": True,
                "included_in_plain_text_authority": False,
                "source_locator": f"{source.filename}#whole-file",
                "plain_text_hash": hashlib.sha256(plain_text.encode("utf-8")).hexdigest(),
            }
        )

    gaps = _dedupe_gaps(gaps)
    unsupported = [
        dict(row)
        for row in _list(result.get("unsupported_content"))
        if isinstance(row, dict)
    ]
    existing_gap_keys = {
        (
            text(row.get("reason_code") or row.get("kind")),
            text(row.get("source_locator")),
        )
        for row in unsupported
    }
    for gap in gaps:
        key = (
            text(gap.get("reason_code") or gap.get("kind")),
            text(gap.get("source_locator")),
        )
        if key not in existing_gap_keys:
            unsupported.append(gap)
            existing_gap_keys.add(key)

    critical_gap_count = sum(
        int(row.get("count") or 0)
        for row in gaps
        if bool(row.get("blocks_formal_understanding"))
    )
    receipt = {
        "schema": EVIDENCE_CLOSURE_SCHEMA,
        "status": "BLOCKED" if critical_gap_count else "PASS",
        "source_id": source.source_id,
        "filename": source.filename,
        "source_hash": source.content_hash,
        "formal_authority_block_count": len(formal_blocks),
        "source_hash_bound_block_count": sum(
            1 for row in formal_blocks if text(row.get("source_hash")) == source.content_hash
        ),
        "traceable_authority_block_count": len(traceable_blocks),
        "exact_address_authority_block_count": len(exact_blocks),
        "untraceable_authority_block_count": len(formal_blocks) - len(traceable_blocks),
        "weak_address_authority_block_count": len(formal_blocks) - len(exact_blocks),
        "source_traceability_rate": _ratio(len(traceable_blocks), len(formal_blocks)),
        "exact_address_rate": _ratio(len(exact_blocks), len(formal_blocks)),
        "locator_conflict_count": sum(
            int(row.get("count") or 0)
            for row in gaps
            if text(row.get("reason_code")) == "DOCUMENT_EVIDENCE_LOCATOR_CONFLICT"
        ),
        "critical_gap_count": critical_gap_count,
        "gaps": gaps,
        "source_bytes_fingerprinted": True,
        "plain_text_requires_block_evidence": True,
        "business_semantics_added": False,
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
    }

    structure_receipt = _dict(result.get("structure_receipt"))
    structure_receipt["evidence_closure_status"] = receipt["status"]
    structure_receipt["evidence_source_traceability_rate"] = receipt[
        "source_traceability_rate"
    ]
    structure_receipt["evidence_exact_address_rate"] = receipt["exact_address_rate"]
    structure_receipt["untraceable_authority_block_count"] = receipt[
        "untraceable_authority_block_count"
    ]
    structure_receipt["weak_address_authority_block_count"] = receipt[
        "weak_address_authority_block_count"
    ]
    structure_receipt["evidence_locator_conflict_count"] = receipt[
        "locator_conflict_count"
    ]
    structure_receipt["unsupported_content"] = unsupported
    structure_receipt["unsupported_content_count"] = sum(
        int(row.get("count") or 0) for row in unsupported
    )
    structure_receipt["critical_unsupported_content_count"] = sum(
        int(row.get("count") or 0)
        for row in unsupported
        if bool(row.get("blocks_formal_understanding"))
    )
    if critical_gap_count:
        structure_receipt["status"] = "BLOCKED"

    result["blocks"] = blocks
    result["unsupported_content"] = unsupported
    result["structure_receipt"] = structure_receipt
    result["evidence_closure_receipt"] = receipt
    return result


__all__ = ["EVIDENCE_CLOSURE_SCHEMA", "apply_document_evidence_closure"]
