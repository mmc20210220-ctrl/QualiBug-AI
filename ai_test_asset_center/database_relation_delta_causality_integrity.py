"""Semantic and receipt integrity gate for causal relation-delta assertions.

This module owns the one formal registration for the causal assertion kind and
operation-causality Observer. If a weaker handler was registered earlier, the same
registry entry is tightened in place; no second kind or Observer is created.
Contract Oracle remains the verdict authority.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .assertion_dsl_base import (
    _REGISTERED_ASSERTION_EVALUATORS,
    _REGISTERED_KIND_EVIDENCE_KEYS,
    register_assertion_kind,
    registered_assertion_kinds,
)
from .database_relation_delta_causality_oracle import (
    OBSERVER_ID,
    evaluate_database_relation_causal_delta as _evaluate_causal_delta,
)
from .database_relation_delta_causality_projection import (
    ASSERTION_KIND,
    PROJECTION_SCHEMA,
)
from .observer_contracts_base import (
    OBSERVER_REGISTRY,
    _REGISTERED_OBSERVER_HANDLERS,
    _receipt,
    register_observer,
    registered_observer_ids,
)
from .operation_causality_receipt_integrity import (
    validate_operation_causality_transport_receipt,
)
from .operation_causality_runtime import TRANSPORT_KEY

_REQUIRED_EVIDENCE_KEYS = (
    "approved_database_observer_phase_receipts",
    "approved_database_relation_phase_receipts",
    TRANSPORT_KEY,
)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def causal_scope_fingerprint(spec: dict[str, Any]) -> str:
    """Recompute every verdict-relevant operation-attribution semantic."""
    row = _dict(spec)
    causal = deepcopy(_dict(row.get("causal_attribution_contract")))
    causal.pop("causal_scope_fingerprint", None)
    payload = {
        "schema": PROJECTION_SCHEMA,
        "base_relation_pair_id": _text(row.get("relation_pair_id")),
        "causal_attribution": causal,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


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


def observe_operation_causality_with_integrity(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    env = _dict(envelope)
    observations = _dict(env.get("observations"))
    raw = [
        dict(row)
        for row in _list(observations.get(TRANSPORT_KEY))
        if isinstance(row, dict)
    ]
    validated: list[dict[str, Any]] = []
    failures: list[str] = []
    for row in raw:
        try:
            validated.append(
                validate_operation_causality_transport_receipt(row)
            )
        except ValueError as exc:
            failures.append(str(exc))
    attributed = [
        row for row in validated if _text(row.get("status")) == "ATTRIBUTED"
    ]
    complete = bool(validated) and not failures and len(attributed) == len(validated)
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED" if complete else "INDETERMINATE",
        reason_code=(
            ""
            if complete
            else "OPERATION_CAUSALITY_TRANSPORT_RECEIPT_INVALID"
            if failures
            else "OPERATION_CAUSALITY_TRANSPORT_RECEIPT_INCOMPLETE"
        ),
        evidence={
            TRANSPORT_KEY: validated,
            "receipt_count": len(raw),
            "validated_receipt_count": len(validated),
            "attributed_receipt_count": len(attributed),
            "integrity_failure_count": len(failures),
            "integrity_failure_codes": sorted(set(failures)),
            "raw_causal_value_retained": False,
            "timestamp_window_attribution_used": False,
            "oracle_verdict_emitted": False,
        },
        campaign_id=_text(env.get("campaign_id")),
        execution_id=_text(env.get("execution_id")),
    )


def evaluate_database_relation_causal_delta_with_integrity(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    env = _dict(envelope)
    spec = _dict(env.get("spec"))
    observations = _dict(env.get("observations"))
    binding = _dict(spec.get("database_relation_delta_binding"))
    declared_scope = _text(spec.get("causal_scope_fingerprint"))
    binding_scope = _text(binding.get("causal_scope_fingerprint"))
    recomputed_scope = causal_scope_fingerprint(spec)
    expected = {
        "causal_scope_fingerprint": declared_scope,
        "binding_causal_scope_fingerprint": binding_scope,
        "recomputed_causal_scope_fingerprint": recomputed_scope,
    }
    actual: dict[str, Any] = {
        "causal_scope_semantic_match": False,
        "transport_receipt_integrity_valid": False,
        "validated_transport_receipt": {},
    }
    if not declared_scope or not binding_scope:
        return _reason(
            "DATABASE_RELATION_CAUSAL_ASSERTION_SPEC_INCOMPLETE",
            expected=expected,
            actual=actual,
        )
    scope_match = bool(
        declared_scope == binding_scope == recomputed_scope
    )
    actual["causal_scope_semantic_match"] = scope_match
    if not scope_match:
        return _reason(
            "DATABASE_RELATION_CAUSAL_SCOPE_SEMANTIC_MISMATCH",
            expected=expected,
            actual=actual,
        )

    causal = _dict(spec.get("causal_attribution_contract"))
    operation_ref = _text(causal.get("operation_ref"))
    candidates = [
        dict(row)
        for row in _list(observations.get(TRANSPORT_KEY))
        if isinstance(row, dict)
        and _text(row.get("causal_scope_fingerprint")) == declared_scope
        and _text(row.get("operation_ref")) == operation_ref
    ]
    if len(candidates) != 1:
        actual["transport_candidate_count"] = len(candidates)
        return _reason(
            (
                "DATABASE_RELATION_CAUSAL_TRANSPORT_RECEIPT_MISSING"
                if len(candidates) < 1
                else "DATABASE_RELATION_CAUSAL_TRANSPORT_RECEIPT_AMBIGUOUS"
            ),
            expected=expected,
            actual=actual,
        )
    try:
        validated = validate_operation_causality_transport_receipt(
            candidates[0]
        )
    except ValueError as exc:
        actual["transport_receipt_integrity_error"] = str(exc)
        return _reason(
            "DATABASE_RELATION_CAUSAL_TRANSPORT_RECEIPT_INTEGRITY_INVALID",
            expected=expected,
            actual=actual,
        )
    actual["transport_receipt_integrity_valid"] = True
    actual["validated_transport_receipt"] = validated

    result = _evaluate_causal_delta(env)
    if not isinstance(result, dict):
        raise TypeError("causal relation delta evaluator returned non-dict")
    output = dict(result)
    result_expected = _dict(output.get("expected"))
    result_actual = _dict(output.get("actual"))
    result_expected.update(expected)
    result_actual["causal_scope_semantic_match"] = True
    result_actual["transport_receipt_integrity_valid"] = True
    result_actual["validated_transport_receipt"] = validated
    output["expected"] = result_expected
    output["actual"] = result_actual
    return output


def _install_or_tighten_observer() -> None:
    if OBSERVER_ID in registered_observer_ids():
        OBSERVER_REGISTRY[OBSERVER_ID] = {
            "surface": "operation_causality",
            "adapter": "process_ledger",
            "implemented": True,
            "evidence_keys": (TRANSPORT_KEY,),
            "registered_at_runtime": True,
        }
        _REGISTERED_OBSERVER_HANDLERS[OBSERVER_ID] = (
            observe_operation_causality_with_integrity
        )
        return
    register_observer(
        OBSERVER_ID,
        surface="operation_causality",
        adapter="process_ledger",
        handler=observe_operation_causality_with_integrity,
        evidence_keys=(TRANSPORT_KEY,),
    )


def _install_or_tighten_assertion() -> str:
    if ASSERTION_KIND in registered_assertion_kinds():
        _REGISTERED_ASSERTION_EVALUATORS[ASSERTION_KIND] = (
            evaluate_database_relation_causal_delta_with_integrity
        )
        _REGISTERED_KIND_EVIDENCE_KEYS[ASSERTION_KIND] = (
            _REQUIRED_EVIDENCE_KEYS
        )
        return ASSERTION_KIND
    return register_assertion_kind(
        ASSERTION_KIND,
        evaluator=evaluate_database_relation_causal_delta_with_integrity,
        required_evidence_keys=_REQUIRED_EVIDENCE_KEYS,
    )


def install_database_relation_causal_delta_assertion() -> str:
    _install_or_tighten_observer()
    return _install_or_tighten_assertion()


# Only the installer is redirected. The base evaluator/observer remain stable inner
# layers for direct unit tests; formal execution always reaches them through the
# registry entries tightened above.
from . import database_relation_delta_causality_oracle as _base  # noqa: E402

_base.install_database_relation_causal_delta_assertion = (
    install_database_relation_causal_delta_assertion
)


__all__ = [
    "causal_scope_fingerprint",
    "evaluate_database_relation_causal_delta_with_integrity",
    "install_database_relation_causal_delta_assertion",
    "observe_operation_causality_with_integrity",
]
