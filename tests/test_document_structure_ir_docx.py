from __future__ import annotations

import io

import pytest

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
)
from ai_test_asset_center.enterprise_knowledge_center._document_ir_context import (
    apply_document_ir_context,
)
from ai_test_asset_center.enterprise_knowledge_center._document_structure_ir_normalizer import (
    extract_normalized_docx_document_ir,
)


def _require_docx():
    return pytest.importorskip("docx")


def _save(document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _asset() -> dict:
    return {
        "business_objects": [{"object": "订单"}, {"object": "合同"}],
        "roles": [{"role": "仓库管理员"}],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def test_docx_ir_preserves_heading_list_table_order_and_header_isolation() -> None:
    docx = _require_docx()
    document = docx.Document()
    document.sections[0].header.paragraphs[0].text = "某公司内部制度"
    document.add_heading("订单管理", level=1)
    document.add_paragraph("订单支付成功后才能发货。")
    document.add_paragraph("1）创建订单")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "状态"
    table.cell(0, 1).text = "动作"
    table.cell(1, 0).text = "待支付"
    table.cell(1, 1).text = "付款"

    ir = extract_normalized_docx_document_ir(_save(document), "订单制度.docx")
    body_blocks = [row for row in ir["blocks"] if row.get("region") == "body"]
    body_types = [row["type"] for row in body_blocks]

    assert body_types[:4] == ["HEADING", "PARAGRAPH", "LIST_ITEM", "TABLE"]
    heading = body_blocks[0]
    paragraph = body_blocks[1]
    list_item = body_blocks[2]
    table_block = body_blocks[3]
    assert heading["level"] == 1
    assert heading["structure_evidence"]["heading_method"] in {
        "docx_outline_level",
        "docx_paragraph_style",
    }
    assert paragraph["parent_id"] == heading["block_id"]
    assert list_item["parent_id"] == heading["block_id"]
    assert list_item["numbering"]["source"] == "visible_list_marker"
    assert table_block["parent_id"] == heading["block_id"]
    assert "订单管理\n订单支付成功后才能发货。\n1）创建订单\n" in ir["plain_text"]

    headers = [row for row in ir["blocks"] if row.get("type") == "HEADER"]
    assert headers and headers[0]["excluded_from_main_flow"] is True
    assert "某公司内部制度" not in ir["plain_text"]
    assert ir["structure_receipt"]["headers_and_footers_excluded_from_main_flow"] is True
    assert ir["structure_receipt"]["document_order_is_business_flow"] is False


def test_docx_ir_preserves_merged_cell_identity() -> None:
    docx = _require_docx()
    document = docx.Document()
    document.add_heading("审批规则", level=1)
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "条件"
    table.cell(0, 1).text = "结果"
    merged = table.cell(1, 0).merge(table.cell(1, 1))
    merged.text = "金额超过十万元需总经理审批"

    ir = extract_normalized_docx_document_ir(_save(document), "审批规则.docx")
    cells = [row for row in ir["blocks"] if row.get("type") == "TABLE_CELL"]
    merged_cells = [
        row
        for row in cells
        if row.get("merged_with_cell_id") or (row.get("merge") or {}).get("grid_span")
    ]
    assert merged_cells


def test_docx_style_heading_resolves_pending_chinese_reference() -> None:
    docx = _require_docx()
    document = docx.Document()
    document.add_heading("订单", level=1)
    document.add_paragraph("其不得发货。")
    source_id = "docx-order-1"
    statement_source = {
        "source_id": source_id,
        "filename": "企业制度.docx",
        "text": "其不得发货。",
    }
    asset = build_chinese_first_comprehension(_asset(), [statement_source])
    assert asset["business_fact_ledger"]["items"][0]["status"] == "PENDING"

    ir = extract_normalized_docx_document_ir(_save(document), "企业制度.docx")
    enriched = apply_document_ir_context(
        asset,
        [
            {
                **statement_source,
                "document_structure": ir,
            }
        ],
    )
    fact = enriched["business_fact_ledger"]["items"][0]
    assert fact["status"] == "ACCEPTED"
    assert fact["subject"]["entity_refs"] == ["订单"]
    assert fact["document_structure_context"]["filename_context_used"] is False
    assert enriched["document_ir_context_resolution_receipt"]["resolved_fact_count"] == 1


def test_docx_filename_cannot_resolve_reference_without_body_heading() -> None:
    docx = _require_docx()
    document = docx.Document()
    document.add_paragraph("其不得发货。")
    source_id = "docx-order-2"
    statement_source = {
        "source_id": source_id,
        "filename": "订单规则.docx",
        "text": "其不得发货。",
    }
    asset = build_chinese_first_comprehension(_asset(), [statement_source])
    ir = extract_normalized_docx_document_ir(_save(document), "订单规则.docx")
    enriched = apply_document_ir_context(
        asset,
        [{**statement_source, "document_structure": ir}],
    )
    fact = enriched["business_fact_ledger"]["items"][0]
    assert fact["status"] == "PENDING"
    receipt = enriched["document_ir_context_resolution_receipt"]
    assert receipt["resolved_fact_count"] == 0
    assert receipt["filename_context_used"] is False


def test_docx_table_cell_uses_own_heading_context_not_table_order() -> None:
    docx = _require_docx()
    document = docx.Document()
    document.add_heading("订单", level=1)
    table = document.add_table(rows=2, cols=1)
    table.cell(0, 0).text = "规则"
    table.cell(1, 0).text = "其不得发货。"
    source_id = "docx-order-table"
    statement_source = {
        "source_id": source_id,
        "filename": "业务制度.docx",
        "text": "其不得发货。",
    }
    asset = build_chinese_first_comprehension(_asset(), [statement_source])
    ir = extract_normalized_docx_document_ir(_save(document), "业务制度.docx")
    enriched = apply_document_ir_context(
        asset,
        [{**statement_source, "document_structure": ir}],
    )
    fact = enriched["business_fact_ledger"]["items"][0]
    assert fact["status"] == "ACCEPTED"
    assert fact["subject"]["entity_refs"] == ["订单"]
    assert fact["document_structure_context"]["block_type"] == "TABLE_CELL"
    assert fact["document_structure_context"]["document_order_as_business_flow_used"] is False


def test_heading_with_multiple_business_objects_keeps_reference_pending() -> None:
    docx = _require_docx()
    document = docx.Document()
    document.add_heading("订单与合同", level=1)
    document.add_paragraph("其不得删除。")
    source_id = "docx-ambiguous-heading"
    statement_source = {
        "source_id": source_id,
        "filename": "企业制度.docx",
        "text": "其不得删除。",
    }
    asset = build_chinese_first_comprehension(_asset(), [statement_source])
    ir = extract_normalized_docx_document_ir(_save(document), "企业制度.docx")
    enriched = apply_document_ir_context(
        asset,
        [{**statement_source, "document_structure": ir}],
    )
    fact = enriched["business_fact_ledger"]["items"][0]
    assert fact["status"] == "PENDING"
    assert any(
        value.startswith("DOCUMENT_IR_HEADING_AMBIGUOUS")
        for value in fact["ambiguities"]
    )
