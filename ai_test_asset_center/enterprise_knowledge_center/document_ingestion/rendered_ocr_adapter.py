"""OCR adapter facade that consumes the shared page-rendering infrastructure."""
from __future__ import annotations

import re
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

_SUPPORTED_SUPPLEMENTAL_GAPS = {
    "SCANNED_PAGE_REQUIRES_OCR",
    "PRESENTATION_IMAGE_CONTENT_UNPARSED",
    "SPREADSHEET_EMBEDDED_IMAGE_NOT_SEMANTICALLY_PARSED",
}
_SLIDE_LOCATOR_RE = re.compile(r"#slide=(\d+)")
_FORMAL_OCR_MINIMUM_CONFIDENCE = 0.85


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _reason(row: dict[str, Any]) -> str:
    return text(row.get("reason_code") or row.get("kind"))


def _supported_trigger_gaps(context: SupplementalContext) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in context.trigger_gaps
        if isinstance(row, dict) and _reason(row) in _SUPPORTED_SUPPLEMENTAL_GAPS
    ]


def _target_pages(gaps: list[dict[str, Any]]) -> list[int]:
    pages: set[int] = set()
    for gap in gaps:
        for value in [*_list(gap.get("pages")), gap.get("page"), gap.get("slide")]:
            if str(value).isdigit() and int(value) > 0:
                pages.add(int(value))
        locator = text(gap.get("source_locator"))
        match = _SLIDE_LOCATOR_RE.search(locator)
        if match:
            pages.add(int(match.group(1)))
    return sorted(pages)


class OcrSupplementalAdapter(_LegacyOcrSupplementalAdapter):
    """Compatibility-preserving OCR adapter backed by generic rendered pages."""

    parser_version = "3"
    capabilities = frozenset({*_LegacyOcrSupplementalAdapter.capabilities, CAP_PAGE_RENDERING})

    def __init__(
        self,
        provider: OcrProvider | None = None,
        minimum_confidence: float = _FORMAL_OCR_MINIMUM_CONFIDENCE,
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
        gaps = _supported_trigger_gaps(context)
        if not gaps:
            return None
        pages = _target_pages(gaps)
        reasons = sorted({_reason(row) for row in gaps})
        page_suffix = ":" + ",".join(str(page) for page in pages) if pages else ":all-rendered-pages"
        return AdapterMatch(
            self.name,
            118,
            "primary_adapter_reported_renderable_visual_gaps:"
            + ",".join(reasons)
            + page_suffix,
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

        raw_unsupported = [
            dict(row)
            for row in (result.get("unsupported_content") or [])
            if isinstance(row, dict)
        ]
        # A high-confidence OCR page with explicit image coordinates is formal evidence,
        # not an unresolved gap. Keep the projection receipt separately for audit.
        projected_rows = [
            row for row in raw_unsupported if _reason(row) == "OCR_PAGE_LAYOUT_PROJECTED"
        ]
        unsupported = [
            row for row in raw_unsupported if _reason(row) != "OCR_PAGE_LAYOUT_PROJECTED"
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
        receipt["formal_ocr_minimum_confidence"] = self.minimum_confidence
        receipt["formal_ocr_projection_count"] = sum(
            int(row.get("count") or 0) for row in projected_rows
        )
        receipt["formal_ocr_projection_evidence"] = projected_rows
        receipt["high_confidence_ocr_coordinates_are_formal_evidence"] = True
        result["unsupported_content"] = unsupported
        result["structure_receipt"] = receipt
        result["page_rendering_receipt"] = batch.receipt
        result["rendered_pages"] = [row.evidence() for row in batch.pages]
        return result

    @staticmethod
    def _project_context_resolutions(
        result: dict[str, Any],
        gaps: list[dict[str, Any]],
        target_pages: list[int],
    ) -> None:
        receipt = dict(result.get("structure_receipt") or {})
        resolved_pages = {
            int(page)
            for page in receipt.get("ocr_resolved_pages") or []
            if str(page).isdigit()
        }
        rendered_pages = {
            int(row.get("page") or 0)
            for row in result.get("rendered_pages") or []
            if isinstance(row, dict) and int(row.get("page") or 0) > 0
        }
        reasons = {_reason(row) for row in gaps}
        resolutions: list[dict[str, Any]] = []

        if "SCANNED_PAGE_REQUIRES_OCR" in reasons:
            scanned_pages = {
                int(page)
                for row in gaps
                if _reason(row) == "SCANNED_PAGE_REQUIRES_OCR"
                for page in _list(row.get("pages"))
                if str(page).isdigit()
            }
            recovered = sorted(scanned_pages & resolved_pages)
            if recovered:
                resolutions.append(
                    {
                        "reason_code": "SCANNED_PAGE_REQUIRES_OCR",
                        "pages": recovered,
                        "resolution": "OCR_TEXT_RECOVERED",
                    }
                )

        for reason in (
            "PRESENTATION_IMAGE_CONTENT_UNPARSED",
            "SPREADSHEET_EMBEDDED_IMAGE_NOT_SEMANTICALLY_PARSED",
        ):
            if reason not in reasons:
                continue
            required = set(target_pages) if target_pages else rendered_pages
            # Office visual gaps are emitted as one whole-source/slide group without a
            # page list. Clear them only when every rendered target was recovered.
            if required and required <= resolved_pages:
                resolutions.append(
                    {
                        "reason_code": reason,
                        "pages": [],
                        "resolution": "OFFICE_VISUAL_TEXT_RECOVERED_ON_ALL_TARGETS",
                        "resolved_rendered_pages": sorted(required),
                    }
                )
        result["resolves_gaps"] = resolutions
        receipt["context_gap_resolution_count"] = len(resolutions)
        receipt["context_gap_resolutions"] = resolutions
        result["structure_receipt"] = receipt

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
        gaps = _supported_trigger_gaps(context)
        if not gaps:
            raise ValueError("OCR supplemental adapter received no supported visual gaps")
        target_pages = _target_pages(gaps)
        batch = self.renderer_registry.render(
            source,
            pages=target_pages or None,
        )
        result = self._ir_from_render_batch(
            source,
            batch,
            target_pages=target_pages,
            format_name="rendered-page-ocr-supplement",
        )
        self._project_context_resolutions(result, gaps, target_pages)
        return result


__all__ = ["OcrSupplementalAdapter"]
