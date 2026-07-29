from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
    _rule_from_fact,
)
from ai_test_asset_center.enterprise_knowledge_center._chinese_document_context import (
    apply_chinese_document_context,
    build_chinese_document_semantic_tree,
)
from ai_test_asset_center.enterprise_knowledge_center._document_ir_fact_evidence import (
    align_business_facts_to_document_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder import (
    build_enterprise_understanding_model,
)


def _asset(*objects: str) -> dict:
    return {
        "asset_id": "asset:semantic-tree-scale",
        "business_objects": [{"object": name} for name in objects],
        "data_tables": [],
        "roles": [],
        "permission_matrix": [],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def _ir_source() -> dict:
    heading_id = "block-h-order"
    list_id = "block-list-deny"
    cell_id = "block-cell-allow"
    other_heading_id = "block-h-shipment"
    other_para_id = "block-para-ship"
    return {
        "source_id": "prd-ir-scale",
        "filename": "企业业务制度.md",
        "text": (
            "订单管理\n"
            "1）其不得删除\n"
            "订单可以查看\n"
            "发货规则\n"
            "其不得发货\n"
        ),
        "document_structure": {
            "plain_text": (
                "订单管理\n"
                "1）其不得删除\n"
                "订单可以查看\n"
                "发货规则\n"
                "其不得发货\n"
            ),
            "blocks": [
                {
                    "block_id": heading_id,
                    "type": "HEADING",
                    "parent_id": "",
                    "order": 1,
                    "region": "body",
                    "level": 1,
                    "text": "订单管理",
                    "start_offset": 0,
                    "end_offset": 4,
                    "source_locator": "企业业务制度.md#block=1;chars=0-4",
                },
                {
                    "block_id": list_id,
                    "type": "LIST_ITEM",
                    "parent_id": heading_id,
                    "order": 2,
                    "region": "body",
                    "text": "1）其不得删除",
                    "start_offset": 5,
                    "end_offset": 12,
                    "source_locator": "企业业务制度.md#block=2;chars=5-12",
                },
                {
                    "block_id": cell_id,
                    "type": "TABLE_CELL",
                    "parent_id": "block-table-1",
                    "table_block_id": "block-table-1",
                    "logical_table_id": "logical-table-order",
                    "order": 3,
                    "region": "body",
                    "text": "订单可以查看",
                    "start_offset": 13,
                    "end_offset": 19,
                    "source_locator": "企业业务制度.md#block=3;chars=13-19",
                },
                {
                    "block_id": "block-table-1",
                    "type": "TABLE",
                    "parent_id": heading_id,
                    "logical_table_id": "logical-table-order",
                    "order": 4,
                    "region": "body",
                    "text": "",
                    "start_offset": 13,
                    "end_offset": 19,
                    "source_locator": "企业业务制度.md#block=4;table=1",
                    "excluded_from_plain_text_projection": True,
                },
                {
                    "block_id": other_heading_id,
                    "type": "HEADING",
                    "parent_id": "",
                    "order": 5,
                    "region": "body",
                    "level": 1,
                    "text": "发货规则",
                    "start_offset": 20,
                    "end_offset": 24,
                    "source_locator": "企业业务制度.md#block=5;chars=20-24",
                },
                {
                    "block_id": other_para_id,
                    "type": "PARAGRAPH",
                    "parent_id": other_heading_id,
                    "order": 6,
                    "region": "body",
                    "text": "其不得发货",
                    "start_offset": 25,
                    "end_offset": 30,
                    "source_locator": "企业业务制度.md#block=6;chars=25-30",
                },
            ],
            "table_groups": [
                {
                    "logical_table_id": "logical-table-order",
                    "fragment_table_ids": ["block-table-1", "block-table-2"],
                    "pages": [1, 2],
                }
            ],
            "visual_table_continuation_receipt": {
                "groups": [
                    {
                        "logical_table_id": "logical-table-order",
                        "fragment_table_ids": ["block-table-1", "block-table-2"],
                        "pages": [1, 2],
                    }
                ]
            },
            "structure_receipt": {},
        },
    }


def test_ir_semantic_tree_preserves_heading_list_table_and_continued_group() -> None:
    tree = build_chinese_document_semantic_tree(_ir_source())
    kinds = [row["span_kind"] for row in tree["nodes"] if row.get("span_kind") != "DOCUMENT_ROOT"]

    assert tree["structure_authority"] == "document_structure_ir"
    assert tree["silent_truncation_applied"] is False
    assert tree["order_is_business_flow"] is False
    assert "HEADING" in kinds
    assert "LIST_ITEM" in kinds
    assert "TABLE_CELL" in kinds
    assert tree["continued_table_group_count"] == 1
    cell = next(row for row in tree["nodes"] if row.get("span_kind") == "TABLE_CELL")
    assert cell["continued_table_group_id"] == "logical-table-order"
    assert cell["path_titles"] == ["订单管理"]


def test_facts_attach_to_list_and_table_spans_without_cross_section_join() -> None:
    source = _ir_source()
    enriched = build_chinese_first_comprehension(_asset("订单"), [source])
    enriched = align_business_facts_to_document_ir(enriched, [source])
    enriched = apply_chinese_document_context(enriched, [source])

    deny = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if "不得删除" in str(row.get("raw_statement") or "")
    )
    ship = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if "不得发货" in str(row.get("raw_statement") or "")
    )

    assert deny["structural_span_attachment"]["status"] == "ATTACHED"
    assert deny["structural_span_attachment"]["span_kind"] == "LIST_ITEM"
    assert deny["structural_span_attachment"]["section_path"] == ["订单管理"]
    assert ship["structural_span_attachment"]["status"] == "ATTACHED"
    assert ship["structural_span_attachment"]["section_path"] == ["发货规则"]
    assert (
        deny["structural_span_attachment"]["section_node_id"]
        != ship["structural_span_attachment"]["section_node_id"]
    )
    # Shipping coreference must not inherit the earlier 订单 section from document order.
    assert ship["status"] == "PENDING"
    assert "订单" not in ship.get("subject", {}).get("entity_refs", [])
    assert any(
        "CROSS_SECTION" in value or value.startswith("COREFERENCE_")
        for value in ship.get("ambiguities", [])
    ) or enriched["document_span_attachment_receipt"][
        "cross_section_coreference_invalidated_count"
    ] >= 1
    assert enriched["document_span_attachment_receipt"]["attached_fact_count"] >= 2
    assert enriched["governance"]["cross_section_join_from_document_order_forbidden"] is True


