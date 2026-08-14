from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ir_tabular_semantics import (
    extract_tabular_enterprise_semantics,
)


def _cell(row: int, column: int, value: str) -> dict:
    ref = f"{chr(64 + column)}{row}"
    locator = f"cases.xlsx#sheet=Cases;cell={ref}"
    return {
        "block_id": f"cell:{ref}",
        "type": "TABLE_CELL",
        "text": value,
        "row_index": row,
        "column_index": column,
        "cell_ref": ref,
        "sheet": "Cases",
        "source_locator": locator,
        "source_id": "src_cases",
        "source_hash": "a" * 64,
        "evidence_address": {
            "source_id": "src_cases",
            "source_hash": "a" * 64,
            "source_locator": locator,
            "sheet": "Cases",
            "cell_ref": ref,
            "address_kind": "SPREADSHEET_CELL",
        },
    }


def _document_ir() -> dict:
    rows = [
        ["用例编号", "用例标题", "前置条件", "步骤序号", "测试步骤", "预期结果"],
        ["TC-001", "订单审批", "存在待审批订单", "1", "提交审批", "状态变为审批中"],
        ["", "", "", "2", "财务审核通过", "状态变为已通过"],
        ["", "", "", "3", "仓库发货", "生成出库单"],
        ["TC-002", "订单驳回", "存在审批中订单", "1", "财务驳回", "状态变为已驳回"],
    ]
    blocks = [
        _cell(row_index, column_index, value)
        for row_index, values in enumerate(rows, start=1)
        for column_index, value in enumerate(values, start=1)
        if value
    ]
    return {
        "blocks": blocks,
        "tables": [
            {
                "block_id": "table:cases",
                "cell_block_ids": [row["block_id"] for row in blocks],
            }
        ],
    }


def test_multi_row_case_is_grouped_without_losing_step_evidence() -> None:
    result = extract_tabular_enterprise_semantics(
        _document_ir(),
        source_id="src_cases",
        source_type="test_case",
        filename="cases.xlsx",
    )

    assert result["historical_bug_count"] == 0
    assert result["test_case_count"] == 2
    assert result["multi_row_test_case_count"] == 1
    first, second = result["test_cases"]
    assert first["case_id"] == "TC-001"
    assert first["title"] == "订单审批"
    assert first["row_indices"] == [2, 3, 4]
    assert first["aggregated_from_multiple_rows"] is True
    assert first["steps"].splitlines() == [
        "提交审批",
        "2. 财务审核通过",
        "3. 仓库发货",
    ]
    assert first["expected"].splitlines() == [
        "状态变为审批中",
        "2. 状态变为已通过",
        "3. 生成出库单",
    ]
    assert len(first["field_evidence_spans"]["steps"]) == 3
    assert len(first["field_evidence_spans"]["expected"]) == 3
    assert {
        row["cell_ref"] for row in first["field_evidence_spans"]["steps"]
    } == {"E2", "E3", "E4"}
    assert result["exact_field_evidence_rate"] == 1.0
    assert second["case_id"] == "TC-002"
    assert second["row_indices"] == [5]
    assert second["aggregated_from_multiple_rows"] is False


def test_conflicting_scalar_values_remain_fail_visible() -> None:
    document_ir = _document_ir()
    # Inject a conflicting scalar into a continuation row. A differing "用例标题"
    # would split the row into a new title-identity case, so a non-identity scalar
    # (前置条件/precondition) is used to exercise the fail-visible conflict path.
    document_ir["blocks"].append(_cell(3, 3, "存在审批中订单"))
    document_ir["tables"][0]["cell_block_ids"].append("cell:C3")

    result = extract_tabular_enterprise_semantics(
        document_ir,
        source_id="src_cases",
        source_type="test_case",
        filename="cases.xlsx",
    )

    first = result["test_cases"][0]
    assert first["title"] == "订单审批"
    assert first["field_conflict_count"] == 1
    assert first["field_conflicts"][0]["field"] == "precondition"
    assert result["field_conflict_count"] == 1
