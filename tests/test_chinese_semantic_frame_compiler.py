"""P0-B: Chinese Semantic Frame enrichment — list scopes, table headers,
typed time windows, enumeration mentions, and signature stability.

Covers SPEC §7.2 (table atomic facts via row/column headers), §7.3 (list
children inherit parent conditions/exceptions/time windows), and the P0-A
contract that the semantic signature stays stable (quote/evidence never enter
it).
"""

from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_clause_parser import (
    parse_chinese_clause_trees,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_context_envelope import (
    build_chinese_semantic_context_envelopes,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_frame_compiler import (
    enrich_frames_with_clause_structure,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_ledger_adapter import (
    project_business_facts_to_semantic_frames,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_schema import (
    semantic_signature,
    validate_semantic_frame,
)


def _heading(block_id: str, text: str, order: int) -> dict:
    return {
        "block_id": block_id, "type": "HEADING", "parent_id": "", "order": order,
        "region": "body", "level": 1, "text": text,
        "source_locator": f"r.docx#block={order}",
    }


def _list_item(block_id: str, text: str, order: int, parent: str, level: int) -> dict:
    return {
        "block_id": block_id, "type": "LIST_ITEM", "parent_id": parent, "order": order,
        "region": "body", "text": text,
        "numbering": {"numbered": True, "level": level},
        "source_locator": f"r.docx#block={order}",
    }


def _fact(
    *,
    fact_id: str,
    statement: str,
    block_id: str,
    modality: str = "MUST_NOT",
    actor_refs: list[str] | None = None,
    ownership: str = "",
) -> dict:
    return {
        "fact_id": fact_id,
        "fact_type": "PERMISSION_RULE",
        "kind": "RULE",
        "language": "zh-CN",
        "statement_frame_id": f"statement_frame:{fact_id}",
        "subject": {
            "actor_refs": list(actor_refs or []),
            "entity_refs": ["订单"],
            "resolution_evidence": [],
        },
        "object": {"entity_refs": ["订单"]},
        "predicate": "",
        "action": {"canonical": "", "raw": ""},
        "conditions": [],
        "condition_combinator": "",
        "condition_frame": {},
        "scope": {"tenant": "", "organization": "", "ownership": ownership, "data_scope": ""},
        "modality": modality,
        "polarity": "POSITIVE",
        "exceptions": [],
        "exception_scope": [],
        "postconditions": [],
        "state_effects": [],
        "data_effects": [],
        "quantity_constraints": [],
        "time_window_constraints": [],
        "formula_constraints": [],
        "compensation": [],
        "raw_statement": statement,
        "source_spans": [
            {
                "evidence_address": {
                    "source_id": "s1",
                    "locator": f"r.docx#block={block_id}",
                    "document_block_id": block_id,
                    "block_type": "LIST_ITEM",
                },
                "quote": statement,
            }
        ],
        "confidence": 1.0,
        "status": "ACCEPTED",
        "ambiguities": [],
        "critical": True,
        "derivation": "structure_first_explicit_fact_compiler",
    }


def _asset_with_frames(*, facts: list[dict], blocks: list[dict], tables: list[dict] | None = None) -> dict:
    asset = {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "fact_authority": "original_chinese_source_span",
            "items": facts,
        },
        "document_structure_assets": {
            "items": [
                {
                    "source_id": "s1",
                    "filename": "r.docx",
                    "blocks": blocks,
                    "tables": tables or [],
                }
            ]
        },
    }
    asset = project_business_facts_to_semantic_frames(asset)
    asset = build_chinese_semantic_context_envelopes(asset)
    asset = parse_chinese_clause_trees(asset)
    return enrich_frames_with_clause_structure(asset)


def _frames_by_origin(asset: dict) -> dict[str, dict]:
    return {
        row["origin"]["origin_fact_id"]: row
        for row in asset["chinese_semantic_frame_ledger"]["items"]
    }


def test_list_child_inherits_parent_condition() -> None:
    h1 = _heading("h1", "订单管理", 1)
    li1 = _list_item("li1", "已取消订单：", 2, "h1", 0)
    li2 = _list_item("li2", "1. 不得支付；", 3, "h1", 1)
    li3 = _list_item("li3", "2. 不得发货。", 4, "h1", 1)
    facts = [
        _fact(fact_id="f:li2", statement="1. 不得支付；", block_id="li2"),
        _fact(fact_id="f:li3", statement="2. 不得发货。", block_id="li3"),
    ]
    asset = _asset_with_frames(facts=facts, blocks=[h1, li1, li2, li3])

    frames = _frames_by_origin(asset)
    for fact_id in ("f:li2", "f:li3"):
        frame = frames[fact_id]
        raws = [(row["raw"], row.get("origin", "")) for row in frame["conditions"]]
        assert ("已取消订单", "list_parent_inheritance") in raws
        assert frame["clause_structure"]["status"] == "ENRICHED"
        # The inherited condition is a typed slot → the signature covers it.
        assert frame["resolution"]["semantic_signature"] == semantic_signature(frame)
        assert validate_semantic_frame(frame) == []


def test_list_child_inherits_parent_exception_scope_with_source_lineage() -> None:
    h1 = _heading("h1", "操作规则", 1)
    li1 = _list_item("li1", "除管理员外：", 2, "h1", 0)
    li2 = _list_item("li2", "1. 不得删除订单。", 3, "h1", 1)
    fact = _fact(fact_id="f:li2", statement="1. 不得删除订单。", block_id="li2")
    asset = _asset_with_frames(facts=[fact], blocks=[h1, li1, li2])

    frame = _frames_by_origin(asset)["f:li2"]
    inherited = [
        row
        for row in frame["exceptions"]
        if row.get("origin") == "list_parent_exception_inheritance"
    ]
    assert [(row["raw"], row["kind"]) for row in inherited] == [
        ("管理员", "EXCLUSION")
    ]
    assert inherited[0]["evidence"] == [
        {
            "origin": "list_parent_exception_inheritance",
            "source_id": "s1",
            "document_block_id": "li1",
            "locator": "r.docx#block=2",
        }
    ]
    assert frame["clause_structure"]["list_parent_exception_count"] == 1
    assert asset["chinese_semantic_frame_ledger"]["enrichment_receipt"][
        "list_parent_exception_count"
    ] == 1
    assert "除管理员外" not in {row["raw"] for row in frame["conditions"]}
    assert frame["resolution"]["semantic_signature"] == semantic_signature(frame)
    assert validate_semantic_frame(frame) == []

    first_exceptions = [dict(row) for row in frame["exceptions"]]
    first_structure = dict(frame["clause_structure"])
    asset = enrich_frames_with_clause_structure(asset)
    rerun = _frames_by_origin(asset)["f:li2"]
    assert rerun["exceptions"] == first_exceptions
    assert rerun["clause_structure"] == first_structure


def test_list_child_inherits_typed_parent_time_window_with_source_lineage() -> None:
    h1 = _heading("h1", "处理时限", 1)
    li1 = _list_item("li1", "订单取消后24小时内：", 2, "h1", 0)
    li2 = _list_item("li2", "1. 必须完成退款。", 3, "h1", 1)
    fact = _fact(
        fact_id="f:li2",
        statement="1. 必须完成退款。",
        block_id="li2",
        modality="MUST",
    )
    asset = _asset_with_frames(facts=[fact], blocks=[h1, li1, li2])

    frame = _frames_by_origin(asset)["f:li2"]
    assert frame["time_constraints"] == [
        {
            "raw": "订单取消后24小时内",
            "anchor": "订单取消后",
            "relation": "WITHIN",
            "duration": "24小时",
            "window_ms": 86_400_000,
            "source_backed": True,
            "resolution_status": "RESOLVED",
            "origin": "list_parent_time_inheritance",
            "evidence": [
                {
                    "origin": "list_parent_time_inheritance",
                    "source_id": "s1",
                    "document_block_id": "li1",
                    "locator": "r.docx#block=2",
                }
            ],
        }
    ]
    assert frame["clause_structure"]["list_parent_time_constraint_count"] == 1
    assert asset["chinese_semantic_frame_ledger"]["enrichment_receipt"][
        "list_parent_time_constraint_count"
    ] == 1
    assert frame["resolution"]["semantic_signature"] == semantic_signature(frame)
    assert validate_semantic_frame(frame) == []

    first_constraints = [dict(row) for row in frame["time_constraints"]]
    asset = enrich_frames_with_clause_structure(asset)
    rerun = _frames_by_origin(asset)["f:li2"]
    assert rerun["time_constraints"] == first_constraints
    assert rerun["clause_structure"]["list_parent_time_constraint_count"] == 1


def test_table_time_header_becomes_typed_constraint_with_header_lineage() -> None:
    h1 = _heading("h1", "处理时限", 1)
    table = {
        "block_id": "t1", "type": "TABLE", "parent_id": "h1", "order": 2,
        "region": "body", "table_index": 0, "text": "x",
        "source_locator": "r.docx#table=0",
    }
    cells = [
        {"block_id": "c0", "type": "TABLE_CELL", "parent_id": "t1", "order": 3,
         "region": "body", "table_index": 0, "row_index": 0, "column_index": 0,
         "text": "角色", "source_locator": "r.docx#table=0;row=0;cell=0"},
        {"block_id": "c1", "type": "TABLE_CELL", "parent_id": "t1", "order": 4,
         "region": "body", "table_index": 0, "row_index": 0, "column_index": 1,
         "text": "提交后2个工作日内", "source_locator": "r.docx#table=0;row=0;cell=1"},
        {"block_id": "c2", "type": "TABLE_CELL", "parent_id": "t1", "order": 5,
         "region": "body", "table_index": 0, "row_index": 1, "column_index": 0,
         "text": "审核人", "source_locator": "r.docx#table=0;row=1;cell=0"},
        {"block_id": "c3", "type": "TABLE_CELL", "parent_id": "t1", "order": 6,
         "region": "body", "table_index": 0, "row_index": 1, "column_index": 1,
         "text": "必须完成审核", "source_locator": "r.docx#table=0;row=1;cell=1"},
    ]
    tables = [{
        "headers": ["角色", "提交后2个工作日内"],
        "rows": [{"角色": "审核人", "提交后2个工作日内": "必须完成审核"}],
        "table_block_id": "t1", "table_index": 0,
        "row_count": 2, "column_count": 2,
    }]
    fact = _fact(
        fact_id="f:cell",
        statement="必须完成审核",
        block_id="c3",
        modality="MUST",
    )
    asset = _asset_with_frames(
        facts=[fact], blocks=[h1, table, *cells], tables=tables
    )

    frame = _frames_by_origin(asset)["f:cell"]
    typed = [
        row
        for row in frame["time_constraints"]
        if row.get("origin") == "table_column_time_header"
    ]
    assert [(row["anchor"], row["duration"], row["relation"]) for row in typed] == [
        ("提交后", "2个工作日", "WITHIN")
    ]
    assert typed[0]["evidence"] == [
        {
            "origin": "table_column_time_header",
            "source_id": "s1",
            "table_id": "t1",
            "column_index": 1,
            "document_block_id": "c1",
            "locator": "r.docx#table=0;row=0;cell=1",
        }
    ]
    assert frame["clause_structure"]["table_time_constraint_count"] == 1
    assert validate_semantic_frame(frame) == []


def test_own_block_time_window_is_typed_but_neighbor_time_never_leaks() -> None:
    h1 = _heading("h1", "处理时限", 1)
    p1 = {
        "block_id": "p1", "type": "PARAGRAPH", "parent_id": "h1", "order": 2,
        "region": "body", "text": "在提交后24小时内必须完成审核。",
        "source_locator": "r.docx#block=2",
    }
    p2 = {
        "block_id": "p2", "type": "PARAGRAPH", "parent_id": "h1", "order": 3,
        "region": "body", "text": "事件结束后2小时内。",
        "source_locator": "r.docx#block=3",
    }
    p3 = {
        "block_id": "p3", "type": "PARAGRAPH", "parent_id": "h1", "order": 4,
        "region": "body", "text": "必须完成归档。",
        "source_locator": "r.docx#block=4",
    }
    own_fact = _fact(
        fact_id="f:own",
        statement=p1["text"],
        block_id="p1",
        modality="MUST",
    )
    neighbor_fact = _fact(
        fact_id="f:neighbor",
        statement=p3["text"],
        block_id="p3",
        modality="MUST",
    )
    asset = _asset_with_frames(
        facts=[own_fact, neighbor_fact], blocks=[h1, p1, p2, p3]
    )

    frames = _frames_by_origin(asset)
    assert [
        (row["anchor"], row["duration"], row["origin"])
        for row in frames["f:own"]["time_constraints"]
    ] == [("提交后", "24小时", "clause_parser_time_constraint")]
    assert frames["f:own"]["clause_structure"]["own_time_constraint_count"] == 1
    # Mere document adjacency is not scope authority. The standalone window
    # remains observable in its own clause tree but cannot qualify p3.
    assert frames["f:neighbor"]["time_constraints"] == []


def test_table_cell_gets_row_and_column_header_mentions() -> None:
    h1 = _heading("h1", "权限", 1)
    table = {"block_id": "t1", "type": "TABLE", "parent_id": "h1", "order": 2,
             "region": "body", "table_index": 0, "text": "x",
             "source_locator": "r.docx#table=0"}
    cells = [
        {"block_id": "c0", "type": "TABLE_CELL", "parent_id": "t1", "order": 3,
         "region": "body", "table_index": 0, "row_index": 0, "column_index": 0,
         "text": "角色", "source_locator": "r.docx#table=0;row=0;cell=0"},
        {"block_id": "c1", "type": "TABLE_CELL", "parent_id": "t1", "order": 4,
         "region": "body", "table_index": 0, "row_index": 0, "column_index": 1,
         "text": "待审核", "source_locator": "r.docx#table=0;row=0;cell=1"},
        {"block_id": "c2", "type": "TABLE_CELL", "parent_id": "t1", "order": 5,
         "region": "body", "table_index": 0, "row_index": 1, "column_index": 0,
         "text": "申请人", "source_locator": "r.docx#table=0;row=1;cell=0"},
        {"block_id": "c3", "type": "TABLE_CELL", "parent_id": "t1", "order": 6,
         "region": "body", "table_index": 0, "row_index": 1, "column_index": 1,
         "text": "可撤回", "source_locator": "r.docx#table=0;row=1;cell=1"},
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
    fact = _fact(fact_id="f:cell", statement="可撤回", block_id="c3", modality="MAY", actor_refs=[])
    fact["action"] = {"canonical": "撤回", "raw": "撤回"}
    asset = _asset_with_frames(facts=[fact], blocks=[h1, table, *cells], tables=tables)

    frame = _frames_by_origin(asset)["f:cell"]
    # Row header recovered the omitted actor as a source-level mention.
    assert frame["actor"]["resolution_status"] == "RESOLVED"
    assert frame["actor"]["mentions"] == ["申请人"]
    assert "OMITTED_ACTOR_UNRESOLVED" not in frame["resolution"]["reason_codes"]
    # Column header became a condition mention with traceable origin.
    column_conditions = [
        row for row in frame["conditions"] if row.get("origin") == "table_column_header"
    ]
    assert [row["raw"] for row in column_conditions] == ["待审核"]
    assert column_conditions[0]["evidence"][0]["table_id"] == "t1"
    assert frame["clause_structure"]["table_context_used"]["row_header"] == "申请人"
    assert validate_semantic_frame(frame) == []


def test_enumeration_mentions_are_never_lost() -> None:
    h1 = _heading("h1", "规则", 1)
    p1 = {"block_id": "p1", "type": "PARAGRAPH", "parent_id": "h1", "order": 2,
          "region": "body", "text": "非管理员不得修改或删除已发布内容。",
          "source_locator": "r.docx#block=2"}
    fact = _fact(fact_id="f:enum", statement="非管理员不得修改或删除已发布内容。", block_id="p1")
    fact["action"] = {"canonical": "修改", "raw": "修改"}
    fact["subject"]["actor_refs"] = []
    asset = _asset_with_frames(facts=[fact], blocks=[h1, p1])

    frame = _frames_by_origin(asset)["f:enum"]
    mentions = frame["action"]["mentions"]
    assert "修改" in mentions
    assert "删除" in mentions
    assert frame["clause_structure"]["enumeration"]["interpretation"] == "ACTION_SPLIT"
    # The actor negation surfaces as a shared condition candidate.
    assert frame["clause_structure"]["negation_scope"]["type"] == "ACTOR_NEGATION"
    assert validate_semantic_frame(frame) == []


def test_ambiguous_enumeration_keeps_raw_without_selection() -> None:
    h1 = _heading("h1", "规则", 1)
    p1 = {"block_id": "p1", "type": "PARAGRAPH", "parent_id": "h1", "order": 2,
          "region": "body", "text": "不得修改订单或发票。",
          "source_locator": "r.docx#block=2"}
    fact = _fact(fact_id="f:amb", statement="不得修改订单或发票。", block_id="p1")
    fact["action"] = {"canonical": "修改", "raw": "修改"}
    asset = _asset_with_frames(facts=[fact], blocks=[h1, p1])

    frame = _frames_by_origin(asset)["f:amb"]
    # AMBIGUOUS interpretation: fact-derived mention stays, nothing selected.
    assert frame["action"]["mentions"] == ["修改"]
    assert frame["clause_structure"]["enumeration"]["interpretation"] == "AMBIGUOUS"


def test_enrichment_is_idempotent() -> None:
    h1 = _heading("h1", "订单管理", 1)
    li1 = _list_item("li1", "已取消订单：", 2, "h1", 0)
    li2 = _list_item("li2", "1. 不得支付；", 3, "h1", 1)
    facts = [_fact(fact_id="f:li2", statement="1. 不得支付；", block_id="li2")]
    asset = _asset_with_frames(facts=facts, blocks=[h1, li1, li2])
    first = dict(asset["chinese_semantic_frame_ledger"]["items"][0])
    asset = enrich_frames_with_clause_structure(asset)
    second = dict(asset["chinese_semantic_frame_ledger"]["items"][0])
    assert first == second
    assert len(second["conditions"]) == len(first["conditions"])


def test_unlocated_frame_is_receipted_not_dropped() -> None:
    h1 = _heading("h1", "规则", 1)
    p1 = {"block_id": "p1", "type": "PARAGRAPH", "parent_id": "h1", "order": 2,
          "region": "body", "text": "不得删除订单。", "source_locator": "r.docx#block=2"}
    fact = _fact(fact_id="f:orphan", statement="一段无法定位的原文。", block_id="ghost-block")
    asset = _asset_with_frames(facts=[fact], blocks=[h1, p1])
    frame = _frames_by_origin(asset)["f:orphan"]
    assert frame["clause_structure"]["status"] == "UNLOCATED"
    assert validate_semantic_frame(frame) == []
    receipt = asset["chinese_semantic_frame_ledger"]["enrichment_receipt"]
    assert receipt["unlocated_count"] >= 1
