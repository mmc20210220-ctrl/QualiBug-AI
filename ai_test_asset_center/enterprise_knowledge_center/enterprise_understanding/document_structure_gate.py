"""Project document-structure gaps into enterprise understanding unknowns."""
from __future__ import annotations

from typing import Any

from .gate import assess_understanding_model
from .schema import as_dict, as_list, new_unknown, text


def _severity(rows: list[dict[str, Any]], default: str) -> str:
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    values = [text(row.get("severity")).upper() for row in rows if text(row.get("severity")).upper() in order]
    return min(values, key=lambda value: order[value]) if values else default


def apply_document_structure_completeness(
    model: dict[str, Any], asset: dict[str, Any]
) -> dict[str, Any]:
    """Prevent structure-loss documents from reporting complete understanding."""
    structures = as_dict(asset.get("document_structure_assets"))
    unknowns = [row for row in as_list(model.get("unknowns")) if isinstance(row, dict)]

    for error in as_list(structures.get("errors")):
        if not isinstance(error, dict):
            continue
        unknowns.append(
            new_unknown(
                "DOCUMENT_STRUCTURE_PARSE_FAILED",
                f"资料“{text(error.get('filename')) or text(error.get('source_id'))}”的原生文档结构解析失败。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code=text(error.get("code")) or "DOCUMENT_STRUCTURE_PARSE_FAILED",
                details=error,
            )
        )

    for structure in as_list(structures.get("items")):
        if not isinstance(structure, dict):
            continue
        source_id = text(structure.get("source_id"))
        filename = text(structure.get("filename"))
        source_label = filename or source_id
        receipt = as_dict(structure.get("structure_receipt"))
        unsupported = [
            row
            for row in as_list(structure.get("unsupported_content") or receipt.get("unsupported_content"))
            if isinstance(row, dict) and int(row.get("count") or 0) > 0
        ]
        critical = [row for row in unsupported if bool(row.get("blocks_formal_understanding"))]
        partial = [row for row in unsupported if not bool(row.get("blocks_formal_understanding"))]

        if critical:
            kinds = sorted({text(row.get("kind")) for row in critical if text(row.get("kind"))})
            reason_codes = sorted(
                {
                    text(row.get("reason_code") or row.get("kind"))
                    for row in critical
                    if text(row.get("reason_code") or row.get("kind"))
                }
            )
            unknowns.append(
                new_unknown(
                    "DOCUMENT_STRUCTURE_CONTENT_UNAVAILABLE",
                    (
                        f"资料“{source_label}”存在无法进入正式业务理解的关键结构内容："
                        f"{'、'.join(kinds)}。"
                    ),
                    severity=_severity(critical, "P0"),
                    blocks_formal_understanding=True,
                    reason_code=reason_codes[0] if len(reason_codes) == 1 else "DOCUMENT_STRUCTURE_CRITICAL_GAPS",
                    details={
                        "source_id": source_id,
                        "filename": filename,
                        "critical_unsupported_content": critical,
                        "structure_status": receipt.get("status"),
                    },
                )
            )

        if partial:
            kinds = sorted({text(row.get("kind")) for row in partial if text(row.get("kind"))})
            unknowns.append(
                new_unknown(
                    "DOCUMENT_STRUCTURE_CONTENT_UNPARSED",
                    (
                        f"资料“{source_label}”包含尚未进入正式语义理解的结构内容："
                        f"{'、'.join(kinds)}。"
                    ),
                    severity=_severity(partial, "P1"),
                    blocks_formal_understanding=False,
                    reason_code="DOCUMENT_STRUCTURE_CONTENT_UNPARSED",
                    details={
                        "source_id": source_id,
                        "filename": filename,
                        "unsupported_content": partial,
                        "structure_status": receipt.get("status"),
                    },
                )
            )

        block_count = len(as_list(structure.get("blocks")))
        source_format = text(structure.get("format")).lower()
        if source_format in {"docx", "pdf"} and block_count == 0:
            label = "Word" if source_format == "docx" else "PDF"
            unknowns.append(
                new_unknown(
                    f"{source_format.upper()}_STRUCTURE_EMPTY",
                    f"{label}资料“{source_label}”未形成任何可追溯结构块。",
                    severity="P0",
                    blocks_formal_understanding=True,
                    reason_code=f"{source_format.upper()}_STRUCTURE_EMPTY",
                    details={"source_id": source_id, "filename": filename},
                )
            )

        if source_format == "pdf":
            page_count = int(receipt.get("page_count") or len(as_list(structure.get("pages"))))
            text_page_count = int(receipt.get("text_page_count") or 0)
            scanned_page_count = int(receipt.get("scanned_page_count") or 0)
            if page_count > 0 and text_page_count == 0 and scanned_page_count == 0:
                unknowns.append(
                    new_unknown(
                        "PDF_NO_TEXTUAL_CONTENT_UNDERSTOOD",
                        f"PDF资料“{source_label}”没有形成任何可验证的文本页内容。",
                        severity="P0",
                        blocks_formal_understanding=True,
                        reason_code="PDF_NO_TEXTUAL_CONTENT_UNDERSTOOD",
                        details={
                            "source_id": source_id,
                            "filename": filename,
                            "page_count": page_count,
                            "text_page_count": text_page_count,
                        },
                    )
                )

    model["unknowns"] = list(
        {
            text(row.get("unknown_id")): row
            for row in unknowns
            if isinstance(row, dict) and text(row.get("unknown_id"))
        }.values()
    )
    model["document_structure_summary"] = {
        "source_count": int(structures.get("source_count") or 0),
        "block_count": int(structures.get("block_count") or 0),
        "page_count": int(structures.get("page_count") or 0),
        "scanned_page_count": int(structures.get("scanned_page_count") or 0),
        "image_count": int(structures.get("image_count") or 0),
        "table_region_count": int(structures.get("table_region_count") or 0),
        "multi_column_page_count": int(structures.get("multi_column_page_count") or 0),
        "unsupported_content_count": int(structures.get("unsupported_content_count") or 0),
        "critical_structure_gap_count": int(structures.get("critical_structure_gap_count") or 0),
        "structure_error_count": len(as_list(structures.get("errors"))),
    }
    gate = assess_understanding_model(
        model,
        upstream_gate=as_dict(asset.get("enterprise_comprehension_gate")),
    )
    model["gate"] = gate
    model["metrics"] = dict(gate.get("metrics") or {})
    return model


__all__ = ["apply_document_structure_completeness"]
