"""Evaluator-only enterprise-understanding benchmark.

Human Ground Truth stays outside ``ai_test_asset_center``. This package only reads the product's
persisted enterprise-understanding artifact and never writes into product runtime state.
"""
from .alignment import align_enterprise_understanding
from .document_ground_truth import (
    DOCUMENT_GROUND_TRUTH_KEY,
    DOCUMENT_GROUND_TRUTH_SCHEMA,
    DocumentGroundTruthValidationError,
    evaluate_document_ground_truth,
    validate_document_ground_truth,
)
from .fact_slot_document import validate_business_fact_slot_document
from .fact_slot_ground_truth import (
    FACT_SLOT_GROUND_TRUTH_SCHEMA,
    FactSlotGroundTruthValidationError,
    validate_business_fact_slot_row,
)
from .fact_slots import (
    FACT_SLOT_MEASUREMENT_SCHEMA,
    evaluate_business_fact_slots,
)
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
    "DOCUMENT_GROUND_TRUTH_SCHEMA",
    "DOCUMENT_GROUND_TRUTH_KEY",
    "INGESTION_EVIDENCE_SCHEMA",
    "FACT_SLOT_GROUND_TRUTH_SCHEMA",
    "FACT_SLOT_MEASUREMENT_SCHEMA",
    "GroundTruthValidationError",
    "DocumentGroundTruthValidationError",
    "FactSlotGroundTruthValidationError",
    "load_ground_truth",
    "validate_ground_truth",
    "validate_document_ground_truth",
    "evaluate_document_ground_truth",
    "validate_business_fact_slot_row",
    "validate_business_fact_slot_document",
    "evaluate_business_fact_slots",
    "align_enterprise_understanding",
    "measure_ingestion_evidence",
    "calculate_benchmark_metrics",
    "analyse_miss_root_causes",
    "run_benchmark",
]
