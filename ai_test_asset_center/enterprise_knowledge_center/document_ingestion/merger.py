"""Merge multiple adapter outputs into one evidence-preserving Document IR."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Iterable

from .contract import (
    DOCUMENT_IR_MERGE_RECEIPT_SCHEMA,
    MODE_SUPPLEMENTAL,
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


def _collect_gap_resolutions(valid: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for execution in valid:
        adapter_name = text(execution.get("adapter_name"))
        document_ir = _dict(execution.get("document_ir"))
        for raw in _list(document_ir.get("resolves_gaps")):
            if not isinstance(raw, dict):
                continue
            reason = text(raw.get("reason_code") or raw.get("kind"))
            if not reason:
                continue
            row = dict(raw)
            row["reason_code"] = reason
            row["resolved_by_adapter"] = adapter_name
            row["pages"] = sorted(
                {int(page) for page in _list(row.get("pages")) if str(page).isdigit()}
            )
            rows.append(row)
    return rows


def _apply_gap_resolutions(
    unsupported: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    by_reason: dict[str, list[dict[str, Any]]] = {}
    for resolution in resolutions:
        by_reason.setdefault(text(resolution.get("reason_code")), []).append(resolution)

    for raw in unsupported:
        gap = dict(raw)
        reason = text(gap.get("reason_code") or gap.get("kind"))
        matching = by_reason.get(reason) or []
        if not matching:
            kept.append(gap)
            continue
        gap_pages = {
            int(page) for page in _list(gap.get("pages")) if str(page).isdigit()
        }
        wildcard = any(not _list(row.get("pages")) for row in matching)
        resolved_pages = {
            int(page)
            for row in matching
            for page in _list(row.get("pages"))
            if str(page).isdigit()
        }
        if gap_pages:
            remaining = sorted(gap_pages - resolved_pages)
            resolved = sorted(gap_pages & resolved_pages)
            if resolved:
                applied.append(
                    {
                        "reason_code": reason,
                        "resolved_pages": resolved,
                        "resolutions": matching,
                    }
                )
            if remaining:
                gap["pages"] = remaining
                gap["count"] = len(remaining)
                gap["partially_resolved_pages"] = resolved
                kept.append(gap)
            continue
        if wildcard:
            applied.append(
                {
                    "reason_code": reason,
                    "resolved_pages": [],
                    "resolutions": matching,
                }
            )
            continue
        kept.append(gap)
    return kept, applied


def _composed_plain_text(blocks: list[dict[str, Any]], fallback: str) -> str:
    # Text-bearing table cells, notes and formulas are part of the enterprise material
    # projection. Container blocks such as TABLE or FIGURE stay excluded to avoid
    # duplicating their child text.
    allowed = {
        "HEADING",
        "PARAGRAPH",
        "LIST_ITEM",
        "TABLE_CELL",
        "KEY_VALUE",
        "NOTE",
        "CAPTION",
        "FORMULA",
    }
    values: list[str] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        if text(block.get("region")) not in {"", "body"}:
            continue
        if block.get("excluded_from_main_flow"):
            continue
        if text(block.get("type")) not in allowed:
            continue
        value = text(block.get("text"))
        if not value:
            continue
        identity = (text(block.get("source_locator")), _normalized_text(value))
        if identity in seen:
            continue
        seen.add(identity)
        values.append(value)
    return "\n".join(values).strip() or str(fallback or "")


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
    full_text_candidates: list[tuple[int, str, str]] = []
    for execution in valid:
        adapter_name = text(execution.get("adapter_name"))
        document_ir = _dict(execution.get("document_ir"))
        receipt = _dict(execution.get("adapter_receipt"))
        adapter_receipts.append(receipt)
        for gap in _list(document_ir.get("unsupported_content")):
            if isinstance(gap, dict):
                gap_rows.append((adapter_name, gap))
        plain_text = str(document_ir.get("plain_text") or "")
        if plain_text.strip() and text(execution.get("mode")) != MODE_SUPPLEMENTAL:
            full_text_candidates.append(
                (int(execution.get("match_score") or 0), adapter_name, plain_text)
            )

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
    resolutions = _collect_gap_resolutions(valid)
    unsupported, applied_resolutions = _apply_gap_resolutions(unsupported, resolutions)

    full_text_candidates.sort(key=lambda row: (-row[0], row[1]))
    authority_text = full_text_candidates[0][2] if full_text_candidates else str(base_ir.get("plain_text") or "")
    text_divergences: list[dict[str, Any]] = []
    authority_normalized = _normalized_text(authority_text)
    for _score, adapter_name, candidate in full_text_candidates[1:]:
        normalized = _normalized_text(candidate)
        if normalized and normalized != authority_normalized:
            text_divergences.append(
                {
                    "adapter_name": adapter_name,
                    "authority_adapter": full_text_candidates[0][1],
                    "authority_hash": hashlib.sha256(authority_text.encode("utf-8")).hexdigest(),
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

    merged_blocks.sort(
        key=lambda row: (
            int(row.get("page") or 0),
            int(row.get("order") or row.get("page_reading_order") or 0),
            text(row.get("source_locator")),
            text(row.get("block_id")),
        )
    )
    plain_text = _composed_plain_text(merged_blocks, authority_text)
    critical = [row for row in unsupported if bool(row.get("blocks_formal_understanding"))]
    status = "BLOCKED" if critical else "PARTIAL" if unsupported else "COMPLETE"
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
        "declared_gap_resolution_count": len(resolutions),
        "applied_gap_resolution_count": len(applied_resolutions),
        "applied_gap_resolutions": applied_resolutions,
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
        "applied_gap_resolution_count": len(applied_resolutions),
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
        "adapter_merge_receipt": merge_receipt,
    }
    result = {
        "schema": text(base_ir.get("schema")),
        "format": text(parsing_plan.get("detected_family")) or text(base_ir.get("format")) or "unknown",
        "filename": source.filename,
        "plain_text": plain_text,
        "blocks": merged_blocks,
        "sections": collections["sections"],
        "tables": collections["tables"],
        "pages": collections["pages"],
        "unsupported_content": unsupported,
        "resolves_gaps": resolutions,
        "applied_gap_resolutions": applied_resolutions,
        "structure_receipt": structure_receipt,
        "parsing_plan": parsing_plan,
        "adapter_receipts": adapter_receipts,
        "adapter_merge_receipt": merge_receipt,
    }
    # The primary adapter's structural metadata is part of the Document IR contract.
    # The merge recomputes block collections, but the exact artifact structure and the
    # schema projection receipt (used for $ref resolution downstream) must survive.
    if base_ir.get("artifact_structure"):
        result["artifact_structure"] = base_ir["artifact_structure"]
    if base_ir.get("openapi_schema_projection_receipt"):
        result["openapi_schema_projection_receipt"] = base_ir[
            "openapi_schema_projection_receipt"
        ]
    return result


__all__ = ["merge_document_irs"]
