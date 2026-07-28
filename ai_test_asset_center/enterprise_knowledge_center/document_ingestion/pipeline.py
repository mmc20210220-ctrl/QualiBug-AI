"""Unified document ingestion pipeline.

Every source follows the same path: fingerprint -> primary plan -> execute primary
adapters -> plan deferred supplemental capabilities -> merge IR -> expose gaps.
Business understanding never selects adapters directly.
"""
from __future__ import annotations

from typing import Any

from .contract import (
    AdapterMatch,
    CAP_TEXT_EXTRACTION,
    DocumentSource,
    MODE_FALLBACK,
    MODE_PRIMARY,
    SupplementalContext,
    text,
    unique_text,
)
from .merger import merge_document_irs
from .planner import plan_deferred_supplementals, plan_document_parsing
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


def _execute_supplemental_adapter(
    source: DocumentSource,
    registry: DocumentAdapterRegistry,
    plan_row: dict[str, Any],
    context: SupplementalContext,
) -> dict[str, Any]:
    adapter = registry.get(text(plan_row.get("adapter_name")))
    match = _match_from_plan(plan_row)
    document_ir = adapter.extract_supplemental(source, context)
    receipt = adapter.receipt(source, match)
    receipt["deferred_execution"] = True
    receipt["trigger_gap_count"] = len(context.trigger_gaps)
    receipt["requested_capabilities"] = list(context.requested_capabilities)
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


def _primary_execution(executions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for execution in executions:
        if text(execution.get("mode")) == MODE_PRIMARY:
            return execution
    return executions[0] if executions else None


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
                selected_names.add(text(row.get("adapter_name")))
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

    deferred_plan: dict[str, Any] = {
        "schema": "qualibug.deferred-document-parsing-plan.v1",
        "status": "NOT_REQUIRED",
        "trigger_gaps": [],
        "requested_capabilities": [],
        "provided_capabilities": [],
        "missing_capabilities": [],
        "selected_adapters": [],
    }
    primary = _primary_execution(executions)
    if primary is not None:
        primary_ir = dict(primary.get("document_ir") or {})
        deferred_plan = plan_deferred_supplementals(
            source,
            primary_ir,
            resolved_registry,
            excluded_names=selected_names,
        )
        context = SupplementalContext(
            primary_document_ir=primary_ir,
            trigger_gaps=tuple(
                dict(row)
                for row in (deferred_plan.get("trigger_gaps") or [])
                if isinstance(row, dict)
            ),
            requested_capabilities=tuple(
                text(value)
                for value in (deferred_plan.get("requested_capabilities") or [])
                if text(value)
            ),
        )
        for row in deferred_plan.get("selected_adapters") or []:
            if not isinstance(row, dict):
                continue
            try:
                executions.append(
                    _execute_supplemental_adapter(
                        source,
                        resolved_registry,
                        row,
                        context,
                    )
                )
                selected_names.add(text(row.get("adapter_name")))
            except Exception as exc:
                errors.append(
                    {
                        "adapter_name": row.get("adapter_name"),
                        "code": "DOCUMENT_SUPPLEMENTAL_ADAPTER_EXECUTION_FAILED",
                        "detail": f"{type(exc).__name__}: {exc}"[:500],
                        "primary": True,
                        "mode": row.get("mode"),
                    }
                )

    provided = unique_text(
        [
            *(parsing_plan.get("provided_capabilities") or []),
            *(deferred_plan.get("provided_capabilities") or []),
        ]
    )
    parsing_plan["provided_capabilities"] = provided
    parsing_plan["missing_capabilities"] = sorted(
        set(parsing_plan.get("required_capabilities") or []) - set(provided)
    )
    parsing_plan["deferred_plan"] = deferred_plan
    parsing_plan["deferred_selected_adapters"] = list(
        deferred_plan.get("selected_adapters") or []
    )

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
        "deferred_plan_status": deferred_plan.get("status"),
        "selected_adapter_count": len(selected_rows),
        "deferred_selected_adapter_count": len(deferred_plan.get("selected_adapters") or []),
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
