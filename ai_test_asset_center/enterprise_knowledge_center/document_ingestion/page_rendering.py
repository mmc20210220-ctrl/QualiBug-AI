"""Generic page rendering providers for visual document understanding.

Rendering is shared infrastructure for OCR, table reconstruction and diagram analysis.
Providers convert source bytes into source-preserving page images and never infer business
semantics.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from .contract import DocumentSource, text

PAGE_RENDER_RECEIPT_SCHEMA = "qualibug.page-render-receipt.v1"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
_OFFICE_SUFFIXES = {
    ".doc",
    ".docx",
    ".rtf",
    ".odt",
    ".ppt",
    ".pptx",
    ".odp",
    ".xls",
    ".xlsx",
    ".ods",
}
_IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"II*\x00",
    b"MM\x00*",
    b"BM",
)


def _looks_like_image(source: DocumentSource) -> bool:
    signature = source.data[:16]
    return (
        source.suffix in _IMAGE_SUFFIXES
        or any(signature.startswith(value) for value in _IMAGE_SIGNATURES)
        or (signature.startswith(b"RIFF") and b"WEBP" in signature)
    )


def _page_filter(values: Iterable[int] | None) -> set[int]:
    result: set[int] = set()
    for value in values or ():
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page > 0:
            result.add(page)
    return result


@dataclass(frozen=True)
class RenderedPage:
    page: int
    image_index: int
    image_bytes: bytes
    width_px: int
    height_px: int
    source_locator: str
    renderer_name: str
    renderer_version: str
    render_method: str
    dpi: int = 0

    def evidence(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "image_index": self.image_index,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "source_locator": self.source_locator,
            "renderer_name": self.renderer_name,
            "renderer_version": self.renderer_version,
            "render_method": self.render_method,
            "dpi": self.dpi,
            "business_semantics_added": False,
        }


@dataclass(frozen=True)
class PageRenderBatch:
    pages: tuple[RenderedPage, ...]
    receipt: dict[str, Any]
    errors: tuple[dict[str, Any], ...] = ()


class PageRenderer(Protocol):
    name: str
    version: str
    priority: int

    def available(self) -> bool:
        ...

    def supports(self, source: DocumentSource) -> bool:
        ...

    def render(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[RenderedPage]:
        ...


class RasterImagePageRenderer:
    name = "raster-image-page-renderer"
    version = "1"
    priority = 120

    def available(self) -> bool:
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            return False
        return True

    def supports(self, source: DocumentSource) -> bool:
        return _looks_like_image(source)

    def render(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[RenderedPage]:
        requested = _page_filter(pages)
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(source.data))
            frame_count = int(getattr(image, "n_frames", 1) or 1)
            rendered: list[RenderedPage] = []
            for frame_index in range(frame_count):
                page_number = frame_index + 1
                if requested and page_number not in requested:
                    continue
                image.seek(frame_index)
                rgb = image.convert("RGB")
                buffer = io.BytesIO()
                rgb.save(buffer, format="PNG")
                rendered.append(
                    RenderedPage(
                        page=page_number,
                        image_index=0,
                        image_bytes=buffer.getvalue(),
                        width_px=int(rgb.width),
                        height_px=int(rgb.height),
                        source_locator=f"{source.filename or 'image'}#rendered_page={page_number}",
                        renderer_name=self.name,
                        renderer_version=self.version,
                        render_method="raster_frame_normalized_to_png",
                    )
                )
            return rendered
        except Exception:
            # Synthetic fixtures and unusual raster containers remain fail-visible.
            # A real OCR provider must still reject unreadable bytes.
            if requested and 1 not in requested:
                return []
            return [
                RenderedPage(
                    page=1,
                    image_index=0,
                    image_bytes=source.data,
                    width_px=0,
                    height_px=0,
                    source_locator=f"{source.filename or 'image'}#rendered_page=1",
                    renderer_name=self.name,
                    renderer_version=self.version,
                    render_method="raster_bytes_passthrough_unverified",
                )
            ]


class PdfiumPdfPageRenderer:
    """Preferred full-page PDF renderer using permissively licensed PDFium bindings."""

    name = "pdfium-pdf-page-renderer"
    version = "1"
    priority = 110

    def __init__(self, dpi: int = 200) -> None:
        self.dpi = max(72, min(600, int(dpi)))

    def available(self) -> bool:
        try:
            import pypdfium2  # noqa: F401
        except ImportError:
            return False
        return True

    def supports(self, source: DocumentSource) -> bool:
        return source.data.lstrip().startswith(b"%PDF-") or source.suffix == ".pdf"

    def render(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[RenderedPage]:
        import pypdfium2 as pdfium

        requested = _page_filter(pages)
        scale = self.dpi / 72.0
        document = pdfium.PdfDocument(source.data)
        rendered: list[RenderedPage] = []
        try:
            for page_index in range(len(document)):
                page_number = page_index + 1
                if requested and page_number not in requested:
                    continue
                page = document[page_index]
                bitmap = None
                try:
                    bitmap = page.render(scale=scale)
                    image = bitmap.to_pil().convert("RGB")
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    rendered.append(
                        RenderedPage(
                            page=page_number,
                            image_index=0,
                            image_bytes=buffer.getvalue(),
                            width_px=int(image.width),
                            height_px=int(image.height),
                            source_locator=f"{source.filename or 'document.pdf'}#rendered_page={page_number}",
                            renderer_name=self.name,
                            renderer_version=self.version,
                            render_method="pdfium_full_page_rasterized",
                            dpi=self.dpi,
                        )
                    )
                finally:
                    close_bitmap = getattr(bitmap, "close", None)
                    if callable(close_bitmap):
                        close_bitmap()
                    close_page = getattr(page, "close", None)
                    if callable(close_page):
                        close_page()
        finally:
            close_document = getattr(document, "close", None)
            if callable(close_document):
                close_document()
        return rendered


class PypdfEmbeddedImagePageRenderer:
    """Lower-fidelity fallback that extracts PDF embedded images, not whole pages."""

    name = "pypdf-embedded-image-renderer"
    version = "1"
    priority = 40

    def available(self) -> bool:
        try:
            import pypdf  # noqa: F401
        except ImportError:
            return False
        return True

    def supports(self, source: DocumentSource) -> bool:
        return source.data.lstrip().startswith(b"%PDF-") or source.suffix == ".pdf"

    def render(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[RenderedPage]:
        from pypdf import PdfReader

        requested = _page_filter(pages)
        reader = PdfReader(io.BytesIO(source.data))
        rendered: list[RenderedPage] = []
        for page_index, page in enumerate(reader.pages):
            page_number = page_index + 1
            if requested and page_number not in requested:
                continue
            try:
                images = list(page.images)
            except Exception:
                images = []
            for image_index, image in enumerate(images):
                data = bytes(getattr(image, "data", b"") or b"")
                if not data:
                    continue
                width = 0
                height = 0
                try:
                    from PIL import Image

                    value = Image.open(io.BytesIO(data))
                    width, height = int(value.width), int(value.height)
                except Exception:
                    pass
                rendered.append(
                    RenderedPage(
                        page=page_number,
                        image_index=image_index,
                        image_bytes=data,
                        width_px=width,
                        height_px=height,
                        source_locator=(
                            f"{source.filename or 'document.pdf'}#page={page_number};"
                            f"embedded_image={image_index}"
                        ),
                        renderer_name=self.name,
                        renderer_version=self.version,
                        render_method="pdf_embedded_image_fallback_not_full_page",
                    )
                )
        return rendered


class LibreOfficeDocumentPageRenderer:
    """Optional office-to-PDF conversion followed by PDFium page rendering."""

    name = "libreoffice-office-page-renderer"
    version = "1"
    priority = 100

    def __init__(self, dpi: int = 200, timeout_seconds: int = 90) -> None:
        self.dpi = max(72, min(600, int(dpi)))
        self.timeout_seconds = max(10, min(300, int(timeout_seconds)))

    def _binary(self) -> str:
        return shutil.which("libreoffice") or shutil.which("soffice") or ""

    def available(self) -> bool:
        return bool(self._binary()) and PdfiumPdfPageRenderer(self.dpi).available()

    def supports(self, source: DocumentSource) -> bool:
        return source.suffix in _OFFICE_SUFFIXES

    def render(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[RenderedPage]:
        binary = self._binary()
        if not binary:
            raise RuntimeError("LibreOffice executable is unavailable")
        safe_name = Path(source.filename or f"source{source.suffix or '.bin'}").name
        with tempfile.TemporaryDirectory(prefix="qualibug-render-") as directory:
            root = Path(directory)
            input_path = root / safe_name
            input_path.write_bytes(source.data)
            completed = subprocess.run(
                [
                    binary,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(root),
                    str(input_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
            pdf_path = root / f"{input_path.stem}.pdf"
            if completed.returncode != 0 or not pdf_path.exists():
                detail = (completed.stderr or completed.stdout).decode("utf-8", errors="replace")
                raise RuntimeError(f"LibreOffice conversion failed: {detail[:400]}")
            pdf_source = DocumentSource(
                source_id=source.source_id,
                filename=f"{input_path.stem}.pdf",
                data=pdf_path.read_bytes(),
                declared_mime="application/pdf",
            )
            rendered = PdfiumPdfPageRenderer(self.dpi).render(pdf_source, pages=pages)
            return [
                RenderedPage(
                    page=row.page,
                    image_index=row.image_index,
                    image_bytes=row.image_bytes,
                    width_px=row.width_px,
                    height_px=row.height_px,
                    source_locator=f"{source.filename}#rendered_page={row.page}",
                    renderer_name=self.name,
                    renderer_version=self.version,
                    render_method="office_to_pdf_then_pdfium_page_rasterized",
                    dpi=row.dpi,
                )
                for row in rendered
            ]


class PageRendererRegistry:
    def __init__(self, renderers: Iterable[PageRenderer] = ()) -> None:
        self._renderers: dict[str, PageRenderer] = {}
        for renderer in renderers:
            self.register(renderer)

    def register(self, renderer: PageRenderer) -> None:
        name = text(getattr(renderer, "name", ""))
        if not name:
            raise ValueError("page renderer name is required")
        if name in self._renderers:
            raise ValueError(f"page renderer already registered: {name}")
        self._renderers[name] = renderer

    def all(self) -> list[PageRenderer]:
        return sorted(
            self._renderers.values(),
            key=lambda row: (-int(getattr(row, "priority", 0)), text(getattr(row, "name", ""))),
        )

    def matching(self, source: DocumentSource) -> list[PageRenderer]:
        return [
            renderer
            for renderer in self.all()
            if renderer.available() and renderer.supports(source)
        ]

    def can_render(self, source: DocumentSource) -> bool:
        return bool(self.matching(source))

    def render(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> PageRenderBatch:
        target_pages = sorted(_page_filter(pages))
        errors: list[dict[str, Any]] = []
        attempted: list[str] = []
        for renderer in self.matching(source):
            attempted.append(renderer.name)
            try:
                rendered = renderer.render(source, pages=target_pages)
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
            receipt = {
                "schema": PAGE_RENDER_RECEIPT_SCHEMA,
                "status": "COMPLETE",
                "source_id": source.source_id,
                "filename": source.filename,
                "source_hash": source.content_hash,
                "renderer_name": renderer.name,
                "renderer_version": renderer.version,
                "target_pages": target_pages,
                "rendered_pages": sorted({row.page for row in rendered}),
                "rendered_image_count": len(rendered),
                "attempted_renderers": attempted,
                "error_count": len(errors),
                "errors": errors,
                "business_semantics_added": False,
            }
            return PageRenderBatch(tuple(rendered), receipt, tuple(errors))
        receipt = {
            "schema": PAGE_RENDER_RECEIPT_SCHEMA,
            "status": "BLOCKED",
            "source_id": source.source_id,
            "filename": source.filename,
            "source_hash": source.content_hash,
            "renderer_name": "",
            "renderer_version": "",
            "target_pages": target_pages,
            "rendered_pages": [],
            "rendered_image_count": 0,
            "attempted_renderers": attempted,
            "error_count": len(errors),
            "errors": errors,
            "reason_code": "PAGE_RENDERER_UNAVAILABLE_OR_FAILED",
            "business_semantics_added": False,
        }
        return PageRenderBatch((), receipt, tuple(errors))


def build_default_page_renderer_registry() -> PageRendererRegistry:
    return PageRendererRegistry(
        [
            RasterImagePageRenderer(),
            PdfiumPdfPageRenderer(),
            LibreOfficeDocumentPageRenderer(),
            PypdfEmbeddedImagePageRenderer(),
        ]
    )


__all__ = [
    "PAGE_RENDER_RECEIPT_SCHEMA",
    "RenderedPage",
    "PageRenderBatch",
    "PageRenderer",
    "RasterImagePageRenderer",
    "PdfiumPdfPageRenderer",
    "PypdfEmbeddedImagePageRenderer",
    "LibreOfficeDocumentPageRenderer",
    "PageRendererRegistry",
    "build_default_page_renderer_registry",
]
