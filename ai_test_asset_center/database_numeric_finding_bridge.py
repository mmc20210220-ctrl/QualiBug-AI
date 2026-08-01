"""Attach exact database numeric evidence to Contract Oracle candidates.

The existing Finalizer remains the finding author.  This postprocessor replaces
legacy HTTP-shaped ``db_snapshot`` content only when the primary failed assertion
was evaluated from approved database numeric BEFORE/AFTER receipts.  It never
changes the Oracle verdict, severity, delivery gate, cleanup decision, or
candidate status.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from .database_numeric_oracle import (
    DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND,
    DATABASE_NUMERIC_DELTA_ASSERTION_KIND,
)

FINDING_EVIDENCE_SCHEMA = "qualibug.database-numeric-finding-evidence.v1"
_NUMERIC_KINDS = frozenset(
    {
        DATABASE_NUMERIC_DELTA_ASSERTION_KIND,
        DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND,
    }
)
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


def _numeric_assertion(finding: dict[str, Any]) -> dict[str, Any]:
    evidence_assertion = _dict(_dict(finding.get("evidence")).get("assertion"))
    if evidence_assertion:
        return (
            evidence_assertion
            if _text(evidence_assertion.get("kind")) in _NUMERIC_KINDS
            else {}
        )
    failed = [_dict(row) for row in _list(finding.get("failed_assertions")) if _dict(row)]
    if len(failed) == 1 and _text(failed[0].get("kind")) in _NUMERIC_KINDS:
        return failed[0]
    return {}


def _phase_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    row = _dict(snapshot)
    return {
        "phase": _text(row.get("phase")),
        "draft_id": _text(row.get("draft_id")),
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
            _text(value) for value in _list(row.get("identity_key")) if _text(value)
        ],
        "identity_parameter_fingerprints": [
            _text(value)
            for value in _list(row.get("identity_parameter_fingerprints"))
            if _text(value)
        ],
        "field_name": _text(row.get("field_name")),
        "field_value": row.get("field_value"),
    }


def _term_evidence(raw: Any) -> dict[str, Any]:
    row = _dict(raw)
    return {
        "term_id": _text(row.get("term_id")),
        "database_observer_contract_ref": _text(
            row.get("database_observer_contract_ref")
        ),
        "database_table_ref": _text(row.get("database_table_ref")),
        "database_table_name": _text(row.get("database_table_name")),
        "database_field_id": _text(row.get("database_field_id")),
        "database_field_name": _text(row.get("database_field_name")),
        "field_binding_id": _text(row.get("field_binding_id")),
        "observed_before": row.get("observed_before"),
        "observed_after": row.get("observed_after"),
        "observed_before_decimal": row.get("observed_before_decimal"),
        "observed_after_decimal": row.get("observed_after_decimal"),
        "actual_delta": row.get("actual_delta"),
        "expected_delta": row.get("expected_delta"),
        "expected_after": row.get("expected_after"),
        "expected_direction": row.get("expected_direction"),
        "tolerance": row.get("tolerance"),
        "coefficient": row.get("coefficient"),
        "before_weighted_value": row.get("before_weighted_value"),
        "after_weighted_value": row.get("after_weighted_value"),
        "term_passed": row.get("term_passed"),
        "lineage_match": row.get("lineage_match") is True,
        "identity_match": row.get("identity_match") is True,
        "before_snapshot": _phase_evidence(_dict(row.get("before_snapshot"))),
        "after_snapshot": _phase_evidence(_dict(row.get("after_snapshot"))),
        "observer_performed_oracle_verdict": (
            row.get("observer_performed_oracle_verdict") is True
        ),
    }


def build_database_numeric_finding_evidence(
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
        "numeric_policy": _text(
            actual.get("numeric_policy") or expected.get("numeric_policy")
        ),
        "term_results": [
            _term_evidence(row)
            for row in _list(actual.get("term_results"))
            if isinstance(row, dict)
        ],
        "same_execution": actual.get("same_execution") is True,
        "single_contract_scope": actual.get("single_contract_scope"),
        "before_weighted_sum": actual.get("before_weighted_sum"),
        "after_weighted_sum": actual.get("after_weighted_sum"),
        "difference": actual.get("difference"),
        "tolerance": actual.get("tolerance"),
        "observer_performed_oracle_verdict": (
            actual.get("observer_performed_oracle_verdict") is True
        ),
        "oracle_authority": "ContractOracle",
        "database_observer_authority": "FACT_ONLY",
        "raw_sql_retained": False,
        "dsn_retained": False,
        "secret_values_retained": False,
        "predicate_values_retained": False,
    }
    return _clean(payload)


def enrich_database_numeric_finding(
    result: Any,
    *,
    experiment: dict[str, Any] | None = None,
) -> Any:
    if not isinstance(result, dict):
        return result
    finding = _dict(result.get("finding"))
    if not finding:
        return result
    assertion = _numeric_assertion(finding)
    if not assertion:
        return result
    database_evidence = build_database_numeric_finding_evidence(assertion)
    if not database_evidence.get("term_results"):
        return result

    output = dict(result)
    enriched = deepcopy(finding)
    evidence = _dict(enriched.get("evidence"))
    evidence["database_numeric"] = database_evidence
    evidence["database_evidence_basis"] = "APPROVED_BEFORE_AFTER_PHASE_RECEIPTS"
    evidence["database_observer_fact_only"] = True
    enriched["evidence"] = evidence

    raw = _dict(enriched.get("raw_evidence"))
    raw["db_snapshot"] = {
        "schema": FINDING_EVIDENCE_SCHEMA,
        "assertion_kind": database_evidence["assertion_kind"],
        "numeric_policy": database_evidence["numeric_policy"],
        "term_results": database_evidence["term_results"],
        "before_weighted_sum": database_evidence["before_weighted_sum"],
        "after_weighted_sum": database_evidence["after_weighted_sum"],
        "difference": database_evidence["difference"],
        "raw_sql_retained": False,
        "dsn_retained": False,
        "secret_values_retained": False,
        "predicate_values_retained": False,
    }
    raw["db_snapshot_source"] = "approved_database_observer_phase_receipts"
    raw["legacy_http_body_used_as_db_snapshot"] = False
    enriched["raw_evidence"] = _clean(raw)

    enriched["category"] = database_evidence["assertion_kind"]
    enriched["database_numeric_evidence"] = database_evidence
    quality = _dict(enriched.get("evidence_quality"))
    quality["database_evidence_strength"] = "EXACT_IDENTITY_BOUND_NUMERIC_BEFORE_AFTER"
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


def _database_numeric_finalizer_hook(
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
    return enrich_database_numeric_finding(
        result,
        experiment=_dict(experiment),
    )


def install_database_numeric_finding_bridge() -> None:
    """Register database evidence enrichment on the canonical Finalizer."""
    from . import experiment_outcome_finalizer as finalizer

    finalizer.register_finalizer_hook(
        "database_numeric_finding",
        _database_numeric_finalizer_hook,
    )


__all__ = [
    "FINDING_EVIDENCE_SCHEMA",
    "build_database_numeric_finding_evidence",
    "enrich_database_numeric_finding",
    "install_database_numeric_finding_bridge",
]
