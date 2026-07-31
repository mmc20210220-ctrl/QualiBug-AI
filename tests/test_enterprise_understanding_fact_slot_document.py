from __future__ import annotations

from copy import deepcopy

import pytest

from benchmark_evaluator.enterprise_understanding.fact_slot_document import (
    validate_business_fact_slot_document,
)
from benchmark_evaluator.enterprise_understanding.fact_slot_ground_truth import (
    FactSlotGroundTruthValidationError,
)


LOCATOR = "rules.md#line=3;chars=10-30"


def _document() -> dict:
    return {
        "explicit_fact_scope_complete": True,
        "explicit_fact_scope_locators": [LOCATOR],
        "source_locator_authority": "generic_text_document_ir_exact_line_char",
        "business_rules": [
            {
                "ground_truth_id": "gt:submit",
                "annotation_status": "CONFIRMED",
                "criticality": "P0",
                "operation": "提交",
                "object_refs": ["订单"],
                "fact_type": "BUSINESS_RULE",
                "modality": "MUST",
                "source_locators": [LOCATOR],
            }
        ],
        "business_behaviors": [],
    }


def test_closed_explicit_fact_scope_is_validated() -> None:
    result = validate_business_fact_slot_document(_document())
    receipt = result["validation_receipt"]

    assert receipt["business_fact_slot_annotation_count"] == 1
    assert receipt["confirmed_business_fact_slot_annotation_count"] == 1
    assert receipt["explicit_fact_scope_complete"] is True
    assert receipt["explicit_fact_scope_locator_count"] == 1
    assert receipt["annotated_fact_locator_count"] == 1
    assert receipt["all_annotated_fact_locators_inside_explicit_scope"] is True


def test_complete_scope_requires_declared_locators() -> None:
    document = _document()
    document.pop("explicit_fact_scope_locators")

    with pytest.raises(
        FactSlotGroundTruthValidationError,
        match="requires explicit_fact_scope_locators",
    ):
        validate_business_fact_slot_document(document)


def test_complete_scope_requires_locator_authority() -> None:
    document = _document()
    document.pop("source_locator_authority")

    with pytest.raises(
        FactSlotGroundTruthValidationError,
        match="requires source_locator_authority",
    ):
        validate_business_fact_slot_document(document)


def test_scope_locator_duplicates_are_rejected() -> None:
    document = _document()
    document["explicit_fact_scope_locators"] = [LOCATOR, LOCATOR]

    with pytest.raises(
        FactSlotGroundTruthValidationError,
        match="must not contain duplicates",
    ):
        validate_business_fact_slot_document(document)


def test_annotated_locator_must_belong_to_closed_scope() -> None:
    document = deepcopy(_document())
    document["business_rules"][0]["source_locators"] = [
        "rules.md#line=99;chars=0-10"
    ]

    with pytest.raises(
        FactSlotGroundTruthValidationError,
        match="must belong to explicit_fact_scope_locators",
    ):
        validate_business_fact_slot_document(document)


def test_incomplete_scope_keeps_precision_authority_optional() -> None:
    document = _document()
    document["explicit_fact_scope_complete"] = False
    document["explicit_fact_scope_locators"] = []
    document.pop("source_locator_authority")

    result = validate_business_fact_slot_document(document)
    receipt = result["validation_receipt"]
    assert receipt["explicit_fact_scope_complete"] is False
    assert receipt["explicit_fact_scope_locator_count"] == 0
