"""Validation for optional slot-level explicit business fact Ground Truth.

The contract extends the existing business_rules/business_behaviors rows in place.
Legacy rows remain valid. Once any extended slot is declared, every declared slot is
validated deterministically before evaluator alignment starts.
"""
from __future__ import annotations

from typing import Any

FACT_SLOT_GROUND_TRUTH_SCHEMA = "qualibug.enterprise-business-fact-slot-ground-truth.v1"
MODALITIES = {"ASSERTS", "MUST", "MUST_NOT", "MAY", "ONLY_IF"}
CONDITION_KINDS = {"", "LEAF", "ALL", "ANY", "UNRESOLVED", "IF_THEN_ELSE", "EXCEPT_OVERLAY"}
CONDITION_COMBINATORS = {"", "SINGLE_CONDITION", "AND", "OR", "UNRESOLVED"}
LIST_OF_TEXT_FIELDS = {
    "actor_refs",
    "object_refs",
    "exception_scope",
    "postconditions",
    "compensation",
    "compensations",
    "source_locators",
}
LIST_OF_OBJECT_FIELDS = {
    "state_effects",
    "data_effects",
    "quantity_constraints",
    "time_window_constraints",
    "formula_constraints",
}
EXTENDED_SLOT_FIELDS = {
    "fact_type",
    "actor_refs",
    "modality",
    "condition_frame",
    "exception_scope",
    "state_effects",
    "data_effects",
    "postconditions",
    "compensation",
    "compensations",
    "quantity_constraints",
    "time_window_constraints",
    "formula_constraints",
    "source_locators",
}

# These fields establish that a row opts into the typed fact-slot contract.
# ``actor_refs`` and ``source_locators`` predate that contract and are common in
# legacy Ground Truth rows; using either as an opt-in signal silently converted
# every legacy business behavior into a slot annotation and required a closed
# explicit-fact scope that the document never declared. They remain validated
# whenever a real typed slot is present, but cannot activate the contract alone.
SLOT_DECLARATION_FIELDS = EXTENDED_SLOT_FIELDS.difference(
    {"actor_refs", "source_locators"}
)


class FactSlotGroundTruthValidationError(ValueError):
    """Raised when an optional typed fact annotation is structurally invalid."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _validate_text_list(value: Any, *, context: str, field: str) -> None:
    if not isinstance(value, list) or any(not _text(item) for item in value):
        raise FactSlotGroundTruthValidationError(
            f"{context}: {field} must be a list of non-empty text values"
        )


def _validate_object_list(value: Any, *, context: str, field: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, dict) or not item for item in value):
        raise FactSlotGroundTruthValidationError(
            f"{context}: {field} must be a list of non-empty objects"
        )


def _validate_condition_frame(value: Any, *, context: str) -> None:
    if not isinstance(value, dict) or not value:
        raise FactSlotGroundTruthValidationError(
            f"{context}: condition_frame must be a non-empty object"
        )
    kind = _text(value.get("kind"))
    combinator = _text(value.get("combinator"))
    if kind not in CONDITION_KINDS:
        raise FactSlotGroundTruthValidationError(
            f"{context}: condition_frame.kind must be one of {sorted(CONDITION_KINDS)}"
        )
    if combinator not in CONDITION_COMBINATORS:
        raise FactSlotGroundTruthValidationError(
            f"{context}: condition_frame.combinator must be one of {sorted(CONDITION_COMBINATORS)}"
        )
    for field in ("conditions", "exception_scopes", "parent_conditions"):
        if field in value:
            _validate_text_list(value.get(field), context=context, field=f"condition_frame.{field}")
    branch = _text(value.get("branch"))
    if branch and branch not in {"THEN", "ELSE", "ELSE_IF"}:
        raise FactSlotGroundTruthValidationError(
            f"{context}: condition_frame.branch must be THEN, ELSE or ELSE_IF"
        )


def slot_annotation_declared(row: dict[str, Any]) -> bool:
    return any(
        row.get(field) not in (None, "", [], {})
        for field in SLOT_DECLARATION_FIELDS
    )


def validate_business_fact_slot_row(row: dict[str, Any], *, context: str) -> dict[str, Any]:
    """Validate one optional slot annotation without mutating the input row."""
    normalized = dict(row)
    if not slot_annotation_declared(normalized):
        return normalized

    fact_type = _text(normalized.get("fact_type"))
    if "fact_type" in normalized and not fact_type:
        raise FactSlotGroundTruthValidationError(
            f"{context}: fact_type must be non-empty text when declared"
        )
    if fact_type:
        normalized["fact_type"] = fact_type.upper()

    modality = _text(normalized.get("modality")).upper()
    if modality and modality not in MODALITIES:
        raise FactSlotGroundTruthValidationError(
            f"{context}: modality must be one of {sorted(MODALITIES)}"
        )
    if modality:
        normalized["modality"] = modality

    for field in LIST_OF_TEXT_FIELDS:
        if field in normalized:
            _validate_text_list(normalized.get(field), context=context, field=field)
            normalized[field] = [_text(item) for item in normalized[field]]

    for field in LIST_OF_OBJECT_FIELDS:
        if field in normalized:
            _validate_object_list(normalized.get(field), context=context, field=field)
            normalized[field] = [dict(item) for item in normalized[field]]

    if "condition_frame" in normalized:
        _validate_condition_frame(normalized.get("condition_frame"), context=context)
        normalized["condition_frame"] = dict(normalized["condition_frame"])

    return normalized


__all__ = [
    "FACT_SLOT_GROUND_TRUTH_SCHEMA",
    "FactSlotGroundTruthValidationError",
    "EXTENDED_SLOT_FIELDS",
    "SLOT_DECLARATION_FIELDS",
    "slot_annotation_declared",
    "validate_business_fact_slot_row",
]