def test_ambiguous_structural_span_fails_closed_not_silent() -> None:
    source = {
        "source_id": "prd-ambiguous-span",
        "filename": "重复单元格.md",
        "text": "订单不得删除\n订单不得删除\n",
        "document_structure": {
            "plain_text": "订单不得删除\n订单不得删除\n",
            "blocks": [
                {
                    "block_id": "cell-a",
                    "type": "TABLE_CELL",
                    "parent_id": "t1",
                    "order": 1,
                    "region": "body",
                    "text": "订单不得删除",
                    "start_offset": 0,
                    "end_offset": 6,
                    "source_locator": "重复单元格.md#cell=a",
                },
                {
                    "block_id": "cell-b",
                    "type": "TABLE_CELL",
                    "parent_id": "t1",
                    "order": 2,
                    "region": "body",
                    "text": "订单不得删除",
                    "start_offset": 7,
                    "end_offset": 13,
                    "source_locator": "重复单元格.md#cell=b",
                },
            ],
            "structure_receipt": {},
        },
    }
    asset = {
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "fact-dup",
                    "kind": "RULE",
                    "status": "ACCEPTED",
                    "raw_statement": "订单不得删除",
                    "subject": {"entity_refs": ["订单"], "actor_refs": []},
                    "object": {"entity_refs": ["订单"]},
                    "action": {"canonical": "删除", "raw": "删除"},
                    "modality": "MUST_NOT",
                    "ambiguities": [],
                    "source_spans": [{"source_id": "prd-ambiguous-span"}],
                }
            ]
        },
        "rule_library": [],
        "summary": {},
        "governance": {},
        "business_objects": [{"object": "订单"}],
        "data_tables": [],
        "roles": [],
        "permission_matrix": [],
        "document_coverage_ledger": {"items": []},
        "enterprise_comprehension_gate": {"metrics": {}, "status": "PASS", "entry_allowed": True},
    }

    enriched = apply_chinese_document_context(asset, [source])
    fact = enriched["business_fact_ledger"]["items"][0]

    assert fact["structural_span_attachment"]["status"] == "AMBIGUOUS"
    assert "DOCUMENT_SEMANTIC_SPAN_AMBIGUOUS" in fact["ambiguities"]
    assert fact["status"] == "PENDING"
    assert enriched["document_span_attachment_receipt"]["unresolved_fact_count"] == 1


