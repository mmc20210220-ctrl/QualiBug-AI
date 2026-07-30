from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    AdapterMatch,
    CAP_OCR,
    CAP_PAGE_RENDERING,
    CAP_TEXT_EXTRACTION,
    DocumentAdapter,
    DocumentSource,
    MODE_FALLBACK,
    MODE_SUPPLEMENTAL,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.planner import (
    plan_document_parsing,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.registry import (
    DocumentAdapterRegistry,
)


class _RenderableSupplemental(DocumentAdapter):
    name = "synthetic-rendered-ocr"
    priority = 200
    mode = MODE_SUPPLEMENTAL
    standalone = True
    capabilities = frozenset({CAP_PAGE_RENDERING, CAP_OCR, CAP_TEXT_EXTRACTION})

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        return AdapterMatch(
            self.name,
            200,
            "synthetic-renderer-available",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def extract(self, source: DocumentSource) -> dict:
        raise AssertionError("planner test must not extract")


class _NativeTextFallback(DocumentAdapter):
    name = "synthetic-native-text"
    priority = 10
    mode = MODE_FALLBACK
    capabilities = frozenset({CAP_TEXT_EXTRACTION})

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        return AdapterMatch(
            self.name,
            10,
            "utf8-markup",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def extract(self, source: DocumentSource) -> dict:
        raise AssertionError("planner test must not extract")


def test_svg_native_markup_precedes_available_rendered_ocr() -> None:
    source = DocumentSource(
        source_id="src_svg",
        filename="checkout.svg",
        data=(
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<text id="submit-label">Submit order</text>'
            b'</svg>'
        ),
    )
    registry = DocumentAdapterRegistry(
        [_RenderableSupplemental(), _NativeTextFallback()]
    )

    plan = plan_document_parsing(source, registry)

    assert plan["detected_family"] == "structured_text"
    assert plan["detection_method"] == "svg_xml_markup_signature"
    assert plan["selected_adapters"][0]["adapter_name"] == "synthetic-native-text"
    assert all(
        row["adapter_name"] != "synthetic-rendered-ocr"
        for row in plan["selected_adapters"]
    )
