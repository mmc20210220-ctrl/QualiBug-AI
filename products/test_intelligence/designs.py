from __future__ import annotations

"""Deterministic Test Design projection over source-backed Test Obligations.

Test Design v1 remains upstream of runtime grounding. It structures what must be
prepared, acted on, observed, and judged without inventing API paths, UI steps,
test accounts, concrete fixture values, environment selection, or executable
bindings that are not already proven by the source-backed obligation.
"""

import hashlib
from copy import deepcopy
from typing import Any

from .obligations import OBLIGATION_SCHEMA

TEST_DESIGN_SCHEMA = "qualibug.test-design.v1"
TEST_DESIGN_PROJECTION_SCHEMA = "qualibug.test-design-projection.v1"
TEST_DESIGN_QUALITY_CLAIM = (
    "DETERMINISTIC_OBLIGATION_DERIVED_TEST_DESIGN_NOT_RUNTIME_GROUNDING_OR_EXECUTION"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({_text(item) for item in value if _text(item)})


def _copy_structured(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    copied: list[Any] = []
    for item in value:
        if isinstance(item, dict):
            copied.append(deepcopy(item))
        elif _text(item):
            copied.append(_text(item))
    return copied


def _design_id(obligation_id: str) -> str:
    digest = hashlib.sha256(obligation_id.encode("utf-8")).hexdigest()[:20]
    return f"test-design:{digest}"


def _observation_from_outcome(
    outcome: dict[str, Any],
    *,
    object_refs: list[str],
) -> dict[str, Any]:
    kind = _text(outcome.get("kind"))
    observation: dict[str, Any] = {
        "observation_kind": kind or "semantic_outcome",
        "binding_status": "NOT_GROUNDED",
        "expected": deepcopy(outcome),
    }
    if kind == "authorization_decision":
        observation["target"] = "operation_authorization_result"
    elif kind == "lifecycle_transition":
        observation["target"] = "business_object_state"
        observation["object_refs"] = list(object_refs)
    elif kind == "business_modality":
        observation["target"] = "business_operation_outcome"
    elif kind == "data_effect":
        observation["target"] = "business_data_effect"
        field = _text(outcome.get("field"))
        object_ref = _text(outcome.get("object"))
        if field:
            observation["field"] = field
        if object_ref:
            observation["object_ref"] = object_ref
    elif kind == "postcondition":
        observation["target"] = "business_postcondition"
    elif kind == "compensation":
        observation["target"] = "business_compensation_result"
    else:
        observation["target"] = "semantic_outcome"
    return observation


def _project_design(obligation: dict[str, Any]) -> dict[str, Any] | None:
    if _text(obligation.get("schema")) != OBLIGATION_SCHEMA:
        return None

    obligation_id = _text(obligation.get("obligation_id"))
    title = _text(obligation.get("title"))
    objective = _text(obligation.get("objective"))
    evidence = _rows(obligation.get("evidence"))
    expected_outcomes = _rows(obligation.get("expected_outcomes"))
    if not obligation_id or not title or not objective or not evidence or not expected_outcomes:
        return None

    object_refs = _strings(obligation.get("object_refs"))
    actor_refs = _strings(obligation.get("actor_refs"))
    operation_ref = _text(obligation.get("operation_ref"))
    preconditions = _copy_structured(obligation.get("preconditions"))
    observations = [
        _observation_from_outcome(outcome, object_refs=object_refs)
        for outcome in expected_outcomes
    ]

    return {
        "schema": TEST_DESIGN_SCHEMA,
        "design_id": _design_id(obligation_id),
        "source_obligation_id": obligation_id,
        "obligation_kind": _text(obligation.get("obligation_kind")),
        "title": f"测试设计：{title}",
        "objective": objective,
        "setup": {
            "actor_refs": actor_refs,
            "object_refs": object_refs,
            "preconditions": preconditions,
            "test_data_requirements": deepcopy(preconditions),
            "test_data_materialization_status": "NOT_MATERIALIZED",
            "environment_status": "NOT_SELECTED",
        },
        "action": {
            "operation_ref": operation_ref,
            "actor_refs": actor_refs,
            "object_refs": object_refs,
            "execution_surface": "NOT_SELECTED",
            "binding_status": "NOT_GROUNDED",
        },
        "observations": observations,
        "oracle": {
            "assertions": deepcopy(expected_outcomes),
            "semantic_status": "SOURCE_DERIVED",
            "binding_status": "NOT_GROUNDED",
        },
        "business_constraints": _strings(obligation.get("business_constraints")),
        "source_refs": _strings(obligation.get("source_refs")),
        "source_ids": _strings(obligation.get("source_ids")),
        "evidence": deepcopy(evidence),
        "derived_from": [
            {
                "kind": "test_obligation",
                "id": obligation_id,
            }
        ],
        "requirement_finding_ids": _strings(
            obligation.get("requirement_finding_ids")
        ),
        "design_status": "STRUCTURED_DESIGN_ONLY",
        "observer_binding_status": "NOT_GROUNDED",
        "oracle_binding_status": "NOT_GROUNDED",
        "runtime_handoff_status": "NOT_REQUESTED",
        "execution_status": "NOT_EXECUTED",
        "safety_review_status": "NOT_ASSESSED",
    }


def project_test_designs(obligations: Any) -> dict[str, Any]:
    """Project structured designs without selecting or executing a runtime surface."""

    source_obligations = _rows(obligations)
    eligible_ids = sorted(
        {
            _text(item.get("obligation_id"))
            for item in source_obligations
            if _text(item.get("schema")) == OBLIGATION_SCHEMA
            and _text(item.get("obligation_id"))
        }
    )
    designs: list[dict[str, Any]] = []
    for obligation in source_obligations:
        design = _project_design(obligation)
        if design is not None:
            designs.append(design)

    designs.sort(key=lambda item: _text(item.get("design_id")))
    designed_obligation_ids = sorted(
        {
            _text(item.get("source_obligation_id"))
            for item in designs
            if _text(item.get("source_obligation_id"))
        }
    )
    undesigned_ids = sorted(set(eligible_ids) - set(designed_obligation_ids))
    if not eligible_ids:
        status = "NOT_MEASURED"
    elif undesigned_ids:
        status = "PARTIAL"
    else:
        status = "DESIGNED"

    return {
        "schema": TEST_DESIGN_PROJECTION_SCHEMA,
        "status": status,
        "quality_claim": TEST_DESIGN_QUALITY_CLAIM,
        "eligible_obligation_count": len(eligible_ids),
        "designed_obligation_count": len(designed_obligation_ids),
        "undesigned_obligation_count": len(undesigned_ids),
        "undesigned_obligation_ids": undesigned_ids,
        "runtime_grounding_status": "NOT_GROUNDED",
        "runtime_execution_status": "NOT_EXECUTED",
        "designs": designs,
    }
