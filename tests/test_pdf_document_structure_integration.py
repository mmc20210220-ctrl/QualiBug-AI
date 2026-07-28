from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import integration


def _minimal_pdf_ir() -> dict:
    return {
        "schema": "qualibug.document-structure-ir.v1",
        "format": "pdf",
        "filename": "制度.pdf",
        "plain_text": "订单不得删除。",
        "pages": [
            {
                "page": 1,
                "width_pt": 595.0,
                "height_pt": 842.0,
                "text_char_count": 7,
                "scanned_page": False,
                "coordinates_available": True,
            }
        ],
        "blocks": [
            {
                "block_id": "pdf-block-1",
                "type": "PARAGRAPH",
                "parent_id": "",
                "page": 1,
                "region": "body",
                "text": "订单不得删除。",
                "bbox": [50.0, 700.0, 140.0, 712.0],
                "source_locator": "制度.pdf#page=1;bbox=50,700,140,712",
            }
        ],
        "sections": [],
        "tables": [],
        "unsupported_content": [],
        "structure_receipt": {
            "schema": "qualibug.document-structure-receipt.v1",
            "status": "COMPLETE",
            "format": "pdf",
            "page_count": 1,
            "text_page_count": 1,
            "scanned_page_count": 0,
            "block_count": 1,
            "image_count": 0,
            "table_region_count": 0,
            "multi_column_page_count": 0,
            "unsupported_content_count": 0,
        },
    }


def test_parsed_sources_for_context_uses_pdf_layout_ir(monkeypatch, tmp_path: Path) -> None:
    stored = tmp_path / "workspace" / "制度.pdf"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"%PDF-fake")
    asset = {
        "source_inventory": [
            {
                "source_id": "pdf-source-1",
                "status": "active",
                "stored_path": str(stored.relative_to(tmp_path)),
                "original_name": "制度.pdf",
                "source_type": "prd",
            }
        ]
    }

    from ai_test_asset_center.enterprise_knowledge_center import _crud
    from ai_test_asset_center.enterprise_knowledge_center import _pdf_document_structure_ir

    monkeypatch.setattr(
        _crud,
        "_record_parse",
        lambda _source, _root: {
            "text": "订单不得删除。",
            "document_structure": {},
            "parser_receipt": {"source_locator": "legacy-pdf-text"},
        },
    )
    calls: list[tuple[bytes, str]] = []

    def fake_extract(data: bytes, filename: str = "") -> dict:
        calls.append((data, filename))
        return _minimal_pdf_ir()

    monkeypatch.setattr(_pdf_document_structure_ir, "extract_pdf_document_ir", fake_extract)

    rows = integration._parsed_sources_for_context(asset, tmp_path)
    assert calls == [(b"%PDF-fake", "制度.pdf")]
    assert rows[0]["document_structure"]["format"] == "pdf"
    assert rows[0]["document_structure"]["blocks"][0]["page"] == 1
    assert rows[0]["text"] == "订单不得删除。"
    assert rows[0]["document_structure_error"] == {}


def test_attach_document_structure_assets_aggregates_pdf_risk_metrics() -> None:
    ir = _minimal_pdf_ir()
    ir["unsupported_content"] = [
        {
            "kind": "PDF_IMAGE_CONTENT_UNPARSED",
            "count": 2,
            "blocks_formal_understanding": False,
        },
        {
            "kind": "SCANNED_PAGE_REQUIRES_OCR",
            "count": 1,
            "blocks_formal_understanding": True,
        },
    ]
    ir["structure_receipt"].update(
        {
            "status": "BLOCKED",
            "page_count": 3,
            "scanned_page_count": 1,
            "image_count": 3,
            "table_region_count": 2,
            "multi_column_page_count": 1,
            "unsupported_content_count": 3,
        }
    )
    asset: dict = {}
    integration._attach_document_structure_assets(
        asset,
        [
            {
                "source_id": "pdf-source-1",
                "filename": "制度.pdf",
                "document_structure": ir,
                "document_structure_error": {},
            }
        ],
    )
    summary = asset["document_structure_assets"]
    assert summary["source_count"] == 1
    assert summary["page_count"] == 3
    assert summary["scanned_page_count"] == 1
    assert summary["image_count"] == 3
    assert summary["table_region_count"] == 2
    assert summary["multi_column_page_count"] == 1
    assert summary["critical_structure_gap_count"] == 1
