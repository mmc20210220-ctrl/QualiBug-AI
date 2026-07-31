"""Human-authored document-ingestion Ground Truth inside the existing evaluator.

This module extends the one enterprise-understanding Ground Truth document with an
optional document profile. It never parses source bytes, generates annotations from
product output, uses fuzzy matching, or writes alignments back into product state.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import Any

DOCUMENT_GROUND_TRUTH_SCHEMA = "qualibug.document-ingestion-ground-truth-profile.v1"
DOCUMENT_GROUND_TRUTH_KEY = "document_ingestion_ground_truth"
ANNOTATION_STATUSES = {"CONFIRMED", "DRAFT", "INCOMPLETE", "REJECTED"}
CRITICALITIES = {"P0", "P1", "P2", "P3"}
CRITICALITY_WEIGHTS = {"P0": 5.0, "P1": 3.0, "P2": 1.0, "P3": 0.5}
EXACT_ADDRESS_KINDS = {
    "PAGE_BBOX",
    "SPREADSHEET_CELL",
    "PRESENTATION_SHAPE",
    "EXACT_SOURCE_LOCATOR",
}
_COORDINATE_FIELDS = (
    "page",
    "bbox",
    "sheet",
    "cell_ref",
    "slide",
    "shape_id",
    "row_index",
    "column_index",
)


class DocumentGroundTruthValidationError(ValueError):
    """Raised when the optional document Ground Truth profile is invalid."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 4) if denominator else None


def _text_hash(value: Any) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()


def _require(row: dict[str, Any], field: str, context: str) -> str:
    value = _text(row.get(field))
    if not value:
        raise DocumentGroundTruthValidationError(f"{context}: missing required field {field}")
    return value


