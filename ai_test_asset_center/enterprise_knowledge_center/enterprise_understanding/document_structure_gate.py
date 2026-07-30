"""Project document-structure and source-evidence gaps into understanding unknowns."""
from __future__ import annotations

from typing import Any

from .gate import assess_understanding_model
from .schema import as_dict, as_list, new_unknown, text

_NATIVE_STRUCTURED_FORMATS = {
    "docx",
    "pdf",
    "xlsx",
    "xlsm",
    "xltx",
    "xltm",
    "pptx",
    "pptm",
    "potx",
    "potm",
    "ppsx",
    "ppsm",
}

_RECEIPT_SUM_METRICS = (
    "visual_table_count",
    "formal_visual_table_count",
    "borderless_visual_table_count",
    "merged_visual_table_count",
    "logical_visual_table_group_count",
    "continued_visual_table_fragment_count",
    "multi_level_header_group_count",
    "repeated_header_cell_count",
    "header_inherited_data_cell_count",
    "ambiguous_table_continuation_count",
    "table_continuation_header_conflict_count",
    "table_header_node_count",
    "table_header_group_node_count",
    "table_header_leaf_node_count",
    "table_row_header_candidate_count",
    "table_condition_column_candidate_count",
    "table_result_column_candidate_count",
    "decision_matrix_candidate_count",
    "table_legend_candidate_count",
    "table_color_legend_candidate_count",
    "table_symbol_legend_candidate_count",
    "legend_mapped_cell_count",
    "decision_column_role_ambiguity_count",
    "rejected_row_header_candidate_count",
    "rejected_unsafe_column_role_candidate_count",
    "rejected_overlapping_decision_matrix_candidate_count",
    "table_legend_token_ambiguity_count",
    "table_symbol_legend_missing_cell_count",
    "table_color_legend_unverified_count",
    "semantic_candidate_inherited_fragment_count",
    "semantic_candidate_inherited_cell_count",
)


def _severity(rows: list[dict[str, Any]], default: str) -> str:
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    values = [
        text(row.get("severity")).upper()
        for row in rows
        if text(row.get("severity")).upper() in order
    ]
    return min(values, key=lambda value: order[value]) if values else default


def _sum_receipt_metric(structures: dict[str, Any], key: str) -> int:
    return sum(
        int(as_dict(row.get("structure_receipt")).get(key) or 0)
        for row in as_list(structures.get("items"))
        if isinstance(row, dict)
    )


def _evidence_totals(structures: dict[str, Any]) -> dict[str, int | float]:
    totals = {
        "formal": 0,
        "traceable": 0,
        "exact": 0,
        "untraceable": 0,
        "weak": 0,
        "conflicts": 0,
        "missing_receipts": 0,
    }
    for row in as_list(structures.get("items")):
        if not isinstance(row, dict):
            continue
        receipt = as_dict(row.get("evidence_closure_receipt"))
        if not receipt:
            totals["missing_receipts"] += 1
            continue
        totals["formal"] += int(receipt.get("formal_authority_block_count") or 0)
        totals["traceable"] += int(
            receipt.get("traceable_authority_block_count") or 0
        )
        totals["exact"] += int(
            receipt.get("exact_address_authority_block_count") or 0
        )
        totals["untraceable"] += int(
            receipt.get("untraceable_authority_block_count") or 0
        )
        totals["weak"] += int(
            receipt.get("weak_address_authority_block_count") or 0
        )
        totals["conflicts"] += int(receipt.get("locator_conflict_count") or 0)
    formal = int(totals["formal"])
    totals["traceability_rate"] = (
        round(int(totals["traceable"]) / formal, 4) if formal else 1.0
    )
    totals["exact_address_rate"] = (
        round(int(totals["exact"]) / formal, 4) if formal else 1.0
    )
    return totals


def _active_source_ids(asset: dict[str, Any]) -> set[str]:
    return {
        text(row.get("source_id"))
        for row in as_list(asset.get("source_inventory"))
        if isinstance(row, dict)
        and text(row.get("status") or "active") == "active"
        and text(row.get("source_id"))
    }


