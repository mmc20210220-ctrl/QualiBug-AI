"""Lineage gate for the existing cross-table delta assertion evaluator.

This module does not create another assertion kind or verdict engine. It freezes
the BEFORE/AFTER relation receipts to the exact relation_pair_id emitted by the
projection, then delegates numeric comparison to database_relation_delta_oracle.
"""
from __future__ import annotations

from typing import Any

from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
from .database_relation_delta_experiment_projection import ASSERTION_KIND
from .database_relation_delta_oracle import (
    evaluate_database_relation_delta_conservation as _evaluate_delta,
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


def evaluate_database_relation_delta_with_lineage(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    env = _dict(envelope)
    spec = _dict(env.get("spec"))
    observations = _dict(env.get("observations"))
    binding = _dict(spec.get("database_relation_delta_binding"))
    relation_ref = _text(spec.get("database_relation_observer_ref"))
    expected_pair_id = _text(
        spec.get("relation_pair_id") or binding.get("relation_pair_id")
    )
    before_draft_id = _text(
        spec.get("relation_before_draft_id")
        or binding.get("relation_before_draft_id")
    )
    after_draft_id = _text(
        spec.get("relation_after_draft_id")
        or binding.get("relation_after_draft_id")
    )
    expected = {
        "database_relation_observer_ref": relation_ref,
        "relation_pair_id": expected_pair_id,
        "relation_before_draft_id": before_draft_id,
        "relation_after_draft_id": after_draft_id,
    }
    actual: dict[str, Any] = {
        "relation_pair_match": False,
        "relation_before_lineage": {},
        "relation_after_lineage": {},
    }
    if not all((relation_ref, expected_pair_id, before_draft_id, after_draft_id)):
        return _reason(
            "DATABASE_RELATION_DELTA_ASSERTION_SPEC_INCOMPLETE",
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
    result_expected["relation_pair_id"] = expected_pair_id
    result_actual["relation_pair_match"] = True
    result_actual["relation_before_lineage"] = before
    result_actual["relation_after_lineage"] = after
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


__all__ = [
    "evaluate_database_relation_delta_with_lineage",
    "install_database_relation_delta_assertion",
]
