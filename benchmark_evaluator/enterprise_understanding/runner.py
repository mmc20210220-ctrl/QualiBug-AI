"""Evaluator-side orchestration for enterprise-understanding measurement."""
from __future__ import annotations

from typing import Any

from benchmark_evaluator.scored_run_comparison import _fingerprint

from .alignment import align_enterprise_understanding
from .ground_truth import SUPPORTED_COLLECTIONS, validate_ground_truth
from .metrics import calculate_benchmark_metrics
from .report import write_benchmark_outputs
from .root_cause import analyse_miss_root_causes

BENCHMARK_RESULT_SCHEMA = "qualibug.enterprise-understanding-benchmark-result.v1"
WORKFLOW_RECEIPT_SCHEMA = "qualibug.enterprise-understanding-benchmark-workflow.v1"


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _model(asset: dict[str, Any]) -> dict[str, Any]:
    value = asset.get("enterprise_understanding_model")
    return value if isinstance(value, dict) else asset


def run_benchmark(
    ground_truth: dict[str, Any],
    product_asset: dict[str, Any],
    *,
    output_dir: str | None = None,
) -> dict[str, Any]:
    validated = validate_ground_truth(ground_truth)
    model = _model(product_asset)
    ground_truth_fingerprint = _fingerprint(validated)
    product_asset_fingerprint = _fingerprint(product_asset)
    model_available = bool(model) and any(
        isinstance(model.get(key), list)
        for key in (
            "business_objects", "actors", "operations", "object_relations", "lifecycles",
            "rules", "business_behaviors",
        )
    )
    ground_truth_summary = {
        "counts": {
            collection: len(_rows(validated.get(collection)))
            for collection in SUPPORTED_COLLECTIONS
        },
        "scope_complete": bool(validated.get("scope_complete")),
        "validation_receipt": validated.get("validation_receipt") or {},
        "ground_truth_fingerprint": ground_truth_fingerprint,
    }
    if not model_available:
        status = "BENCHMARK_ENTERPRISE_UNDERSTANDING_MODEL_MISSING"
        result = {
            "schema": BENCHMARK_RESULT_SCHEMA,
            "project_id": validated.get("project_id"),
            "status": status,
            "ground_truth_status": validated.get("validation_receipt", {}).get("status"),
            "ground_truth_fingerprint": ground_truth_fingerprint,
            "product_asset_fingerprint": product_asset_fingerprint,
            "ground_truth_summary": ground_truth_summary,
            "alignment": {"alignments": [], "model_writeback_allowed": False},
            "metrics": {},
            "root_cause_analysis": {
                "highest_impact_root_cause": "SOURCE_NOT_PARSED",
                "repair_policy": "BUILD_THE_EXISTING_ENTERPRISE_UNDERSTANDING_MAINLINE_ASSET",
                "model_writeback_allowed": False,
            },
            "model_writeback_allowed": False,
        }
    else:
        alignment = align_enterprise_understanding(validated, product_asset)
        metrics = calculate_benchmark_metrics(validated, alignment)
        root_causes = analyse_miss_root_causes(validated, product_asset, alignment)
        ground_truth_status = validated.get("validation_receipt", {}).get("status")
        status = "BENCHMARK_GROUND_TRUTH_INCOMPLETE" if ground_truth_status != "PASS" else "PASS"
        result = {
            "schema": BENCHMARK_RESULT_SCHEMA,
            "project_id": validated.get("project_id"),
            "status": status,
            "ground_truth_status": ground_truth_status,
            "ground_truth_fingerprint": ground_truth_fingerprint,
            "product_asset_fingerprint": product_asset_fingerprint,
            "ground_truth_summary": ground_truth_summary,
            "alignment": alignment,
            "metrics": metrics,
            "root_cause_analysis": root_causes,
            "next_repair_target": root_causes.get("highest_impact_root_cause") or "",
            "repair_policy": "FIX_THE_EARLIEST_EXISTING_MAINLINE_MODULE_NOT_A_DOWNSTREAM_PATCH",
            "model_writeback_allowed": False,
            "product_model_can_self_label_true_or_false": False,
        }
    workflow_receipt = {
        "schema_version": WORKFLOW_RECEIPT_SCHEMA,
        "status": result.get("status"),
        "project_id": result.get("project_id"),
        "ground_truth_fingerprint": ground_truth_fingerprint,
        "product_asset_fingerprint": product_asset_fingerprint,
        "metric_authority": "evaluator_side_human_source_backed_ground_truth",
        "hidden_ground_truth_entered_product_runtime": False,
        "model_writeback_allowed": False,
    }
    workflow_receipt["receipt_fingerprint"] = _fingerprint(workflow_receipt)
    result["workflow_receipt"] = workflow_receipt
    result["result_fingerprint"] = _fingerprint(
        {key: value for key, value in result.items() if key not in {"output_files", "result_fingerprint"}}
    )
    if output_dir:
        result["output_files"] = write_benchmark_outputs(result, output_dir)
    return result


__all__ = [
    "BENCHMARK_RESULT_SCHEMA", "WORKFLOW_RECEIPT_SCHEMA", "run_benchmark"
]
