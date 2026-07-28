from __future__ import annotations

from typing import Iterable

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    DocumentSource,
    PageRendererRegistry,
    RenderedPage,
)


class _Renderer:
    version = "test"
    priority = 100

    def __init__(self, name: str, available_pages: set[int]) -> None:
        self.name = name
        self.available_pages = available_pages
        self.requests: list[list[int]] = []

    def available(self) -> bool:
        return True

    def supports(self, source: DocumentSource) -> bool:
        return True

    def render(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[RenderedPage]:
        requested = sorted(int(value) for value in (pages or self.available_pages))
        self.requests.append(requested)
        return [
            RenderedPage(
                page=page,
                image_index=0,
                image_bytes=f"{self.name}-{page}".encode(),
                width_px=100,
                height_px=200,
                source_locator=f"{source.filename}#rendered_page={page}",
                renderer_name=self.name,
                renderer_version=self.version,
                render_method="test",
            )
            for page in requested
            if page in self.available_pages
        ]


def test_registry_combines_renderers_to_cover_all_target_pages() -> None:
    first = _Renderer("first", {1})
    second = _Renderer("second", {2})
    first.priority = 200
    registry = PageRendererRegistry([first, second])
    source = DocumentSource("source-1", "scan.pdf", b"%PDF-test")
    batch = registry.render(source, pages=[1, 2])
    assert [row.page for row in batch.pages] == [1, 2]
    assert batch.receipt["status"] == "COMPLETE"
    assert batch.receipt["renderer_name"] == "MULTI_PROVIDER"
    assert batch.receipt["missing_pages"] == []
    assert first.requests == [[1, 2]]
    assert second.requests == [[2]]


def test_registry_reports_partial_when_target_page_remains_missing() -> None:
    registry = PageRendererRegistry([_Renderer("only-page-one", {1})])
    source = DocumentSource("source-2", "scan.pdf", b"%PDF-test")
    batch = registry.render(source, pages=[1, 2])
    assert [row.page for row in batch.pages] == [1]
    assert batch.receipt["status"] == "PARTIAL"
    assert batch.receipt["reason_code"] == "PAGE_RENDERING_TARGET_PAGES_INCOMPLETE"
    assert batch.receipt["missing_pages"] == [2]
