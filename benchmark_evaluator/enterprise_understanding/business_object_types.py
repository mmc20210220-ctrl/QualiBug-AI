"""Evaluator-side business-object type Ground Truth and measurement."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

BUSINESS_OBJECT_MEASUREMENT_SCHEMA = (
    "qualibug.enterprise-understanding-business-object-measurement.v1"
)


@lru_cache(maxsize=1)
def _benchmark_authority() -> dict[str, Any]:
    """Reuse the product's object-benchmark authority lazily.

    The evaluator package must stay importable without loading the product
    facade (boundary contract). Schema constants and the measurement function
    are resolved only when an actual validation/evaluation runs, never at
    evaluator import time.
    """
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.business_object_benchmark import (  # noqa: E501
        ANNOTATION_SCOPE,
        GROUND_TRUTH_SCHEMA,
        OBJECT_TYPE,
        SUPPORTED_TYPES,
        evaluate_business_object_recognition,
    )

    return {
        "ANNOTATION_SCOPE": ANNOTATION_SCOPE,
        "GROUND_TRUTH_SCHEMA": GROUND_TRUTH_SCHEMA,
        "OBJECT_TYPE": OBJECT_TYPE,
        "SUPPORTED_TYPES": SUPPORTED_TYPES,
        "evaluate_business_object_recognition": evaluate_business_object_recognition,
    }


class BusinessObjectGroundTruthValidationError(ValueError):
    """Raised when evaluator-side object-type annotations are invalid."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _key(value: Any) -> str:
    return "".join(_text(value).split()).casefold()


def _labels(row: dict[str, Any]) -> list[str]:
    values: list[Any] = [row.get("label"), row.get("canonical_label")]
    for field in ("labels", "aliases"):
        if isinstance(row.get(field), list):
            values.extend(row.get(field) or [])
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        label = _text(value)
        identity = _key(label)
        if label and identity not in seen:
            seen.add(identity)
            result.append(label)
    return result


def _expected_business_object(row: dict[str, Any]) -> bool | None:
    authority = _benchmark_authority()
    object_type = authority["OBJECT_TYPE"]
    supported_types = authority["SUPPORTED_TYPES"]
    if "expected_business_object" in row:
        value = row.get("expected_business_object")
        return value if isinstance(value, bool) else None
    expected_type = _text(row.get("expected_type")).upper()
    return expected_type == object_type if expected_type in supported_types else None


def _semantic_roles(row: dict[str, Any], expected_object: bool) -> set[str]:
    object_type = _benchmark_authority()["OBJECT_TYPE"]
    values: list[Any] = [row.get("expected_type")]
    for field in ("semantic_roles", "allowed_context_roles"):
        if isinstance(row.get(field), list):
            values.extend(row.get(field) or [])
    roles = {_text(value).upper() for value in values if _text(value)}
    if expected_object:
        roles.add(object_type)
    if not roles:
        roles.add(object_type if expected_object else "OTHER_NON_OBJECT")
    return roles


def _ordered_roles(roles: set[str]) -> list[str]:
    object_type = _benchmark_authority()["OBJECT_TYPE"]
    return ([object_type] if object_type in roles else []) + sorted(roles - {object_type})


