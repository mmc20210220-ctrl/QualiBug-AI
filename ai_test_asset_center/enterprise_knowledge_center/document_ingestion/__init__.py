"""Format-agnostic enterprise document ingestion."""
from .advanced_visual_table_providers import (
    CompositeVisualTableProvider,
    MergedCellRuledGridVisualTableProvider,
    TesseractWordLayoutProvider,
    TextAlignedVisualTableProvider,
    WordLayoutProvider,
    build_default_advanced_visual_table_provider,
)
from .contract import (
    AdapterMatch,
    DocumentAdapter,
    DocumentSource,
    SupplementalContext,
)
from .decision_matrix_candidate_gate import (
    DECISION_MATRIX_GATE_SCHEMA,
    apply_decision_matrix_candidate_gate,
)
from .evidence_closure import (
    EVIDENCE_CLOSURE_SCHEMA,
    apply_document_evidence_closure,
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
from .office_adapters import PresentationDocumentAdapter, SpreadsheetDocumentAdapter
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
from .visual_table_adapter import (
    RuledGridVisualTableProvider,
    VisualTableProvider,
    VisualTableSupplementalAdapter,
)
from .visual_table_continuation import (
    TABLE_CONTINUATION_SCHEMA,
    apply_visual_table_continuations,
)
from .visual_table_provider_gate import GeometryFormalEnforcingVisualTableProvider
from .visual_table_semantic_candidates import (
    TABLE_SEMANTIC_CANDIDATE_SCHEMA,
    apply_visual_table_semantic_candidates,
)
from .visual_table_semantic_normalizer import (
    SEMANTIC_NORMALIZATION_SCHEMA,
    normalize_visual_table_semantic_candidates,
)
from .visual_table_semantic_validation import (
    SEMANTIC_VALIDATION_SCHEMA,
    validate_visual_table_semantic_candidates,
)

__all__ = [
    "AdapterMatch",
    "DocumentAdapter",
    "DocumentSource",
    "SupplementalContext",
    "OcrProvider",
    "OcrSupplementalAdapter",
    "TesseractOcrProvider",
    "SpreadsheetDocumentAdapter",
    "PresentationDocumentAdapter",
    "EVIDENCE_CLOSURE_SCHEMA",
    "apply_document_evidence_closure",
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
    "VisualTableProvider",
    "RuledGridVisualTableProvider",
    "WordLayoutProvider",
    "TesseractWordLayoutProvider",
    "MergedCellRuledGridVisualTableProvider",
    "TextAlignedVisualTableProvider",
    "CompositeVisualTableProvider",
    "GeometryFormalEnforcingVisualTableProvider",
    "build_default_advanced_visual_table_provider",
    "VisualTableSupplementalAdapter",
    "TABLE_CONTINUATION_SCHEMA",
    "apply_visual_table_continuations",
    "TABLE_SEMANTIC_CANDIDATE_SCHEMA",
    "apply_visual_table_semantic_candidates",
    "SEMANTIC_NORMALIZATION_SCHEMA",
    "normalize_visual_table_semantic_candidates",
    "SEMANTIC_VALIDATION_SCHEMA",
    "validate_visual_table_semantic_candidates",
    "DECISION_MATRIX_GATE_SCHEMA",
    "apply_decision_matrix_candidate_gate",
    "DocumentAdapterRegistry",
    "build_default_registry",
    "plan_document_parsing",
    "plan_deferred_supplementals",
    "build_document_structure_ir",
]
