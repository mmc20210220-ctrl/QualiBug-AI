"""Runtime authority gate for operation-attributed relation deltas.

The approved relation decision fingerprints the complete read-only child field
catalog. Therefore the causal correlation field must use that same decision; a
second arbitrary non-empty decision id is not authority. This module tightens the
same causal assertion kind in place and delegates all semantic/receipt/numeric
checks to the existing integrity evaluator.
"""
from __future__ import annotations

from typing import Any

from .assertion_dsl_base import (
    _REGISTERED_ASSERTION_EVALUATORS,
    _REGISTERED_KIND_EVIDENCE_KEYS,
)
from .database_relation_delta_causality_integrity import (
    evaluate_database_relation_causal_delta_with_integrity,
    install_database_relation_causal_delta_assertion as _install_integrity,
)
from .database_relation_delta_causality_projection import ASSERTION_KIND
from .operation_causality_runtime import TRANSPORT_KEY

_REQUIRED_EVIDENCE_KEYS = (
    "approved_database_observer_phase_receipts",
    "approved_database_relation_phase_receipts",
    TRANSPORT_KEY,
)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


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


def evaluate_database_relation_causal_delta_with_authority(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    env = _dict(envelope)
    spec = _dict(env.get("spec"))
    causal = _dict(spec.get("causal_attribution_contract"))
    binding = _dict(spec.get("database_relation_delta_binding"))
    causal_decision = _text(causal.get("mapping_decision_id"))
    bound_causal_decision = _text(binding.get("causal_mapping_decision_id"))
    relation_decision = _text(binding.get("relation_mapping_decision_id"))
    expected = {
        "causal_mapping_decision_id": causal_decision,
        "bound_causal_mapping_decision_id": bound_causal_decision,
        "relation_mapping_decision_id": relation_decision,
        "authority_basis": "APPROVED_DATABASE_RELATION_FIELD_CATALOG",
    }
    actual: dict[str, Any] = {
        "relation_authority_match": False,
        "automatic_authority_selection_used": False,
    }
    if not all((causal_decision, bound_causal_decision, relation_decision)):
        return _reason(
            "DATABASE_RELATION_CAUSAL_APPROVED_BINDING_INCOMPLETE",
            expected=expected,
            actual=actual,
        )
    authority_match = bool(
        causal_decision == bound_causal_decision == relation_decision
    )
    actual["relation_authority_match"] = authority_match
    if not authority_match:
        return _reason(
            "DATABASE_RELATION_CAUSAL_RELATION_AUTHORITY_MISMATCH",
            expected=expected,
            actual=actual,
        )

    result = evaluate_database_relation_causal_delta_with_integrity(env)
    if not isinstance(result, dict):
        raise TypeError("causal integrity evaluator returned non-dict")
    output = dict(result)
    result_expected = _dict(output.get("expected"))
    result_actual = _dict(output.get("actual"))
    result_expected.update(expected)
    result_actual["relation_authority_match"] = True
    result_actual["automatic_authority_selection_used"] = False
    output["expected"] = result_expected
    output["actual"] = result_actual
    return output


def install_database_relation_causal_delta_assertion() -> str:
    _install_integrity()
    _REGISTERED_ASSERTION_EVALUATORS[ASSERTION_KIND] = (
        evaluate_database_relation_causal_delta_with_authority
    )
    _REGISTERED_KIND_EVIDENCE_KEYS[ASSERTION_KIND] = _REQUIRED_EVIDENCE_KEYS
    return ASSERTION_KIND


# Redirect every installer entrypoint to the same authority-gated registration.
from . import database_relation_delta_causality_integrity as _integrity  # noqa: E402
from . import database_relation_delta_causality_oracle as _base  # noqa: E402

_integrity.install_database_relation_causal_delta_assertion = (
    install_database_relation_causal_delta_assertion
)
_base.install_database_relation_causal_delta_assertion = (
    install_database_relation_causal_delta_assertion
)


__all__ = [
    "evaluate_database_relation_causal_delta_with_authority",
    "install_database_relation_causal_delta_assertion",
]
