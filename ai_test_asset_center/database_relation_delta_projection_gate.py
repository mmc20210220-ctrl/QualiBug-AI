"""Preflight explicit relation-delta expressions before exact binding projection.

This is a visibility gate around the one relation-delta projector, not a second
projector. It records malformed explicit delta expressions as compile gaps and
then delegates every binding decision to database_relation_delta_experiment_projection.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .database_relation_delta_experiment_projection import (
    _OPERATORS,
    _delta_operand,
    _dict,
    _list,
    _source_kind,
    _text,
    project_database_relation_delta_assertions as _project_exact,
)

_EXPLICIT_EXPRESSION_TYPES = frozenset(
    {
        "delta_conservation",
        "relation_delta_conservation",
        "cross_entity_delta_conservation",
    }
)


def _explicit_problem(assertion: dict[str, Any]) -> str:
    expr = _dict(assertion.get("structured_expression"))
    expression_type = _text(
        expr.get("type") or expr.get("node_type")
    ).lower()
    left, _ = _delta_operand(_dict(expr.get("left")))
    right, _ = _delta_operand(_dict(expr.get("right")))
    explicit = bool(
        expression_type in _EXPLICIT_EXPRESSION_TYPES
        or (left and right)
    )
    if not explicit:
        return ""
    if not left or not right:
        return "DATABASE_RELATION_DELTA_BOTH_DELTA_OPERANDS_REQUIRED"
    operator = _text(
        expr.get("operator") or assertion.get("operator")
    ).upper()
    if not operator:
        return "DATABASE_RELATION_DELTA_COMPARISON_OPERATOR_MISSING"
    if operator not in _OPERATORS:
        return "DATABASE_RELATION_DELTA_COMPARISON_OPERATOR_UNSUPPORTED"
    return ""


def project_database_relation_delta_assertions(
    experiment_pack: dict[str, Any],
) -> dict[str, Any]:
    """Record malformed explicit expressions, then run the exact projector."""
    source = deepcopy(dict(experiment_pack or {}))
    problems: dict[tuple[str, str], str] = {}
    for raw_experiment in _list(source.get("experiments")):
        experiment = _dict(raw_experiment)
        experiment_id = _text(
            experiment.get("experiment_id")
            or experiment.get("obligation_id")
        )
        for raw_assertion in _list(experiment.get("assertions")):
            assertion = _dict(raw_assertion)
            reason = _explicit_problem(assertion)
            if reason:
                problems[
                    (
                        experiment_id,
                        _text(assertion.get("assertion_id")),
                    )
                ] = reason

    projected = _project_exact(source)
    if not problems:
        return projected

    incomplete_count = 0
    experiments: list[dict[str, Any]] = []
    for raw_experiment in _list(projected.get("experiments")):
        experiment = deepcopy(_dict(raw_experiment))
        experiment_id = _text(
            experiment.get("experiment_id")
            or experiment.get("obligation_id")
        )
        gaps = [
            dict(row)
            for row in _list(
                experiment.get("database_relation_delta_projection_gaps")
            )
            if isinstance(row, dict)
        ]
        existing = {
            (
                _text(row.get("assertion_id")),
                _text(row.get("reason_code")),
            )
            for row in gaps
        }
        for raw_assertion in _list(experiment.get("assertions")):
            assertion = _dict(raw_assertion)
            assertion_id = _text(assertion.get("assertion_id"))
            reason = problems.get((experiment_id, assertion_id), "")
            if not reason or (assertion_id, reason) in existing:
                continue
            gaps.append(
                {
                    "assertion_id": assertion_id,
                    "reason_code": reason,
                    "explicit_relation_delta_expression": True,
                    "automatic_expression_repair_allowed": False,
                    "automatic_sign_inference_allowed": False,
                }
            )
            incomplete_count += 1
        if gaps:
            experiment["database_relation_delta_projection_gaps"] = gaps
            current = _text(
                experiment.get("database_relation_delta_projection_status")
            ).upper()
            experiment["database_relation_delta_projection_status"] = (
                "PARTIAL" if current == "BOUND" else "INCOMPLETE"
            )
            receipt = _dict(experiment.get("compile_receipt"))
            receipt["database_relation_delta_projection_status"] = experiment[
                "database_relation_delta_projection_status"
            ]
            receipt["database_relation_delta_expression_repair_count"] = 0
            experiment["compile_receipt"] = receipt
        experiments.append(experiment)
    projected["experiments"] = experiments

    summary = _dict(
        projected.get("database_relation_delta_experiment_projection")
    )
    summary["incomplete_assertion_count"] = int(
        summary.get("incomplete_assertion_count") or 0
    ) + incomplete_count
    summary["malformed_explicit_expression_count"] = incomplete_count
    summary["automatic_expression_repair_count"] = 0
    if incomplete_count and _text(summary.get("status")).upper() == "PASS":
        summary["status"] = "PARTIAL"
    projected["database_relation_delta_experiment_projection"] = summary
    return projected


__all__ = ["project_database_relation_delta_assertions"]
