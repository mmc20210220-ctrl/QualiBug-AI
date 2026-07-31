"""Add exact operation-attribution evidence to the existing relation Observer.

The base relation executor still owns connection, policy, SQL construction and
read-only execution. This wrapper only validates the additional approved predicate
scope and rebuilds the content-addressed receipt so FK identity fingerprints and
operation-correlation fingerprints cannot be confused.
"""
from __future__ import annotations

import functools
import sys
from copy import deepcopy
from typing import Any

from .database_observer_runtime import _fingerprint, _sensitive_field, _text
from .observer_contracts_base import _receipt

_PREDICATE_KIND = "OPERATION_ATTRIBUTION"
_INSTALL_MARKER = "__qualibug_database_relation_causality_runtime_v1__"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _attribution_predicates(contract: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    return [
        (index, dict(row))
        for index, row in enumerate(_list(contract.get("relation_predicates")))
        if isinstance(row, dict)
        and _text(row.get("predicate_kind")) == _PREDICATE_KIND
    ]


def _refusal(
    *,
    reason_code: str,
    campaign_id: str,
    execution_id: str,
    detail: str,
) -> dict[str, Any]:
    return _receipt(
        observer_id="approved_database_relation_aggregate",
        status="INDETERMINATE",
        reason_code=reason_code,
        evidence={
            "schema": "qualibug.database-relation-causality-runtime-refusal.v1",
            "detail": detail,
            "write_attempted": False,
            "raw_sql_retained": False,
            "predicate_values_retained": False,
            "secret_values_retained": False,
            "dsn_retained": False,
            "oracle_verdict_emitted": False,
        },
        campaign_id=campaign_id,
        execution_id=execution_id,
    )


def install_database_relation_causality_runtime() -> None:
    """Wrap the one existing relation executor; do not create another executor."""
    from . import database_relation_observer_runtime as base

    original = getattr(base, "execute_database_relation_observer_contract", None)
    if not callable(original) or getattr(original, _INSTALL_MARKER, False):
        return

    @functools.wraps(original)
    def wrapped(
        contract: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        approved = deepcopy(_dict(contract))
        attribution = _attribution_predicates(approved)
        if not attribution:
            return original(contract, *args, **kwargs)
        campaign_id = _text(kwargs.get("campaign_id"))
        execution_id = _text(kwargs.get("execution_id"))
        causal_contract = _dict(approved.get("causal_attribution_contract"))
        causal_scope_fingerprint = _text(
            approved.get("causal_scope_fingerprint")
            or causal_contract.get("causal_scope_fingerprint")
        )
        relation_mapping_decision_id = _text(
            approved.get("relation_mapping_decision_id")
        )
        if len(attribution) != 1:
            return _refusal(
                reason_code="DATABASE_RELATION_CAUSAL_PREDICATE_COUNT_INVALID",
                campaign_id=campaign_id,
                execution_id=execution_id,
                detail=f"predicate_count:{len(attribution)}",
            )
        index, predicate = attribution[0]
        child_name = _text(predicate.get("child_database_field_name"))
        child_id = _text(predicate.get("child_database_field_id"))
        value_source = _text(predicate.get("value_source"))
        mapping_decision_id = _text(predicate.get("mapping_decision_id"))
        contract_mapping_decision_id = _text(
            causal_contract.get("mapping_decision_id")
        )
        operation_ref = _text(predicate.get("operation_ref"))
        authority_match = bool(
            mapping_decision_id
            and mapping_decision_id == contract_mapping_decision_id
            and mapping_decision_id == relation_mapping_decision_id
        )
        if not authority_match:
            return _refusal(
                reason_code="DATABASE_RELATION_CAUSAL_RELATION_AUTHORITY_MISMATCH",
                campaign_id=campaign_id,
                execution_id=execution_id,
                detail="causal_mapping_decision_not_relation_authority",
            )
        if (
            not child_id
            or not child_name
            or _sensitive_field(child_name)
            or _text(predicate.get("operator")) != "="
            or not value_source.startswith("request.body.")
            or not operation_ref
            or not causal_scope_fingerprint
            or _text(causal_contract.get("status")) != "BOUND"
        ):
            return _refusal(
                reason_code="DATABASE_RELATION_CAUSAL_CONTRACT_REFUSED",
                campaign_id=campaign_id,
                execution_id=execution_id,
                detail="causal_attribution_contract_incomplete_or_sensitive",
            )

        result = original(contract, *args, **kwargs)
        if not isinstance(result, dict) or _text(result.get("status")).upper() != "OBSERVED":
            return result
        evidence = deepcopy(_dict(result.get("evidence")))
        payload = deepcopy(
            _dict(evidence.get("approved_database_relation_aggregate_snapshot"))
        )
        fingerprints = [
            _text(value)
            for value in _list(payload.get("relation_parameter_fingerprints"))
        ]
        predicates = [
            dict(row)
            for row in _list(approved.get("relation_predicates"))
            if isinstance(row, dict)
        ]
        if len(fingerprints) != len(predicates) or index >= len(fingerprints):
            return _refusal(
                reason_code="DATABASE_RELATION_CAUSAL_PARAMETER_SCOPE_MISMATCH",
                campaign_id=campaign_id,
                execution_id=execution_id,
                detail=(
                    f"fingerprints:{len(fingerprints)}:predicates:{len(predicates)}"
                ),
            )

        relation_indexes = [
            item_index
            for item_index, row in enumerate(predicates)
            if _text(row.get("predicate_kind")) != _PREDICATE_KIND
        ]
        relation_key = [
            {
                "child_database_field_name": _text(
                    predicates[item_index].get("child_database_field_name")
                ),
                "parent_database_field_name": _text(
                    predicates[item_index].get("parent_database_field_name")
                ),
            }
            for item_index in relation_indexes
        ]
        if not relation_key or any(
            not row["child_database_field_name"]
            or not row["parent_database_field_name"]
            for row in relation_key
        ):
            return _refusal(
                reason_code="DATABASE_RELATION_CAUSAL_FK_SCOPE_MISSING",
                campaign_id=campaign_id,
                execution_id=execution_id,
                detail="approved_fk_predicates_missing",
            )

        payload["relation_key"] = relation_key
        payload["relation_parameter_fingerprints"] = [
            fingerprints[item_index] for item_index in relation_indexes
        ]
        payload["causal_attribution_scope"] = {
            "schema": "qualibug.database-relation-causal-attribution-scope.v1",
            "causal_scope_fingerprint": causal_scope_fingerprint,
            "operation_ref": operation_ref,
            "value_source": value_source,
            "child_database_field_id": child_id,
            "child_database_field_name": child_name,
            "mapping_decision_id": mapping_decision_id,
            "relation_mapping_decision_id": relation_mapping_decision_id,
            "relation_authority_match": True,
            "attribution_mode": "EXACT_REQUEST_CORRELATION",
            "timestamp_window_attribution_used": False,
            "response_generated_identifier_used": False,
            "raw_causal_value_retained": False,
        }
        payload["causal_attribution_parameter_fingerprints"] = [
            fingerprints[index]
        ]
        payload["causal_attribution_applied"] = True
        payload["causal_attribution_predicate_count"] = 1
        payload["oracle_verdict_emitted"] = False
        evidence["approved_database_relation_aggregate_snapshot"] = payload
        evidence["observation_fingerprint"] = _fingerprint(payload)
        return _receipt(
            observer_id=_text(result.get("observer_id")),
            status=_text(result.get("status")),
            reason_code=_text(result.get("reason_code")),
            evidence=evidence,
            campaign_id=_text(result.get("campaign_id")),
            execution_id=_text(result.get("execution_id")),
        )

    setattr(wrapped, _INSTALL_MARKER, True)
    setattr(wrapped, "__qualibug_original__", original)
    base.execute_database_relation_observer_contract = wrapped
    phase = sys.modules.get(
        f"{__package__}.database_relation_observer_experiment_runtime"
    )
    if phase is not None:
        phase.execute_database_relation_observer_contract = wrapped


__all__ = ["install_database_relation_causality_runtime"]
