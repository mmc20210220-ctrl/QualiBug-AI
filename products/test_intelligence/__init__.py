"""Test Intelligence product domain."""

from .coverage import (
    ANALYSIS_SCHEMA,
    COVERAGE_QUALITY_CLAIM,
    COVERAGE_SCHEMA,
    analyze_test_intelligence,
    build_coverage_projection,
)
from .obligations import OBLIGATION_SCHEMA, PROJECTION_SCHEMA, project_test_obligations
from .product import (
    MANIFEST,
    TestIntelligenceManifest,
    TestIntelligenceStatus,
    TestObligationKind,
    get_product_manifest,
)

__all__ = [
    "ANALYSIS_SCHEMA",
    "COVERAGE_QUALITY_CLAIM",
    "COVERAGE_SCHEMA",
    "OBLIGATION_SCHEMA",
    "PROJECTION_SCHEMA",
    "MANIFEST",
    "TestIntelligenceManifest",
    "TestIntelligenceStatus",
    "TestObligationKind",
    "analyze_test_intelligence",
    "build_coverage_projection",
    "get_product_manifest",
    "project_test_obligations",
]
