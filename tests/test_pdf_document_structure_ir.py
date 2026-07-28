from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pypdf

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
)
from ai_test_asset_center.enterprise_knowledge_center._document_ir_context import (
    apply_document_ir_context,
)
from ai_test_asset_center.enterprise_knowledge_center._pdf_document_structure_ir import (
    extract_pdf_document_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.document_structure_gate import (
    apply_document_structure_completeness,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    empty_model,
)


@dataclass
class _Box:
    width: float = 595.0
    height: float = 842.0


class _FakePage:
    def __init__(
        self,
        fragments: list[dict[str, Any]] | None = None,
        *,
        images: int = 0,
        forms: int = 0,
        rotation: int = 0,
        coordinate_failure: bool = False,
    ) -> None:
        self.fragments = fragments or []
        self.mediabox = _Box()
        self.rotation = rotation
        self.coordinate_failure = coordinate_failure
        self._resources = {
            "/XObject": {
                **{f"/Im{index}": {"/Subtype": "/Image"} for index in range(images)},
                **{f"/Fm{index}": {"/Subtype": "/Form"} for index in range(forms)},
            }
        }

    def get(self, key: str, default: Any = None) -> Any:
        if key == "/Resources":
            return self._resources
        if key == "/Rotate":
            return self.rotation
        return default

    def extract_text(self, *args: Any, visitor_text=None, **kwargs: Any) -> str:
        if self.coordinate_failure and visitor_text is not None:
            raise TypeError("visitor_text unsupported")
        values: list[str] = []
        for fragment in self.fragments:
            value = str(fragment["text"])
            values.append(value)
            if visitor_text is not None:
                visitor_text(
                    value,
                    [1, 0, 0, 1, 0, 0],
                    [1, 0, 0, 1, fragment.get("x", 50), fragment.get("y", 700)],
                    {"/BaseFont": f"/{fragment.get('font', 'SimSun')}"},
                    fragment.get("size", 11),
                )
        return "\n".join(values)


class _FakeReader:
    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = pages
        self.is_encrypted = False


def _patch_reader(monkeypatch, pages: list[_FakePage]) -> None:
    monkeypatch.setattr(pypdf, "PdfReader", lambda _stream: _FakeReader(pages))


def _asset() -> dict[str, Any]:
    return {
        "business_objects": [{"object": "订单"}, {"object": "合同"}],
        "roles": [{"role": "仓库管理员"}],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def test_pdf_ir_preserves_page_coordinates_font_hierarchy_and_repeated_header(monkeypatch) -> None:
    pages = [
        _FakePage(
            [
                {"text": "某公司内部制度", "x": 40, "y": 825, "size": 8},
                {"text": "订单管理", "x": 50, "y": 760, "size": 18, "font": "SimHei-Bold"},
                {"text": "其不得发货。", "x": 50, "y": 720, "size": 11},
            ]
        ),
        _FakePage(
            [
                {"text": "某公司内部制度", "x": 40, "y": 825, "size": 8},
                {"text": "合同管理", "x": 50, "y": 760, "size": 18, "font": "SimHei-Bold"},
                {"text": "其可以归档。", "x": 50, "y": 720, "size": 11},
            ]
        ),
    ]
    _patch_reader(monkeypatch, pages)

    ir = extract_pdf_document_ir(b"%PDF-fake", "企业制度.pdf")

    headers = [row for row in ir["blocks"] if row.get("type") == "HEADER"]
    headings = [row for row in ir["blocks"] if row.get("type") == "HEADING"]
    paragraphs = [row for row in ir["blocks"] if row.get("type") == "PARAGRAPH"]
    assert len(headers) == 2
    assert all(row.get("excluded_from_main_flow") is True for row in headers)
    assert "某公司内部制度" not in ir["plain_text"]
    assert {row["text"] for row in headings} == {"订单管理", "合同管理"}
    assert all(row.get("bbox") and row.get("page") in {1, 2} for row in paragraphs)
    assert all(row.get("parent_id") for row in paragraphs)
    assert ir["structure_receipt"]["repeated_header_block_count"] == 2
    assert ir["structure_receipt"]["coordinate_system"] == "PDF_BOTTOM_LEFT_POINTS"
    assert ir["structure_receipt"]["headers_and_footers_excluded_from_main_flow"] is True


def test_pdf_heading_context_resolves_pending_chinese_reference(monkeypatch) -> None:
    page = _FakePage(
        [
            {"text": "订单", "x": 50, "y": 760, "size": 18, "font": "SimHei-Bold"},
            {"text": "其不得发货。", "x": 50, "y": 720, "size": 11},
        ]
    )
    _patch_reader(monkeypatch, [page])
    source_id = "pdf-order-1"
    source = {"source_id": source_id, "filename": "企业制度.pdf", "text": "其不得发货。"}
    asset = build_chinese_first_comprehension(_asset(), [source])
    fact = next(row for row in asset["business_fact_ledger"]["items"] if row.get("raw_statement") == "其不得发货")
    assert fact["status"] == "PENDING"

    ir = extract_pdf_document_ir(b"%PDF-fake", "企业制度.pdf")
    enriched = apply_document_ir_context(asset, [{**source, "document_structure": ir}])
    fact = next(row for row in enriched["business_fact_ledger"]["items"] if row.get("raw_statement") == "其不得发货")
    assert fact["status"] == "ACCEPTED"
    assert fact["subject"]["entity_refs"] == ["订单"]
    assert fact["document_structure_context"]["filename_context_used"] is False
    assert fact["document_structure_context"]["block_type"] == "PARAGRAPH"


def test_pdf_scanned_page_is_formally_blocking(monkeypatch) -> None:
    _patch_reader(monkeypatch, [_FakePage([], images=1)])
    ir = extract_pdf_document_ir(b"%PDF-scan", "扫描制度.pdf")
    assert ir["structure_receipt"]["status"] == "BLOCKED"
    assert ir["structure_receipt"]["scanned_page_count"] == 1
    scanned_gap = next(
        row for row in ir["unsupported_content"] if row.get("kind") == "SCANNED_PAGE_REQUIRES_OCR"
    )
    assert scanned_gap["blocks_formal_understanding"] is True

    model = empty_model()
    asset = {
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "document_structure_assets": {
            "source_count": 1,
            "block_count": len(ir["blocks"]),
            "page_count": 1,
            "scanned_page_count": 1,
            "unsupported_content_count": 1,
            "critical_structure_gap_count": 1,
            "items": [{"source_id": "scan-1", "filename": "扫描制度.pdf", **ir}],
            "errors": [],
        },
    }
    gated = apply_document_structure_completeness(model, asset)
    assert gated["gate"]["entry_allowed"] is False
    assert gated["gate"]["status"] == "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN"
    assert any(
        row.get("reason_code") == "SCANNED_PAGE_REQUIRES_OCR"
        for row in gated["gate"]["critical_unknowns"]
    )


def test_pdf_missing_coordinates_is_formally_blocking(monkeypatch) -> None:
    page = _FakePage(
        [{"text": "订单不得删除。", "x": 50, "y": 720, "size": 11}],
        coordinate_failure=True,
    )
    _patch_reader(monkeypatch, [page])
    ir = extract_pdf_document_ir(b"%PDF-no-coordinates", "无坐标.pdf")
    gap = next(
        row for row in ir["unsupported_content"] if row.get("kind") == "PDF_TEXT_COORDINATES_UNAVAILABLE"
    )
    assert gap["blocks_formal_understanding"] is True
    assert ir["structure_receipt"]["status"] == "BLOCKED"


def test_pdf_multi_column_layout_is_partial_and_order_is_projection(monkeypatch) -> None:
    page = _FakePage(
        [
            {"text": "左栏第一条", "x": 40, "y": 760, "size": 11},
            {"text": "右栏第一条", "x": 340, "y": 735, "size": 11},
            {"text": "左栏第二条", "x": 40, "y": 700, "size": 11},
            {"text": "右栏第二条", "x": 340, "y": 675, "size": 11},
        ]
    )
    _patch_reader(monkeypatch, [page])
    ir = extract_pdf_document_ir(b"%PDF-columns", "双栏制度.pdf")
    assert ir["pages"][0]["column_count_projection"] == 2
    assert ir["structure_receipt"]["reading_order_is_projection"] is True
    assert ir["structure_receipt"]["status"] == "PARTIAL"
    assert any(
        row.get("kind") == "PDF_MULTI_COLUMN_READING_ORDER_HEURISTIC"
        for row in ir["unsupported_content"]
    )


def test_pdf_table_like_rows_create_unresolved_table_region(monkeypatch) -> None:
    page = _FakePage(
        [
            {"text": "状态", "x": 50, "y": 720, "size": 11},
            {"text": "动作", "x": 250, "y": 720, "size": 11},
            {"text": "待支付", "x": 50, "y": 690, "size": 11},
            {"text": "付款", "x": 250, "y": 690, "size": 11},
            {"text": "已支付", "x": 50, "y": 660, "size": 11},
            {"text": "发货", "x": 250, "y": 660, "size": 11},
        ]
    )
    _patch_reader(monkeypatch, [page])
    ir = extract_pdf_document_ir(b"%PDF-table", "状态表.pdf")
    regions = [row for row in ir["blocks"] if row.get("type") == "TABLE_REGION"]
    assert regions
    assert regions[0]["cell_structure_parsed"] is False
    assert ir["structure_receipt"]["table_region_count"] >= 1
    assert any(
        row.get("kind") == "PDF_TABLE_REGION_NOT_CELL_PARSED"
        for row in ir["unsupported_content"]
    )