def _append_structure_unknowns(
    unknowns: list[dict[str, Any]],
    structure: dict[str, Any],
) -> None:
    source_id = text(structure.get("source_id"))
    filename = text(structure.get("filename"))
    source_label = filename or source_id
    receipt = as_dict(structure.get("structure_receipt"))
    pipeline_receipt = as_dict(structure.get("ingestion_pipeline_receipt"))
    evidence_receipt = as_dict(structure.get("evidence_closure_receipt"))
    unsupported = [
        row
        for row in as_list(
            structure.get("unsupported_content") or receipt.get("unsupported_content")
        )
        if isinstance(row, dict) and int(row.get("count") or 0) > 0
    ]
    critical = [
        row for row in unsupported if bool(row.get("blocks_formal_understanding"))
    ]
    partial = [
        row for row in unsupported if not bool(row.get("blocks_formal_understanding"))
    ]

    if not pipeline_receipt:
        unknowns.append(
            new_unknown(
                "DOCUMENT_INGESTION_PIPELINE_RECEIPT_MISSING",
                f"资料“{source_label}”没有统一资料接入主链回执，无法证明其经过规划、适配、合并和证据闭环。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="DOCUMENT_INGESTION_PIPELINE_RECEIPT_MISSING",
                details={"source_id": source_id, "filename": filename},
            )
        )

    if not evidence_receipt:
        unknowns.append(
            new_unknown(
                "DOCUMENT_EVIDENCE_CLOSURE_RECEIPT_MISSING",
                f"资料“{source_label}”没有源字节到结构块的证据闭环回执。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="DOCUMENT_EVIDENCE_CLOSURE_RECEIPT_MISSING",
                details={"source_id": source_id, "filename": filename},
            )
        )
    elif (
        text(evidence_receipt.get("status")) != "PASS"
        or int(evidence_receipt.get("untraceable_authority_block_count") or 0) > 0
        or int(evidence_receipt.get("locator_conflict_count") or 0) > 0
    ):
        unknowns.append(
            new_unknown(
                "DOCUMENT_EVIDENCE_CHAIN_INCOMPLETE",
                f"资料“{source_label}”存在无法唯一回到原文件位置的正式文本块。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="DOCUMENT_EVIDENCE_CHAIN_INCOMPLETE",
                details={
                    "source_id": source_id,
                    "filename": filename,
                    "evidence_closure_receipt": evidence_receipt,
                },
            )
        )
    elif int(evidence_receipt.get("weak_address_authority_block_count") or 0) > 0:
        unknowns.append(
            new_unknown(
                "DOCUMENT_EVIDENCE_ADDRESS_WEAK",
                f"资料“{source_label}”仍有结构块只有弱位置地址，尚未达到页/段/行/单元格/形状级定位。",
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="DOCUMENT_EVIDENCE_ADDRESS_WEAK",
                details={
                    "source_id": source_id,
                    "filename": filename,
                    "weak_address_authority_block_count": evidence_receipt.get(
                        "weak_address_authority_block_count"
                    ),
                },
            )
        )

    if critical:
        kinds = sorted(
            {text(row.get("kind")) for row in critical if text(row.get("kind"))}
        )
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
                reason_code=(
                    reason_codes[0]
                    if len(reason_codes) == 1
                    else "DOCUMENT_STRUCTURE_CRITICAL_GAPS"
                ),
                details={
                    "source_id": source_id,
                    "filename": filename,
                    "critical_unsupported_content": critical,
                    "structure_status": receipt.get("status"),
                },
            )
        )

    if partial:
        kinds = sorted(
            {text(row.get("kind")) for row in partial if text(row.get("kind"))}
        )
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
    if source_format in _NATIVE_STRUCTURED_FORMATS and block_count == 0:
        unknowns.append(
            new_unknown(
                f"{source_format.upper()}_STRUCTURE_EMPTY",
                f"资料“{source_label}”未形成任何可追溯结构块。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code=f"{source_format.upper()}_STRUCTURE_EMPTY",
                details={"source_id": source_id, "filename": filename},
            )
        )

    if source_format == "pdf":
        page_count = int(
            receipt.get("page_count") or len(as_list(structure.get("pages")))
        )
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


