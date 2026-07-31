"""Runtime authority gate for operation-attributed relation deltas.

The approved relation decision fingerprints the complete read-only child field
catalog. Therefore the causal correlation field must use that same decision; a
second arbitrary non-empty decision id is not authority. The same decision must
also be carried by both database relation phase receipts.
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
from .database_relation_observer_runtime import EVIDENCE_KEY as RELATION_EVIDENCE_KEY
from .operation_causality_runtime import TRANSPORT_KEY

_RELATION_RECEIPT_KEY = "approved_database_relation_phase_receipts"
_REQUIRED_EVIDENCE_KEYS = (
    "approved_database_observer_phase_receipts",
    _RELATION_RECEIPT_KEY,
    TRANSPORT_KEY,
)


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


def _phase_authority_scope(
    observations: dict[str, Any],
    *,
    relation_ref: str,
    draft_id: str,
    phase: str,
) -> tuple[dict[str, Any], bool | None]:
    target = _text(phase).upper()
    matches = [
        _dict(raw)
        for raw in _list(observations.get(_RELATION_RECEIPT_KEY))
        if _text(_dict(raw).get("relation_observer_contract_ref")) == relation_ref
        and _text(_dict(raw).get("draft_id")) == draft_id
        and _text(_dict(raw).get("observation_phase")).upper() == target
    ]
    if len(matches) != 1:
        return {"phase": target, "candidate_count": len(matches)}, None
    receipt = matches[0]
    payload = _dict(_dict(receipt.get("evidence")).get(RELATION_EVIDENCE_KEY))
    scope = _dict(payload.get("causal_attribution_scope"))
    return {
        "phase": target,
        "draft_id": _text(receipt.get("draft_id")),
        "receipt_id": _text(receipt.get("receipt_id")),
        "mapping_decision_id": _text(scope.get("mapping_decision_id")),
        "relation_mapping_decision_id": _text(
            scope.get("relation_mapping_decision_id")
        ),
        "relation_authority_match": scope.get("relation_authority_match") is True,
    }, True


def evaluate_database_relation_causal_delta_with_authority(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    env = _dict(envelope)
    spec = _dict(env.get("spec"))
    observations = _dict(env.get("observations"))
    causal = _dict(spec.get("causal_attribution_contract"))
    binding = _dict(spec.get("database_relation_delta_binding"))
    causal_decision = _text(causal.get("mapping_decision_id"))
    bound_causal_decision = _text(binding.get("causal_mapping_decision_id"))
    relation_decision = _text(binding.get("relation_mapping_decision_id"))
    relation_ref = _text(spec.get("database_relation_observer_ref"))
    before_draft_id = _text(spec.get("relation_before_draft_id"))
    after_draft_id = _text(spec.get("relation_after_draft_id"))
    expected = {
        "causal_mapping_decision_id": causal_decision,
        "bound_causal_mapping_decision_id": bound_causal_decision,
        "relation_mapping_decision_id": relation_decision,
        "authority_basis": "APPROVED_DATABASE_RELATION_FIELD_CATALOG",
    }
    actual: dict[str, Any] = {
        "relation_authority_match": False,
        "relation_receipt_authority_match": False,
        "relation_before_authority_scope": {},
        "relation_after_authority_scope": {},
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

    before_scope, before_available = _phase_authority_scope(
        observations,
        relation_ref=relation_ref,
        draft_id=before_draft_id,
        phase="BEFORE",
    )
    after_scope, after_available = _phase_authority_scope(
        observations,
        relation_ref=relation_ref,
        draft_id=after_draft_id,
        phase="AFTER",
    )
    actual["relation_before_authority_scope"] = before_scope
    actual["relation_after_authority_scope"] = after_scope
    if before_available is True and after_available is True:
        receipt_authority_match = bool(
            before_scope.get("mapping_decision_id") == relation_decision
            and before_scope.get("relation_mapping_decision_id")
            == relation_decision
            and before_scope.get("relation_authority_match") is True
            and after_scope.get("mapping_decision_id") == relation_decision
            and after_scope.get("relation_mapping_decision_id")
            == relation_decision
            and after_scope.get("relation_authority_match") is True
        )
        actual["relation_receipt_authority_match"] = receipt_authority_match
        if not receipt_authority_match:
            return _reason(
                "DATABASE_RELATION_CAUSAL_RECEIPT_AUTHORITY_MISMATCH",
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
    if before_available is True and after_available is True:
        result_actual["relation_receipt_authority_match"] = True
        result_actual["relation_before_authority_scope"] = before_scope
        result_actual["relation_after_authority_scope"] = after_scope
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
