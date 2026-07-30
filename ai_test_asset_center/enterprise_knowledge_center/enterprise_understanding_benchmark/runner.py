"""Benchmark orchestration over the existing enterprise understanding asset."""
from __future__ import annotations

from typing import Any

from .alignment import align_enterprise_understanding
from .ground_truth import SUPPORTED_COLLECTIONS, validate_ground_truth
from .metrics import calculate_benchmark_metrics
from .report import write_benchmark_outputs
from .root_cause import analyse_miss_root_causes

BENCHMARK_RESULT_SCHEMA = "qualibug.enterprise-understanding-benchmark-result.v1"


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _model(asset: dict[str, Any]) -> dict[str, Any]:
    model = asset.get("enterprise_understanding_model")
    return model if isinstance(model, dict) else asset


def run_benchmark(
    ground_truth: dict[str, Any],
    asset: dict[str, Any],
    *,
    output_dir: str | None = None,
) -> dict[str, Any]:
    validated = validate_ground_truth(ground_truth)
    model = _model(asset)
    model_available = bool(model) and any(
        isinstance(model.get(key), list)
        for key in (
            "business_objects",
            "actors",
            "operations",
            "object_relations",
            "lifecycles",
            "rules",
            "business_behaviors",
        )
    )
    if not model_available:
        result = {
            "schema": BENCHMARK_RESULT_SCHEMA,
            "project_id": validated.get("project_id"),
            "status": "BENCHMARK_ENTERPRISE_UNDERSTANDING_MODEL_MISSING",
            "ground_truth_status": validated.get("validation_receipt", {}).get("status"),
            "ground_truth_summary": {
                "counts": {
                    collection: len(_rows(validated.get(collection)))
                    for collection in SUPPORTED_COLLECTIONS
                },
                "validation_receipt": validated.get("validation_receipt") or {},
            },
            "alignment": {"alignments": [], "model_writeback_allowed": False},
            "metrics": {},
            "root_cause_analysis": {
                "highest_impact_root_cause": "SOURCE_NOT_PARSED",
                "repair_policy": "BUILD_THE_EXISTING_ENTERPRISE_UNDERSTANDING_MAINLINE_ASSET",
                "model_writeback_allowed": False,
            },
            "model_writeback_allowed": False,
        }
        if output_dir:
            result["output_files"] = write_benchmark_outputs(result, output_dir)
        return result

    alignment = align_enterprise_understanding(validated, asset)
    metrics = calculate_benchmark_metrics(validated, alignment)
    root_causes = analyse_miss_root_causes(validated, asset, alignment)
    ground_truth_status = validated.get("validation_receipt", {}).get("status")
    status = (
        "BENCHMARK_GROUND_TRUTH_INCOMPLETE"
        if ground_truth_status != "PASS"
        else "PASS"
    )
    result = {
        "schema": BENCHMARK_RESULT_SCHEMA,
        "project_id": validated.get("project_id"),
        "status": status,
        "ground_truth_status": ground_truth_status,
        "ground_truth_summary": {
            "counts": {
                collection: len(_rows(validated.get(collection)))
                for collection in SUPPORTED_COLLECTIONS
            },
            "scope_complete": bool(validated.get("scope_complete")),
            "validation_receipt": validated.get("validation_receipt") or {},
        },
        "alignment": alignment,
        "metrics": metrics,
        "root_cause_analysis": root_causes,
        "next_repair_target": root_causes.get("highest_impact_root_cause") or "",
        "repair_policy": "FIX_THE_EARLIEST_EXISTING_MAINLINE_MODULE_NOT_A_DOWNSTREAM_PATCH",
        "model_writeback_allowed": False,
        "benchmark_can_confirm_business_facts": False,
    }
    if output_dir:
        result["output_files"] = write_benchmark_outputs(result, output_dir)
    return result


__all__ = ["BENCHMARK_RESULT_SCHEMA", "run_benchmark"]
