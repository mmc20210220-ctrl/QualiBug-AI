from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.document_structure_gate import (
    apply_document_structure_completeness,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    empty_model,
)


def _asset() -> dict:
    unsupported = [
        {
            "kind": "TABLE_SYMBOL_LEGEND_MISSING",
            "reason_code": "TABLE_SYMBOL_LEGEND_MISSING",
            "count": 2,
            "severity": "P1",
            "blocks_formal_understanding": False,
        },
        {
            "kind": "TABLE_COLOR_LEGEND_VISUAL_SAMPLE_UNVERIFIED",
            "reason_code": "TABLE_COLOR_LEGEND_VISUAL_SAMPLE_UNVERIFIED",
            "count": 1,
            "severity": "P1",
            "blocks_formal_understanding": False,
        },
    ]
    return {
        "enterprise_comprehension_gate": {"entry_allowed": True, "status": "PASS"},
        "document_structure_assets": {
            "source_count": 1,
            "block_count": 30,
            "page_count": 2,
            "items": [
                {
                    "source_id": "source-matrix",
                    "filename": "审批矩阵.pdf",
                    "format": "pdf",
                    "blocks": [{"block_id": "table-1"}],
                    "unsupported_content": unsupported,
                    # The table-semantic-summary gate is tested in isolation: the
                    # source already passed the ingestion pipeline and
                    # evidence-closure gates, leaving only the partial
                    # table-semantic candidates as a non-blocking gap.
                    "ingestion_pipeline_receipt": {"final_status": "COMPLETE"},
                    "evidence_closure_receipt": {
                        "status": "PASS",
                        "untraceable_authority_block_count": 0,
                        "weak_address_authority_block_count": 0,
                        "locator_conflict_count": 0,
                    },
                    "structure_receipt": {
                        "status": "PARTIAL",
                        "page_count": 2,
                        "text_page_count": 2,
                        "unsupported_content": unsupported,
                        "table_header_node_count": 5,
                        "table_header_group_node_count": 2,
                        "table_header_leaf_node_count": 3,
                        "table_row_header_candidate_count": 4,
                        "table_condition_column_candidate_count": 2,
                        "table_result_column_candidate_count": 1,
                        "decision_matrix_candidate_count": 1,
                        "table_legend_candidate_count": 3,
                        "table_color_legend_candidate_count": 1,
                        "table_symbol_legend_candidate_count": 2,
                        "legend_mapped_cell_count": 6,
                        "decision_column_role_ambiguity_count": 0,
                        "rejected_row_header_candidate_count": 1,
                        "rejected_unsafe_column_role_candidate_count": 1,
                        "rejected_overlapping_decision_matrix_candidate_count": 0,
                        "table_legend_token_ambiguity_count": 0,
                        "table_symbol_legend_missing_cell_count": 2,
                        "table_color_legend_unverified_count": 1,
                        "semantic_candidate_inherited_fragment_count": 1,
                        "semantic_candidate_inherited_cell_count": 6,
                    },
                }
            ],
            "errors": [],
        },
    }


def test_table_semantic_candidates_are_visible_but_not_formal_business_rules() -> None:
    model = apply_document_structure_completeness(empty_model(), _asset())
    summary = model["document_structure_summary"]
    assert summary["table_header_node_count"] == 5
    assert summary["table_header_group_node_count"] == 2
    assert summary["table_header_leaf_node_count"] == 3
    assert summary["table_row_header_candidate_count"] == 4
    assert summary["table_condition_column_candidate_count"] == 2
    assert summary["table_result_column_candidate_count"] == 1
    assert summary["decision_matrix_candidate_count"] == 1
    assert summary["table_legend_candidate_count"] == 3
    assert summary["legend_mapped_cell_count"] == 6
    assert summary["table_symbol_legend_missing_cell_count"] == 2
    assert summary["table_color_legend_unverified_count"] == 1
    assert summary["semantic_candidate_inherited_fragment_count"] == 1
    assert summary["semantic_candidate_inherited_cell_count"] == 6

    unknown = next(
        row
        for row in model["unknowns"]
        if row.get("kind") == "DOCUMENT_STRUCTURE_CONTENT_UNPARSED"
    )
    assert unknown["blocks_formal_understanding"] is False
    assert model["gate"]["status"] == "PARTIAL_ENTERPRISE_UNDERSTANDING"
    assert model.get("rules") == []
    assert model.get("processes") == []
