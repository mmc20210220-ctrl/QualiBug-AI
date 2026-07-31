"""Ground Truth contract for evaluator-side enterprise-understanding measurement.

Ground Truth is human-authored or explicitly source-backed. It is never generated from the current
model and is never written back into the enterprise knowledge asset.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .document_ground_truth import (
    DOCUMENT_GROUND_TRUTH_KEY,
    DocumentGroundTruthValidationError,
    validate_document_ground_truth,
)

GROUND_TRUTH_SCHEMA = "qualibug.enterprise-understanding-ground-truth.v1"
SUPPORTED_COLLECTIONS = (
    "business_objects",
    "actors",
    "operations",
    "object_relations",
    "lifecycles",
    "state_transitions",
    "business_rules",
    "business_behaviors",
    "conflicts",
    "expected_unknowns",
    "bug_dependencies",
)
CRITICALITIES = {"P0", "P1", "P2", "P3"}
ANNOTATION_STATUSES = {"CONFIRMED", "DRAFT", "INCOMPLETE", "REJECTED"}


class GroundTruthValidationError(ValueError):
    """Raised when benchmark annotations are structurally invalid or incomplete."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _require_text(row: dict[str, Any], field: str, *, context: str) -> str:
    value = _text(row.get(field))
    if not value:
        raise GroundTruthValidationError(f"{context}: missing required field {field}")
    return value


def _validate_entry(row: dict[str, Any], *, collection: str, index: int) -> None:
    context = f"{collection}[{index}]"
    _require_text(row, "ground_truth_id", context=context)
    status = _text(row.get("annotation_status") or "CONFIRMED").upper()
    if status not in ANNOTATION_STATUSES:
        raise GroundTruthValidationError(
            f"{context}: annotation_status must be one of {sorted(ANNOTATION_STATUSES)}"
        )
    criticality = _text(row.get("criticality") or "P2").upper()
    if criticality not in CRITICALITIES:
        raise GroundTruthValidationError(
            f"{context}: criticality must be one of {sorted(CRITICALITIES)}"
        )
    source_refs = row.get("source_refs")
    source_locators = row.get("source_locators")
    if status == "CONFIRMED" and not (
        isinstance(source_refs, list) and source_refs
    ) and not (isinstance(source_locators, list) and source_locators):
        raise GroundTruthValidationError(
            f"{context}: confirmed annotation requires source_refs or source_locators"
        )
    if collection in {"business_objects", "actors", "operations"}:
        _require_text(row, "canonical_name", context=context)
    if collection == "object_relations":
        _require_text(row, "from_object", context=context)
        _require_text(row, "to_object", context=context)
        _require_text(row, "relation_type", context=context)
    if collection == "state_transitions":
        _require_text(row, "object_ref", context=context)
        _require_text(row, "from_state", context=context)
        _require_text(row, "to_state", context=context)
    if collection in {"business_rules", "business_behaviors"}:
        _require_text(row, "operation", context=context)
        objects = row.get("object_refs")
        if not isinstance(objects, list) or not any(_text(value) for value in objects):
            raise GroundTruthValidationError(f"{context}: object_refs must not be empty")
    if collection == "bug_dependencies":
        _require_text(row, "bug_id", context=context)
        dependency_ids = row.get("required_ground_truth_ids")
        if not isinstance(dependency_ids, list) or not any(
            _text(value) for value in dependency_ids
        ):
            raise GroundTruthValidationError(
                f"{context}: required_ground_truth_ids must not be empty"
            )


