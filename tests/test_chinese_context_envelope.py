"""P0-B: Semantic Context Envelope — structural coordinates over the IR.

Covers list-stack ancestor chains (with cross-section isolation), table
row/column header coordinates, section paths and unique-match block lookup.
"""

from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_context_envelope import (
    CHINESE_SEMANTIC_CONTEXT_ENVELOPE_SCHEMA,
    block_context_for,
    build_chinese_semantic_context_envelopes,
    locate_unique_block,
)


def _heading(block_id: str, text: str, order: int, level: int, parent: str) -> dict:
    return {
        "block_id": block_id,
        "type": "HEADING",
        "parent_id": parent,
        "order": order,
        "region": "body",
        "level": level,
        "text": text,
        "source_locator": f"r.docx#block={order}",
    }


def _list_item(block_id: str, text: str, order: int, parent: str, level: int) -> dict:
    return {
        "block_id": block_id,
        "type": "LIST_ITEM",
        "parent_id": parent,
        "order": order,
        "region": "body",
        "text": text,
        "numbering": {"numbered": True, "level": level},
        "source_locator": f"r.docx#block={order}",
    }


def _cell(block_id: str, table: str, row: int, col: int, text: str, order: int) -> dict:
    return {
        "block_id": block_id,
        "type": "TABLE_CELL",
        "parent_id": table,
        "order": order,
        "region": "body",
        "table_index": 0,
        "row_index": row,
        "column_index": col,
        "text": text,
        "source_locator": f"r.docx#table=0;row={row};cell={col}",
    }


def _asset(blocks: list[dict], tables: list[dict] | None = None) -> dict:
    return {
        "document_structure_assets": {
            "items": [
                {
                    "source_id": "s1",
                    "filename": "r.docx",
                    "blocks": blocks,
                    "tables": tables or [],
                }
            ]
        }
    }


def test_envelope_receipt_and_schema() -> None:
    asset = build_chinese_semantic_context_envelopes(_asset([]))
    envelope = asset["chinese_semantic_context_envelopes"]
    assert envelope["schema"] == CHINESE_SEMANTIC_CONTEXT_ENVELOPE_SCHEMA
    assert envelope["receipt"]["source_count"] == 0
    assert envelope["receipt"]["document_order_is_business_flow"] is False


def test_list_stack_builds_ancestor_chain() -> None:
    h1 = _heading("h1", "订单管理", 1, 1, "")
    li1 = _list_item("li1", "已取消订单：", 2, "h1", 0)
    li2 = _list_item("li2", "1. 不得支付；", 3, "h1", 1)
    li3 = _list_item("li3", "1.1 不得发货。", 4, "h1", 2)
    asset = build_chinese_semantic_context_envelopes(_asset([h1, li1, li2, li3]))

    ctx2 = block_context_for(asset, "s1", "li2")["list_context"]
    assert ctx2["list_parent"] == "li1"
    assert ctx2["list_ancestor_chain"] == ["li1"]

    ctx3 = block_context_for(asset, "s1", "li3")["list_context"]
    assert ctx3["list_parent"] == "li2"
    assert ctx3["list_ancestor_chain"] == ["li1", "li2"]


def test_sibling_items_do_not_nest() -> None:
    h1 = _heading("h1", "规则", 1, 1, "")
    li1 = _list_item("li1", "a. 不得支付；", 2, "h1", 0)
    li2 = _list_item("li2", "b. 不得发货。", 3, "h1", 0)
    asset = build_chinese_semantic_context_envelopes(_asset([h1, li1, li2]))
    ctx2 = block_context_for(asset, "s1", "li2")["list_context"]
    assert ctx2["list_parent"] == ""
    assert ctx2["list_ancestor_chain"] == []


def test_list_context_never_crosses_sections() -> None:
    h1 = _heading("h1", "第一节", 1, 1, "")
    li1 = _list_item("li1", "已取消订单：", 2, "h1", 0)
    h2 = _heading("h2", "第二节", 3, 1, "")
    li2 = _list_item("li2", "不得支付；", 4, "h2", 0)
    asset = build_chinese_semantic_context_envelopes(_asset([h1, li1, h2, li2]))
    ctx2 = block_context_for(asset, "s1", "li2")["list_context"]
    assert ctx2["list_parent"] == ""
    assert ctx2["list_ancestor_chain"] == []
    # Section paths follow the heading chain, not the list chain.
    assert block_context_for(asset, "s1", "li2")["section_path"] == ["第二节"]
    assert block_context_for(asset, "s1", "li1")["section_path"] == ["第一节"]


