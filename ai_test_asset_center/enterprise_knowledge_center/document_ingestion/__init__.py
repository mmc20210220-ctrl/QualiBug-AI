"""Format-agnostic enterprise document ingestion."""
from .contract import (
    AdapterMatch,
    DocumentAdapter,
    DocumentSource,
    SupplementalContext,
)
from .ocr_adapter import OcrProvider, TesseractOcrProvider
from .page_rendering import (
    LibreOfficeDocumentPageRenderer,
    PageRenderBatch,
    PageRendererRegistry,
    PdfiumPdfPageRenderer,
    PypdfEmbeddedImagePageRenderer,
    RasterImagePageRenderer,
    RenderedPage,
    build_default_page_renderer_registry,
)
from .pipeline import build_document_structure_ir
from .planner import plan_deferred_supplementals, plan_document_parsing
from .registry import DocumentAdapterRegistry, build_default_registry
from .rendered_ocr_adapter import OcrSupplementalAdapter

__all__ = [
    "AdapterMatch",
    "DocumentAdapter",
    "DocumentSource",
    "SupplementalContext",
    "OcrProvider",
    "OcrSupplementalAdapter",
    "TesseractOcrProvider",
    "RenderedPage",
    "PageRenderBatch",
    "PageRendererRegistry",
    "RasterImagePageRenderer",
    "PdfiumPdfPageRenderer",
    "PypdfEmbeddedImagePageRenderer",
    "LibreOfficeDocumentPageRenderer",
    "build_default_page_renderer_registry",
    "DocumentAdapterRegistry",
    "build_default_registry",
    "plan_document_parsing",
    "plan_deferred_supplementals",
    "build_document_structure_ir",
]
