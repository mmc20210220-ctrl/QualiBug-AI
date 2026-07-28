"""Format-agnostic enterprise document ingestion."""
from .contract import AdapterMatch, DocumentAdapter, DocumentSource
from .pipeline import build_document_structure_ir
from .planner import plan_document_parsing
from .registry import DocumentAdapterRegistry, build_default_registry

__all__ = [
    "AdapterMatch",
    "DocumentAdapter",
    "DocumentSource",
    "DocumentAdapterRegistry",
    "build_default_registry",
    "plan_document_parsing",
    "build_document_structure_ir",
]
