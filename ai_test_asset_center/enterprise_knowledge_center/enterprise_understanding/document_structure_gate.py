"""Project document-structure gaps into enterprise understanding unknowns."""
from __future__ import annotations

from typing import Any

from .gate import assess_understanding_model
from .schema import as_dict, as_list, new_unknown, text


def _severity(rows: list[dict[str, Any]], default: str) -> str:
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    values = [text(row.get("severity")).upper() for row in rows if text(row.get("severity")).upper() in order]
    return min(values, key=lambda value: order[value]) if values else default


def _sum_receipt_metric(structures: dict[str, Any], key: str) -> int:
    return sum(
        int(as_dict(row.get("structure_receipt")).get(key) or 0)
        for row in as_list(structures.get("items"))
        if isinstance(row, dict)
    )


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
        "visual_table_count": _sum_receipt_metric(structures, "visual_table_count"),
        "formal_visual_table_count": _sum_receipt_metric(structures, "formal_visual_table_count"),
        "borderless_visual_table_count": _sum_receipt_metric(structures, "borderless_visual_table_count"),
        "merged_visual_table_count": _sum_receipt_metric(structures, "merged_visual_table_count"),
        "logical_visual_table_group_count": _sum_receipt_metric(
            structures, "logical_visual_table_group_count"
        ),
        "continued_visual_table_fragment_count": _sum_receipt_metric(
            structures, "continued_visual_table_fragment_count"
        ),
        "multi_level_header_group_count": _sum_receipt_metric(
            structures, "multi_level_header_group_count"
        ),
        "repeated_header_cell_count": _sum_receipt_metric(
            structures, "repeated_header_cell_count"
        ),
        "header_inherited_data_cell_count": _sum_receipt_metric(
            structures, "header_inherited_data_cell_count"
        ),
        "ambiguous_table_continuation_count": _sum_receipt_metric(
            structures, "ambiguous_table_continuation_count"
        ),
        "table_continuation_header_conflict_count": _sum_receipt_metric(
            structures, "table_continuation_header_conflict_count"
        ),
        "table_header_node_count": _sum_receipt_metric(structures, "table_header_node_count"),
        "table_header_group_node_count": _sum_receipt_metric(
            structures, "table_header_group_node_count"
        ),
        "table_header_leaf_node_count": _sum_receipt_metric(
            structures, "table_header_leaf_node_count"
        ),
        "table_row_header_candidate_count": _sum_receipt_metric(
            structures, "table_row_header_candidate_count"
        ),
        "table_condition_column_candidate_count": _sum_receipt_metric(
            structures, "table_condition_column_candidate_count"
        ),
        "table_result_column_candidate_count": _sum_receipt_metric(
            structures, "table_result_column_candidate_count"
        ),
        "decision_matrix_candidate_count": _sum_receipt_metric(
            structures, "decision_matrix_candidate_count"
        ),
        "table_legend_candidate_count": _sum_receipt_metric(
            structures, "table_legend_candidate_count"
        ),
        "table_color_legend_candidate_count": _sum_receipt_metric(
            structures, "table_color_legend_candidate_count"
        ),
        "table_symbol_legend_candidate_count": _sum_receipt_metric(
            structures, "table_symbol_legend_candidate_count"
        ),
        "legend_mapped_cell_count": _sum_receipt_metric(
            structures, "legend_mapped_cell_count"
        ),
        "decision_column_role_ambiguity_count": _sum_receipt_metric(
            structures, "decision_column_role_ambiguity_count"
        ),
        "rejected_row_header_candidate_count": _sum_receipt_metric(
            structures, "rejected_row_header_candidate_count"
        ),
        "rejected_unsafe_column_role_candidate_count": _sum_receipt_metric(
            structures, "rejected_unsafe_column_role_candidate_count"
        ),
        "rejected_overlapping_decision_matrix_candidate_count": _sum_receipt_metric(
            structures, "rejected_overlapping_decision_matrix_candidate_count"
        ),
        "table_legend_token_ambiguity_count": _sum_receipt_metric(
            structures, "table_legend_token_ambiguity_count"
        ),
        "table_symbol_legend_missing_cell_count": _sum_receipt_metric(
            structures, "table_symbol_legend_missing_cell_count"
        ),
        "table_color_legend_unverified_count": _sum_receipt_metric(
            structures, "table_color_legend_unverified_count"
        ),
        "semantic_candidate_inherited_fragment_count": _sum_receipt_metric(
            structures, "semantic_candidate_inherited_fragment_count"
        ),
        "semantic_candidate_inherited_cell_count": _sum_receipt_metric(
            structures, "semantic_candidate_inherited_cell_count"
        ),
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
