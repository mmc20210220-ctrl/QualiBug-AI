from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._document_ir_fact_evidence import (
    align_business_facts_to_document_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.visual_table_projection import (
    apply_visual_table_projection_authority,
)


def test_formal_visual_cells_supersede_native_pdf_text_inside_target_region() -> None:
    structure = {
        "plain_text": "订单不得删除\n订单不得删除",
        "unsupported_content": [],
        "blocks": [
            {
                "block_id": "native-region-1",
                "type": "TABLE_REGION",
                "page": 1,
                "order": 1,
                "region": "body",
                "bbox": [40.0, 300.0, 260.0, 500.0],
                "text": "",
                "excluded_from_main_flow": True,
                "source_locator": "制度.pdf#page=1;table_region=1",
            },
            {
                "block_id": "native-pdf-row",
                "type": "PARAGRAPH",
                "page": 1,
                "order": 2,
                "region": "body",
                "bbox": [60.0, 360.0, 240.0, 390.0],
                "text": "订单不得删除",
                "source_locator": "制度.pdf#page=1;bbox=60,360,240,390",
            },
            {
                "block_id": "visual-table-1",
                "type": "TABLE",
                "page": 1,
                "order": 3,
                "region": "body",
                "bbox": [130, 200, 870, 900],
                "text": "",
                "formal_table_structure": True,
                "excluded_from_main_flow": True,
                "source_locator": "制度.pdf#page=1;visual_table=1",
                "structure_evidence": {
                    "target_region_id": "native-region-1",
                    "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE",
                },
            },
            {
                "block_id": "visual-cell-1",
                "type": "TABLE_CELL",
                "parent_id": "visual-row-1",
                "table_block_id": "visual-table-1",
                "page": 1,
                "order": 4,
                "region": "body",
                "bbox": [150, 250, 500, 340],
                "text": "订单不得删除",
                "source_locator": "制度.pdf#page=1;visual_table=1;row=0;column=0",
            },
        ],
        "structure_receipt": {},
    }

    projected = apply_visual_table_projection_authority(structure)
    native_row = next(
        row for row in projected["blocks"] if row["block_id"] == "native-pdf-row"
    )
    assert native_row["excluded_from_plain_text_projection"] is True
    assert native_row["superseded_by_table_region_id"] == "native-region-1"
    assert projected["plain_text"] == "订单不得删除"
    receipt = projected["visual_table_text_authority_receipt"]
    assert receipt["native_pdf_table_text_can_be_superseded"] is True
    assert receipt["superseded_visual_text_block_count"] == 1

    asset = {
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "native-table-fact",
                    "raw_statement": "订单不得删除",
                    "source_spans": [{"source_id": "pdf-source"}],
                }
            ]
        }
    }
    aligned = align_business_facts_to_document_ir(
        asset,
        [{"source_id": "pdf-source", "document_structure": projected}],
    )
    fact = aligned["business_fact_ledger"]["items"][0]
    assert fact["document_structure_alignment"]["block_id"] == "visual-cell-1"
    assert fact["document_structure_alignment"]["block_type"] == "TABLE_CELL"