def validate_ground_truth(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise GroundTruthValidationError("Ground Truth root must be an object")
    if _text(document.get("schema")) != GROUND_TRUTH_SCHEMA:
        raise GroundTruthValidationError(f"schema must equal {GROUND_TRUTH_SCHEMA}")
    _require_text(document, "project_id", context="ground_truth")
    normalized = dict(document)
    normalized_collections: dict[str, list[dict[str, Any]]] = {}
    seen_ids: set[str] = set()
    for collection in SUPPORTED_COLLECTIONS:
        rows = _rows(document.get(collection))
        for index, row in enumerate(rows):
            _validate_entry(row, collection=collection, index=index)
            ground_truth_id = _text(row.get("ground_truth_id"))
            if ground_truth_id in seen_ids:
                raise GroundTruthValidationError(
                    f"duplicate ground_truth_id: {ground_truth_id}"
                )
            seen_ids.add(ground_truth_id)
            row["annotation_status"] = _text(
                row.get("annotation_status") or "CONFIRMED"
            ).upper()
            row["criticality"] = _text(row.get("criticality") or "P2").upper()
        normalized_collections[collection] = rows
        normalized[collection] = rows

    try:
        document_profile = validate_document_ground_truth(
            document.get(DOCUMENT_GROUND_TRUTH_KEY)
        )
    except DocumentGroundTruthValidationError as exc:
        raise GroundTruthValidationError(str(exc)) from exc
    normalized[DOCUMENT_GROUND_TRUTH_KEY] = document_profile
    document_ids: list[str] = []
    if document_profile:
        for collection in ("sources", "elements", "reading_order_pairs"):
            for row in _rows(document_profile.get(collection)):
                identity = _text(row.get("ground_truth_id"))
                if identity in seen_ids:
                    raise GroundTruthValidationError(
                        f"duplicate ground_truth_id across business and document profile: {identity}"
                    )
                seen_ids.add(identity)
                document_ids.append(identity)

    minimum_profile = document.get("minimum_profile")
    minimums = dict(minimum_profile) if isinstance(minimum_profile, dict) else {}
    shortfalls: list[dict[str, Any]] = []
    for collection, expected in minimums.items():
        if collection not in SUPPORTED_COLLECTIONS:
            continue
        try:
            expected_count = int(expected)
        except (TypeError, ValueError):
            raise GroundTruthValidationError(
                f"minimum_profile.{collection} must be an integer"
            ) from None
        actual_count = len(normalized_collections[collection])
        if actual_count < expected_count:
            shortfalls.append(
                {
                    "collection": collection,
                    "expected_minimum": expected_count,
                    "actual": actual_count,
                }
            )

    incomplete_annotations = [
        row.get("ground_truth_id")
        for collection in SUPPORTED_COLLECTIONS
        for row in normalized_collections[collection]
        if row.get("annotation_status") != "CONFIRMED"
    ]
    document_receipt = (
        document_profile.get("validation_receipt")
        if isinstance(document_profile, dict)
        and isinstance(document_profile.get("validation_receipt"), dict)
        else {}
    )
    document_status = _text(document_receipt.get("status")) or "NOT_DECLARED"
    document_incomplete = list(document_receipt.get("incomplete_annotation_ids") or [])
    overall_incomplete = bool(
        shortfalls
        or incomplete_annotations
        or (document_profile is not None and document_status != "PASS")
    )
    normalized["validation_receipt"] = {
        "status": (
            "BENCHMARK_GROUND_TRUTH_INCOMPLETE" if overall_incomplete else "PASS"
        ),
        "shortfalls": shortfalls,
        "incomplete_annotation_ids": [
            *incomplete_annotations,
            *document_incomplete,
        ],
        "ground_truth_id_count": len(seen_ids),
        "business_ground_truth_id_count": len(seen_ids) - len(document_ids),
        "document_ground_truth_id_count": len(document_ids),
        "document_ground_truth_status": document_status,
        "document_ground_truth_counts": document_receipt.get("counts") or {},
        "model_writeback_allowed": False,
        "generated_from_current_model": False,
        "document_ground_truth_generated_from_product_output": False,
    }
    return normalized


def load_ground_truth(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GroundTruthValidationError(f"Ground Truth file not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise GroundTruthValidationError(
            f"Ground Truth is not valid JSON: {source}: {exc}"
        ) from exc
    return validate_ground_truth(document)


__all__ = [
    "GROUND_TRUTH_SCHEMA",
    "SUPPORTED_COLLECTIONS",
    "DOCUMENT_GROUND_TRUTH_KEY",
    "GroundTruthValidationError",
    "load_ground_truth",
    "validate_ground_truth",
]
