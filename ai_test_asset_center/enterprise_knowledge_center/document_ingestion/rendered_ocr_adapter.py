"""OCR adapter facade that consumes the shared page-rendering infrastructure."""
from __future__ import annotations

from typing import Any

from .contract import (
    AdapterMatch,
    CAP_PAGE_RENDERING,
    DocumentSource,
    SupplementalContext,
    text,
)
from .ocr_adapter import OcrProvider, OcrSupplementalAdapter as _LegacyOcrSupplementalAdapter
from .page_render_registry import PageRendererRegistry, build_default_page_renderer_registry
from .page_rendering import PageRenderBatch


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class OcrSupplementalAdapter(_LegacyOcrSupplementalAdapter):
    """Compatibility-preserving OCR adapter backed by generic rendered pages."""

    parser_version = "2"
    capabilities = frozenset({*_LegacyOcrSupplementalAdapter.capabilities, CAP_PAGE_RENDERING})

    def __init__(
        self,
        provider: OcrProvider | None = None,
        minimum_confidence: float = 0.55,
        renderer_registry: PageRendererRegistry | None = None,
    ) -> None:
        super().__init__(provider=provider, minimum_confidence=minimum_confidence)
        self.renderer_registry = renderer_registry or build_default_page_renderer_registry()

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        if not self.provider.available() or not self.renderer_registry.can_render(source):
            return None
        return AdapterMatch(
            self.name,
            110,
            "source_can_be_rendered_for_visual_text_recovery",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def probe_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> AdapterMatch | None:
        if not self.provider.available() or not self.renderer_registry.can_render(source):
            return None
        scanned_pages = sorted(
            {
                int(page)
                for gap in context.trigger_gaps
                if text(gap.get("reason_code") or gap.get("kind"))
                == "SCANNED_PAGE_REQUIRES_OCR"
                for page in _list(gap.get("pages"))
                if str(page).isdigit()
            }
        )
        if not scanned_pages:
            return None
        return AdapterMatch(
            self.name,
            118,
            "primary_adapter_reported_renderable_scanned_pages:"
            + ",".join(str(page) for page in scanned_pages),
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def _ir_from_render_batch(
        self,
        source: DocumentSource,
        batch: PageRenderBatch,
        *,
        target_pages: list[int],
        format_name: str,
    ) -> dict[str, Any]:
        page_images = [(row.page, row.image_index, row.image_bytes) for row in batch.pages]
        effective_pages = target_pages or sorted({row.page for row in batch.pages}) or [1]
        result = self._build_ir(
            source,
            page_images,
            target_pages=effective_pages,
            format_name=format_name,
        )
        render_index = {(row.page, row.image_index): row for row in batch.pages}
        render_by_page: dict[int, list[dict[str, Any]]] = {}
        for rendered in batch.pages:
            render_by_page.setdefault(rendered.page, []).append(rendered.evidence())

        for block in result.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            page = int(block.get("page") or 0)
            locator = text(block.get("source_locator"))
            image_index = 0
            marker = "embedded_image="
            if marker in locator:
                try:
                    image_index = int(locator.split(marker, 1)[1].split(";", 1)[0])
                except (TypeError, ValueError):
                    image_index = 0
            rendered = render_index.get((page, image_index))
            if not rendered:
                continue
            evidence = dict(block.get("structure_evidence") or {})
            evidence["page_rendering"] = rendered.evidence()
            evidence["coordinate_system"] = "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE"
            block["structure_evidence"] = evidence
            block["rendered_page_source_locator"] = rendered.source_locator

        for page_row in result.get("pages") or []:
            if not isinstance(page_row, dict):
                continue
            page_number = int(page_row.get("page") or 0)
            render_rows = render_by_page.get(page_number) or []
            page_row["page_rendering"] = render_rows
            page_row["page_rendered"] = bool(render_rows)
            page_row["page_renderer_names"] = sorted(
                {text(row.get("renderer_name")) for row in render_rows if text(row.get("renderer_name"))}
            )

        unsupported = [
            dict(row)
            for row in (result.get("unsupported_content") or [])
            if isinstance(row, dict)
        ]
        if not batch.pages:
            unsupported.append(
                {
                    "kind": "PAGE_RENDERER_UNAVAILABLE_OR_FAILED",
                    "reason_code": "PAGE_RENDERER_UNAVAILABLE_OR_FAILED",
                    "count": max(1, len(effective_pages)),
                    "pages": effective_pages,
                    "status": "SOURCE_COULD_NOT_BE_RENDERED_FOR_OCR",
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                    "included_in_plain_text_authority": False,
                    "render_receipt": batch.receipt,
                }
            )
        elif batch.receipt.get("missing_pages"):
            unsupported.append(
                {
                    "kind": "PAGE_RENDERING_TARGET_PAGES_INCOMPLETE",
                    "reason_code": "PAGE_RENDERING_TARGET_PAGES_INCOMPLETE",
                    "count": len(batch.receipt.get("missing_pages") or []),
                    "pages": list(batch.receipt.get("missing_pages") or []),
                    "status": "SOME_TARGET_PAGES_COULD_NOT_BE_RENDERED",
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                    "included_in_plain_text_authority": False,
                    "render_receipt": batch.receipt,
                }
            )
        receipt = dict(result.get("structure_receipt") or {})
        critical = [row for row in unsupported if bool(row.get("blocks_formal_understanding"))]
        receipt["status"] = "BLOCKED" if critical else "PARTIAL" if unsupported else "COMPLETE"
        receipt["unsupported_content"] = unsupported
        receipt["unsupported_content_count"] = sum(int(row.get("count") or 0) for row in unsupported)
        receipt["critical_unsupported_content_count"] = sum(
            int(row.get("count") or 0) for row in critical
        )
        receipt["page_rendering_receipt"] = batch.receipt
        receipt["page_renderer_name"] = batch.receipt.get("renderer_name")
        receipt["page_renderer_version"] = batch.receipt.get("renderer_version")
        receipt["rendered_page_count"] = len(render_by_page)
        result["unsupported_content"] = unsupported
        result["structure_receipt"] = receipt
        result["page_rendering_receipt"] = batch.receipt
        result["rendered_pages"] = [row.evidence() for row in batch.pages]
        return result

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        if not self.provider.available():
            raise RuntimeError("OCR provider unavailable")
        batch = self.renderer_registry.render(source)
        target_pages = sorted({row.page for row in batch.pages})
        return self._ir_from_render_batch(
            source,
            batch,
            target_pages=target_pages,
            format_name=source.suffix.lstrip(".") or "rendered-document",
        )

    def extract_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> dict[str, Any]:
        if not self.provider.available():
            raise RuntimeError("OCR provider unavailable")
        target_pages = sorted(
            {
                int(page)
                for gap in context.trigger_gaps
                if text(gap.get("reason_code") or gap.get("kind"))
                == "SCANNED_PAGE_REQUIRES_OCR"
                for page in _list(gap.get("pages"))
                if str(page).isdigit()
            }
        )
        if not target_pages:
            raise ValueError("OCR supplemental adapter received no scanned-page targets")
        batch = self.renderer_registry.render(source, pages=target_pages)
        return self._ir_from_render_batch(
            source,
            batch,
            target_pages=target_pages,
            format_name="rendered-page-ocr-supplement",
        )


__all__ = ["OcrSupplementalAdapter"]
