from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.document_structure_gate import (
    apply_document_structure_completeness,
)


def _asset(reason_code: str, *, blocking: bool) -> dict:
    unsupported = [
        {
            "kind": reason_code,
            "reason_code": reason_code,
            "count": 1,
            "severity": "P0" if blocking else "P1",
            "blocks_formal_understanding": blocking,
        }
    ]
    return {
        "enterprise_comprehension_gate": {"entry_allowed": True, "status": "PASS"},
        "document_structure_assets": {
            "source_count": 1,
            "block_count": 20,
            "page_count": 2,
            "items": [
                {
                    "source_id": "source-table",
                    "filename": "审批矩阵.pdf",
                    "format": "pdf",
                    "blocks": [{"block_id": "table-1"}],
                    "unsupported_content": unsupported,
                    "structure_receipt": {
                        "status": "BLOCKED" if blocking else "PARTIAL",
                        "page_count": 2,
                        "text_page_count": 2,
                        "unsupported_content": unsupported,
                        "logical_visual_table_group_count": 1,
                        "continued_visual_table_fragment_count": 1,
                        "multi_level_header_group_count": 1,
                        "repeated_header_cell_count": 4,
                        "header_inherited_data_cell_count": 6,
                        "ambiguous_table_continuation_count": 0 if blocking else 1,
                        "table_continuation_header_conflict_count": 1 if blocking else 0,
                    },
                }
            ],
            "errors": [],
        },
    }


def test_ambiguous_continuation_becomes_visible_nonblocking_unknown() -> None:
    model = apply_document_structure_completeness(
        {"unknowns": [], "conflicts": []},
        _asset("VISUAL_TABLE_CONTINUATION_AMBIGUOUS", blocking=False),
    )
    unknown = next(
        row
        for row in model["unknowns"]
        if row.get("unknown_type") == "DOCUMENT_STRUCTURE_CONTENT_UNPARSED"
    )
    assert unknown["blocks_formal_understanding"] is False
    summary = model["document_structure_summary"]
    assert summary["logical_visual_table_group_count"] == 1
    assert summary["continued_visual_table_fragment_count"] == 1
    assert summary["multi_level_header_group_count"] == 1
    assert summary["repeated_header_cell_count"] == 4
    assert summary["header_inherited_data_cell_count"] == 6
    assert summary["ambiguous_table_continuation_count"] == 1


def test_header_conflict_becomes_blocking_structure_unknown() -> None:
    model = apply_document_structure_completeness(
        {"unknowns": [], "conflicts": []},
        _asset("VISUAL_TABLE_CONTINUATION_HEADER_CONFLICT", blocking=True),
    )
    unknown = next(
        row
        for row in model["unknowns"]
        if row.get("unknown_type") == "DOCUMENT_STRUCTURE_CONTENT_UNAVAILABLE"
    )
    assert unknown["blocks_formal_understanding"] is True
    assert unknown["reason_code"] == "VISUAL_TABLE_CONTINUATION_HEADER_CONFLICT"
    assert model["document_structure_summary"]["table_continuation_header_conflict_count"] == 1
