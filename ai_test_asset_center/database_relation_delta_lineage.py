"""Lineage and semantic-integrity gate for the cross-table delta evaluator.

This module does not create another assertion kind or verdict engine. It verifies
that the compiled rule, approved bindings and BEFORE/AFTER relation receipts still
describe the same complete semantics, then delegates numeric comparison to the
existing relation-delta evaluator.
"""
from __future__ import annotations

from typing import Any

from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
from .database_relation_delta_experiment_projection import ASSERTION_KIND
from .database_relation_delta_oracle import (
    _decimal,
    evaluate_database_relation_delta_conservation as _evaluate_delta,
)
from .database_relation_delta_projection_gate import (
    SEMANTIC_PAIR_SCHEMA,
    semantic_relation_delta_pair_id,
)

_RELATION_RECEIPT_KEY = "approved_database_relation_phase_receipts"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _reason(
    code: str,
    *,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> dict[str, Any]:
    return {
        "passed": None,
        "reason_code": code,
        "expected": expected,
        "actual": actual,
    }


def _phase_receipt(
    observations: dict[str, Any],
    *,
    relation_ref: str,
    draft_id: str,
    phase: str,
) -> tuple[dict[str, Any], str]:
    target = _text(phase).upper()
    rows = [
        _dict(raw)
        for raw in _list(observations.get(_RELATION_RECEIPT_KEY))
        if _text(_dict(raw).get("relation_observer_contract_ref")) == relation_ref
        and _text(_dict(raw).get("draft_id")) == draft_id
        and _text(_dict(raw).get("observation_phase")).upper() == target
    ]
    if len(rows) != 1:
        return {"phase": target, "candidate_count": len(rows)}, (
            "DATABASE_RELATION_DELTA_PHASE_RECEIPT_MISSING"
            if len(rows) < 1
            else "DATABASE_RELATION_DELTA_PHASE_RECEIPT_AMBIGUOUS"
        )
    row = rows[0]
    return {
        "phase": target,
        "draft_id": _text(row.get("draft_id")),
        "relation_pair_id": _text(row.get("relation_pair_id")),
        "phase_receipt_id": _text(
            row.get("phase_receipt_id") or row.get("receipt_id")
        ),
        "receipt_id": _text(row.get("receipt_id")),
        "campaign_id": _text(row.get("campaign_id")),
        "execution_id": _text(row.get("execution_id")),
    }, ""


def _merge_pair_lineage(
    snapshot: Any,
    *,
    pair_id: str,
    lineage: dict[str, Any],
) -> dict[str, Any]:
    row = _dict(snapshot)
    row["relation_pair_id"] = pair_id
    row["phase_receipt_id"] = _text(
        row.get("phase_receipt_id") or lineage.get("phase_receipt_id")
    )
    return row


def evaluate_database_relation_delta_with_lineage(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    env = _dict(envelope)
    spec = _dict(env.get("spec"))
    observations = _dict(env.get("observations"))
    binding = _dict(spec.get("database_relation_delta_binding"))
    relation_ref = _text(spec.get("database_relation_observer_ref"))
    expected_pair_id = _text(spec.get("relation_pair_id"))
    before_draft_id = _text(spec.get("relation_before_draft_id"))
    after_draft_id = _text(spec.get("relation_after_draft_id"))
    comparison_phase_pair = _text(spec.get("comparison_phase_pair")).upper()
    comparison_operator = _text(spec.get("comparison_operator")).upper()
    aggregate_on_left = spec.get("aggregate_on_left")
    left_coefficient = _decimal(spec.get("left_coefficient"))
    right_coefficient = _decimal(spec.get("right_coefficient"))
    source_refs = [
        dict(row)
        for row in _list(spec.get("source_refs"))
        if isinstance(row, dict) and row
    ]
    root_field_binding_id = _text(spec.get("root_field_binding_id"))
    relation_mapping_decision_id = _text(
        binding.get("relation_mapping_decision_id")
    )
    recomputed_pair_id = semantic_relation_delta_pair_id(spec)

    expected = {
        "database_relation_observer_ref": relation_ref,
        "semantic_pair_schema": SEMANTIC_PAIR_SCHEMA,
        "relation_pair_id": expected_pair_id,
        "recomputed_relation_pair_id": recomputed_pair_id,
        "relation_before_draft_id": before_draft_id,
        "relation_after_draft_id": after_draft_id,
        "comparison_phase_pair": "BEFORE_AFTER",
        "comparison_operator": comparison_operator,
        "aggregate_on_left": aggregate_on_left,
        "left_coefficient": spec.get("left_coefficient"),
        "right_coefficient": spec.get("right_coefficient"),
        "source_refs": source_refs,
        "root_field_binding_id": root_field_binding_id,
        "relation_mapping_decision_id": relation_mapping_decision_id,
    }
    actual: dict[str, Any] = {
        "source_evidence_present": bool(source_refs),
        "approved_binding_ids_present": bool(
            root_field_binding_id and relation_mapping_decision_id
        ),
        "binding_match": False,
        "semantic_pair_match": False,
        "relation_pair_match": False,
        "relation_before_lineage": {},
        "relation_after_lineage": {},
    }

    explicit_fields_valid = bool(
        relation_ref
        and expected_pair_id
        and before_draft_id
        and after_draft_id
        and comparison_phase_pair == "BEFORE_AFTER"
        and comparison_operator
        and isinstance(aggregate_on_left, bool)
        and "left_coefficient" in spec
        and "right_coefficient" in spec
    )
    if not explicit_fields_valid:
        return _reason(
            "DATABASE_RELATION_DELTA_ASSERTION_SPEC_INCOMPLETE",
            expected=expected,
            actual=actual,
        )
    if not source_refs:
        return _reason(
            "DATABASE_RELATION_DELTA_SOURCE_EVIDENCE_MISSING",
            expected=expected,
            actual=actual,
        )
    if not root_field_binding_id or not relation_mapping_decision_id:
        return _reason(
            "DATABASE_RELATION_DELTA_APPROVED_BINDING_MISSING",
            expected=expected,
            actual=actual,
        )
    if left_coefficient is None or right_coefficient is None:
        return _reason(
            "DATABASE_RELATION_DELTA_COEFFICIENT_INVALID",
            expected=expected,
            actual=actual,
        )
    if left_coefficient == 0 and right_coefficient == 0:
        return _reason(
            "DATABASE_RELATION_DELTA_VACUOUS_COEFFICIENTS",
            expected=expected,
            actual=actual,
        )

    binding_match = bool(
        _text(binding.get("semantic_pair_schema")) == SEMANTIC_PAIR_SCHEMA
        and binding.get("pair_covers_complete_assertion_semantics") is True
        and _text(binding.get("relation_pair_id")) == expected_pair_id
        and _text(binding.get("relation_before_draft_id")) == before_draft_id
        and _text(binding.get("relation_after_draft_id")) == after_draft_id
    )
    actual["binding_match"] = binding_match
    if not binding_match:
        return _reason(
            "DATABASE_RELATION_DELTA_BINDING_MISMATCH",
            expected=expected,
            actual=actual,
        )

    semantic_pair_match = recomputed_pair_id == expected_pair_id
    actual["semantic_pair_match"] = semantic_pair_match
    if not semantic_pair_match:
        return _reason(
            "DATABASE_RELATION_DELTA_SEMANTIC_PAIR_MISMATCH",
            expected=expected,
            actual=actual,
        )

    before, before_reason = _phase_receipt(
        observations,
        relation_ref=relation_ref,
        draft_id=before_draft_id,
        phase="BEFORE",
    )
    after, after_reason = _phase_receipt(
        observations,
        relation_ref=relation_ref,
        draft_id=after_draft_id,
        phase="AFTER",
    )
    actual["relation_before_lineage"] = before
    actual["relation_after_lineage"] = after
    if before_reason or after_reason:
        return _reason(
            before_reason or after_reason,
            expected=expected,
            actual=actual,
        )

    before_pair = _text(before.get("relation_pair_id"))
    after_pair = _text(after.get("relation_pair_id"))
    if not before_pair or not after_pair:
        return _reason(
            "DATABASE_RELATION_DELTA_PAIR_LINEAGE_MISSING",
            expected=expected,
            actual=actual,
        )
    pair_match = bool(
        before_pair == expected_pair_id
        and after_pair == expected_pair_id
        and before_pair == after_pair
    )
    actual["relation_pair_match"] = pair_match
    if not pair_match:
        return _reason(
            "DATABASE_RELATION_DELTA_PAIR_LINEAGE_MISMATCH",
            expected=expected,
            actual=actual,
        )

    result = _evaluate_delta(env)
    if not isinstance(result, dict):
        raise TypeError("database relation delta evaluator returned non-dict")
    result = dict(result)
    result_expected = _dict(result.get("expected"))
    result_actual = _dict(result.get("actual"))
    result_expected.update(expected)
    result_actual["source_evidence_present"] = True
    result_actual["approved_binding_ids_present"] = True
    result_actual["binding_match"] = True
    result_actual["semantic_pair_match"] = True
    result_actual["relation_pair_match"] = True
    result_actual["relation_before_lineage"] = before
    result_actual["relation_after_lineage"] = after
    result_actual["relation_before_snapshot"] = _merge_pair_lineage(
        result_actual.get("relation_before_snapshot"),
        pair_id=expected_pair_id,
        lineage=before,
    )
    result_actual["relation_after_snapshot"] = _merge_pair_lineage(
        result_actual.get("relation_after_snapshot"),
        pair_id=expected_pair_id,
        lineage=after,
    )
    result["expected"] = result_expected
    result["actual"] = result_actual
    return result


def install_database_relation_delta_assertion() -> str:
    if ASSERTION_KIND in registered_assertion_kinds():
        return ASSERTION_KIND
    return register_assertion_kind(
        ASSERTION_KIND,
        evaluator=evaluate_database_relation_delta_with_lineage,
        required_evidence_keys=(
            "approved_database_observer_phase_receipts",
            _RELATION_RECEIPT_KEY,
        ),
    )


# Close the latent bypass where another module later imports the base installer.
# Both names now install the same lineage-gated evaluator; no second kind is added.
from . import database_relation_delta_oracle as _base_oracle  # noqa: E402

_base_oracle.install_database_relation_delta_assertion = (
    install_database_relation_delta_assertion
)


__all__ = [
    "evaluate_database_relation_delta_with_lineage",
    "install_database_relation_delta_assertion",
]
