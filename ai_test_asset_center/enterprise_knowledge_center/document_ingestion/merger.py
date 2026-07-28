"""Merge multiple adapter outputs into one evidence-preserving Document IR."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Iterable

from .contract import (
    DOCUMENT_IR_MERGE_RECEIPT_SCHEMA,
    DocumentSource,
    text,
    unique_text,
    validate_document_ir,
)


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", "", text(value))


def _status_rank(value: Any) -> int:
    return {"COMPLETE": 0, "PASS": 0, "PARTIAL": 1, "BLOCKED": 2}.get(text(value).upper(), 1)


def _merge_gap_rows(rows: Iterable[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, tuple[int, ...]], dict[str, Any]] = {}
    for adapter_name, raw in rows:
        if not isinstance(raw, dict):
            continue
        kind = text(raw.get("kind") or raw.get("reason_code") or "DOCUMENT_STRUCTURE_GAP")
        reason = text(raw.get("reason_code") or kind)
        pages = tuple(sorted(int(page) for page in _list(raw.get("pages")) if str(page).isdigit()))
        key = (kind, reason, pages)
        row = dict(raw)
        if key not in merged:
            row["observed_by_adapters"] = [adapter_name]
            merged[key] = row
            continue
        existing = merged[key]
        existing["count"] = max(int(existing.get("count") or 0), int(row.get("count") or 0))
        existing["blocks_formal_understanding"] = bool(existing.get("blocks_formal_understanding")) or bool(
            row.get("blocks_formal_understanding")
        )
        existing["observed_by_adapters"] = unique_text(
            [*_list(existing.get("observed_by_adapters")), adapter_name]
        )
    return list(merged.values())


def merge_document_irs(
    source: DocumentSource,
    parsing_plan: dict[str, Any],
    executions: list[dict[str, Any]],
    *,
    execution_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge successful adapter executions and expose every disagreement."""
    errors = [dict(row) for row in (execution_errors or []) if isinstance(row, dict)]
    valid: list[dict[str, Any]] = []
    validation_failures: list[dict[str, Any]] = []
    for execution in executions:
        if not isinstance(execution, dict):
            continue
        adapter_name = text(execution.get("adapter_name"))
        document_ir = _dict(execution.get("document_ir"))
        violations = validate_document_ir(document_ir)
        if violations:
            validation_failures.append(
                {
                    "adapter_name": adapter_name,
                    "code": "DOCUMENT_ADAPTER_IR_INVALID",
                    "violations": violations,
                    "blocks_formal_understanding": True,
                }
            )
            continue
        valid.append(execution)
    errors.extend(validation_failures)
    if not valid:
        raise ValueError(f"no valid document IR results to merge: {errors}")

    base_ir = _dict(valid[0].get("document_ir"))
    merged_blocks: list[dict[str, Any]] = []
    locator_index: dict[str, dict[str, Any]] = {}
    block_ids: set[str] = set()
    conflicts: list[dict[str, Any]] = []
    duplicate_count = 0

    for execution in valid:
        adapter_name = text(execution.get("adapter_name"))
        for raw_block in _list(_dict(execution.get("document_ir")).get("blocks")):
            if not isinstance(raw_block, dict):
                continue
            block = dict(raw_block)
            locator = text(block.get("source_locator"))
            existing = locator_index.get(locator) if locator else None
            if existing:
                same_type = text(existing.get("type")) == text(block.get("type"))
                same_text = _normalized_text(existing.get("text")) == _normalized_text(block.get("text"))
                if same_type and same_text:
                    existing["observed_by_adapters"] = unique_text(
                        [*_list(existing.get("observed_by_adapters")), adapter_name]
                    )
                    duplicate_count += 1
                    continue
                conflict = {
                    "kind": "DOCUMENT_ADAPTER_BLOCK_CONFLICT",
                    "reason_code": "DOCUMENT_ADAPTER_BLOCK_CONFLICT",
                    "count": 1,
                    "status": "SOURCE_LOCATOR_HAS_CONFLICTING_ADAPTER_OUTPUTS",
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                    "included_in_plain_text_authority": False,
                    "source_locator": locator,
                    "left": {
                        "block_id": existing.get("block_id"),
                        "type": existing.get("type"),
                        "text": existing.get("text"),
                        "adapters": existing.get("observed_by_adapters"),
                    },
                    "right": {
                        "block_id": block.get("block_id"),
                        "type": block.get("type"),
                        "text": block.get("text"),
                        "adapter": adapter_name,
                    },
                }
                conflicts.append(conflict)
                # Keep both contradictory observations. Their source locator remains the
                # same so the conflict is auditable, while block identity stays unique.
            block_id = text(block.get("block_id"))
            if block_id in block_ids:
                block_id = _stable_id("merged_document_block", block_id, adapter_name, locator)
                block["original_block_id"] = block.get("block_id")
                block["block_id"] = block_id
            block_ids.add(block_id)
            block["observed_by_adapters"] = unique_text(
                [*_list(block.get("observed_by_adapters")), adapter_name]
            )
            merged_blocks.append(block)
            if locator and locator not in locator_index:
                locator_index[locator] = block

    collections: dict[str, list[dict[str, Any]]] = {
        "sections": [],
        "tables": [],
        "pages": [],
    }
    seen_collection: dict[str, set[str]] = {key: set() for key in collections}
    for execution in valid:
        adapter_name = text(execution.get("adapter_name"))
        document_ir = _dict(execution.get("document_ir"))
        for key in collections:
            for raw in _list(document_ir.get(key)):
                if not isinstance(raw, dict):
                    continue
                identity = text(raw.get("block_id") or raw.get("source_locator")) or hashlib.sha256(
                    repr(sorted(raw.items())).encode("utf-8")
                ).hexdigest()
                if identity in seen_collection[key]:
                    continue
                seen_collection[key].add(identity)
                row = dict(raw)
                row["observed_by_adapters"] = unique_text(
                    [*_list(row.get("observed_by_adapters")), adapter_name]
                )
                collections[key].append(row)

    gap_rows: list[tuple[str, dict[str, Any]]] = []
    adapter_receipts: list[dict[str, Any]] = []
    text_candidates: list[tuple[int, str, str]] = []
    for execution in valid:
        adapter_name = text(execution.get("adapter_name"))
        document_ir = _dict(execution.get("document_ir"))
        receipt = _dict(execution.get("adapter_receipt"))
        adapter_receipts.append(receipt)
        for gap in _list(document_ir.get("unsupported_content")):
            if isinstance(gap, dict):
                gap_rows.append((adapter_name, gap))
        plain_text = str(document_ir.get("plain_text") or "")
        if plain_text.strip():
            text_candidates.append((int(execution.get("match_score") or 0), adapter_name, plain_text))

    for error in errors:
        gap_rows.append(
            (
                text(error.get("adapter_name")) or "adapter-execution",
                {
                    "kind": text(error.get("code")) or "DOCUMENT_ADAPTER_EXECUTION_FAILED",
                    "reason_code": text(error.get("code")) or "DOCUMENT_ADAPTER_EXECUTION_FAILED",
                    "count": 1,
                    "status": "ADAPTER_EXECUTION_FAILED",
                    "severity": "P0" if bool(error.get("primary", True)) else "P1",
                    "blocks_formal_understanding": bool(error.get("primary", True)),
                    "included_in_plain_text_authority": False,
                    "detail": error.get("detail"),
                },
            )
        )
    gap_rows.extend(("document-ir-merger", conflict) for conflict in conflicts)
    unsupported = _merge_gap_rows(gap_rows)

    text_candidates.sort(key=lambda row: (-row[0], row[1]))
    plain_text = text_candidates[0][2] if text_candidates else str(base_ir.get("plain_text") or "")
    text_divergences: list[dict[str, Any]] = []
    authority_normalized = _normalized_text(plain_text)
    for _score, adapter_name, candidate in text_candidates[1:]:
        normalized = _normalized_text(candidate)
        if normalized and normalized != authority_normalized:
            text_divergences.append(
                {
                    "adapter_name": adapter_name,
                    "authority_adapter": text_candidates[0][1],
                    "authority_hash": hashlib.sha256(plain_text.encode("utf-8")).hexdigest(),
                    "candidate_hash": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
                }
            )
    if text_divergences:
        unsupported.append(
            {
                "kind": "DOCUMENT_ADAPTER_PLAIN_TEXT_DIVERGENCE",
                "reason_code": "DOCUMENT_ADAPTER_PLAIN_TEXT_DIVERGENCE",
                "count": len(text_divergences),
                "status": "TEXT_PROJECTIONS_DIFFER",
                "severity": "P1",
                "blocks_formal_understanding": False,
                "included_in_plain_text_authority": True,
                "details": text_divergences,
            }
        )

    critical = [row for row in unsupported if bool(row.get("blocks_formal_understanding"))]
    input_statuses = [
        text(_dict(_dict(execution.get("document_ir")).get("structure_receipt")).get("status"))
        for execution in valid
    ]
    status = "BLOCKED" if critical or any(_status_rank(value) >= 2 for value in input_statuses) else "PARTIAL" if unsupported or any(_status_rank(value) == 1 for value in input_statuses) else "COMPLETE"
    merged_blocks.sort(
        key=lambda row: (
            int(row.get("page") or 0),
            int(row.get("order") or row.get("page_reading_order") or 0),
            text(row.get("source_locator")),
            text(row.get("block_id")),
        )
    )
    block_counts = Counter(text(block.get("type")) for block in merged_blocks)
    traceable = [block for block in merged_blocks if text(block.get("source_locator"))]
    capabilities = unique_text(
        capability
        for receipt in adapter_receipts
        for capability in _list(receipt.get("capabilities"))
    )
    merge_receipt = {
        "schema": DOCUMENT_IR_MERGE_RECEIPT_SCHEMA,
        "status": status,
        "adapter_count": len(valid),
        "adapter_names": [text(row.get("adapter_name")) for row in valid],
        "parser_versions": {
            text(receipt.get("adapter_name")): text(receipt.get("parser_version"))
            for receipt in adapter_receipts
            if text(receipt.get("adapter_name"))
        },
        "capabilities_provided": capabilities,
        "duplicate_block_count": duplicate_count,
        "block_conflict_count": len(conflicts),
        "plain_text_divergence_count": len(text_divergences),
        "execution_error_count": len(errors),
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
    }
    structure_receipt = {
        **_dict(base_ir.get("structure_receipt")),
        "status": status,
        "format": text(parsing_plan.get("detected_family")) or text(base_ir.get("format")) or "unknown",
        "block_count": len(merged_blocks),
        "source_traceability_rate": round(len(traceable) / len(merged_blocks), 4) if merged_blocks else 0.0,
        "block_type_distribution": dict(block_counts),
        "section_count": len(collections["sections"]),
        "unsupported_content_count": sum(int(row.get("count") or 0) for row in unsupported),
        "unsupported_content": unsupported,
        "critical_unsupported_content_count": sum(int(row.get("count") or 0) for row in critical),
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
        "adapter_merge_receipt": merge_receipt,
    }
    return {
        "schema": text(base_ir.get("schema")),
        "format": text(parsing_plan.get("detected_family")) or text(base_ir.get("format")) or "unknown",
        "filename": source.filename,
        "plain_text": plain_text,
        "blocks": merged_blocks,
        "sections": collections["sections"],
        "tables": collections["tables"],
        "pages": collections["pages"],
        "unsupported_content": unsupported,
        "structure_receipt": structure_receipt,
        "parsing_plan": parsing_plan,
        "adapter_receipts": adapter_receipts,
        "adapter_merge_receipt": merge_receipt,
    }


__all__ = ["merge_document_irs"]
