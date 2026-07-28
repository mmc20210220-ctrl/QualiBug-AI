"""Project document-structure gaps into enterprise understanding unknowns."""
from __future__ import annotations

from typing import Any

from .gate import assess_understanding_model
from .schema import as_dict, as_list, new_unknown, text


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
        receipt = as_dict(structure.get("structure_receipt"))
        unsupported = [
            row
            for row in as_list(structure.get("unsupported_content") or receipt.get("unsupported_content"))
            if isinstance(row, dict) and int(row.get("count") or 0) > 0
        ]
        if unsupported:
            kinds = sorted({text(row.get("kind")) for row in unsupported if text(row.get("kind"))})
            unknowns.append(
                new_unknown(
                    "DOCUMENT_STRUCTURE_CONTENT_UNPARSED",
                    (
                        f"资料“{filename or source_id}”包含尚未进入正式语义理解的结构内容："
                        f"{'、'.join(kinds)}。"
                    ),
                    severity="P1",
                    blocks_formal_understanding=False,
                    reason_code="DOCUMENT_STRUCTURE_CONTENT_UNPARSED",
                    details={
                        "source_id": source_id,
                        "filename": filename,
                        "unsupported_content": unsupported,
                        "structure_status": receipt.get("status"),
                    },
                )
            )
        block_count = len(as_list(structure.get("blocks")))
        if text(structure.get("format")) == "docx" and block_count == 0:
            unknowns.append(
                new_unknown(
                    "DOCX_STRUCTURE_EMPTY",
                    f"Word资料“{filename or source_id}”未形成任何可追溯结构块。",
                    severity="P0",
                    blocks_formal_understanding=True,
                    reason_code="DOCX_STRUCTURE_EMPTY",
                    details={"source_id": source_id, "filename": filename},
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
        "unsupported_content_count": int(structures.get("unsupported_content_count") or 0),
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
