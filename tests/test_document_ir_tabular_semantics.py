from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ir_tabular_semantics import (
    extract_tabular_enterprise_semantics,
)


def _cell(
    block_id: str,
    text: str,
    row: int,
    column: int,
    *,
    sheet: str = "Sheet1",
) -> dict:
    locator = f"企业资料.xlsx#sheet={sheet};cell=R{row}C{column}"
    return {
        "block_id": block_id,
        "type": "TABLE_CELL",
        "parent_id": "table:1",
        "order": row * 100 + column,
        "region": "body",
        "text": text,
        "source_locator": locator,
        "source_id": "src_table",
        "source_hash": "a" * 64,
        "sheet": sheet,
        "cell_ref": f"R{row}C{column}",
        "row_index": row,
        "column_index": column,
        "evidence_address": {
            "source_id": "src_table",
            "source_hash": "a" * 64,
            "source_locator": locator,
            "sheet": sheet,
            "cell_ref": f"R{row}C{column}",
            "address_kind": "SPREADSHEET_CELL",
        },
    }


def _document_ir(rows: list[list[str]], *, sheet: str = "Sheet1") -> dict:
    blocks: list[dict] = []
    ids: list[str] = []
    for row_index, values in enumerate(rows, start=1):
        for column_index, value in enumerate(values, start=1):
            if value == "":
                continue
            block_id = f"cell:{row_index}:{column_index}"
            blocks.append(
                _cell(
                    block_id,
                    value,
                    row_index,
                    column_index,
                    sheet=sheet,
                )
            )
            ids.append(block_id)
    return {
        "schema": "qualibug.document-ir.v1",
        "format": "xlsx",
        "filename": "企业资料.xlsx",
        "plain_text": "",
        "blocks": blocks,
        "sections": [],
        "tables": [
            {
                "block_id": "table:1",
                "type": "TABLE",
                "source_locator": f"企业资料.xlsx#sheet={sheet}",
                "cell_block_ids": ids,
            }
        ],
        "pages": [],
        "unsupported_content": [],
        "structure_receipt": {"status": "COMPLETE"},
    }


def test_historical_bug_rows_preserve_selected_header_and_cell_evidence() -> None:
    document_ir = _document_ir(
        [
            ["2026年历史缺陷汇总", "", "", "", "", ""],
            ["缺陷编号", "缺陷标题", "严重程度", "复现步骤", "预期结果", "实际结果"],
            ["BUG-101", "重复支付生成两笔流水", "严重", "连续点击支付", "只生成一笔", "生成两笔"],
        ],
        sheet="历史Bug",
    )

    result = extract_tabular_enterprise_semantics(
        document_ir,
        source_id="src_table",
        source_type="historical_bug",
        filename="历史Bug.xlsx",
    )

    assert result["historical_bug_count"] == 1
    assert result["test_case_count"] == 0
    assert result["exact_field_evidence_rate"] == 1.0
    bug = result["historical_bugs"][0]
    assert bug["bug_id"] == "BUG-101"
    assert bug["title"] == "重复支付生成两笔流水"
    assert bug["severity"] == "严重"
    assert bug["steps"] == "连续点击支付"
    assert bug["expected"] == "只生成一笔"
    assert bug["actual"] == "生成两笔"
    assert bug["header_row_index"] == 2
    assert bug["row_index"] == 3
    assert bug["field_evidence"]["actual"]["cell_ref"] == "R3C6"
    assert bug["field_evidence"]["actual"]["source_hash"] == "a" * 64
    assert result["tables"][0]["header_row"] == 2


def test_test_case_table_is_detected_without_explicit_source_type() -> None:
    document_ir = _document_ir(
        [
            ["用例编号", "用例名称", "前置条件", "测试步骤", "测试数据", "预期结果", "优先级"],
            ["TC-001", "订单金额超限审批", "已登录", "提交订单", "金额=60000", "进入财务审批", "P0"],
        ],
        sheet="测试用例",
    )

    result = extract_tabular_enterprise_semantics(
        document_ir,
        source_id="src_table",
        source_type="other_document",
        filename="订单测试.xlsx",
    )

    assert result["historical_bug_count"] == 0
    assert result["test_case_count"] == 1
    case = result["test_cases"][0]
    assert case["case_id"] == "TC-001"
    assert case["title"] == "订单金额超限审批"
    assert case["precondition"] == "已登录"
    assert case["steps"] == "提交订单"
    assert case["test_data"] == "金额=60000"
    assert case["expected"] == "进入财务审批"
    assert case["field_evidence"]["expected"]["source_locator"].endswith("cell=R2C6")


def test_database_dictionary_is_not_misclassified_as_bug_or_test_case() -> None:
    document_ir = _document_ir(
        [
            ["字段名", "类型", "是否必填", "说明"],
            ["order_id", "bigint", "是", "订单主键"],
        ],
        sheet="数据字典",
    )

    result = extract_tabular_enterprise_semantics(
        document_ir,
        source_id="src_table",
        source_type="db_field_dictionary",
        filename="数据库字典.xlsx",
    )

    assert result["historical_bug_count"] == 0
    assert result["test_case_count"] == 0
    assert result["matched_table_count"] == 0
    assert result["tables"][0]["status"] == "NOT_APPLICABLE"
