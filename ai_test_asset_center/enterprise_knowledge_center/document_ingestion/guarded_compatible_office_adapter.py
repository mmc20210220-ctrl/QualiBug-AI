"""Security and evidence policy wrapper for compatible Office normalization.

The existing compatible adapter remains the only conversion/OOXML delegation implementation.
This wrapper adds one precondition: inspect the immutable source container before LibreOffice
opens it, and fail closed when embedded automation is present or a structured container cannot
be inspected reliably.
"""
from __future__ import annotations

from typing import Any

from .._document_structure_ir import DOCUMENT_IR_SCHEMA, STRUCTURE_RECEIPT_SCHEMA
from .compatible_office_adapter import CompatibleOfficeDocumentAdapter
from .contract import DocumentSource
from .office_container_inspection import inspect_office_container


def _blocked_document_ir(
    source: DocumentSource,
    inspection: dict[str, Any],
    *,
    reason_code: str,
    status: str,
    detail: str,
) -> dict[str, Any]:
    gap = {
        "kind": reason_code,
        "reason_code": reason_code,
        "count": 1,
        "status": status,
        "severity": "P0",
        "blocks_formal_understanding": True,
        "included_in_plain_text_authority": False,
        "source_locator": f"{source.filename}#whole-file",
        "detail": detail[:500],
        "automation_indicators": list(inspection.get("automation_indicators") or []),
        "inspection_status": inspection.get("status"),
    }
    return {
        "schema": DOCUMENT_IR_SCHEMA,
        "format": source.suffix.lstrip(".") or "unknown",
        "filename": source.filename,
        "plain_text": "",
        "blocks": [],
        "sections": [],
        "tables": [],
        "pages": [],
        "unsupported_content": [gap],
        "office_container_inspection": dict(inspection),
        "structure_receipt": {
            "schema": STRUCTURE_RECEIPT_SCHEMA,
            "status": "BLOCKED",
            "format": source.suffix.lstrip(".") or "unknown",
            "block_count": 0,
            "source_traceability_rate": 1.0,
            "block_type_distribution": {},
            "section_count": 0,
            "table_count": 0,
            "unsupported_content_count": 1,
            "critical_unsupported_content_count": 1,
            "unsupported_content": [gap],
            "office_container_inspection": dict(inspection),
            "normalization_attempted": False,
            "original_source_hash_is_evidence_root": True,
            "document_order_is_business_flow": False,
            "filename_is_business_context": False,
        },
    }


def _attach_inspection(
    document_ir: dict[str, Any],
    source: DocumentSource,
    inspection: dict[str, Any],
) -> dict[str, Any]:
    result = dict(document_ir or {})
    unsupported = [
        dict(row)
        for row in (result.get("unsupported_content") or [])
        if isinstance(row, dict)
    ]
    if not bool(inspection.get("inspection_complete")):
        unsupported.append(
            {
                "kind": "OFFICE_CONTAINER_AUTOMATION_INSPECTION_PARTIAL",
                "reason_code": "OFFICE_CONTAINER_AUTOMATION_INSPECTION_PARTIAL",
                "count": 1,
                "status": "SOURCE_NORMALIZED_WITH_PARTIAL_PRECONVERSION_AUTOMATION_INSPECTION",
                "severity": "P1",
                "blocks_formal_understanding": False,
                "included_in_plain_text_authority": False,
                "source_locator": f"{source.filename}#whole-file",
                "inspection_status": inspection.get("status"),
                "inspection_errors": list(inspection.get("errors") or []),
            }
        )
    critical = sum(
        int(row.get("count") or 0)
        for row in unsupported
        if bool(row.get("blocks_formal_understanding"))
    )
    status = "BLOCKED" if critical else "PARTIAL" if unsupported else "COMPLETE"
    receipt = dict(result.get("structure_receipt") or {})
    receipt.update(
        {
            "status": status,
            "unsupported_content": unsupported,
            "unsupported_content_count": sum(int(row.get("count") or 0) for row in unsupported),
            "critical_unsupported_content_count": critical,
            "office_container_inspection": dict(inspection),
            "preconversion_automation_inspection_performed": True,
            "automation_code_executed": False,
        }
    )
    result["unsupported_content"] = unsupported
    result["office_container_inspection"] = dict(inspection)
    result["structure_receipt"] = receipt
    return result


class GuardedCompatibleOfficeDocumentAdapter(CompatibleOfficeDocumentAdapter):
    """Compatible Office adapter with pre-conversion automation blocking."""

    parser_version = "3"

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        inspection = inspect_office_container(source)
        if bool(inspection.get("automation_artifact_detected")):
            return _blocked_document_ir(
                source,
                inspection,
                reason_code="OFFICE_EMBEDDED_AUTOMATION_NOT_INTERPRETED",
                status="EMBEDDED_AUTOMATION_PRESENT_NORMALIZATION_NOT_STARTED",
                detail="The source contains VBA, Basic, or script artifacts whose behavior is not interpreted.",
            )
        container_kind = str(inspection.get("container_kind") or "")
        if (
            container_kind in {"ZIP_PACKAGE", "OLE_COMPOUND_FILE"}
            and not bool(inspection.get("inspection_complete"))
        ):
            return _blocked_document_ir(
                source,
                inspection,
                reason_code="OFFICE_CONTAINER_SECURITY_INSPECTION_FAILED",
                status="STRUCTURED_CONTAINER_COULD_NOT_BE_SAFELY_INSPECTED",
                detail="The structured Office container could not be inspected completely before normalization.",
            )
        return _attach_inspection(super().extract(source), source, inspection)


__all__ = ["GuardedCompatibleOfficeDocumentAdapter"]
