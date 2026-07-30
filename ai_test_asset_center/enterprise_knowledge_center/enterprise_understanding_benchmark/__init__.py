"""Measurement-only benchmark for the existing enterprise understanding asset.

This package never builds, repairs, confirms, or writes enterprise business facts. It compares
human-authored Ground Truth with the one existing ``enterprise_understanding_model`` and reports
the earliest visible understanding breakpoints.
"""
from .alignment import align_enterprise_understanding
from .ground_truth import (
    GROUND_TRUTH_SCHEMA,
    GroundTruthValidationError,
    load_ground_truth,
    validate_ground_truth,
)
from .metrics import calculate_benchmark_metrics
from .root_cause import analyse_miss_root_causes
from .runner import run_benchmark

__all__ = [
    "GROUND_TRUTH_SCHEMA",
    "GroundTruthValidationError",
    "load_ground_truth",
    "validate_ground_truth",
    "align_enterprise_understanding",
    "calculate_benchmark_metrics",
    "analyse_miss_root_causes",
    "run_benchmark",
]
