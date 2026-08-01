"""Attach exact root and FK aggregate delta evidence to Contract Oracle candidates."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from .database_relation_delta_experiment_projection import ASSERTION_KIND
from .database_relation_delta_projection_gate import SEMANTIC_PAIR_SCHEMA

FINDING_EVIDENCE_SCHEMA = "qualibug.database-relation-delta-finding-evidence.v1"
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


def _root_snapshot(raw: Any) -> dict[str, Any]:
    row = _dict(raw)
    return {
        "draft_id": _text(row.get("draft_id")),
        "phase": _text(row.get("phase")),
        "phase_receipt_id": _text(row.get("phase_receipt_id")),
        "source_observer_id": _text(row.get("source_observer_id")),
        "campaign_id": _text(row.get("campaign_id")),
        "execution_id": _text(row.get("execution_id")),
        "database_table_ref": _text(row.get("database_table_ref")),
        "database_table_name": _text(row.get("database_table_name")),
        "match_status": _text(row.get("match_status")),
        "row_count": row.get("row_count"),
        "row_fingerprint": _text(row.get("row_fingerprint")),
        "identity_key": [
            _text(value)
            for value in _list(row.get("identity_key"))
            if _text(value)
        ],
        "identity_parameter_fingerprints": [
            _text(value)
            for value in _list(row.get("identity_parameter_fingerprints"))
            if _text(value)
        ],
        "field_name": _text(row.get("field_name")),
        "field_value": row.get("field_value"),
    }


def _relation_snapshot(raw: Any) -> dict[str, Any]:
    row = _dict(raw)
    return {
        "draft_id": _text(row.get("draft_id")),
        "relation_pair_id": _text(row.get("relation_pair_id")),
        "phase": _text(row.get("phase")),
        "phase_receipt_id": _text(row.get("phase_receipt_id")),
        "receipt_id": _text(row.get("receipt_id")),
        "source_observer_id": _text(row.get("source_observer_id")),
        "campaign_id": _text(row.get("campaign_id")),
        "execution_id": _text(row.get("execution_id")),
        "relation_observer_ref": _text(row.get("relation_observer_ref")),
        "root_observer_ref": _text(row.get("root_observer_ref")),
        "database_relationship_id": _text(row.get("database_relationship_id")),
        "parent_table_ref": _text(row.get("parent_table_ref")),
        "child_table_ref": _text(row.get("child_table_ref")),
        "child_table_name": _text(row.get("child_table_name")),
        "relation_key": _clean(_list(row.get("relation_key"))),
        "relation_parameter_fingerprints": [
            _text(value)
            for value in _list(row.get("relation_parameter_fingerprints"))
            if _text(value)
        ],
        "aggregate_requests": _clean(_list(row.get("aggregate_requests"))),
        "aggregate_alias": _text(row.get("aggregate_alias")),
        "aggregate_value": row.get("aggregate_value"),
        "scope_count_alias": _text(row.get("scope_count_alias")),
        "scope_count": row.get("scope_count"),
        "aggregate_fingerprint": _text(row.get("aggregate_fingerprint")),
        "aggregate_request_match": row.get("aggregate_request_match") is True,
        "client_side_filter_used": row.get("client_side_filter_used") is True,
        "raw_rows_retained": row.get("raw_rows_retained") is True,
    }


def build_database_relation_delta_finding_evidence(
    assertion_receipt: dict[str, Any],
) -> dict[str, Any]:
    assertion = _dict(assertion_receipt)
    expected = _dict(assertion.get("expected"))
    actual = _dict(assertion.get("actual"))
    payload = {
        "schema": FINDING_EVIDENCE_SCHEMA,
        "assertion_id": _text(assertion.get("assertion_id")),
        "assertion_kind": _text(assertion.get("kind")),
        "assertion_status": _text(assertion.get("status")),
        "reason_code": _text(assertion.get("reason_code")),
        "source_refs": _clean(_list(expected.get("source_refs"))),
        "source_evidence_present": actual.get("source_evidence_present") is True,
        "root_field_binding_id": _text(expected.get("root_field_binding_id")),
        "relation_mapping_decision_id": _text(
            expected.get("relation_mapping_decision_id")
        ),
        "approved_binding_ids_present": actual.get(
            "approved_binding_ids_present"
        ) is True,
        "semantic_pair_schema": _text(
            expected.get("semantic_pair_schema") or SEMANTIC_PAIR_SCHEMA
        ),
        "database_relation_observer_ref": _text(expected.get("database_relation_observer_ref")),
        "database_relationship_id": _text(expected.get("database_relationship_id")),
        "relation_key": _clean(_list(expected.get("relation_key"))),
        "relation_pair_id": _text(expected.get("relation_pair_id")),
        "recomputed_relation_pair_id": _text(
            expected.get("recomputed_relation_pair_id")
        ),
        "relation_before_draft_id": _text(expected.get("relation_before_draft_id")),
        "relation_after_draft_id": _text(expected.get("relation_after_draft_id")),
        "root_observer_contract_ref": _text(expected.get("root_observer_contract_ref")),
        "root_before_draft_id": _text(expected.get("root_before_draft_id")),
        "root_after_draft_id": _text(expected.get("root_after_draft_id")),
        "root_table_ref": _text(expected.get("root_table_ref")),
        "root_database_field_id": _text(expected.get("root_database_field_id")),
        "root_database_field_name": _text(expected.get("root_database_field_name")),
        "child_table_ref": _text(expected.get("child_table_ref")),
        "child_database_field_id": _text(expected.get("child_database_field_id")),
        "child_database_field_name": _text(expected.get("child_database_field_name")),
        "aggregate": _text(expected.get("aggregate")),
        "aggregate_alias": _text(expected.get("aggregate_alias")),
        "scope_count_alias": _text(expected.get("scope_count_alias")),
        "comparison_operator": _text(expected.get("comparison_operator")),
        "aggregate_on_left": expected.get("aggregate_on_left") is True,
        "root_before": actual.get("root_before"),
        "root_after": actual.get("root_after"),
        "root_delta": actual.get("root_delta"),
        "relation_before": actual.get("relation_before"),
        "relation_after": actual.get("relation_after"),
        "relation_delta": actual.get("relation_delta"),
        "left_coefficient": actual.get("left_coefficient"),
        "right_coefficient": actual.get("right_coefficient"),
        "weighted_left_delta": actual.get("weighted_left_delta"),
        "weighted_right_delta": actual.get("weighted_right_delta"),
        "difference": actual.get("difference"),
        "tolerance": actual.get("tolerance"),
        "binding_match": actual.get("binding_match") is True,
        "semantic_pair_match": actual.get("semantic_pair_match") is True,
        "lineage_match": actual.get("lineage_match") is True,
        "relation_pair_match": actual.get("relation_pair_match") is True,
        "root_identity_match": actual.get("root_identity_match") is True,
        "relation_identity_match": actual.get("relation_identity_match") is True,
        "cross_observer_identity_match": actual.get("cross_observer_identity_match") is True,
        "relation_scope_match": actual.get("relation_scope_match") is True,
        "aggregate_request_match": actual.get("aggregate_request_match") is True,
        "root_before_snapshot": _root_snapshot(actual.get("root_before_snapshot")),
        "root_after_snapshot": _root_snapshot(actual.get("root_after_snapshot")),
        "relation_before_snapshot": _relation_snapshot(actual.get("relation_before_snapshot")),
        "relation_after_snapshot": _relation_snapshot(actual.get("relation_after_snapshot")),
        "observer_performed_oracle_verdict": actual.get("observer_performed_oracle_verdict") is True,
        "oracle_authority": "ContractOracle",
        "database_observer_authority": "FACT_ONLY",
        "raw_sql_retained": False,
        "dsn_retained": False,
        "secret_values_retained": False,
        "predicate_values_retained": False,
        "raw_child_rows_retained": False,
    }
    return _clean(payload)


def enrich_database_relation_delta_finding(
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
    evidence_payload = build_database_relation_delta_finding_evidence(assertion)
    if not evidence_payload.get("database_relation_observer_ref"):
        return result

    output = dict(result)
    enriched = deepcopy(finding)
    evidence = _dict(enriched.get("evidence"))
    evidence["database_relation_delta"] = evidence_payload
    evidence["database_evidence_basis"] = (
        "APPROVED_ROOT_AND_FK_AGGREGATE_BEFORE_AFTER_PHASE_RECEIPTS"
    )
    evidence["database_observer_fact_only"] = True
    enriched["evidence"] = evidence

    raw = _dict(enriched.get("raw_evidence"))
    raw["db_snapshot"] = {
        "schema": FINDING_EVIDENCE_SCHEMA,
        "source_refs": evidence_payload["source_refs"],
        "source_evidence_present": evidence_payload["source_evidence_present"],
        "root_field_binding_id": evidence_payload["root_field_binding_id"],
        "relation_mapping_decision_id": evidence_payload[
            "relation_mapping_decision_id"
        ],
        "approved_binding_ids_present": evidence_payload[
            "approved_binding_ids_present"
        ],
        "semantic_pair_schema": evidence_payload["semantic_pair_schema"],
        "database_relationship_id": evidence_payload["database_relationship_id"],
        "relation_key": evidence_payload["relation_key"],
        "relation_pair_id": evidence_payload["relation_pair_id"],
        "recomputed_relation_pair_id": evidence_payload[
            "recomputed_relation_pair_id"
        ],
        "root_table_ref": evidence_payload["root_table_ref"],
        "root_database_field_id": evidence_payload["root_database_field_id"],
        "child_table_ref": evidence_payload["child_table_ref"],
        "child_database_field_id": evidence_payload["child_database_field_id"],
        "aggregate": evidence_payload["aggregate"],
        "comparison_operator": evidence_payload["comparison_operator"],
        "actual": {
            "root_before": evidence_payload["root_before"],
            "root_after": evidence_payload["root_after"],
            "root_delta": evidence_payload["root_delta"],
            "relation_before": evidence_payload["relation_before"],
            "relation_after": evidence_payload["relation_after"],
            "relation_delta": evidence_payload["relation_delta"],
            "weighted_left_delta": evidence_payload["weighted_left_delta"],
            "weighted_right_delta": evidence_payload["weighted_right_delta"],
            "difference": evidence_payload["difference"],
            "binding_match": evidence_payload["binding_match"],
            "semantic_pair_match": evidence_payload["semantic_pair_match"],
            "lineage_match": evidence_payload["lineage_match"],
            "relation_pair_match": evidence_payload["relation_pair_match"],
            "cross_observer_identity_match": evidence_payload["cross_observer_identity_match"],
            "relation_scope_match": evidence_payload["relation_scope_match"],
            "aggregate_request_match": evidence_payload["aggregate_request_match"],
        },
        "root_before_receipt": evidence_payload["root_before_snapshot"],
        "root_after_receipt": evidence_payload["root_after_snapshot"],
        "relation_before_receipt": evidence_payload["relation_before_snapshot"],
        "relation_after_receipt": evidence_payload["relation_after_snapshot"],
        "raw_sql_retained": False,
        "dsn_retained": False,
        "secret_values_retained": False,
        "raw_child_rows_retained": False,
    }
    raw["db_snapshot_source"] = (
        "approved_root_and_database_relation_before_after_phase_receipts"
    )
    raw["legacy_http_body_used_as_db_snapshot"] = False
    enriched["raw_evidence"] = _clean(raw)
    enriched["category"] = ASSERTION_KIND
    enriched["database_relation_delta_evidence"] = evidence_payload
    quality = _dict(enriched.get("evidence_quality"))
    quality["database_evidence_strength"] = (
        "EXACT_SOURCE_BACKED_FK_SCOPED_ROOT_AND_AGGREGATE_DELTA"
    )
    enriched["evidence_quality"] = quality
    enriched["gate_passed"] = finding.get("gate_passed") is True
    enriched["customer_delivery_status"] = finding.get("customer_delivery_status", "candidate")
    enriched["final_review_status"] = finding.get("final_review_status", "PENDING_DELIVERY_GATE")
    output["finding"] = enriched
    return output


def _database_relation_delta_finalizer_hook(
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
    return enrich_database_relation_delta_finding(
        result,
        experiment=_dict(experiment),
    )


def install_database_relation_delta_finding_bridge() -> None:
    """Register relation-delta evidence on the canonical Finalizer."""
    from . import experiment_outcome_finalizer as finalizer

    finalizer.register_finalizer_hook(
        "database_relation_delta_finding",
        _database_relation_delta_finalizer_hook,
    )


__all__ = [
    "FINDING_EVIDENCE_SCHEMA",
    "build_database_relation_delta_finding_evidence",
    "enrich_database_relation_delta_finding",
    "install_database_relation_delta_finding_bridge",
]
