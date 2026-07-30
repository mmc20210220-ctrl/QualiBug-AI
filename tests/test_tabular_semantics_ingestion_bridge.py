from __future__ import annotations

import io

import openpyxl

from ai_test_asset_center.enterprise_knowledge_center.source_ingestion import (
    parse_enterprise_source,
)


def _workbook_bytes(rows: list[list[str]], sheet_name: str) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def test_real_xlsx_historical_bug_enters_compatibility_ticket_with_cell_evidence() -> None:
    blob = _workbook_bytes(
        [
            ["历史缺陷汇总", "", "", "", "", ""],
            ["缺陷编号", "缺陷标题", "严重程度", "复现步骤", "预期结果", "实际结果"],
            ["BUG-9", "支付重复扣款", "严重", "连续点击支付", "只扣款一次", "扣款两次"],
        ],
        "历史Bug",
    )

    parsed = parse_enterprise_source(
        blob,
        "历史Bug.xlsx",
        "historical_bug",
        "src_bug_xlsx",
    )

    assert parsed["document_ir_status"] == "COMPLETE"
    assert len(parsed["historical_bugs"]) == 1
    assert len(parsed["tickets"]) == 1
    bug = parsed["historical_bugs"][0]
    ticket = parsed["tickets"][0]
    assert bug["bug_id"] == "BUG-9"
    assert bug["header_row_index"] == 2
    assert bug["field_evidence"]["actual"]["cell_ref"] == "F3"
    assert bug["field_evidence"]["actual"]["source_locator"].endswith(";cell=F3")
    assert bug["field_evidence"]["actual"]["source_hash"]
    assert ticket["title"] == "支付重复扣款"
    assert ticket["severity"] == "P1"
    assert ticket["severity_raw"] == "严重"
    assert ticket["high_fidelity_document_ir_projection"] is True
    assert ticket["field_evidence"]["steps"]["cell_ref"] == "D3"
    assert parsed["parser_receipt"]["outputs"]["historical_bugs"] == 1
    assert parsed["parser_receipt"]["outputs"]["tickets"] == 1
    assert parsed["parser_receipt"]["tabular_semantic_receipt"][
        "exact_field_evidence_rate"
    ] == 1.0


def test_real_xlsx_test_cases_are_independent_semantic_assets() -> None:
    blob = _workbook_bytes(
        [
            ["用例编号", "用例名称", "前置条件", "测试步骤", "测试数据", "预期结果"],
            ["TC-12", "订单金额超限审批", "用户已登录", "提交订单", "金额=60000", "进入财务审批"],
        ],
        "测试用例",
    )

    parsed = parse_enterprise_source(
        blob,
        "订单测试用例.xlsx",
        "other_document",
        "src_case_xlsx",
    )

    assert parsed["document_ir_status"] == "COMPLETE"
    assert parsed["historical_bugs"] == []
    assert len(parsed["test_cases"]) == 1
    case = parsed["test_cases"][0]
    assert case["case_id"] == "TC-12"
    assert case["title"] == "订单金额超限审批"
    assert case["steps"] == "提交订单"
    assert case["expected"] == "进入财务审批"
    assert case["field_evidence"]["expected"]["cell_ref"] == "F2"
    assert parsed["tickets"] == []
    assert parsed["parser_receipt"]["outputs"]["test_cases"] == 1