def test_table_cell_gets_row_and_column_headers() -> None:
    h1 = _heading("h1", "权限", 1, 1, "")
    table = {"block_id": "t1", "type": "TABLE", "parent_id": "h1", "order": 2,
             "region": "body", "table_index": 0, "text": "x",
             "source_locator": "r.docx#table=0"}
    cells = [
        _cell("c0", "t1", 0, 0, "角色", 3),
        _cell("c1", "t1", 0, 1, "待审核", 4),
        _cell("c2", "t1", 1, 0, "申请人", 5),
        _cell("c3", "t1", 1, 1, "可撤回", 6),
    ]
    tables = [
        {
            "headers": ["角色", "待审核"],
            "rows": [{"角色": "申请人", "待审核": "可撤回"}],
            "table_block_id": "t1",
            "table_index": 0,
            "row_count": 2,
            "column_count": 2,
        }
    ]
    asset = build_chinese_semantic_context_envelopes(
        _asset([h1, table, *cells], tables=tables)
    )
    ctx = block_context_for(asset, "s1", "c3")["table_context"]
    assert ctx["table_id"] == "t1"
    assert ctx["row_index"] == 1
    assert ctx["column_index"] == 1
    assert ctx["row_header"] == "申请人"
    assert ctx["column_header"] == "待审核"
    # Header cells themselves carry coordinates but no row/column header text.
    ctx0 = block_context_for(asset, "s1", "c1")["table_context"]
    assert ctx0["column_header"] == "待审核"


def test_missing_headers_stay_empty_never_guessed() -> None:
    h1 = _heading("h1", "表", 1, 1, "")
    table = {"block_id": "t1", "type": "TABLE", "parent_id": "h1", "order": 2,
             "region": "body", "table_index": 0, "text": "x",
             "source_locator": "r.docx#table=0"}
    cells = [_cell("c0", "t1", 1, 1, "可撤回", 3)]
    asset = build_chinese_semantic_context_envelopes(
        _asset([h1, table, *cells], tables=[{"table_block_id": "t1", "table_index": 0}])
    )
    ctx = block_context_for(asset, "s1", "c0")["table_context"]
    assert ctx["row_header"] == ""
    assert ctx["column_header"] == ""


def test_unique_block_lookup_and_ambiguity() -> None:
    h1 = _heading("h1", "订单管理", 1, 1, "")
    p1 = {"block_id": "p1", "type": "PARAGRAPH", "parent_id": "h1", "order": 2,
          "region": "body", "text": "订单状态为已支付时不得取消。",
          "source_locator": "r.docx#block=2"}
    p2 = {"block_id": "p2", "type": "PARAGRAPH", "parent_id": "h1", "order": 3,
          "region": "body", "text": "订单状态为已支付时不得取消。",
          "source_locator": "r.docx#block=3"}
    asset = build_chinese_semantic_context_envelopes(_asset([h1, p1, p2]))

    # Duplicate text → ambiguous → resolves to nothing.
    assert locate_unique_block(asset, source_id="s1", quote="订单状态为已支付时不得取消。") == {}
    # Unknown quote → nothing.
    assert locate_unique_block(asset, source_id="s1", quote="完全不存在的内容") == {}
    # Unique text resolves.
    p3 = {"block_id": "p3", "type": "PARAGRAPH", "parent_id": "h1", "order": 4,
          "region": "body", "text": "仅限本人查询。", "source_locator": "r.docx#block=4"}
    asset = build_chinese_semantic_context_envelopes(_asset([h1, p1, p3]))
    hit = locate_unique_block(asset, source_id="s1", quote="仅限本人查询。")
    assert hit["block_id"] == "p3"


def test_neighbors_reference_surrounding_blocks() -> None:
    h1 = _heading("h1", "规则", 1, 1, "")
    blocks = [
        _list_item(f"li{i}", f"规则{i}：", i + 2, "h1", 0)
        for i in range(5)
    ]
    asset = build_chinese_semantic_context_envelopes(_asset([h1, *blocks]))
    ctx = block_context_for(asset, "s1", "li2")["neighbors"]
    assert "li1" in ctx["previous"]
    assert "li3" in ctx["next"]
    assert "li0" in ctx["previous"]
