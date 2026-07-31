"""Causality gate for exact cross-table database delta conservation.

The gate proves that the FK aggregate was additionally filtered by the exact
source-backed correlation value used by one executed treatment step. It delegates
all numeric, FK, identity, lineage and semantic-pair comparison to the existing
delta evaluator. No timestamp proximity or response-generated key is accepted.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
from .database_relation_delta_causality_projection import ASSERTION_KIND
from .database_relation_delta_lineage import (
    evaluate_database_relation_delta_with_lineage,
)
from .database_relation_observer_runtime import (
    EVIDENCE_KEY as RELATION_EVIDENCE_KEY,
    OBSERVER_ID as DIRECT_RELATION_OBSERVER_ID,
)
from .observer_contracts_base import (
    _receipt,
    register_observer,
    registered_observer_ids,
)
from .operation_causality_runtime import TRANSPORT_KEY

OBSERVER_ID = "operation_causality_transport"
CAUSAL_ORACLE_SCHEMA = "qualibug.database-relation-causal-delta-oracle.v1"
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


def observe_operation_causality(envelope: dict[str, Any]) -> dict[str, Any]:
    env = _dict(envelope)
    observations = _dict(env.get("observations"))
    receipts = [
        deepcopy(row)
        for row in _list(observations.get(TRANSPORT_KEY))
        if isinstance(row, dict)
    ]
    observed = [row for row in receipts if _text(row.get("status")) == "ATTRIBUTED"]
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED" if receipts and len(observed) == len(receipts) else "INDETERMINATE",
        reason_code=(
            ""
            if receipts and len(observed) == len(receipts)
            else "OPERATION_CAUSALITY_TRANSPORT_RECEIPT_INCOMPLETE"
        ),
        evidence={
            TRANSPORT_KEY: receipts,
            "receipt_count": len(receipts),
            "attributed_receipt_count": len(observed),
            "raw_causal_value_retained": False,
            "timestamp_window_attribution_used": False,
            "oracle_verdict_emitted": False,
        },
        campaign_id=_text(env.get("campaign_id")),
        execution_id=_text(env.get("execution_id")),
    )


def _relation_phase(
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
            "DATABASE_RELATION_CAUSAL_PHASE_RECEIPT_MISSING"
            if len(rows) < 1
            else "DATABASE_RELATION_CAUSAL_PHASE_RECEIPT_AMBIGUOUS"
        )
    receipt = rows[0]
    payload = _dict(_dict(receipt.get("evidence")).get(RELATION_EVIDENCE_KEY))
    return {
        "phase": target,
        "draft_id": _text(receipt.get("draft_id")),
        "receipt_id": _text(receipt.get("receipt_id")),
        "phase_receipt_id": _text(
            receipt.get("phase_receipt_id") or receipt.get("receipt_id")
        ),
        "campaign_id": _text(receipt.get("campaign_id")),
        "execution_id": _text(receipt.get("execution_id")),
        "source_observer_id": _text(receipt.get("observer_id")),
        "status": _text(receipt.get("status")).upper(),
        "causal_attribution_applied": payload.get("causal_attribution_applied") is True,
        "causal_attribution_scope": deepcopy(
            _dict(payload.get("causal_attribution_scope"))
        ),
        "causal_attribution_parameter_fingerprints": [
            _text(value)
            for value in _list(
                payload.get("causal_attribution_parameter_fingerprints")
            )
            if _text(value)
        ],
        "payload_oracle_verdict_emitted": payload.get("oracle_verdict_emitted"),
    }, ""


def evaluate_database_relation_causal_delta(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    env = _dict(envelope)
    spec = _dict(env.get("spec"))
    observations = _dict(env.get("observations"))
    causal = _dict(spec.get("causal_attribution_contract"))
    binding = _dict(spec.get("database_relation_delta_binding"))
    relation_ref = _text(spec.get("database_relation_observer_ref"))
    before_draft_id = _text(spec.get("relation_before_draft_id"))
    after_draft_id = _text(spec.get("relation_after_draft_id"))
    scope_fp = _text(spec.get("causal_scope_fingerprint"))
    operation_ref = _text(causal.get("operation_ref"))
    step_id = _text(causal.get("treatment_step_id"))
    value_source = _text(causal.get("value_source"))
    field_id = _text(causal.get("child_database_field_id"))
    field_name = _text(causal.get("child_database_field_name"))
    mapping_decision_id = _text(causal.get("mapping_decision_id"))

    expected = {
        "schema": CAUSAL_ORACLE_SCHEMA,
        "causal_scope_fingerprint": scope_fp,
        "operation_ref": operation_ref,
        "treatment_step_id": step_id,
        "value_source": value_source,
        "child_database_field_id": field_id,
        "child_database_field_name": field_name,
        "mapping_decision_id": mapping_decision_id,
        "relation_before_draft_id": before_draft_id,
        "relation_after_draft_id": after_draft_id,
        "attribution_mode": "EXACT_REQUEST_CORRELATION",
    }
    actual: dict[str, Any] = {
        "schema": CAUSAL_ORACLE_SCHEMA,
        "transport_receipt": {},
        "relation_before_causal_scope": {},
        "relation_after_causal_scope": {},
        "transport_scope_match": False,
        "relation_scope_match": False,
        "causal_value_fingerprint_match": False,
        "causal_lineage_match": False,
        "timestamp_window_attribution_used": False,
        "observer_performed_oracle_verdict": False,
    }
    required = (
        relation_ref,
        before_draft_id,
        after_draft_id,
        scope_fp,
        operation_ref,
        step_id,
        value_source,
        field_id,
        field_name,
        mapping_decision_id,
    )
    if not all(required) or _text(causal.get("status")) != "BOUND":
        return _reason(
            "DATABASE_RELATION_CAUSAL_ASSERTION_SPEC_INCOMPLETE",
            expected=expected,
            actual=actual,
        )
    binding_match = bool(
        binding.get("causal_scope_exact") is True
        and _text(binding.get("causal_scope_fingerprint")) == scope_fp
        and _text(binding.get("causal_mapping_decision_id")) == mapping_decision_id
        and _text(binding.get("causal_value_source")) == value_source
        and _text(binding.get("causal_operation_ref")) == operation_ref
    )
    if not binding_match:
        return _reason(
            "DATABASE_RELATION_CAUSAL_BINDING_MISMATCH",
            expected=expected,
            actual=actual,
        )

    transport_rows = [
        dict(row)
        for row in _list(observations.get(TRANSPORT_KEY))
        if isinstance(row, dict)
        and _text(row.get("causal_scope_fingerprint")) == scope_fp
        and _text(row.get("operation_ref")) == operation_ref
    ]
    if len(transport_rows) != 1:
        actual["transport_candidate_count"] = len(transport_rows)
        return _reason(
            (
                "DATABASE_RELATION_CAUSAL_TRANSPORT_RECEIPT_MISSING"
                if len(transport_rows) < 1
                else "DATABASE_RELATION_CAUSAL_TRANSPORT_RECEIPT_AMBIGUOUS"
            ),
            expected=expected,
            actual=actual,
        )
    transport = transport_rows[0]
    actual["transport_receipt"] = deepcopy(transport)
    transport_fp = _text(transport.get("transport_value_fingerprint"))
    transport_scope_match = bool(
        _text(transport.get("schema"))
        == "qualibug.operation-causality-transport-receipt.v1"
        and _text(transport.get("status")) == "ATTRIBUTED"
        and transport.get("source_value_fingerprint_match") is True
        and transport.get("transport_reached") is True
        and _text(transport.get("treatment_step_id")) == step_id
        and _text(transport.get("value_source")) == value_source
        and _text(transport.get("transport_receipt_id"))
        and _text(transport.get("request_semantics_fingerprint"))
        and transport_fp
        and transport.get("raw_causal_value_retained") is False
        and transport.get("timestamp_window_attribution_used") is False
    )
    actual["transport_scope_match"] = transport_scope_match
    if not transport_scope_match:
        return _reason(
            "DATABASE_RELATION_CAUSAL_TRANSPORT_SCOPE_INVALID",
            expected=expected,
            actual=actual,
        )

    before, before_reason = _relation_phase(
        observations,
        relation_ref=relation_ref,
        draft_id=before_draft_id,
        phase="BEFORE",
    )
    after, after_reason = _relation_phase(
        observations,
        relation_ref=relation_ref,
        draft_id=after_draft_id,
        phase="AFTER",
    )
    actual["relation_before_causal_scope"] = before
    actual["relation_after_causal_scope"] = after
    if before_reason or after_reason:
        return _reason(
            before_reason or after_reason,
            expected=expected,
            actual=actual,
        )
    for phase_row in (before, after):
        if (
            phase_row.get("source_observer_id") != DIRECT_RELATION_OBSERVER_ID
            or phase_row.get("status") != "OBSERVED"
            or not phase_row.get("phase_receipt_id")
            or phase_row.get("causal_attribution_applied") is not True
            or phase_row.get("payload_oracle_verdict_emitted") is not False
        ):
            return _reason(
                "DATABASE_RELATION_CAUSAL_OBSERVER_EVIDENCE_INVALID",
                expected=expected,
                actual=actual,
            )

    expected_scope = {
        "causal_scope_fingerprint": scope_fp,
        "operation_ref": operation_ref,
        "value_source": value_source,
        "child_database_field_id": field_id,
        "child_database_field_name": field_name,
        "mapping_decision_id": mapping_decision_id,
        "attribution_mode": "EXACT_REQUEST_CORRELATION",
    }
    def scope_matches(row: dict[str, Any]) -> bool:
        scope = _dict(row.get("causal_attribution_scope"))
        return bool(
            all(_text(scope.get(key)) == value for key, value in expected_scope.items())
            and scope.get("timestamp_window_attribution_used") is False
            and scope.get("response_generated_identifier_used") is False
            and scope.get("raw_causal_value_retained") is False
        )

    relation_scope_match = scope_matches(before) and scope_matches(after)
    actual["relation_scope_match"] = relation_scope_match
    if not relation_scope_match:
        return _reason(
            "DATABASE_RELATION_CAUSAL_RELATION_SCOPE_MISMATCH",
            expected=expected,
            actual=actual,
        )
    before_fp = _list(before.get("causal_attribution_parameter_fingerprints"))
    after_fp = _list(after.get("causal_attribution_parameter_fingerprints"))
    causal_fp_match = bool(
        len(before_fp) == 1
        and len(after_fp) == 1
        and _text(before_fp[0]) == transport_fp
        and _text(after_fp[0]) == transport_fp
    )
    actual["causal_value_fingerprint_match"] = causal_fp_match
    if not causal_fp_match:
        return _reason(
            "DATABASE_RELATION_CAUSAL_VALUE_FINGERPRINT_MISMATCH",
            expected=expected,
            actual=actual,
        )
    lineages = {
        (_text(transport.get("campaign_id")), _text(transport.get("execution_id"))),
        (_text(before.get("campaign_id")), _text(before.get("execution_id"))),
        (_text(after.get("campaign_id")), _text(after.get("execution_id"))),
    }
    lineage = next(iter(lineages)) if len(lineages) == 1 else ("", "")
    causal_lineage_match = bool(len(lineages) == 1 and all(lineage))
    actual["causal_lineage_match"] = causal_lineage_match
    if not causal_lineage_match:
        return _reason(
            "DATABASE_RELATION_CAUSAL_RECEIPT_LINEAGE_MISMATCH",
            expected=expected,
            actual=actual,
        )

    base_result = evaluate_database_relation_delta_with_lineage(env)
    if not isinstance(base_result, dict):
        raise TypeError("database relation delta lineage evaluator returned non-dict")
    result = dict(base_result)
    result_expected = _dict(result.get("expected"))
    result_actual = _dict(result.get("actual"))
    result_expected.update(expected)
    result_actual.update(actual)
    result_actual["transport_scope_match"] = True
    result_actual["relation_scope_match"] = True
    result_actual["causal_value_fingerprint_match"] = True
    result_actual["causal_lineage_match"] = True
    result["expected"] = result_expected
    result["actual"] = result_actual
    return result


def install_database_relation_causal_delta_assertion() -> str:
    if OBSERVER_ID not in registered_observer_ids():
        register_observer(
            OBSERVER_ID,
            surface="operation_causality",
            adapter="process_ledger",
            handler=observe_operation_causality,
            evidence_keys=(TRANSPORT_KEY,),
        )
    if ASSERTION_KIND in registered_assertion_kinds():
        return ASSERTION_KIND
    return register_assertion_kind(
        ASSERTION_KIND,
        evaluator=evaluate_database_relation_causal_delta,
        required_evidence_keys=(
            "approved_database_observer_phase_receipts",
            _RELATION_RECEIPT_KEY,
            TRANSPORT_KEY,
        ),
    )


__all__ = [
    "ASSERTION_KIND",
    "OBSERVER_ID",
    "evaluate_database_relation_causal_delta",
    "install_database_relation_causal_delta_assertion",
    "observe_operation_causality",
]
