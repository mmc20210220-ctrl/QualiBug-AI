"""Attach exact root-row and FK aggregate evidence to Contract Oracle candidates."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from .database_relation_numeric_experiment_projection import ASSERTION_KIND

FINDING_EVIDENCE_SCHEMA = "qualibug.database-relation-numeric-finding-evidence.v1"
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
    if primary:
        return primary if _text(primary.get("kind")) == ASSERTION_KIND else {}
    failed = [
        _dict(row)
        for row in _list(finding.get("failed_assertions"))
        if _dict(row)
    ]
    return (
        failed[0]
        if len(failed) == 1
        and _text(failed[0].get("kind")) == ASSERTION_KIND
        else {}
    )


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


def _aggregate_requests(value: Any) -> list[dict[str, str]]:
    return [
        {
            "aggregate": _text(row.get("aggregate")).upper(),
            "database_field_id": _text(row.get("database_field_id")),
            "database_field_name": _text(row.get("database_field_name")),
            "alias": _text(row.get("alias")),
        }
        for row in _list(value)
        if isinstance(row, dict)
    ]


def _relation_snapshot(raw: Any) -> dict[str, Any]:
    row = _dict(raw)
    return {
        "draft_id": _text(row.get("draft_id")),
        "phase_receipt_id": _text(row.get("phase_receipt_id")),
        "source_observer_id": _text(row.get("source_observer_id")),
        "campaign_id": _text(row.get("campaign_id")),
        "execution_id": _text(row.get("execution_id")),
        "relation_observer_ref": _text(row.get("relation_observer_ref")),
        "root_observer_ref": _text(row.get("root_observer_ref")),
        "database_relationship_id": _text(
            row.get("database_relationship_id")
        ),
        "parent_table_ref": _text(row.get("parent_table_ref")),
        "child_table_ref": _text(row.get("child_table_ref")),
        "child_table_name": _text(row.get("child_table_name")),
        "relation_key": _clean(_list(row.get("relation_key"))),
        "relation_parameter_fingerprints": [
            _text(value)
            for value in _list(row.get("relation_parameter_fingerprints"))
            if _text(value)
        ],
        "aggregate_requests": _aggregate_requests(
            row.get("aggregate_requests")
        ),
        "aggregate_alias": _text(row.get("aggregate_alias")),
        "aggregate_value": row.get("aggregate_value"),
        "aggregate_fingerprint": _text(row.get("aggregate_fingerprint")),
        "client_side_filter_used": (
            row.get("client_side_filter_used") is True
        ),
        "raw_rows_retained": row.get("raw_rows_retained") is True,
        "payload_oracle_verdict_emitted": row.get(
            "payload_oracle_verdict_emitted"
        ),
    }


def build_database_relation_finding_evidence(
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
        "database_relation_observer_ref": _text(
            expected.get("database_relation_observer_ref")
        ),
        "database_relation_draft_id": _text(
            expected.get("database_relation_draft_id")
        ),
        "database_relationship_id": _text(
            expected.get("database_relationship_id")
        ),
        "relation_key": _clean(_list(expected.get("relation_key"))),
        "root_observer_contract_ref": _text(
            expected.get("root_observer_contract_ref")
        ),
        "root_database_draft_id": _text(
            expected.get("root_database_draft_id")
        ),
        "root_table_ref": _text(expected.get("root_table_ref")),
        "root_database_field_id": _text(
            expected.get("root_database_field_id")
        ),
        "root_database_field_name": _text(
            expected.get("root_database_field_name")
        ),
        "child_table_ref": _text(expected.get("child_table_ref")),
        "child_database_field_id": _text(
            expected.get("child_database_field_id")
        ),
        "child_database_field_name": _text(
            expected.get("child_database_field_name")
        ),
        "aggregate": _text(expected.get("aggregate")),
        "aggregate_alias": _text(expected.get("aggregate_alias")),
        "comparison_operator": _text(
            expected.get("comparison_operator")
        ),
        "aggregate_on_left": expected.get("aggregate_on_left") is True,
        "root_value": actual.get("root_value"),
        "aggregate_value": actual.get("aggregate_value"),
        "left_value": actual.get("left_value"),
        "right_value": actual.get("right_value"),
        "difference": actual.get("difference"),
        "tolerance": actual.get("tolerance"),
        "lineage_match": actual.get("lineage_match") is True,
        "identity_match": actual.get("identity_match") is True,
        "relation_key_match": actual.get("relation_key_match") is True,
        "aggregate_request_match": (
            actual.get("aggregate_request_match") is True
        ),
        "root_snapshot": _root_snapshot(actual.get("root_snapshot")),
        "relation_snapshot": _relation_snapshot(
            actual.get("relation_snapshot")
        ),
        "observer_performed_oracle_verdict": (
            actual.get("observer_performed_oracle_verdict") is True
        ),
        "oracle_authority": "ContractOracle",
        "database_observer_authority": "FACT_ONLY",
        "raw_sql_retained": False,
        "dsn_retained": False,
        "secret_values_retained": False,
        "predicate_values_retained": False,
        "raw_child_rows_retained": False,
    }
    return _clean(payload)


def enrich_database_relation_finding(
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
    evidence_payload = build_database_relation_finding_evidence(assertion)
    if not evidence_payload.get("database_relation_observer_ref"):
        return result

    output = dict(result)
    enriched = deepcopy(finding)
    evidence = _dict(enriched.get("evidence"))
    evidence["database_relation_numeric"] = evidence_payload
    evidence["database_evidence_basis"] = (
        "APPROVED_ROOT_AND_FK_AGGREGATE_PHASE_RECEIPTS"
    )
    evidence["database_observer_fact_only"] = True
    enriched["evidence"] = evidence

    raw = _dict(enriched.get("raw_evidence"))
    raw["db_snapshot"] = {
        "schema": FINDING_EVIDENCE_SCHEMA,
        "database_relationship_id": evidence_payload[
            "database_relationship_id"
        ],
        "relation_key": evidence_payload["relation_key"],
        "root_table_ref": evidence_payload["root_table_ref"],
        "root_database_field_id": evidence_payload[
            "root_database_field_id"
        ],
        "child_table_ref": evidence_payload["child_table_ref"],
        "child_database_field_id": evidence_payload[
            "child_database_field_id"
        ],
        "aggregate": evidence_payload["aggregate"],
        "aggregate_alias": evidence_payload["aggregate_alias"],
        "comparison_operator": evidence_payload["comparison_operator"],
        "scope_match": {
            "relation_key_match": evidence_payload["relation_key_match"],
            "aggregate_request_match": evidence_payload[
                "aggregate_request_match"
            ],
        },
        "actual": {
            "root_value": evidence_payload["root_value"],
            "aggregate_value": evidence_payload["aggregate_value"],
            "difference": evidence_payload["difference"],
            "lineage_match": evidence_payload["lineage_match"],
            "identity_match": evidence_payload["identity_match"],
        },
        "root_receipt": evidence_payload["root_snapshot"],
        "relation_receipt": evidence_payload["relation_snapshot"],
        "raw_sql_retained": False,
        "dsn_retained": False,
        "secret_values_retained": False,
        "raw_child_rows_retained": False,
    }
    raw["db_snapshot_source"] = (
        "approved_root_and_database_relation_phase_receipts"
    )
    raw["legacy_http_body_used_as_db_snapshot"] = False
    enriched["raw_evidence"] = _clean(raw)
    enriched["category"] = ASSERTION_KIND
    enriched["database_relation_numeric_evidence"] = evidence_payload
    quality = _dict(enriched.get("evidence_quality"))
    quality["database_evidence_strength"] = (
        "EXACT_FK_SCOPED_ROOT_AND_AGGREGATE"
    )
    enriched["evidence_quality"] = quality
    enriched["gate_passed"] = finding.get("gate_passed") is True
    enriched["customer_delivery_status"] = finding.get(
        "customer_delivery_status",
        "candidate",
    )
    enriched["final_review_status"] = finding.get(
        "final_review_status",
        "PENDING_DELIVERY_GATE",
    )
    output["finding"] = enriched
    return output


def _database_relation_numeric_finalizer_hook(
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
    return enrich_database_relation_finding(
        result,
        experiment=_dict(experiment),
    )


def install_database_relation_numeric_finding_bridge() -> None:
    """Register relation-numeric evidence on the canonical Finalizer."""
    from . import experiment_outcome_finalizer as finalizer

    finalizer.register_finalizer_hook(
        "database_relation_numeric_finding",
        _database_relation_numeric_finalizer_hook,
    )


__all__ = [
    "FINDING_EVIDENCE_SCHEMA",
    "build_database_relation_finding_evidence",
    "enrich_database_relation_finding",
    "install_database_relation_numeric_finding_bridge",
]