def test_missing_document_ir_alignment_is_not_silent_drop() -> None:
    asset = {
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "fact-orphan",
                    "raw_statement": "订单不得删除",
                    "source_spans": [{"source_id": "missing-source"}],
                }
            ]
        }
    }
    aligned = align_business_facts_to_document_ir(asset, [])
    receipt = aligned["document_ir_fact_evidence_receipt"]
    assert receipt["unresolved_fact_count"] == 1
    assert receipt["unresolved"][0]["reason"] == "DOCUMENT_IR_SOURCE_STRUCTURE_UNAVAILABLE"


def test_rule_and_model_preserve_structural_span_attachment() -> None:
    source = _ir_source()
    enriched = build_chinese_first_comprehension(_asset("订单"), [source])
    enriched = align_business_facts_to_document_ir(enriched, [source])
    enriched = apply_chinese_document_context(enriched, [source])
    fact = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if row.get("status") == "ACCEPTED"
        and row.get("structural_span_attachment", {}).get("status") == "ATTACHED"
    )
    rule = _rule_from_fact(fact)
    assert rule is not None
    assert rule["structural_span_attachment"]["node_id"]
    assert rule["document_block_id"]

    model = build_enterprise_understanding_model(enriched)
    model_rule = next(
        row for row in model["rules"] if row.get("fact_id") == fact.get("fact_id")
    )
    assert model_rule["structural_span_attachment"]["status"] == "ATTACHED"
    assert model_rule["document_block_id"]


def test_multi_section_enterprise_doc_does_not_silently_drop_structured_facts() -> None:
    sections = []
    blocks = []
    offset = 0
    for index in range(1, 13):
        title = f"对象{index}规则"
        statement = f"对象{index}不得删除"
        heading_id = f"h-{index}"
        para_id = f"p-{index}"
        sections.append(f"# {title}\n{statement}。")
        blocks.append(
            {
                "block_id": heading_id,
                "type": "HEADING",
                "parent_id": "",
                "order": index * 2 - 1,
                "region": "body",
                "level": 1,
                "text": title,
                "start_offset": offset,
                "end_offset": offset + len(title),
                "source_locator": f"规模制度.md#h={index}",
            }
        )
        offset += len(title) + 1
        blocks.append(
            {
                "block_id": para_id,
                "type": "PARAGRAPH",
                "parent_id": heading_id,
                "order": index * 2,
                "region": "body",
                "text": statement,
                "start_offset": offset,
                "end_offset": offset + len(statement),
                "source_locator": f"规模制度.md#p={index}",
            }
        )
        offset += len(statement) + 1

    text = "\n".join(sections)
    source = {
        "source_id": "prd-enterprise-scale",
        "filename": "规模制度.md",
        "text": text,
        "document_structure": {
            "plain_text": text,
            "blocks": blocks,
            "structure_receipt": {},
        },
    }
    objects = [f"对象{index}" for index in range(1, 13)]
    enriched = build_chinese_first_comprehension(_asset(*objects), [source])
    enriched = align_business_facts_to_document_ir(enriched, [source])
    enriched = apply_chinese_document_context(enriched, [source])

    facts = [
        row
        for row in enriched["business_fact_ledger"]["items"]
        if row.get("kind") in {"RULE", "STATE_TRANSITION"}
    ]
    attached = [
        row
        for row in facts
        if row.get("structural_span_attachment", {}).get("status") == "ATTACHED"
    ]
    tree = enriched["document_semantic_trees"]["items"][0]

    assert len(facts) >= 12
    assert len(attached) >= 12
    assert tree["silent_truncation_applied"] is False
    assert tree["node_count"] >= 25
    assert enriched["summary"]["document_span_attached_fact_count"] >= 12