def validate_business_object_ground_truth(document: dict[str, Any]) -> dict[str, Any]:
    authority = _benchmark_authority()
    ground_truth_schema = authority["GROUND_TRUTH_SCHEMA"]
    annotation_scope = authority["ANNOTATION_SCOPE"]
    supported_types = authority["SUPPORTED_TYPES"]
    if not isinstance(document, dict):
        raise BusinessObjectGroundTruthValidationError(
            "business-object Ground Truth root must be an object"
        )
    if _text(document.get("schema")) != ground_truth_schema:
        raise BusinessObjectGroundTruthValidationError(
            f"schema must equal {ground_truth_schema}"
        )
    project_id = _text(document.get("project_id"))
    if not project_id:
        raise BusinessObjectGroundTruthValidationError("project_id is required")
    if _text(document.get("annotation_scope")) != annotation_scope:
        raise BusinessObjectGroundTruthValidationError(
            f"annotation_scope must equal {annotation_scope}"
        )
    if bool(document.get("ground_truth_generated_from_product_output")):
        raise BusinessObjectGroundTruthValidationError(
            "product output cannot generate business-object Ground Truth"
        )

    source_snapshot = _rows(document.get("source_snapshot"))
    if not source_snapshot:
        raise BusinessObjectGroundTruthValidationError(
            "source_snapshot must declare the frozen public source authority"
        )
    for index, row in enumerate(source_snapshot):
        if not _text(row.get("path")) or not _text(row.get("blob_sha")):
            raise BusinessObjectGroundTruthValidationError(
                f"source_snapshot[{index}] requires path and blob_sha"
            )

    labels = _rows(document.get("labels"))
    if not labels:
        raise BusinessObjectGroundTruthValidationError("labels must not be empty")

    seen_ids: set[str] = set()
    decisions: dict[str, bool] = {}
    roles_by_key: dict[str, set[str]] = {}
    normalized_rows: list[dict[str, Any]] = []
    for index, row in enumerate(labels):
        context = f"labels[{index}]"
        ground_truth_id = _text(row.get("ground_truth_id"))
        if not ground_truth_id:
            raise BusinessObjectGroundTruthValidationError(
                f"{context}: ground_truth_id is required"
            )
        if ground_truth_id in seen_ids:
            raise BusinessObjectGroundTruthValidationError(
                f"duplicate ground_truth_id: {ground_truth_id}"
            )
        seen_ids.add(ground_truth_id)
        if _text(row.get("annotation_status") or "CONFIRMED").upper() != "CONFIRMED":
            raise BusinessObjectGroundTruthValidationError(
                f"{context}: closed-world labels must be CONFIRMED"
            )

        row_labels = _labels(row)
        if not row_labels:
            raise BusinessObjectGroundTruthValidationError(
                f"{context}: canonical_label, labels or aliases is required"
            )
        expected_object = _expected_business_object(row)
        if expected_object is None:
            raise BusinessObjectGroundTruthValidationError(
                f"{context}: expected_business_object must be boolean or expected_type supported"
            )
        roles = _semantic_roles(row, expected_object)
        invalid_roles = sorted(roles - set(supported_types))
        if invalid_roles:
            raise BusinessObjectGroundTruthValidationError(
                f"{context}: unsupported semantic_roles {invalid_roles}"
            )

        source_refs = (
            [_text(value) for value in row.get("source_refs", []) if _text(value)]
            if isinstance(row.get("source_refs"), list)
            else []
        )
        source_locators = (
            [_text(value) for value in row.get("source_locators", []) if _text(value)]
            if isinstance(row.get("source_locators"), list)
            else []
        )
        if not source_refs and not source_locators:
            raise BusinessObjectGroundTruthValidationError(
                f"{context}: source_refs or source_locators is required"
            )

        for label in row_labels:
            identity = _key(label)
            prior = decisions.get(identity)
            if prior is not None and prior != expected_object:
                raise BusinessObjectGroundTruthValidationError(
                    f"{context}: contradictory object promotion annotation for {label}"
                )
            decisions[identity] = expected_object
            roles_by_key.setdefault(identity, set()).update(roles)

        normalized = dict(row)
        normalized["annotation_status"] = "CONFIRMED"
        normalized["expected_business_object"] = expected_object
        normalized["semantic_roles"] = _ordered_roles(roles)
        normalized_rows.append(normalized)

    object_count = sum(1 for value in decisions.values() if value)
    normalized_document = dict(document)
    normalized_document["labels"] = normalized_rows
    normalized_document["validation_receipt"] = {
        "status": "PASS",
        "project_id": project_id,
        "annotation_scope": _benchmark_authority()["ANNOTATION_SCOPE"],
        "closed_world": True,
        "label_row_count": len(normalized_rows),
        "normalized_label_count": len(decisions),
        "expected_object_label_count": object_count,
        "expected_non_object_label_count": len(decisions) - object_count,
        "polysemous_label_count": sum(
            1 for roles in roles_by_key.values() if len(roles) > 1
        ),
        "source_snapshot_count": len(source_snapshot),
        "generated_from_product_output": False,
        "model_writeback_allowed": False,
    }
    return normalized_document


def load_business_object_ground_truth(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BusinessObjectGroundTruthValidationError(
            f"business-object Ground Truth file not found: {source}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise BusinessObjectGroundTruthValidationError(
            f"business-object Ground Truth is not valid JSON: {source}: {exc}"
        ) from exc
    return validate_business_object_ground_truth(document)


def _recognition(product_asset: dict[str, Any]) -> dict[str, Any]:
    direct = product_asset.get("business_object_recognition")
    if isinstance(direct, dict):
        return direct
    model = product_asset.get("enterprise_understanding_model")
    if isinstance(model, dict):
        nested = model.get("business_object_recognition")
        if isinstance(nested, dict):
            return nested
    return {}


def _not_measured(reason_code: str, **details: Any) -> dict[str, Any]:
    result = {
        "schema": BUSINESS_OBJECT_MEASUREMENT_SCHEMA,
        "status": "NOT_MEASURED",
        "reason_code": reason_code,
        "quality_claim_allowed": False,
        "ground_truth_entered_product_runtime": False,
        "model_writeback_allowed": False,
    }
    result.update({key: value for key, value in details.items() if value not in (None, "", {})})
    return result


def evaluate_business_object_types(
    ground_truth: dict[str, Any] | None,
    product_asset: dict[str, Any],
) -> dict[str, Any]:
    if not ground_truth:
        return _not_measured("EVALUATOR_BUSINESS_OBJECT_GROUND_TRUTH_NOT_PROVIDED")
    validated = validate_business_object_ground_truth(ground_truth)
    validation_receipt = validated.get("validation_receipt") or {}
    recognition = _recognition(product_asset)
    if not recognition:
        return _not_measured(
            "PRODUCT_BUSINESS_OBJECT_RECOGNITION_MISSING",
            ground_truth_validation_receipt=validation_receipt,
        )

    measured = _benchmark_authority()["evaluate_business_object_recognition"](
        recognition, validated
    )
    product_benchmark_schema = measured.get("schema")
    result = dict(measured)
    result.update(
        {
            "schema": BUSINESS_OBJECT_MEASUREMENT_SCHEMA,
            "product_business_object_benchmark_schema": product_benchmark_schema,
            "ground_truth_validation_receipt": validation_receipt,
            "metric_authority": "evaluator_side_human_source_backed_ground_truth",
            "ground_truth_entered_product_runtime": False,
            "model_writeback_allowed": False,
        }
    )
    return result


__all__ = [
    "BUSINESS_OBJECT_MEASUREMENT_SCHEMA",
    "BusinessObjectGroundTruthValidationError",
    "evaluate_business_object_types",
    "load_business_object_ground_truth",
    "validate_business_object_ground_truth",
]
