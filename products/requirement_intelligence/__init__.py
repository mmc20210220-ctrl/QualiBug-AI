"""Requirement Intelligence product entry.

The package is intentionally thin. Existing QualiBug ingestion, enterprise
understanding, evidence, and persistence authorities are reused through explicit
adapters as the product is implemented.
"""

from .analysis import ANALYSIS_SCHEMA, READINESS_SCHEMA, analyze_knowledge_asset
from .product import (
    MANIFEST,
    ProductStatus,
    RequirementFindingKind,
    RequirementIntelligenceManifest,
    get_product_manifest,
)

__all__ = [
    "ANALYSIS_SCHEMA",
    "READINESS_SCHEMA",
    "MANIFEST",
    "ProductStatus",
    "RequirementFindingKind",
    "RequirementIntelligenceManifest",
    "analyze_knowledge_asset",
    "get_product_manifest",
]
