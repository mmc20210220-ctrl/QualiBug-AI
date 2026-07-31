"""Document-level application of the optional fact-slot Ground Truth contract."""
from __future__ import annotations

from typing import Any

from .fact_slot_ground_truth import (
    FACT_SLOT_GROUND_TRUTH_SCHEMA,
    FactSlotGroundTruthValidationError,
    slot_annotation_declared,
    validate_business_fact_slot_row,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _scope_locators(document: dict[str, Any]) -> list[str]:
    raw = document.get("explicit_fact_scope_locators")
    if raw in (None, ""):
        return []
    if not isinstance(raw, list) or any(not _text(value) for value in raw):
        raise FactSlotGroundTruthValidationError(
            "explicit_fact_scope_locators must be a list of non-empty stable locators"
        )
    values = [_text(value) for value in raw]
    if len(values) != len(set(values)):
        raise FactSlotGroundTruthValidationError(
            "explicit_fact_scope_locators must not contain duplicates"
        )
    return values


def validate_business_fact_slot_document(document: dict[str, Any]) -> dict[str, Any]:
    """Validate slots on an already validated enterprise Ground Truth document."""
    normalized = dict(document)
    annotated_count = 0
    confirmed_count = 0
    annotated_locators: set[str] = set()
    for collection in ("business_rules", "business_behaviors"):
        normalized_rows: list[dict[str, Any]] = []
        for index, row in enumerate(_rows(document.get(collection))):
            item = validate_business_fact_slot_row(
                row, context=f"{collection}[{index}]"
            )
            if slot_annotation_declared(item):
                annotated_count += 1
                if _text(item.get("annotation_status") or "CONFIRMED").upper() == "CONFIRMED":
                    confirmed_count += 1
            for locator in item.get("source_locators") or []:
                if _text(locator):
                    annotated_locators.add(_text(locator))
            normalized_rows.append(item)
        normalized[collection] = normalized_rows

    scope_locators = _scope_locators(document)
    scope_complete = document.get("explicit_fact_scope_complete") is True
    if scope_complete and not scope_locators:
        raise FactSlotGroundTruthValidationError(
            "explicit_fact_scope_complete=true requires explicit_fact_scope_locators"
        )
    missing_from_scope = sorted(annotated_locators.difference(scope_locators))
    if missing_from_scope:
        raise FactSlotGroundTruthValidationError(
            "annotated source_locators must belong to explicit_fact_scope_locators: "
            + ",".join(missing_from_scope)
        )
    if scope_complete and not _text(document.get("source_locator_authority")):
        raise FactSlotGroundTruthValidationError(
            "explicit_fact_scope_complete=true requires source_locator_authority"
        )
    normalized["explicit_fact_scope_locators"] = scope_locators

    receipt = dict(normalized.get("validation_receipt") or {})
    receipt.update(
        {
            "business_fact_slot_contract_schema": FACT_SLOT_GROUND_TRUTH_SCHEMA,
            "business_fact_slot_contract_validated": True,
            "business_fact_slot_annotation_count": annotated_count,
            "confirmed_business_fact_slot_annotation_count": confirmed_count,
            "business_fact_ground_truth_generated_from_product_output": False,
            "explicit_fact_scope_complete": scope_complete,
            "explicit_fact_scope_locator_count": len(scope_locators),
            "annotated_fact_locator_count": len(annotated_locators),
            "all_annotated_fact_locators_inside_explicit_scope": not missing_from_scope,
        }
    )
    normalized["validation_receipt"] = receipt
    return normalized


__all__ = ["validate_business_fact_slot_document"]
