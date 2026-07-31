"""Preflight and semantic-freeze the one exact relation-delta projector.

This module does not create a second projector. It records malformed explicit
delta expressions, delegates every binding decision to the exact projector, and
then freezes each bound BEFORE/AFTER pair to the full assertion semantics.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .database_relation_delta_experiment_projection import (
    ASSERTION_KIND,
    _OPERATORS,
    _delta_operand,
    _dict,
    _fingerprint,
    _list,
    _stable_id,
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
_GENERIC_BINDING_GAP = "DATABASE_RELATION_DELTA_EXACT_BINDING_MISSING"
SEMANTIC_PAIR_SCHEMA = "qualibug.database-relation-delta-semantic-pair.v1"


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def semantic_relation_delta_pair_id(assertion: dict[str, Any]) -> str:
    """Return the deterministic pair id for all verdict-relevant semantics."""
    binding = _dict(assertion.get("database_relation_delta_binding"))
    source_refs = sorted(
        [deepcopy(row) for row in _list(assertion.get("source_refs"))],
        key=_canonical,
    )
    payload = {
        "schema": SEMANTIC_PAIR_SCHEMA,
        "assertion_id": _text(assertion.get("assertion_id")),
        "source_assertion_kind": _text(assertion.get("source_assertion_kind")),
        "source_refs": source_refs,
        "database_relation_observer_ref": _text(
            assertion.get("database_relation_observer_ref")
        ),
        "database_relationship_id": _text(
            assertion.get("database_relationship_id")
        ),
        "relation_mapping_decision_id": _text(
            binding.get("relation_mapping_decision_id")
        ),
        "relation_key": _list(assertion.get("relation_key")),
        "root_observer_contract_ref": _text(
            assertion.get("root_observer_contract_ref")
        ),
        "root_before_draft_id": _text(assertion.get("root_before_draft_id")),
        "root_after_draft_id": _text(assertion.get("root_after_draft_id")),
        "root_table_ref": _text(assertion.get("root_table_ref")),
        "root_field_binding_id": _text(
            assertion.get("root_field_binding_id")
        ),
        "root_database_field_id": _text(
            assertion.get("root_database_field_id")
        ),
        "root_database_field_name": _text(
            assertion.get("root_database_field_name")
        ),
        "child_table_ref": _text(assertion.get("child_table_ref")),
        "child_database_field_id": _text(
            assertion.get("child_database_field_id")
        ),
        "child_database_field_name": _text(
            assertion.get("child_database_field_name")
        ),
        "aggregate": _text(assertion.get("aggregate")).upper(),
        "aggregate_alias": _text(assertion.get("aggregate_alias")),
        "scope_count_alias": _text(assertion.get("scope_count_alias")),
        "comparison_operator": _text(
            assertion.get("comparison_operator")
        ).upper(),
        "aggregate_on_left": assertion.get("aggregate_on_left") is True,
        "left_coefficient": assertion.get("left_coefficient", 1),
        "right_coefficient": assertion.get("right_coefficient", 1),
        "tolerance": assertion.get("tolerance"),
        "comparison_phase_pair": _text(
            assertion.get("comparison_phase_pair")
        ),
    }
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return f"database_relation_delta_pair:{digest[:24]}"


def _freeze_semantic_pairs(
    experiment: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    row = deepcopy(experiment)
    assertions = [
        dict(raw)
        for raw in _list(row.get("assertions"))
        if isinstance(raw, dict)
    ]
    drafts = [
        dict(raw)
        for raw in _list(
            row.get("database_relation_observer_execution_drafts")
        )
        if isinstance(raw, dict)
    ]
    replacements: dict[str, dict[str, str]] = {}
    pair_count = 0

    for assertion in assertions:
        if _text(assertion.get("kind")) != ASSERTION_KIND:
            continue
        pair_count += 1
        old_pair_id = _text(assertion.get("relation_pair_id"))
        old_before_id = _text(assertion.get("relation_before_draft_id"))
        old_after_id = _text(assertion.get("relation_after_draft_id"))
        relation_ref = _text(assertion.get("database_relation_observer_ref"))
        assertion_id = _text(assertion.get("assertion_id"))
        new_pair_id = semantic_relation_delta_pair_id(assertion)
        new_before_id = _stable_id(
            "database_relation_observer_execution_draft",
            relation_ref,
            assertion_id,
            "BEFORE",
            new_pair_id,
        )
        new_after_id = _stable_id(
            "database_relation_observer_execution_draft",
            relation_ref,
            assertion_id,
            "AFTER",
            new_pair_id,
        )
        replacements[old_before_id] = {
            "draft_id": new_before_id,
            "relation_pair_id": new_pair_id,
        }
        replacements[old_after_id] = {
            "draft_id": new_after_id,
            "relation_pair_id": new_pair_id,
        }
        assertion["relation_pair_id"] = new_pair_id
        assertion["relation_before_draft_id"] = new_before_id
        assertion["relation_after_draft_id"] = new_after_id
        binding = _dict(assertion.get("database_relation_delta_binding"))
        binding.update(
            {
                "semantic_pair_schema": SEMANTIC_PAIR_SCHEMA,
                "previous_relation_pair_id": old_pair_id,
                "relation_pair_id": new_pair_id,
                "relation_before_draft_id": new_before_id,
                "relation_after_draft_id": new_after_id,
                "pair_covers_complete_assertion_semantics": True,
            }
        )
        assertion["database_relation_delta_binding"] = binding

    for draft in drafts:
        replacement = replacements.get(_text(draft.get("draft_id")))
        if replacement:
            draft.update(replacement)

    row["assertions"] = assertions
    row["database_relation_observer_execution_drafts"] = drafts
    bound = [
        assertion
        for assertion in assertions
        if _text(assertion.get("kind")) == ASSERTION_KIND
    ]
    if bound:
        fingerprint = _fingerprint(bound)
        row["database_relation_delta_assertion_fingerprint"] = fingerprint
        receipt = _dict(row.get("compile_receipt"))
        receipt.update(
            {
                "database_relation_delta_assertion_fingerprint": fingerprint,
                "database_relation_delta_semantic_pair_count": len(bound),
                "database_relation_delta_semantic_pair_schema": SEMANTIC_PAIR_SCHEMA,
            }
        )
        row["compile_receipt"] = receipt
    return row, pair_count


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
    """Record malformed expressions, bind exactly, then freeze semantic pairs."""
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
    incomplete_count = 0
    removed_generic_count = 0
    semantic_pair_count = 0
    experiments: list[dict[str, Any]] = []
    for raw_experiment in _list(projected.get("experiments")):
        frozen, frozen_count = _freeze_semantic_pairs(_dict(raw_experiment))
        experiment = frozen
        semantic_pair_count += frozen_count
        experiment_id = _text(
            experiment.get("experiment_id")
            or experiment.get("obligation_id")
        )
        problem_ids = {
            assertion_id
            for (candidate_experiment_id, assertion_id), _reason_code in problems.items()
            if candidate_experiment_id == experiment_id
        }
        gaps: list[dict[str, Any]] = []
        for raw_gap in _list(
            experiment.get("database_relation_delta_projection_gaps")
        ):
            if not isinstance(raw_gap, dict):
                continue
            gap = dict(raw_gap)
            if (
                _text(gap.get("assertion_id")) in problem_ids
                and _text(gap.get("reason_code")) == _GENERIC_BINDING_GAP
            ):
                removed_generic_count += 1
                continue
            gaps.append(gap)
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
    prior_incomplete = int(summary.get("incomplete_assertion_count") or 0)
    summary["incomplete_assertion_count"] = max(
        0,
        prior_incomplete - removed_generic_count + incomplete_count,
    )
    summary["malformed_explicit_expression_count"] = incomplete_count
    summary["replaced_generic_gap_count"] = removed_generic_count
    summary["semantic_pair_count"] = semantic_pair_count
    summary["semantic_pair_schema"] = SEMANTIC_PAIR_SCHEMA
    summary["automatic_expression_repair_count"] = 0
    if incomplete_count and _text(summary.get("status")).upper() == "PASS":
        summary["status"] = "PARTIAL"
    projected["database_relation_delta_experiment_projection"] = summary
    return projected


__all__ = [
    "SEMANTIC_PAIR_SCHEMA",
    "project_database_relation_delta_assertions",
    "semantic_relation_delta_pair_id",
]