def _reconcile_fact_evidence(
    unknowns: list[dict[str, Any]], asset: dict[str, Any]
) -> dict[str, Any]:
    ledger = as_dict(asset.get("business_fact_ledger"))
    accepted_fact_ids = {
        text(row.get("fact_id"))
        for row in as_list(ledger.get("items"))
        if isinstance(row, dict)
        and text(row.get("status")) == "ACCEPTED"
        and text(row.get("fact_id"))
    }
    receipt = as_dict(asset.get("document_ir_fact_evidence_receipt"))
    if accepted_fact_ids and not receipt:
        unknowns.append(
            new_unknown(
                "DOCUMENT_IR_FACT_EVIDENCE_RECEIPT_MISSING",
                (
                    f"存在{len(accepted_fact_ids)}条已接受业务事实，但没有 Document IR 事实证据回执；"
                    "不能假定这些事实已定位到原文。"
                ),
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="DOCUMENT_IR_FACT_EVIDENCE_RECEIPT_MISSING",
                details={"accepted_fact_ids": sorted(accepted_fact_ids)[:500]},
            )
        )

    aligned_rows = [
        dict(row)
        for row in as_list(receipt.get("aligned"))
        if isinstance(row, dict) and text(row.get("fact_id"))
    ]
    unresolved_rows = [
        dict(row)
        for row in as_list(receipt.get("unresolved"))
        if isinstance(row, dict) and text(row.get("fact_id"))
    ]
    aligned_ids = {text(row.get("fact_id")) for row in aligned_rows}
    unresolved_ids = {text(row.get("fact_id")) for row in unresolved_rows}
    omitted_ids = accepted_fact_ids - aligned_ids - unresolved_ids
    unresolved_accepted = [
        row for row in unresolved_rows if text(row.get("fact_id")) in accepted_fact_ids
    ]
    unresolved_accepted.extend(
        {
            "fact_id": fact_id,
            "reason": "DOCUMENT_IR_FACT_EVIDENCE_RECEIPT_OMITTED_FACT",
            "candidate_block_ids": [],
            "candidate_block_spans": [],
        }
        for fact_id in sorted(omitted_ids)
    )
    if unresolved_accepted:
        unknowns.append(
            new_unknown(
                "FORMAL_FACT_WITHOUT_EXACT_DOCUMENT_EVIDENCE",
                (
                    f"{len(unresolved_accepted)}条已接受业务事实无法唯一定位到原始 Document IR 块；"
                    "这些事实不能作为正式企业理解依据。"
                ),
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="FORMAL_FACT_WITHOUT_EXACT_DOCUMENT_EVIDENCE",
                details={
                    "accepted_fact_count": len(accepted_fact_ids),
                    "unresolved_accepted_fact_count": len(unresolved_accepted),
                    "omitted_accepted_fact_count": len(omitted_ids),
                    "unresolved": unresolved_accepted[:500],
                },
            )
        )

    exact_aligned_ids = (accepted_fact_ids & aligned_ids) - unresolved_ids
    return {
        "ledger": ledger,
        "receipt": receipt,
        "accepted_fact_ids": accepted_fact_ids,
        "aligned_fact_count": int(receipt.get("aligned_fact_count") or len(aligned_rows)),
        "aligned_accepted_fact_count": len(exact_aligned_ids),
        "unresolved_accepted_fact_count": len(unresolved_accepted),
        "omitted_accepted_fact_count": len(omitted_ids),
    }


