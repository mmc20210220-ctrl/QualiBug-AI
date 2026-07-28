"""Unified document ingestion pipeline.

Every source follows the same path: fingerprint -> plan -> execute registered adapters
-> merge IR -> expose gaps.  Business understanding never selects adapters directly.
"""
from __future__ import annotations

from typing import Any

from .contract import (
    AdapterMatch,
    CAP_TEXT_EXTRACTION,
    DocumentSource,
    MODE_FALLBACK,
    text,
)
from .merger import merge_document_irs
from .planner import plan_document_parsing
from .registry import DocumentAdapterRegistry, build_default_registry


def _match_from_plan(row: dict[str, Any]) -> AdapterMatch:
    return AdapterMatch(
        adapter_name=text(row.get("adapter_name")),
        score=int(row.get("score") or 0),
        reason=text(row.get("reason")),
        capabilities=tuple(str(value) for value in (row.get("capabilities") or []) if str(value).strip()),
        mode=text(row.get("mode")),
    )


def _execute_adapter(
    source: DocumentSource,
    registry: DocumentAdapterRegistry,
    plan_row: dict[str, Any],
) -> dict[str, Any]:
    adapter = registry.get(text(plan_row.get("adapter_name")))
    match = _match_from_plan(plan_row)
    document_ir = adapter.extract(source)
    receipt = adapter.receipt(source, match)
    return {
        "adapter_name": adapter.name,
        "mode": adapter.mode,
        "match_score": match.score,
        "document_ir": document_ir,
        "adapter_receipt": receipt,
    }


def _fallback_rows(
    source: DocumentSource,
    registry: DocumentAdapterRegistry,
    excluded_names: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for adapter, match in registry.matches(source):
        if adapter.name in excluded_names or match.mode != MODE_FALLBACK:
            continue
        rows.append(
            {
                **match.to_dict(),
                "parser_version": str(getattr(adapter, "parser_version", "")),
                "priority": int(getattr(adapter, "priority", 0)),
            }
        )
    return rows


def _apply_capability_gaps(
    document_ir: dict[str, Any], parsing_plan: dict[str, Any]
) -> dict[str, Any]:
    missing = sorted(
        {
            text(value)
            for value in (parsing_plan.get("missing_capabilities") or [])
            if text(value)
        }
    )
    if not missing:
        return document_ir
    result = dict(document_ir)
    unsupported = [
        dict(row)
        for row in (result.get("unsupported_content") or [])
        if isinstance(row, dict)
    ]
    existing = {
        text(row.get("missing_capability"))
        for row in unsupported
        if text(row.get("kind")) == "DOCUMENT_ADAPTER_CAPABILITY_GAP"
    }
    for capability in missing:
        if capability in existing:
            continue
        critical = capability == CAP_TEXT_EXTRACTION
        unsupported.append(
            {
                "kind": "DOCUMENT_ADAPTER_CAPABILITY_GAP",
                "reason_code": "DOCUMENT_ADAPTER_CAPABILITY_GAP",
                "missing_capability": capability,
                "count": 1,
                "status": "NO_SELECTED_ADAPTER_PROVIDES_REQUIRED_CAPABILITY",
                "severity": "P0" if critical else "P1",
                "blocks_formal_understanding": critical,
                "included_in_plain_text_authority": False,
            }
        )
    blocked = any(bool(row.get("blocks_formal_understanding")) for row in unsupported)
    final_status = "BLOCKED" if blocked else "PARTIAL"
    merge_receipt = dict(result.get("adapter_merge_receipt") or {})
    merge_receipt["status"] = final_status
    merge_receipt["missing_capabilities"] = missing
    receipt = dict(result.get("structure_receipt") or {})
    receipt["status"] = final_status
    receipt["unsupported_content"] = unsupported
    receipt["unsupported_content_count"] = sum(int(row.get("count") or 0) for row in unsupported)
    receipt["critical_unsupported_content_count"] = sum(
        int(row.get("count") or 0)
        for row in unsupported
        if bool(row.get("blocks_formal_understanding"))
    )
    receipt["missing_adapter_capabilities"] = missing
    receipt["adapter_merge_receipt"] = merge_receipt
    result["unsupported_content"] = unsupported
    result["structure_receipt"] = receipt
    result["adapter_merge_receipt"] = merge_receipt
    return result


def build_document_structure_ir(
    data: bytes,
    *,
    filename: str,
    source_id: str = "",
    declared_mime: str = "",
    legacy_text: str = "",
    registry: DocumentAdapterRegistry | None = None,
) -> dict[str, Any]:
    """Build one format-independent Document IR from immutable source bytes."""
    resolved_registry = registry or build_default_registry()
    source = DocumentSource(
        source_id=str(source_id or ""),
        filename=str(filename or "document"),
        data=bytes(data or b""),
        declared_mime=str(declared_mime or ""),
        legacy_text=str(legacy_text or ""),
    )
    parsing_plan = plan_document_parsing(source, resolved_registry)
    selected_rows = [
        dict(row)
        for row in (parsing_plan.get("selected_adapters") or [])
        if isinstance(row, dict) and text(row.get("adapter_name"))
    ]
    executions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    selected_names = {text(row.get("adapter_name")) for row in selected_rows}

    for index, row in enumerate(selected_rows):
        try:
            executions.append(_execute_adapter(source, resolved_registry, row))
        except Exception as exc:
            errors.append(
                {
                    "adapter_name": row.get("adapter_name"),
                    "code": "DOCUMENT_ADAPTER_EXECUTION_FAILED",
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                    "primary": index == 0 and text(row.get("mode")) != MODE_FALLBACK,
                    "mode": row.get("mode"),
                }
            )

    # A primary adapter failure may still leave a reliable generic text projection.
    # The fallback is attempted only when no adapter produced valid IR; it never masks
    # the failed primary receipt, which remains a formal structure gap.
    if not executions:
        for row in _fallback_rows(source, resolved_registry, selected_names):
            try:
                execution = _execute_adapter(source, resolved_registry, row)
                executions.append(execution)
                parsing_plan["runtime_fallback_adapter"] = row
                break
            except Exception as exc:
                errors.append(
                    {
                        "adapter_name": row.get("adapter_name"),
                        "code": "DOCUMENT_FALLBACK_ADAPTER_EXECUTION_FAILED",
                        "detail": f"{type(exc).__name__}: {exc}"[:500],
                        "primary": True,
                        "mode": row.get("mode"),
                    }
                )

    if not executions:
        raise ValueError(f"document ingestion produced no adapter output: {errors}")

    merged = merge_document_irs(
        source,
        parsing_plan,
        executions,
        execution_errors=errors,
    )
    merged = _apply_capability_gaps(merged, parsing_plan)
    merged["ingestion_pipeline_receipt"] = {
        "schema": "qualibug.document-ingestion-pipeline-receipt.v1",
        "source_id": source.source_id,
        "filename": source.filename,
        "source_hash": source.content_hash,
        "plan_status": parsing_plan.get("status"),
        "selected_adapter_count": len(selected_rows),
        "executed_adapter_count": len(executions),
        "execution_error_count": len(errors),
        "runtime_fallback_used": bool(parsing_plan.get("runtime_fallback_adapter")),
        "missing_capability_count": len(parsing_plan.get("missing_capabilities") or []),
        "final_status": (merged.get("structure_receipt") or {}).get("status"),
        "business_semantics_added": False,
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
    }
    return merged


__all__ = ["build_document_structure_ir"]
