"""Evaluator-side orchestration for enterprise-understanding measurement."""
from __future__ import annotations

from typing import Any

from benchmark_evaluator.scored_run_comparison import _fingerprint

from .alignment import align_enterprise_understanding
from .business_object_types import evaluate_business_object_types
from .document_ground_truth import DOCUMENT_GROUND_TRUTH_KEY
from .fact_slot_document import validate_business_fact_slot_document
from .fact_slots import evaluate_business_fact_slots
from .ground_truth import SUPPORTED_COLLECTIONS, validate_ground_truth
from .ingestion_evidence import measure_ingestion_evidence
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


def _business_object_repair_target(measurement: dict[str, Any]) -> str:
    status = str(measurement.get("status") or "").strip()
    reason = str(measurement.get("reason_code") or "").strip()
    metrics = (
        measurement.get("metrics")
        if isinstance(measurement.get("metrics"), dict)
        else {}
    )
    if status != "MEASURED":
        if reason == "BUSINESS_OBJECT_GROUND_TRUTH_CANDIDATE_UNIVERSE_INCOMPLETE":
            return "COMPLETE_OBJECT_TYPE_GROUND_TRUTH_CANDIDATE_UNIVERSE"
        if reason == "PRODUCT_BUSINESS_OBJECT_RECOGNITION_MISSING":
            return "PERSIST_BUSINESS_OBJECT_RECOGNITION_IN_FINAL_ASSET"
        return ""
    false_positive = int(metrics.get("false_positive_object_count") or 0)
    false_negative = int(metrics.get("false_negative_object_count") or 0)
    if false_positive > false_negative:
        return "BUSINESS_OBJECT_OVERPROMOTION"
    if false_negative:
        return "BUSINESS_OBJECT_MENTION_OR_PROMOTION_RECALL"
    if int(metrics.get("silent_object_error_count") or 0):
        return "BUSINESS_OBJECT_UNSURFACED_ERROR"
    return ""


