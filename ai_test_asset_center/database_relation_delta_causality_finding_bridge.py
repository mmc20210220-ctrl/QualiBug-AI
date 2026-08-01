"""Attach operation-causality proof to causal database delta candidates."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from .database_relation_delta_causality_projection import ASSERTION_KIND
from .database_relation_delta_finding_bridge import (
    build_database_relation_delta_finding_evidence,
)

FINDING_EVIDENCE_SCHEMA = "qualibug.database-relation-causal-delta-finding-evidence.v1"
_FORBIDDEN_KEYS = frozenset(
    {
        "raw_sql",
        "sql",
        "statement",
        "dsn",
        "password",
        "secret",
        "credential",
        "connection_string",
        "predicate_values",
        "rows",
        "aggregate_values",
        "value",
        "raw_value",
    }
)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _clean(child)
            for key, child in value.items()
            if _text(key).lower() not in _FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [_clean(child) for child in value]
    if isinstance(value, tuple):
        return [_clean(child) for child in value]
    return value


def _assertion(finding: dict[str, Any]) -> dict[str, Any]:
    primary = _dict(_dict(finding.get("evidence")).get("assertion"))
    if primary and _text(primary.get("kind")) == ASSERTION_KIND:
        return primary
    matches = [
        _dict(row)
        for row in _list(finding.get("failed_assertions"))
        if _text(_dict(row).get("kind")) == ASSERTION_KIND
    ]
    return matches[0] if len(matches) == 1 else {}


def _transport(raw: Any) -> dict[str, Any]:
    row = _dict(raw)
    return {
        "schema": _text(row.get("schema")),
        "receipt_id": _text(row.get("receipt_id")),
        "causal_scope_fingerprint": _text(row.get("causal_scope_fingerprint")),
        "operation_ref": _text(row.get("operation_ref")),
        "treatment_step_id": _text(row.get("treatment_step_id")),
        "value_source": _text(row.get("value_source")),
        "preflight_value_fingerprint": _text(
            row.get("preflight_value_fingerprint")
        ),
        "transport_value_fingerprint": _text(
            row.get("transport_value_fingerprint")
        ),
        "source_value_fingerprint_match": row.get(
            "source_value_fingerprint_match"
        ) is True,
        "request_semantics_fingerprint": _text(
            row.get("request_semantics_fingerprint")
        ),
        "transport_receipt_id": _text(row.get("transport_receipt_id")),
        "status_code": row.get("status_code"),
        "campaign_id": _text(row.get("campaign_id")),
        "execution_id": _text(row.get("execution_id")),
        "status": _text(row.get("status")),
        "reason_code": _text(row.get("reason_code")),
        "transport_reached": row.get("transport_reached") is True,
        "raw_causal_value_retained": False,
        "timestamp_window_attribution_used": False,
    }


def _relation_scope(raw: Any) -> dict[str, Any]:
    row = _dict(raw)
    scope = _dict(row.get("causal_attribution_scope"))
    return {
        "phase": _text(row.get("phase")),
        "draft_id": _text(row.get("draft_id")),
        "receipt_id": _text(row.get("receipt_id")),
        "phase_receipt_id": _text(row.get("phase_receipt_id")),
        "campaign_id": _text(row.get("campaign_id")),
        "execution_id": _text(row.get("execution_id")),
        "source_observer_id": _text(row.get("source_observer_id")),
        "causal_attribution_applied": row.get("causal_attribution_applied") is True,
        "causal_scope_fingerprint": _text(scope.get("causal_scope_fingerprint")),
        "operation_ref": _text(scope.get("operation_ref")),
        "value_source": _text(scope.get("value_source")),
        "child_database_field_id": _text(scope.get("child_database_field_id")),
        "child_database_field_name": _text(scope.get("child_database_field_name")),
        "mapping_decision_id": _text(scope.get("mapping_decision_id")),
        "relation_mapping_decision_id": _text(
            scope.get("relation_mapping_decision_id")
        ),
        "relation_authority_match": scope.get("relation_authority_match") is True,
        "attribution_mode": _text(scope.get("attribution_mode")),
        "causal_attribution_parameter_fingerprints": [
            _text(value)
            for value in _list(
                row.get("causal_attribution_parameter_fingerprints")
            )
            if _text(value)
        ],
        "raw_causal_value_retained": False,
        "timestamp_window_attribution_used": False,
    }


def build_database_relation_causal_delta_finding_evidence(
    assertion_receipt: dict[str, Any],
) -> dict[str, Any]:
    assertion = _dict(assertion_receipt)
    expected = _dict(assertion.get("expected"))
    actual = _dict(assertion.get("actual"))
    base = build_database_relation_delta_finding_evidence(assertion)
    causal = {
        "schema": FINDING_EVIDENCE_SCHEMA,
        "assertion_id": _text(assertion.get("assertion_id")),
        "assertion_kind": _text(assertion.get("kind")),
        "assertion_status": _text(assertion.get("status")),
        "reason_code": _text(assertion.get("reason_code")),
        "causal_scope_fingerprint": _text(
            expected.get("causal_scope_fingerprint")
        ),
        "operation_ref": _text(expected.get("operation_ref")),
        "treatment_step_id": _text(expected.get("treatment_step_id")),
        "value_source": _text(expected.get("value_source")),
        "child_database_field_id": _text(
            expected.get("child_database_field_id")
        ),
        "child_database_field_name": _text(
            expected.get("child_database_field_name")
        ),
        "mapping_decision_id": _text(expected.get("mapping_decision_id")),
        "causal_mapping_decision_id": _text(
            expected.get("causal_mapping_decision_id")
            or expected.get("mapping_decision_id")
        ),
        "bound_causal_mapping_decision_id": _text(
            expected.get("bound_causal_mapping_decision_id")
        ),
        "relation_mapping_decision_id": _text(
            expected.get("relation_mapping_decision_id")
        ),
        "authority_basis": _text(expected.get("authority_basis")),
        "relation_authority_match": actual.get("relation_authority_match") is True,
        "automatic_authority_selection_used": actual.get(
            "automatic_authority_selection_used"
        ) is True,
        "attribution_mode": _text(expected.get("attribution_mode")),
        "causal_scope_semantic_match": actual.get(
            "causal_scope_semantic_match"
        ) is True,
        "transport_receipt_integrity_valid": actual.get(
            "transport_receipt_integrity_valid"
        ) is True,
        "transport_scope_match": actual.get("transport_scope_match") is True,
        "relation_scope_match": actual.get("relation_scope_match") is True,
        "causal_value_fingerprint_match": actual.get(
            "causal_value_fingerprint_match"
        ) is True,
        "causal_lineage_match": actual.get("causal_lineage_match") is True,
        "transport_receipt": _transport(
            actual.get("validated_transport_receipt")
            or actual.get("transport_receipt")
        ),
        "relation_before_causal_scope": _relation_scope(
            actual.get("relation_before_causal_scope")
        ),
        "relation_after_causal_scope": _relation_scope(
            actual.get("relation_after_causal_scope")
        ),
        "timestamp_window_attribution_used": False,
        "response_generated_identifier_used": False,
        "raw_causal_value_retained": False,
        "oracle_authority": "ContractOracle",
        "database_observer_authority": "FACT_ONLY",
        "operation_causality_observer_authority": "FACT_ONLY",
        "delta_evidence": base,
    }
    return _clean(causal)


def enrich_database_relation_causal_delta_finding(
    result: Any,
    *,
    experiment: dict[str, Any] | None = None,
) -> Any:
    if not isinstance(result, dict):
        return result
    finding = _dict(result.get("finding"))
    if not finding:
        return result
    assertion = _assertion(finding)
    if not assertion:
        return result
    payload = build_database_relation_causal_delta_finding_evidence(assertion)
    if not payload.get("causal_scope_fingerprint"):
        return result

    output = dict(result)
    enriched = deepcopy(finding)
    evidence = _dict(enriched.get("evidence"))
    evidence["database_relation_causal_delta"] = payload
    evidence["database_evidence_basis"] = (
        "APPROVED_FK_AGGREGATE_DELTA_PLUS_EXACT_OPERATION_CORRELATION"
    )
    evidence["operation_causality_fact_only"] = True
    enriched["evidence"] = evidence

    raw = _dict(enriched.get("raw_evidence"))
    raw["db_snapshot"] = {
        "schema": FINDING_EVIDENCE_SCHEMA,
        "causal_scope_fingerprint": payload["causal_scope_fingerprint"],
        "operation_ref": payload["operation_ref"],
        "treatment_step_id": payload["treatment_step_id"],
        "value_source": payload["value_source"],
        "child_database_field_id": payload["child_database_field_id"],
        "causal_mapping_decision_id": payload["causal_mapping_decision_id"],
        "bound_causal_mapping_decision_id": payload[
            "bound_causal_mapping_decision_id"
        ],
        "relation_mapping_decision_id": payload[
            "relation_mapping_decision_id"
        ],
        "authority_basis": payload["authority_basis"],
        "transport_receipt": payload["transport_receipt"],
        "relation_before_causal_scope": payload[
            "relation_before_causal_scope"
        ],
        "relation_after_causal_scope": payload[
            "relation_after_causal_scope"
        ],
        "actual": {
            "relation_authority_match": payload["relation_authority_match"],
            "automatic_authority_selection_used": payload[
                "automatic_authority_selection_used"
            ],
            "causal_scope_semantic_match": payload[
                "causal_scope_semantic_match"
            ],
            "transport_receipt_integrity_valid": payload[
                "transport_receipt_integrity_valid"
            ],
            "transport_scope_match": payload["transport_scope_match"],
            "relation_scope_match": payload["relation_scope_match"],
            "causal_value_fingerprint_match": payload[
                "causal_value_fingerprint_match"
            ],
            "causal_lineage_match": payload["causal_lineage_match"],
        },
        "delta_evidence": payload["delta_evidence"],
        "raw_causal_value_retained": False,
        "timestamp_window_attribution_used": False,
        "raw_sql_retained": False,
        "dsn_retained": False,
        "secret_values_retained": False,
        "raw_child_rows_retained": False,
    }
    raw["db_snapshot_source"] = (
        "approved_relation_delta_and_operation_causality_receipts"
    )
    raw["legacy_http_body_used_as_db_snapshot"] = False
    enriched["raw_evidence"] = _clean(raw)
    enriched["category"] = ASSERTION_KIND
    enriched["database_relation_causal_delta_evidence"] = payload
    quality = _dict(enriched.get("evidence_quality"))
    quality["database_evidence_strength"] = (
        "EXACT_OPERATION_ATTRIBUTED_APPROVED_RELATION_DELTA"
    )
    enriched["evidence_quality"] = quality
    enriched["gate_passed"] = finding.get("gate_passed") is True
    enriched["customer_delivery_status"] = finding.get(
        "customer_delivery_status", "candidate"
    )
    enriched["final_review_status"] = finding.get(
        "final_review_status", "PENDING_DELIVERY_GATE"
    )
    output["finding"] = enriched
    return output


def _database_relation_causal_delta_finalizer_hook(
    next_call: Callable[[tuple[Any, ...], dict[str, Any]], dict[str, Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    result = next_call(args, kwargs)
    experiment = kwargs.get("exp") or kwargs.get("experiment")
    if not isinstance(experiment, dict):
        experiment = next(
            (
                arg
                for arg in args
                if isinstance(arg, dict) and arg.get("experiment_id")
            ),
            {},
        )
    return enrich_database_relation_causal_delta_finding(
        result,
        experiment=_dict(experiment),
    )


def install_database_relation_causal_delta_finding_bridge() -> None:
    """Register causal relation-delta evidence on the canonical Finalizer."""
    from . import experiment_outcome_finalizer as finalizer

    finalizer.register_finalizer_hook(
        "database_relation_causal_delta_finding",
        _database_relation_causal_delta_finalizer_hook,
    )


__all__ = [
    "FINDING_EVIDENCE_SCHEMA",
    "build_database_relation_causal_delta_finding_evidence",
    "enrich_database_relation_causal_delta_finding",
    "install_database_relation_causal_delta_finding_bridge",
]