def _normalize_source_ref(value: Any, *, context: str) -> str:
    reference = _text(value).replace("\\", "/")
    if not reference:
        return ""
    if "://" in reference:
        if any(ord(char) < 32 for char in reference):
            raise DocumentGroundTruthValidationError(
                f"{context}: source_ref contains control characters"
            )
        return reference
    if reference.startswith("/") or PurePosixPath(reference).is_absolute():
        raise DocumentGroundTruthValidationError(
            f"{context}: source_ref must be workspace-independent"
        )
    parts = [part for part in reference.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise DocumentGroundTruthValidationError(
            f"{context}: source_ref must be a portable relative path or URI"
        )
    return "/".join(parts)


def _normalize_source_selector(result: dict[str, Any], context: str) -> dict[str, Any]:
    source_id = _text(result.get("source_id"))
    source_ref = _normalize_source_ref(result.get("source_ref"), context=context)
    if bool(source_id) == bool(source_ref):
        raise DocumentGroundTruthValidationError(
            f"{context}: declare exactly one of source_ref or source_id"
        )
    result["source_id"] = source_id
    result["source_ref"] = source_ref
    result["source_identity_kind"] = "SOURCE_REF" if source_ref else "SOURCE_ID"
    return result


def _source_key(row: dict[str, Any]) -> tuple[str, str]:
    source_ref = _text(row.get("source_ref"))
    if source_ref:
        return ("SOURCE_REF", source_ref)
    return ("SOURCE_ID", _text(row.get("source_id")))


def _normalize_common(row: dict[str, Any], context: str) -> dict[str, Any]:
    result = dict(row)
    _require(result, "ground_truth_id", context)
    status = _text(result.get("annotation_status") or "CONFIRMED").upper()
    if status not in ANNOTATION_STATUSES:
        raise DocumentGroundTruthValidationError(
            f"{context}: annotation_status must be one of {sorted(ANNOTATION_STATUSES)}"
        )
    criticality = _text(result.get("criticality") or "P2").upper()
    if criticality not in CRITICALITIES:
        raise DocumentGroundTruthValidationError(
            f"{context}: criticality must be one of {sorted(CRITICALITIES)}"
        )
    result["annotation_status"] = status
    result["criticality"] = criticality
    return result


def _normalize_source(row: dict[str, Any], index: int) -> dict[str, Any]:
    context = f"{DOCUMENT_GROUND_TRUTH_KEY}.sources[{index}]"
    result = _normalize_source_selector(_normalize_common(row, context), context)
    if result["annotation_status"] == "CONFIRMED" and not (
        _text(result.get("filename")) or _text(result.get("source_hash"))
    ):
        raise DocumentGroundTruthValidationError(
            f"{context}: confirmed source requires filename or source_hash"
        )
    result["expected_format"] = _text(result.get("expected_format")).lower()
    result["source_hash"] = _text(result.get("source_hash")).lower()
    return result


def _normalize_element(row: dict[str, Any], index: int) -> dict[str, Any]:
    context = f"{DOCUMENT_GROUND_TRUTH_KEY}.elements[{index}]"
    result = _normalize_source_selector(_normalize_common(row, context), context)
    result["block_type"] = _require(result, "block_type", context).upper()
    locator = _text(result.get("source_locator"))
    declared_hash = _text(result.get("text_hash")).lower()
    raw_text = result.get("text")
    computed_hash = _text_hash(raw_text) if _text(raw_text) else ""
    if declared_hash and computed_hash and declared_hash != computed_hash:
        raise DocumentGroundTruthValidationError(
            f"{context}: text_hash does not match exact text"
        )
    result["source_locator"] = locator
    result["text_hash"] = declared_hash or computed_hash
    result.pop("text", None)
    if not locator and not result["text_hash"]:
        raise DocumentGroundTruthValidationError(
            f"{context}: deterministic identity requires source_locator or text/text_hash"
        )
    address_kind = _text(result.get("address_kind")).upper()
    if address_kind and address_kind not in EXACT_ADDRESS_KINDS:
        raise DocumentGroundTruthValidationError(
            f"{context}: address_kind must be one of {sorted(EXACT_ADDRESS_KINDS)}"
        )
    result["address_kind"] = address_kind
    if result.get("order") not in (None, ""):
        try:
            result["order"] = int(result["order"])
        except (TypeError, ValueError):
            raise DocumentGroundTruthValidationError(
                f"{context}: order must be an integer"
            ) from None
    return result


def _normalize_order_pair(row: dict[str, Any], index: int) -> dict[str, Any]:
    context = f"{DOCUMENT_GROUND_TRUTH_KEY}.reading_order_pairs[{index}]"
    result = _normalize_source_selector(_normalize_common(row, context), context)
    _require(result, "before_element_id", context)
    _require(result, "after_element_id", context)
    if result["before_element_id"] == result["after_element_id"]:
        raise DocumentGroundTruthValidationError(
            f"{context}: before_element_id and after_element_id must differ"
        )
    return result


def validate_document_ground_truth(value: Any) -> dict[str, Any] | None:
    """Validate and normalize the optional document profile."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DocumentGroundTruthValidationError(
            f"{DOCUMENT_GROUND_TRUTH_KEY} must be an object"
        )
    if _text(value.get("schema")) != DOCUMENT_GROUND_TRUTH_SCHEMA:
        raise DocumentGroundTruthValidationError(
            f"{DOCUMENT_GROUND_TRUTH_KEY}.schema must equal {DOCUMENT_GROUND_TRUTH_SCHEMA}"
        )
    normalized = dict(value)
    sources = [
        _normalize_source(row, index)
        for index, row in enumerate(_rows(value.get("sources")))
    ]
    elements = [
        _normalize_element(row, index)
        for index, row in enumerate(_rows(value.get("elements")))
    ]
    order_pairs = [
        _normalize_order_pair(row, index)
        for index, row in enumerate(_rows(value.get("reading_order_pairs")))
    ]

    seen_ids: set[str] = set()
    for row in [*sources, *elements, *order_pairs]:
        identity = _text(row.get("ground_truth_id"))
        if identity in seen_ids:
            raise DocumentGroundTruthValidationError(
                f"duplicate document ground_truth_id: {identity}"
            )
        seen_ids.add(identity)

    source_keys: set[tuple[str, str]] = set()
    for row in sources:
        key = _source_key(row)
        if key in source_keys:
            raise DocumentGroundTruthValidationError(
                f"duplicate document source identity: {key[0]}:{key[1]}"
            )
        source_keys.add(key)

    element_by_id = {_text(row.get("ground_truth_id")): row for row in elements}
    deterministic_keys: set[tuple[str, str, str, str]] = set()
    locator_keys: set[tuple[str, str, str]] = set()
    for row in elements:
        source_kind, source_value = _source_key(row)
        if (source_kind, source_value) not in source_keys:
            raise DocumentGroundTruthValidationError(
                "document element references undeclared source identity: "
                f"{source_kind}:{source_value}"
            )
        locator = _text(row.get("source_locator"))
        text_hash = _text(row.get("text_hash"))
        key = (source_kind, source_value, locator, text_hash)
        if key in deterministic_keys:
            raise DocumentGroundTruthValidationError(
                "duplicate document element deterministic identity: " + ":".join(key)
            )
        deterministic_keys.add(key)
        if locator:
            locator_key = (source_kind, source_value, locator)
            if locator_key in locator_keys:
                raise DocumentGroundTruthValidationError(
                    "one exact source locator cannot identify multiple Ground Truth elements: "
                    + ":".join(locator_key)
                )
            locator_keys.add(locator_key)
    for index, row in enumerate(order_pairs):
        context = f"{DOCUMENT_GROUND_TRUTH_KEY}.reading_order_pairs[{index}]"
        before = element_by_id.get(_text(row.get("before_element_id")))
        after = element_by_id.get(_text(row.get("after_element_id")))
        if not before or not after:
            raise DocumentGroundTruthValidationError(
                f"{context}: referenced element does not exist"
            )
        source_key = _source_key(row)
        if source_key not in source_keys:
            raise DocumentGroundTruthValidationError(
                f"{context}: order pair references undeclared source identity"
            )
        if _source_key(before) != source_key or _source_key(after) != source_key:
            raise DocumentGroundTruthValidationError(
                f"{context}: order pair and both elements must share source identity"
            )

    minimums = _dict(value.get("minimum_profile"))
    actuals = {
        "sources": len(sources),
        "elements": len(elements),
        "reading_order_pairs": len(order_pairs),
        "table_cells": sum(row.get("block_type") == "TABLE_CELL" for row in elements),
    }
    shortfalls: list[dict[str, Any]] = []
    for name, expected in minimums.items():
        if name not in actuals:
            continue
        try:
            expected_count = int(expected)
        except (TypeError, ValueError):
            raise DocumentGroundTruthValidationError(
                f"{DOCUMENT_GROUND_TRUTH_KEY}.minimum_profile.{name} must be an integer"
            ) from None
        if actuals[name] < expected_count:
            shortfalls.append(
                {
                    "collection": name,
                    "expected_minimum": expected_count,
                    "actual": actuals[name],
                }
            )
    incomplete_ids = [
        _text(row.get("ground_truth_id"))
        for row in [*sources, *elements, *order_pairs]
        if _text(row.get("annotation_status")) != "CONFIRMED"
    ]
    status = (
        "PASS"
        if not shortfalls and not incomplete_ids
        else "BENCHMARK_GROUND_TRUTH_INCOMPLETE"
    )
    normalized.update(
        {
            "schema": DOCUMENT_GROUND_TRUTH_SCHEMA,
            "sources": sources,
            "elements": elements,
            "reading_order_pairs": order_pairs,
            "scope_complete": bool(value.get("scope_complete")),
            "validation_receipt": {
                "status": status,
                "counts": actuals,
                "shortfalls": shortfalls,
                "incomplete_annotation_ids": incomplete_ids,
                "ground_truth_id_count": len(seen_ids),
                "source_reference_authority": "SOURCE_REF_OR_LEGACY_SOURCE_ID_EXACTLY_ONE",
                "runtime_source_id_required_in_human_ground_truth": False,
                "generated_from_product_output": False,
                "model_writeback_allowed": False,
            },
        }
    )
    return normalized


def _source_inventory(product_asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _rows(product_asset.get("source_inventory"))
    return rows if rows else _rows(product_asset.get("sources"))


def _structure_items(product_asset: dict[str, Any]) -> list[dict[str, Any]]:
    assets = _dict(product_asset.get("document_structure_assets"))
    if not assets:
        assets = _dict(
            _dict(product_asset.get("enterprise_understanding_model")).get(
                "document_structure_assets"
            )
        )
    inventory_by_id = {
        _text(row.get("source_id")): row
        for row in _source_inventory(product_asset)
        if _text(row.get("source_id"))
    }
    result: list[dict[str, Any]] = []
    for raw in _rows(assets.get("items")):
        row = dict(raw)
        inventory = _dict(inventory_by_id.get(_text(row.get("source_id"))))
        row["_source_ref"] = _text(
            inventory.get("external_ref")
            or inventory.get("source_origin_ref")
            or row.get("source_ref")
        ).replace("\\", "/")
        row["_inventory_filename"] = _text(
            inventory.get("original_name") or inventory.get("filename")
        )
        row["_inventory_source_hash"] = _text(
            inventory.get("content_hash") or inventory.get("source_hash")
        ).lower()
        result.append(row)
    return result


def _candidate_blocks(item: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    source_id = _text(item.get("source_id"))
    source_ref = _text(item.get("_source_ref"))
    for index, row in enumerate(_rows(item.get("blocks")), start=1):
        evidence = _dict(row.get("evidence_address"))
        text_hash = _text(row.get("text_hash")).lower()
        if not text_hash and _text(row.get("text")):
            text_hash = _text_hash(row.get("text"))
        result.append(
            {
                "candidate_key": f"{source_id}:{_text(row.get('block_id')) or index}",
                "source_id": source_id,
                "source_ref": source_ref,
                "block_id": _text(row.get("block_id")),
                "block_type": _text(row.get("type")).upper(),
                "source_locator": _text(
                    row.get("source_locator") or evidence.get("source_locator")
                ),
                "text_hash": text_hash,
                "order": row.get("order"),
                "address_kind": _text(evidence.get("address_kind")).upper(),
                **{
                    field: (
                        row.get(field)
                        if row.get(field) not in (None, "")
                        else evidence.get(field)
                    )
                    for field in _COORDINATE_FIELDS
                },
            }
        )
    return result


def _item_format(item: dict[str, Any]) -> str:
    structure = _dict(item.get("structure_receipt"))
    ingestion = _dict(item.get("ingestion_pipeline_receipt"))
    return _text(
        item.get("format")
        or structure.get("format")
        or structure.get("detected_format")
        or ingestion.get("detected_format")
    ).lower()


def _matches_source_selector(candidate: dict[str, Any], expected: dict[str, Any]) -> bool:
    source_ref = _text(expected.get("source_ref"))
    if source_ref:
        return _text(candidate.get("_source_ref")) == source_ref
    return _text(candidate.get("source_id")) == _text(expected.get("source_id"))


def _source_alignment(
    expected: dict[str, Any], items: list[dict[str, Any]]
) -> dict[str, Any]:
    candidates = [row for row in items if _matches_source_selector(row, expected)]
    source_id = _text(expected.get("source_id"))
    source_ref = _text(expected.get("source_ref"))
    if not candidates:
        status = "MISSING"
        candidate = {}
        mismatches = ["source_ref" if source_ref else "source_id"]
    elif len(candidates) > 1:
        status = "AMBIGUOUS"
        candidate = {}
        mismatches = ["duplicate_product_source_reference"]
    else:
        candidate = candidates[0]
        evidence = _dict(candidate.get("evidence_closure_receipt"))
        expected_slots = {
            "filename": _text(expected.get("filename")),
            "source_hash": _text(expected.get("source_hash")).lower(),
            "expected_format": _text(expected.get("expected_format")).lower(),
        }
        actual_slots = {
            "filename": _text(
                candidate.get("filename")
                or evidence.get("filename")
                or candidate.get("_inventory_filename")
            ),
            "source_hash": _text(
                evidence.get("source_hash")
                or candidate.get("source_hash")
                or candidate.get("_inventory_source_hash")
            ).lower(),
            "expected_format": _item_format(candidate),
        }
        mismatches = [
            key
            for key, value in expected_slots.items()
            if value and actual_slots.get(key) != value
        ]
        status = "EXACT_MATCH" if not mismatches else "PARTIAL_MATCH"
    return {
        "ground_truth_id": expected.get("ground_truth_id"),
        "source_id": source_id,
        "source_ref": source_ref,
        "source_identity_kind": expected.get("source_identity_kind"),
        "criticality": expected.get("criticality"),
        "alignment_status": status,
        "mismatched_slots": mismatches,
        "candidate": {
            "source_id": _text(candidate.get("source_id")),
            "source_ref": _text(candidate.get("_source_ref")),
            "filename": _text(
                candidate.get("filename") or candidate.get("_inventory_filename")
            ),
            "format": _item_format(candidate),
        }
        if candidate
        else {},
    }


def _element_candidates(
    expected: dict[str, Any], blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    locator = _text(expected.get("source_locator"))
    text_hash = _text(expected.get("text_hash")).lower()
    source_ref = _text(expected.get("source_ref"))
    source_id = _text(expected.get("source_id"))
    result: list[dict[str, Any]] = []
    for candidate in blocks:
        if source_ref:
            if candidate.get("source_ref") != source_ref:
                continue
        elif candidate.get("source_id") != source_id:
            continue
        if locator and candidate.get("source_locator") != locator:
            continue
        if text_hash and candidate.get("text_hash") != text_hash:
            continue
        result.append(candidate)
    return result


def _element_alignment(
    expected: dict[str, Any], blocks: list[dict[str, Any]]
) -> dict[str, Any]:
    candidates = _element_candidates(expected, blocks)
    base = {
        "ground_truth_id": expected.get("ground_truth_id"),
        "source_id": expected.get("source_id"),
        "source_ref": expected.get("source_ref"),
        "source_identity_kind": expected.get("source_identity_kind"),
        "block_type": expected.get("block_type"),
        "criticality": expected.get("criticality"),
    }
    if not candidates:
        return {
            **base,
            "alignment_status": "MISSING",
            "mismatched_slots": ["deterministic_identity"],
            "candidate": {},
        }
    if len(candidates) > 1:
        return {
            **base,
            "alignment_status": "AMBIGUOUS",
            "mismatched_slots": ["multiple_deterministic_candidates"],
            "candidate_keys": [row.get("candidate_key") for row in candidates],
            "candidate": {},
        }
    candidate = candidates[0]
    mismatches: list[str] = []
    if candidate.get("block_type") != expected.get("block_type"):
        mismatches.append("block_type")
    if (
        expected.get("address_kind")
        and candidate.get("address_kind") != expected.get("address_kind")
    ):
        mismatches.append("address_kind")
    if (
        expected.get("order") not in (None, "")
        and candidate.get("order") != expected.get("order")
    ):
        mismatches.append("order")
    for field in _COORDINATE_FIELDS:
        expected_value = expected.get(field)
        if expected_value not in (None, "") and candidate.get(field) != expected_value:
            mismatches.append(field)
    return {
        **base,
        "alignment_status": "EXACT_MATCH" if not mismatches else "PARTIAL_MATCH",
        "mismatched_slots": mismatches,
        "candidate": candidate,
    }


def _order_alignment(
    expected: dict[str, Any], element_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    before = element_by_id.get(_text(expected.get("before_element_id")), {})
    after = element_by_id.get(_text(expected.get("after_element_id")), {})
    candidate_before = _dict(before.get("candidate"))
    candidate_after = _dict(after.get("candidate"))
    if (
        before.get("alignment_status") not in {"EXACT_MATCH", "PARTIAL_MATCH"}
        or after.get("alignment_status") not in {"EXACT_MATCH", "PARTIAL_MATCH"}
    ):
        status = "MISSING"
    else:
        try:
            status = (
                "EXACT_MATCH"
                if int(candidate_before.get("order")) < int(candidate_after.get("order"))
                else "WRONG_ORDER"
            )
        except (TypeError, ValueError):
            status = "INDETERMINATE"
    return {
        "ground_truth_id": expected.get("ground_truth_id"),
        "source_id": expected.get("source_id"),
        "source_ref": expected.get("source_ref"),
        "source_identity_kind": expected.get("source_identity_kind"),
        "criticality": expected.get("criticality"),
        "before_element_id": expected.get("before_element_id"),
        "after_element_id": expected.get("after_element_id"),
        "alignment_status": status,
        "before_candidate_key": candidate_before.get("candidate_key"),
        "after_candidate_key": candidate_after.get("candidate_key"),
    }


def _ground_truth_gap(
    source_alignments: list[dict[str, Any]],
    element_alignments: list[dict[str, Any]],
    order_alignments: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    reason_by_status = {
        ("source", "MISSING"): "DOCUMENT_GROUND_TRUTH_SOURCE_MISSING",
        ("source", "AMBIGUOUS"): "DOCUMENT_GROUND_TRUTH_SOURCE_AMBIGUOUS",
        ("source", "PARTIAL_MATCH"): "DOCUMENT_GROUND_TRUTH_SOURCE_IDENTITY_WRONG",
        ("element", "MISSING"): "DOCUMENT_STRUCTURE_ELEMENT_MISSING",
        ("element", "AMBIGUOUS"): "DOCUMENT_STRUCTURE_ELEMENT_AMBIGUOUS",
        ("element", "PARTIAL_MATCH"): "DOCUMENT_STRUCTURE_ELEMENT_SLOT_WRONG",
        ("order", "WRONG_ORDER"): "DOCUMENT_READING_ORDER_WRONG",
        ("order", "INDETERMINATE"): "DOCUMENT_READING_ORDER_INDETERMINATE",
        ("order", "MISSING"): "DOCUMENT_READING_ORDER_ELEMENT_MISSING",
    }
    impacts: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "weighted_impact": 0.0, "ground_truth_ids": []}
    )
    for kind, rows in (
        ("source", source_alignments),
        ("element", element_alignments),
        ("order", order_alignments),
    ):
        for row in rows:
            reason = reason_by_status.get((kind, _text(row.get("alignment_status"))))
            if not reason:
                continue
            impact = impacts[reason]
            impact["count"] += 1
            impact["weighted_impact"] += CRITICALITY_WEIGHTS.get(
                _text(row.get("criticality") or "P2").upper(), 1.0
            )
            impact["ground_truth_ids"].append(_text(row.get("ground_truth_id")))
    ranked = [
        {
            "reason_code": reason,
            "count": data["count"],
            "criticality_weighted_impact": round(data["weighted_impact"], 2),
            "ground_truth_ids": sorted(
                value for value in data["ground_truth_ids"] if value
            ),
        }
        for reason, data in impacts.items()
    ]
    ranked.sort(
        key=lambda row: (-row["criticality_weighted_impact"], row["reason_code"])
    )
    return (ranked[0]["reason_code"] if ranked else "NONE", ranked)


def evaluate_document_ground_truth(
    profile: dict[str, Any] | None, product_asset: dict[str, Any]
) -> dict[str, Any]:
    """Deterministically align human document annotations to persisted Document IR."""
    if profile is None:
        return {
            "status": "NOT_DECLARED",
            "validation_status": "NOT_DECLARED",
            "scope_complete": False,
            "highest_impact_gap": "DOCUMENT_STRUCTURE_GROUND_TRUTH_NOT_DECLARED",
            "metrics": {
                "true_structure_recall_measured": False,
                "source_recall": None,
                "strict_structure_element_recall": None,
                "coverage_structure_element_recall": None,
                "block_type_accuracy": None,
                "exact_evidence_address_accuracy": None,
                "table_cell_recall": None,
                "reading_order_accuracy": None,
            },
            "source_alignments": [],
            "element_alignments": [],
            "reading_order_alignments": [],
            "gap_distribution": [],
            "source_reference_resolution_authority": "SOURCE_INVENTORY_EXTERNAL_REF",
            "runtime_source_id_required_in_human_ground_truth": False,
            "product_output_generated_ground_truth": False,
            "fuzzy_or_llm_alignment_used": False,
            "automatic_winner_used": False,
        }

    items = _structure_items(product_asset)
    blocks = [block for item in items for block in _candidate_blocks(item)]
    confirmed_sources = [
        row
        for row in _rows(profile.get("sources"))
        if row.get("annotation_status") == "CONFIRMED"
    ]
    confirmed_elements = [
        row
        for row in _rows(profile.get("elements"))
        if row.get("annotation_status") == "CONFIRMED"
    ]
    confirmed_pairs = [
        row
        for row in _rows(profile.get("reading_order_pairs"))
        if row.get("annotation_status") == "CONFIRMED"
    ]
    source_alignments = [_source_alignment(row, items) for row in confirmed_sources]
    element_alignments = [_element_alignment(row, blocks) for row in confirmed_elements]

    candidate_usage = Counter(
        _text(_dict(row.get("candidate")).get("candidate_key"))
        for row in element_alignments
        if _text(_dict(row.get("candidate")).get("candidate_key"))
    )
    for row in element_alignments:
        candidate_key = _text(_dict(row.get("candidate")).get("candidate_key"))
        if candidate_key and candidate_usage[candidate_key] > 1:
            row["alignment_status"] = "AMBIGUOUS"
            row["mismatched_slots"] = [
                "candidate_reused_by_multiple_ground_truth_elements"
            ]

    element_by_id = {
        _text(row.get("ground_truth_id")): row for row in element_alignments
    }
    order_alignments = [
        _order_alignment(row, element_by_id) for row in confirmed_pairs
    ]
    highest_gap, gap_distribution = _ground_truth_gap(
        source_alignments, element_alignments, order_alignments
    )

    exact_sources = sum(
        row.get("alignment_status") == "EXACT_MATCH" for row in source_alignments
    )
    exact_elements = sum(
        row.get("alignment_status") == "EXACT_MATCH" for row in element_alignments
    )
    covered_elements = sum(
        row.get("alignment_status") in {"EXACT_MATCH", "PARTIAL_MATCH"}
        for row in element_alignments
    )
    unique_matched = [
        row
        for row in element_alignments
        if row.get("alignment_status") in {"EXACT_MATCH", "PARTIAL_MATCH"}
    ]
    correct_type = sum(
        "block_type" not in row.get("mismatched_slots", []) for row in unique_matched
    )
    address_measurable = [
        row
        for expected, row in zip(confirmed_elements, element_alignments)
        if expected.get("address_kind")
        or any(
            expected.get(field) not in (None, "") for field in _COORDINATE_FIELDS
        )
    ]
    address_slots = {"address_kind", *_COORDINATE_FIELDS}
    address_correct = sum(
        not any(slot in address_slots for slot in row.get("mismatched_slots", []))
        for row in address_measurable
        if row.get("alignment_status") in {"EXACT_MATCH", "PARTIAL_MATCH"}
    )
    table_rows = [
        row for row in element_alignments if row.get("block_type") == "TABLE_CELL"
    ]
    exact_order = sum(
        row.get("alignment_status") == "EXACT_MATCH" for row in order_alignments
    )
    by_type: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in element_alignments:
        by_type[_text(row.get("block_type")) or "UNKNOWN"].append(row)

    validation_status = _text(
        _dict(profile.get("validation_receipt")).get("status")
    )
    true_measured = bool(confirmed_elements and validation_status == "PASS")
    metrics = {
        "true_structure_recall_measured": true_measured,
        "source_ground_truth_count": len(source_alignments),
        "source_exact_match_count": exact_sources,
        "source_recall": _ratio(exact_sources, len(source_alignments)),
        "structure_element_ground_truth_count": len(element_alignments),
        "structure_element_exact_match_count": exact_elements,
        "structure_element_covered_count": covered_elements,
        "structure_element_missing_count": sum(
            row.get("alignment_status") == "MISSING" for row in element_alignments
        ),
        "structure_element_ambiguous_count": sum(
            row.get("alignment_status") == "AMBIGUOUS" for row in element_alignments
        ),
        "strict_structure_element_recall": _ratio(
            exact_elements, len(element_alignments)
        ),
        "coverage_structure_element_recall": _ratio(
            covered_elements, len(element_alignments)
        ),
        "block_type_accuracy": _ratio(correct_type, len(unique_matched)),
        "exact_evidence_address_accuracy": _ratio(
            address_correct, len(address_measurable)
        ),
        "table_cell_recall": _ratio(
            sum(
                row.get("alignment_status") == "EXACT_MATCH" for row in table_rows
            ),
            len(table_rows),
        ),
        "reading_order_pair_count": len(order_alignments),
        "reading_order_accuracy": _ratio(exact_order, len(order_alignments)),
        "by_block_type": {
            name: {
                "ground_truth_count": len(rows),
                "exact_match_count": sum(
                    row.get("alignment_status") == "EXACT_MATCH" for row in rows
                ),
                "strict_recall": _ratio(
                    sum(
                        row.get("alignment_status") == "EXACT_MATCH"
                        for row in rows
                    ),
                    len(rows),
                ),
            }
            for name, rows in sorted(by_type.items())
        },
    }
    profile_pass = bool(
        validation_status == "PASS"
        and profile.get("scope_complete")
        and highest_gap == "NONE"
        and metrics["strict_structure_element_recall"] == 1.0
        and metrics["source_recall"] in {None, 1.0}
        and metrics["exact_evidence_address_accuracy"] in {None, 1.0}
        and metrics["reading_order_accuracy"] in {None, 1.0}
    )
    return {
        "status": "PASS" if profile_pass else "PARTIAL",
        "validation_status": validation_status,
        "scope_complete": bool(profile.get("scope_complete")),
        "highest_impact_gap": highest_gap,
        "metrics": metrics,
        "source_alignments": source_alignments,
        "element_alignments": element_alignments,
        "reading_order_alignments": order_alignments,
        "gap_distribution": gap_distribution,
        "profile_five_of_five_pass": profile_pass,
        "universal_cross_industry_five_of_five_proven": False,
        "source_reference_resolution_authority": "SOURCE_INVENTORY_EXTERNAL_REF",
        "runtime_source_id_required_in_human_ground_truth": False,
        "product_output_generated_ground_truth": False,
        "fuzzy_or_llm_alignment_used": False,
        "automatic_winner_used": False,
        "model_writeback_allowed": False,
    }


__all__ = [
    "DOCUMENT_GROUND_TRUTH_SCHEMA",
    "DOCUMENT_GROUND_TRUTH_KEY",
    "DocumentGroundTruthValidationError",
    "validate_document_ground_truth",
    "evaluate_document_ground_truth",
]