def run_benchmark(
    ground_truth: dict[str, Any],
    product_asset: dict[str, Any],
    *,
    business_object_ground_truth: dict[str, Any] | None = None,
    output_dir: str | None = None,
) -> dict[str, Any]:
    validated = validate_business_fact_slot_document(validate_ground_truth(ground_truth))
    model = _model(product_asset)
    ground_truth_fingerprint = _fingerprint(validated)
    product_asset_fingerprint = _fingerprint(product_asset)
    ingestion_evidence = measure_ingestion_evidence(product_asset, validated)
    fact_slot_measurement = evaluate_business_fact_slots(validated, product_asset)
    business_object_measurement = evaluate_business_object_types(
        business_object_ground_truth, product_asset
    )
    business_object_metrics = (
        business_object_measurement.get("metrics")
        if isinstance(business_object_measurement.get("metrics"), dict)
        else {}
    )
    business_object_repair_target = _business_object_repair_target(
        business_object_measurement
    )
    ingestion_summary = (
        ingestion_evidence.get("summary")
        if isinstance(ingestion_evidence.get("summary"), dict)
        else {}
    )
    document_profile = (
        validated.get(DOCUMENT_GROUND_TRUTH_KEY)
        if isinstance(validated.get(DOCUMENT_GROUND_TRUTH_KEY), dict)
        else {}
    )
    document_receipt = (
        document_profile.get("validation_receipt")
        if isinstance(document_profile.get("validation_receipt"), dict)
        else {}
    )
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
    ground_truth_summary = {
        "counts": {
            collection: len(_rows(validated.get(collection)))
            for collection in SUPPORTED_COLLECTIONS
        },
        "scope_complete": bool(validated.get("scope_complete")),
        "document_ingestion_ground_truth": {
            "declared": bool(document_profile),
            "scope_complete": bool(document_profile.get("scope_complete")),
            "counts": document_receipt.get("counts") or {},
            "validation_status": document_receipt.get("status") or "NOT_DECLARED",
        },
        "business_fact_slot_ground_truth": {
            "status": fact_slot_measurement.get("status"),
            "annotated_fact_count": (
                fact_slot_measurement.get("metrics", {}).get("annotated_fact_count")
                if isinstance(fact_slot_measurement.get("metrics"), dict)
                else 0
            ),
            "contract_validated": bool(
                (validated.get("validation_receipt") or {}).get(
                    "business_fact_slot_contract_validated"
                )
            ),
            "generated_from_product_output": False,
        },
        "business_object_type_ground_truth": {
            "declared": bool(business_object_ground_truth),
            "status": business_object_measurement.get("status"),
            "reason_code": business_object_measurement.get("reason_code"),
            "validation_receipt": business_object_measurement.get(
                "ground_truth_validation_receipt"
            )
            or {},
            "generated_from_product_output": False,
        },
        "validation_receipt": validated.get("validation_receipt") or {},
        "ground_truth_fingerprint": ground_truth_fingerprint,
        "business_object_ground_truth_fingerprint": (
            _fingerprint(business_object_ground_truth)
            if business_object_ground_truth
            else ""
        ),
    }
    next_ingestion_target = (
        ingestion_summary.get("next_five_of_five_gap")
        or ingestion_summary.get("highest_impact_gap")
        or ""
    )
    common_measurements = {
        "business_fact_slot_measurement": fact_slot_measurement,
        "business_object_type_measurement": business_object_measurement,
        "ingestion_evidence_measurement": ingestion_evidence,
        "next_business_object_repair_target": business_object_repair_target,
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
            **common_measurements,
            "next_ingestion_repair_target": next_ingestion_target,
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
            "ground_truth_fingerprint": ground_truth_fingerprint,
            "product_asset_fingerprint": product_asset_fingerprint,
            "ground_truth_summary": ground_truth_summary,
            "alignment": alignment,
            "metrics": metrics,
            **common_measurements,
            "next_ingestion_repair_target": next_ingestion_target,
            "root_cause_analysis": root_causes,
            "next_repair_target": (
                business_object_repair_target
                or root_causes.get("highest_impact_root_cause")
                or ""
            ),
            "repair_policy": "FIX_THE_EARLIEST_EXISTING_MAINLINE_MODULE_NOT_A_DOWNSTREAM_PATCH",
            "model_writeback_allowed": False,
            "product_model_can_self_label_true_or_false": False,
        }
    document_measurement = (
        ingestion_evidence.get("document_ground_truth_measurement")
        if isinstance(ingestion_evidence.get("document_ground_truth_measurement"), dict)
        else {}
    )
    fact_slot_metrics = (
        fact_slot_measurement.get("metrics")
        if isinstance(fact_slot_measurement.get("metrics"), dict)
        else {}
    )
    workflow_receipt = {
        "schema_version": WORKFLOW_RECEIPT_SCHEMA,
        "status": result.get("status"),
        "project_id": result.get("project_id"),
        "ground_truth_fingerprint": ground_truth_fingerprint,
        "business_object_ground_truth_fingerprint": (
            _fingerprint(business_object_ground_truth)
            if business_object_ground_truth
            else ""
        ),
        "product_asset_fingerprint": product_asset_fingerprint,
        "metric_authority": "evaluator_side_human_source_backed_ground_truth",
        "ingestion_evidence_measurement_status": ingestion_evidence.get("status"),
        "ingestion_evidence_highest_impact_gap": ingestion_summary.get(
            "highest_impact_gap"
        ),
        "document_ground_truth_measurement_status": document_measurement.get("status"),
        "document_ground_truth_highest_impact_gap": document_measurement.get(
            "highest_impact_gap"
        ),
        "business_fact_slot_contract_validated": bool(
            (validated.get("validation_receipt") or {}).get(
                "business_fact_slot_contract_validated"
            )
        ),
        "business_fact_slot_measurement_status": fact_slot_measurement.get("status"),
        "business_fact_slot_exact_accuracy": fact_slot_metrics.get("slot_exact_accuracy"),
        "business_fact_exact_rate": fact_slot_metrics.get("exact_fact_rate"),
        "p0_business_fact_exact_recall": fact_slot_metrics.get("p0_exact_fact_recall"),
        "business_object_type_measurement_status": business_object_measurement.get(
            "status"
        ),
        "business_object_type_measurement_reason": business_object_measurement.get(
            "reason_code"
        ),
        "business_object_quality_claim_allowed": bool(
            business_object_measurement.get("quality_claim_allowed")
        ),
        "business_object_type_precision": business_object_metrics.get(
            "object_type_precision"
        ),
        "business_object_type_recall": business_object_metrics.get(
            "object_type_recall"
        ),
        "business_object_type_f1": business_object_metrics.get("object_type_f1"),
        "business_object_overpromotion_rate": business_object_metrics.get(
            "object_overpromotion_rate"
        ),
        "business_object_miss_rate": business_object_metrics.get("object_miss_rate"),
        "business_object_silent_error_count": business_object_metrics.get(
            "silent_object_error_count"
        ),
        "next_business_object_repair_target": business_object_repair_target,
        "next_ingestion_repair_target": next_ingestion_target,
        "product_ingestion_receipts_are_ground_truth": False,
        "document_ground_truth_generated_from_product_output": False,
        "business_fact_ground_truth_generated_from_product_output": False,
        "business_object_ground_truth_generated_from_product_output": False,
        "business_object_ground_truth_entered_product_runtime": False,
        "hidden_ground_truth_entered_product_runtime": False,
        "model_writeback_allowed": False,
    }
    workflow_receipt["receipt_fingerprint"] = _fingerprint(workflow_receipt)
    result["workflow_receipt"] = workflow_receipt
    result["result_fingerprint"] = _fingerprint(
        {
            key: value
            for key, value in result.items()
            if key not in {"output_files", "result_fingerprint"}
        }
    )
    if output_dir:
        result["output_files"] = write_benchmark_outputs(result, output_dir)
    return result


__all__ = ["BENCHMARK_RESULT_SCHEMA", "WORKFLOW_RECEIPT_SCHEMA", "run_benchmark"]
