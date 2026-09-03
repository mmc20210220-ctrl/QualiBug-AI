"""Requirement Intelligence product entry.

The package is intentionally thin. Existing QualiBug ingestion, enterprise
understanding, evidence, and persistence authorities are reused through explicit
adapters as the product is implemented.
"""

from .product import (
    MANIFEST,
    ProductStatus,
    RequirementFindingKind,
    RequirementIntelligenceManifest,
    get_product_manifest,
)

__all__ = [
    "MANIFEST",
    "ProductStatus",
    "RequirementFindingKind",
    "RequirementIntelligenceManifest",
    "get_product_manifest",
]
