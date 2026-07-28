"""Format-agnostic enterprise document ingestion."""
from .contract import (
    AdapterMatch,
    DocumentAdapter,
    DocumentSource,
    SupplementalContext,
)
from .image_decoding import (
    CairoSvgImageDecoder,
    DecodedImageFrame,
    ImageDecodeBatch,
    ImageDecoder,
    ImageDecoderRegistry,
    PillowImageDecoder,
    RawpyCameraImageDecoder,
    build_default_image_decoder_registry,
    sniff_image_source,
)
from .ocr_adapter import OcrProvider, TesseractOcrProvider
from .page_render_registry import PageRendererRegistry, build_default_page_renderer_registry
from .page_rendering import (
    LibreOfficeDocumentPageRenderer,
    PageRenderBatch,
    PdfiumPdfPageRenderer,
    PypdfEmbeddedImagePageRenderer,
    RasterImagePageRenderer,
    RenderedPage,
)
from .pipeline import build_document_structure_ir
from .planner import plan_deferred_supplementals, plan_document_parsing
from .registry import DocumentAdapterRegistry, build_default_registry
from .rendered_ocr_adapter import OcrSupplementalAdapter
from .universal_image_renderer import UniversalImagePageRenderer

__all__ = [
    "AdapterMatch",
    "DocumentAdapter",
    "DocumentSource",
    "SupplementalContext",
    "OcrProvider",
    "OcrSupplementalAdapter",
    "TesseractOcrProvider",
    "DecodedImageFrame",
    "ImageDecodeBatch",
    "ImageDecoder",
    "ImageDecoderRegistry",
    "PillowImageDecoder",
    "CairoSvgImageDecoder",
    "RawpyCameraImageDecoder",
    "UniversalImagePageRenderer",
    "build_default_image_decoder_registry",
    "sniff_image_source",
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