def apply_document_structure_completeness(
    model: dict[str, Any], asset: dict[str, Any]
) -> dict[str, Any]:
    """Prevent source loss, structure loss, or orphan formal facts from reporting PASS."""
    structures = as_dict(asset.get("document_structure_assets"))
    unknowns = [row for row in as_list(model.get("unknowns")) if isinstance(row, dict)]
    active_source_ids = _active_source_ids(asset)
    structure_items = [
        row for row in as_list(structures.get("items")) if isinstance(row, dict)
    ]
    represented_source_ids = {
        text(row.get("source_id"))
        for row in structure_items
        if text(row.get("source_id"))
    }
    missing_structure_source_ids = sorted(active_source_ids - represented_source_ids)
    if missing_structure_source_ids:
        unknowns.append(
            new_unknown(
                "ACTIVE_SOURCE_WITHOUT_DOCUMENT_STRUCTURE",
                (
                    f"{len(missing_structure_source_ids)}份已启用企业资料没有形成统一 Document IR；"
                    "任何旧文本缓存都不能替代原始源字节结构解析。"
                ),
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="ACTIVE_SOURCE_WITHOUT_DOCUMENT_STRUCTURE",
                details={
                    "active_source_count": len(active_source_ids),
                    "represented_source_count": len(represented_source_ids),
                    "missing_source_ids": missing_structure_source_ids,
                },
            )
        )

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

    for structure in structure_items:
        _append_structure_unknowns(unknowns, structure)

    fact_evidence = _reconcile_fact_evidence(unknowns, asset)
    model["unknowns"] = list(
        {
            text(row.get("unknown_id")): row
            for row in unknowns
            if isinstance(row, dict) and text(row.get("unknown_id"))
        }.values()
    )

    evidence_totals = _evidence_totals(structures)
    accepted_fact_count = len(fact_evidence["accepted_fact_ids"])
    aligned_accepted_fact_count = int(fact_evidence["aligned_accepted_fact_count"])
    summary: dict[str, Any] = {
        "active_source_count": len(active_source_ids),
        "represented_source_count": len(represented_source_ids),
        "missing_structure_source_count": len(missing_structure_source_ids),
        "source_count": int(structures.get("source_count") or 0),
        "block_count": int(structures.get("block_count") or 0),
        "page_count": int(structures.get("page_count") or 0),
        "scanned_page_count": int(structures.get("scanned_page_count") or 0),
        "image_count": int(structures.get("image_count") or 0),
        "table_region_count": int(structures.get("table_region_count") or 0),
        "multi_column_page_count": int(structures.get("multi_column_page_count") or 0),
        "unsupported_content_count": int(structures.get("unsupported_content_count") or 0),
        "critical_structure_gap_count": int(
            structures.get("critical_structure_gap_count") or 0
        ),
        "structure_error_count": len(as_list(structures.get("errors"))),
        "evidence_formal_authority_block_count": evidence_totals["formal"],
        "evidence_traceable_authority_block_count": evidence_totals["traceable"],
        "evidence_exact_address_authority_block_count": evidence_totals["exact"],
        "evidence_untraceable_authority_block_count": evidence_totals["untraceable"],
        "evidence_weak_address_authority_block_count": evidence_totals["weak"],
        "evidence_locator_conflict_count": evidence_totals["conflicts"],
        "evidence_missing_receipt_count": evidence_totals["missing_receipts"],
        "evidence_source_traceability_rate": evidence_totals["traceability_rate"],
        "evidence_exact_address_rate": evidence_totals["exact_address_rate"],
        "business_fact_count": len(as_list(fact_evidence["ledger"].get("items"))),
        "accepted_business_fact_count": accepted_fact_count,
        "document_ir_aligned_fact_count": fact_evidence["aligned_fact_count"],
        "aligned_accepted_fact_count": aligned_accepted_fact_count,
        "unresolved_accepted_fact_count": fact_evidence[
            "unresolved_accepted_fact_count"
        ],
        "omitted_accepted_fact_count": fact_evidence["omitted_accepted_fact_count"],
        "accepted_fact_exact_evidence_rate": (
            round(aligned_accepted_fact_count / accepted_fact_count, 4)
            if accepted_fact_count
            else 1.0
        ),
    }
    summary.update(
        {
            key: _sum_receipt_metric(structures, key)
            for key in _RECEIPT_SUM_METRICS
        }
    )
    model["document_structure_summary"] = summary

    gate = assess_understanding_model(
        model,
        upstream_gate=as_dict(asset.get("enterprise_comprehension_gate")),
    )
    model["gate"] = gate
    model["metrics"] = dict(gate.get("metrics") or {})
    model["metrics"].update(
        {
            "document_source_coverage_rate": (
                round(len(represented_source_ids) / len(active_source_ids), 4)
                if active_source_ids
                else 1.0
            ),
            "document_evidence_source_traceability_rate": evidence_totals[
                "traceability_rate"
            ],
            "document_evidence_exact_address_rate": evidence_totals[
                "exact_address_rate"
            ],
            "accepted_fact_exact_evidence_rate": summary[
                "accepted_fact_exact_evidence_rate"
            ],
        }
    )
    return model


__all__ = ["apply_document_structure_completeness"]
