"""Attach exact database transition evidence to Contract Oracle candidates.

The existing Finalizer remains the finding author. This explicit postprocessor
only replaces its legacy HTTP-shaped ``db_snapshot`` when the failed assertion
was evaluated from approved database BEFORE/AFTER receipts. It never changes the
Oracle verdict, delivery gate, severity, cleanup decision, or candidate status.
"""
from __future__ import annotations

import functools
import sys
from copy import deepcopy
from typing import Any

from .database_state_transition_oracle import (
    DATABASE_STATE_TRANSITION_ASSERTION_KIND,
)

FINDING_EVIDENCE_SCHEMA = "qualibug.database-state-transition-finding-evidence.v1"
_INSTALL_MARKER = "__qualibug_database_state_transition_finding_bridge_v1__"
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


def _database_assertion(finding: dict[str, Any]) -> dict[str, Any]:
    evidence_assertion = _dict(_dict(finding.get("evidence")).get("assertion"))
    if _text(evidence_assertion.get("kind")) == DATABASE_STATE_TRANSITION_ASSERTION_KIND:
        return evidence_assertion
    for raw in _list(finding.get("failed_assertions")):
        row = _dict(raw)
        if _text(row.get("kind")) == DATABASE_STATE_TRANSITION_ASSERTION_KIND:
            return row
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


def build_database_state_transition_finding_evidence(
    assertion_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Build a secret-free evidence projection from one typed assertion receipt."""
    assertion = _dict(assertion_receipt)
    expected = _dict(assertion.get("expected"))
    actual = _dict(assertion.get("actual"))
    before = _phase_evidence(_dict(actual.get("before_snapshot")))
    after = _phase_evidence(_dict(actual.get("after_snapshot")))
    payload = {
        "schema": FINDING_EVIDENCE_SCHEMA,
        "assertion_id": _text(assertion.get("assertion_id")),
        "assertion_kind": _text(assertion.get("kind")),
        "assertion_status": _text(assertion.get("status")),
        "reason_code": _text(assertion.get("reason_code")),
        "database_observer_contract_ref": _text(
            expected.get("database_observer_contract_ref")
            or actual.get("database_observer_contract_ref")
        ),
        "database_table_ref": _text(
            expected.get("database_table_ref") or actual.get("database_table_ref")
        ),
        "database_field_id": _text(
            expected.get("database_field_id") or actual.get("database_field_id")
        ),
        "database_field_name": _text(
            expected.get("database_field_name") or actual.get("database_field_name")
        ),
        "transition_policy": _text(expected.get("transition_policy")),
        "expected_before": expected.get("before"),
        "expected_after": expected.get("after"),
        "observed_before": actual.get("observed_before"),
        "observed_after": actual.get("observed_after"),
        "identity_match": actual.get("identity_match") is True,
        "before_snapshot": before,
        "after_snapshot": after,
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


def enrich_database_state_transition_finding(
    result: Any,
    *,
    experiment: dict[str, Any] | None = None,
) -> Any:
    """Replace only the legacy DB-shaped HTTP evidence on a DB assertion candidate."""
    if not isinstance(result, dict):
        return result
    finding = _dict(result.get("finding"))
    if not finding:
        return result
    assertion = _database_assertion(finding)
    if not assertion:
        return result
    database_evidence = build_database_state_transition_finding_evidence(assertion)
    if not database_evidence.get("database_observer_contract_ref"):
        return result

    output = dict(result)
    enriched_finding = deepcopy(finding)
    evidence = _dict(enriched_finding.get("evidence"))
    evidence["database_state_transition"] = database_evidence
    evidence["database_evidence_basis"] = "APPROVED_BEFORE_AFTER_PHASE_RECEIPTS"
    evidence["database_observer_fact_only"] = True
    enriched_finding["evidence"] = evidence

    raw = _dict(enriched_finding.get("raw_evidence"))
    raw["db_snapshot"] = {
        "schema": FINDING_EVIDENCE_SCHEMA,
        "database_observer_contract_ref": database_evidence[
            "database_observer_contract_ref"
        ],
        "database_table_ref": database_evidence["database_table_ref"],
        "database_field_id": database_evidence["database_field_id"],
        "database_field_name": database_evidence["database_field_name"],
        "expected": {
            "transition_policy": database_evidence["transition_policy"],
            "before": database_evidence["expected_before"],
            "after": database_evidence["expected_after"],
        },
        "actual": {
            "before": database_evidence["observed_before"],
            "after": database_evidence["observed_after"],
            "identity_match": database_evidence["identity_match"],
        },
        "before_receipt": database_evidence["before_snapshot"],
        "after_receipt": database_evidence["after_snapshot"],
        "raw_sql_retained": False,
        "dsn_retained": False,
        "secret_values_retained": False,
    }
    raw["db_snapshot_source"] = "approved_database_observer_phase_receipts"
    raw["legacy_http_body_used_as_db_snapshot"] = False
    enriched_finding["raw_evidence"] = _clean(raw)

    enriched_finding["category"] = DATABASE_STATE_TRANSITION_ASSERTION_KIND
    enriched_finding["database_state_transition_evidence"] = database_evidence
    quality = _dict(enriched_finding.get("evidence_quality"))
    quality["database_evidence_strength"] = "EXACT_IDENTITY_BOUND_BEFORE_AFTER"
    enriched_finding["evidence_quality"] = quality
    # Explicitly preserve independent delivery authority. This bridge never upgrades
    # a candidate or marks the finding customer-deliverable.
    enriched_finding["gate_passed"] = finding.get("gate_passed") is True
    enriched_finding["customer_delivery_status"] = finding.get(
        "customer_delivery_status", "candidate"
    )
    enriched_finding["final_review_status"] = finding.get(
        "final_review_status", "PENDING_DELIVERY_GATE"
    )
    output["finding"] = enriched_finding
    return output


def install_database_state_transition_finding_bridge() -> None:
    """Wrap the existing Finalizer after its current explicit compatibility layers."""
    try:
        from . import experiment_outcome_finalizer as finalizer
    except Exception:
        return
    original = getattr(finalizer, "finalize_experiment_execution", None)
    if not callable(original) or getattr(original, _INSTALL_MARKER, False):
        return

    @functools.wraps(original)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
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
        return enrich_database_state_transition_finding(
            result,
            experiment=_dict(experiment),
        )

    setattr(wrapped, _INSTALL_MARKER, True)
    setattr(wrapped, "__qualibug_original__", original)
    finalizer.finalize_experiment_execution = wrapped
    executor = sys.modules.get(f"{__package__}.experiment_executor")
    if executor is not None:
        executor.finalize_experiment_execution = wrapped


__all__ = [
    "FINDING_EVIDENCE_SCHEMA",
    "build_database_state_transition_finding_evidence",
    "enrich_database_state_transition_finding",
    "install_database_state_transition_finding_bridge",
]
