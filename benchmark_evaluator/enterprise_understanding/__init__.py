"""Evaluator-only enterprise-understanding benchmark.

Human Ground Truth stays outside ``ai_test_asset_center``. This package only reads the product's
persisted enterprise-understanding artifact and never writes into product runtime state.
"""
from .alignment import align_enterprise_understanding
from .ground_truth import (
    GROUND_TRUTH_SCHEMA,
    GroundTruthValidationError,
    load_ground_truth,
    validate_ground_truth,
)
from .ingestion_evidence import (
    INGESTION_EVIDENCE_SCHEMA,
    measure_ingestion_evidence,
)
from .metrics import calculate_benchmark_metrics
from .root_cause import analyse_miss_root_causes
from .runner import run_benchmark

__all__ = [
    "GROUND_TRUTH_SCHEMA",
    "INGESTION_EVIDENCE_SCHEMA",
    "GroundTruthValidationError",
    "load_ground_truth",
    "validate_ground_truth",
    "align_enterprise_understanding",
    "measure_ingestion_evidence",
    "calculate_benchmark_metrics",
    "analyse_miss_root_causes",
    "run_benchmark",
]
