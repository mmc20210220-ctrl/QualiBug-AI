"""Page-complete renderer registry built on the shared provider contracts."""
from __future__ import annotations

from typing import Any, Iterable

from .contract import DocumentSource
from .page_rendering import (
    PAGE_RENDER_RECEIPT_SCHEMA,
    LibreOfficeDocumentPageRenderer,
    PageRenderBatch,
    PageRendererRegistry as _BasePageRendererRegistry,
    PdfiumPdfPageRenderer,
    PypdfEmbeddedImagePageRenderer,
    RasterImagePageRenderer,
    RenderedPage,
)


def _targets(values: Iterable[int] | None) -> set[int]:
    result: set[int] = set()
    for value in values or ():
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page > 0:
            result.add(page)
    return result


class PageRendererRegistry(_BasePageRendererRegistry):
    """Resolve rendered pages across providers without hiding missing target pages."""

    def render(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> PageRenderBatch:
        target_pages = _targets(pages)
        errors: list[dict[str, Any]] = []
        attempted: list[str] = []
        selected: dict[int, list[RenderedPage]] = {}

        for renderer in self.matching(source):
            attempted.append(renderer.name)
            remaining = sorted(target_pages - set(selected)) if target_pages else []
            if target_pages and not remaining:
                break
            try:
                rendered = renderer.render(source, pages=remaining)
            except Exception as exc:
                errors.append(
                    {
                        "renderer_name": renderer.name,
                        "code": "PAGE_RENDERER_EXECUTION_FAILED",
                        "detail": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )
                continue
            if not rendered:
                errors.append(
                    {
                        "renderer_name": renderer.name,
                        "code": "PAGE_RENDERER_RETURNED_NO_PAGES",
                        "detail": "renderer completed without producing requested page images",
                    }
                )
                continue
            for row in rendered:
                if target_pages and row.page not in target_pages:
                    continue
                # The first successful provider is authoritative for a page. Multiple
                # images from that same provider/page remain available to OCR.
                if row.page in selected and selected[row.page][0].renderer_name != renderer.name:
                    continue
                selected.setdefault(row.page, []).append(row)
            if not target_pages and selected:
                # Standalone visual sources use one provider projection; downstream
                # adapters can request another provider explicitly if comparison is needed.
                break

        rendered_pages = tuple(
            row
            for page_number in sorted(selected)
            for row in sorted(selected[page_number], key=lambda value: value.image_index)
        )
        covered_pages = set(selected)
        missing_pages = sorted(target_pages - covered_pages)
        if not rendered_pages:
            status = "BLOCKED"
            reason_code = "PAGE_RENDERER_UNAVAILABLE_OR_FAILED"
        elif missing_pages:
            status = "PARTIAL"
            reason_code = "PAGE_RENDERING_TARGET_PAGES_INCOMPLETE"
        else:
            status = "COMPLETE"
            reason_code = ""
        renderer_names = sorted({row.renderer_name for row in rendered_pages})
        renderer_versions = {
            row.renderer_name: row.renderer_version
            for row in rendered_pages
            if row.renderer_name
        }
        receipt = {
            "schema": PAGE_RENDER_RECEIPT_SCHEMA,
            "status": status,
            "source_id": source.source_id,
            "filename": source.filename,
            "source_hash": source.content_hash,
            "renderer_name": renderer_names[0] if len(renderer_names) == 1 else "MULTI_PROVIDER",
            "renderer_names": renderer_names,
            "renderer_versions": renderer_versions,
            "target_pages": sorted(target_pages),
            "rendered_pages": sorted(covered_pages),
            "missing_pages": missing_pages,
            "rendered_image_count": len(rendered_pages),
            "attempted_renderers": attempted,
            "error_count": len(errors),
            "errors": errors,
            "reason_code": reason_code,
            "business_semantics_added": False,
        }
        return PageRenderBatch(rendered_pages, receipt, tuple(errors))


def build_default_page_renderer_registry() -> PageRendererRegistry:
    return PageRendererRegistry(
        [
            RasterImagePageRenderer(),
            PdfiumPdfPageRenderer(),
            LibreOfficeDocumentPageRenderer(),
            PypdfEmbeddedImagePageRenderer(),
        ]
    )


__all__ = ["PageRendererRegistry", "build_default_page_renderer_registry"]
