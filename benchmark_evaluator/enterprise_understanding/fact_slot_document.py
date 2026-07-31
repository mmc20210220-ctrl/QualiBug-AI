"""Document-level application of the optional fact-slot Ground Truth contract."""
from __future__ import annotations

from typing import Any

from .fact_slot_ground_truth import (
    FACT_SLOT_GROUND_TRUTH_SCHEMA,
    slot_annotation_declared,
    validate_business_fact_slot_row,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def validate_business_fact_slot_document(document: dict[str, Any]) -> dict[str, Any]:
    """Validate slots on an already validated enterprise Ground Truth document."""
    normalized = dict(document)
    annotated_count = 0
    confirmed_count = 0
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
            normalized_rows.append(item)
        normalized[collection] = normalized_rows

    receipt = dict(normalized.get("validation_receipt") or {})
    receipt.update(
        {
            "business_fact_slot_contract_schema": FACT_SLOT_GROUND_TRUTH_SCHEMA,
            "business_fact_slot_contract_validated": True,
            "business_fact_slot_annotation_count": annotated_count,
            "confirmed_business_fact_slot_annotation_count": confirmed_count,
            "business_fact_ground_truth_generated_from_product_output": False,
        }
    )
    normalized["validation_receipt"] = receipt
    return normalized


__all__ = ["validate_business_fact_slot_document"]
