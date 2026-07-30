"""Exact database state-transition assertions on approved read-only snapshots.

The database Observer remains fact-only. This module registers one restricted
Assertion DSL kind that compares exact BEFORE/AFTER snapshots emitted by the
approved database phase aggregate. It never opens a connection, executes SQL,
invents a field mapping, or authors a delivery decision; the existing Contract
Oracle remains the only verdict authority.
"""
from __future__ import annotations

from typing import Any

from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds

DATABASE_STATE_TRANSITION_ASSERTION_KIND = "database_state_transition"
DATABASE_STATE_TRANSITION_ORACLE_SCHEMA = "qualibug.database-state-transition-oracle.v1"
_REQUIRED_EVIDENCE_KEY = "approved_database_snapshots"
_DIRECT_READBACK_OBSERVER_ID = "approved_database_readback"
_UNKNOWN_STATES = frozenset({"", "unknown", "unknown_state"})


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _state_token(value: Any) -> str:
    normalized = _text(value).replace("-", " ").replace("_", " ")
    return "_".join(normalized.split()).casefold()


def _reason(code: str, *, expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    return {"passed": None, "reason_code": code, "expected": expected, "actual": actual}


def _phase_rows(
    observations: dict[str, Any],
    *,
    contract_ref: str,
    before_draft_id: str,
    after_draft_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    for raw in _list(observations.get(_REQUIRED_EVIDENCE_KEY)):
        row = _dict(raw)
        if _text(row.get("observer_contract_ref")) != contract_ref:
            continue
        phase = _text(row.get("phase")).upper()
        draft_id = _text(row.get("draft_id"))
        if phase == "BEFORE" and (not before_draft_id or draft_id == before_draft_id):
            before.append(row)
        elif phase == "AFTER" and (not after_draft_id or draft_id == after_draft_id):
            after.append(row)
    return before, after


def _snapshot_value(
    phase_row: dict[str, Any],
    *,
    field_name: str,
    expected_table_ref: str,
) -> tuple[dict[str, Any], str]:
    snapshot = _dict(phase_row.get("snapshot"))
    actual: dict[str, Any] = {
        "draft_id": _text(phase_row.get("draft_id")),
        "phase": _text(phase_row.get("phase")).upper(),
        "phase_receipt_id": _text(phase_row.get("receipt_id")),
        "source_observer_id": _text(phase_row.get("source_observer_id")),
        "database_table_ref": _text(snapshot.get("database_table_ref")),
        "database_table_name": _text(snapshot.get("database_table_name")),
        "match_status": _text(snapshot.get("match_status")),
        "row_count": snapshot.get("row_count"),
        "row_fingerprint": _text(snapshot.get("row_fingerprint")),
        "identity_key": _list(snapshot.get("identity_key")),
        "identity_parameter_fingerprints": _list(snapshot.get("identity_parameter_fingerprints")),
        "field_name": field_name,
        "field_value": None,
    }
    if actual["source_observer_id"] != _DIRECT_READBACK_OBSERVER_ID:
        return actual, "DATABASE_STATE_SNAPSHOT_SOURCE_INVALID"
    if phase_row.get("oracle_verdict_emitted") is not False:
        return actual, "DATABASE_STATE_OBSERVER_AUTHORITY_INVALID"
    if expected_table_ref and actual["database_table_ref"] != expected_table_ref:
        return actual, "DATABASE_STATE_TABLE_SCOPE_MISMATCH"
    match_status = actual["match_status"]
    if match_status == "NOT_FOUND":
        return actual, "DATABASE_STATE_ROW_NOT_FOUND"
    if match_status == "NON_UNIQUE_IDENTITY":
        return actual, "DATABASE_STATE_IDENTITY_NOT_UNIQUE"
    rows = [dict(row) for row in _list(snapshot.get("rows")) if isinstance(row, dict)]
    if match_status != "MATCHED_ONE" or actual["row_count"] != 1 or len(rows) != 1:
        return actual, "DATABASE_STATE_UNIQUE_ROW_NOT_PROVEN"
    if field_name not in rows[0]:
        return actual, "DATABASE_STATE_FIELD_NOT_OBSERVED"
    actual["field_value"] = rows[0].get(field_name)
    return actual, ""


def evaluate_database_state_transition(envelope: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one exact database transition without performing observation."""
    env = _dict(envelope)
    spec = _dict(env.get("spec"))
    observations = _dict(env.get("observations"))
    contract_ref = _text(spec.get("database_observer_contract_ref"))
    table_ref = _text(spec.get("database_table_ref"))
    field_name = _text(spec.get("database_field_name"))
    field_id = _text(spec.get("database_field_id"))
    before_draft_id = _text(spec.get("before_draft_id"))
    after_draft_id = _text(spec.get("after_draft_id"))
    from_state = _text(spec.get("from_state"))
    to_state = _text(spec.get("to_state"))
    operator = _text(spec.get("operator") or "must_transition").lower()
    forbidden = operator in {"must_not_transition", "forbidden", "must_not"} or _text(
        spec.get("source_assertion_kind")
    ).lower() == "forbidden_state_transition"
    expected = {
        "schema": DATABASE_STATE_TRANSITION_ORACLE_SCHEMA,
        "database_observer_contract_ref": contract_ref,
        "database_table_ref": table_ref,
        "database_field_id": field_id,
        "database_field_name": field_name,
        "transition_policy": "MUST_NOT_TRANSITION" if forbidden else "MUST_TRANSITION",
        "before": from_state,
        "after": to_state,
    }
    actual: dict[str, Any] = {
        "schema": DATABASE_STATE_TRANSITION_ORACLE_SCHEMA,
        "database_observer_contract_ref": contract_ref,
        "database_table_ref": table_ref,
        "database_field_id": field_id,
        "database_field_name": field_name,
        "before_snapshot": {},
        "after_snapshot": {},
        "identity_match": False,
        "observed_before": None,
        "observed_after": None,
        "observer_performed_oracle_verdict": False,
    }
    if (
        not contract_ref
        or not field_name
        or _state_token(from_state) in _UNKNOWN_STATES
        or _state_token(to_state) in _UNKNOWN_STATES
    ):
        return _reason("DATABASE_STATE_ASSERTION_SPEC_INCOMPLETE", expected=expected, actual=actual)

    before_rows, after_rows = _phase_rows(
        observations,
        contract_ref=contract_ref,
        before_draft_id=before_draft_id,
        after_draft_id=after_draft_id,
    )
    if len(before_rows) != 1 or len(after_rows) != 1:
        actual["before_candidate_count"] = len(before_rows)
        actual["after_candidate_count"] = len(after_rows)
        return _reason(
            "DATABASE_STATE_SNAPSHOT_PAIR_MISSING"
            if len(before_rows) < 1 or len(after_rows) < 1
            else "DATABASE_STATE_SNAPSHOT_PAIR_AMBIGUOUS",
            expected=expected,
            actual=actual,
        )

    before_snapshot, before_reason = _snapshot_value(
        before_rows[0], field_name=field_name, expected_table_ref=table_ref
    )
    after_snapshot, after_reason = _snapshot_value(
        after_rows[0], field_name=field_name, expected_table_ref=table_ref
    )
    actual["before_snapshot"] = before_snapshot
    actual["after_snapshot"] = after_snapshot
    if before_reason or after_reason:
        return _reason(before_reason or after_reason, expected=expected, actual=actual)

    before_identity = before_snapshot["identity_parameter_fingerprints"]
    after_identity = after_snapshot["identity_parameter_fingerprints"]
    before_key = before_snapshot["identity_key"]
    after_key = after_snapshot["identity_key"]
    identity_match = bool(
        before_identity
        and before_identity == after_identity
        and before_key
        and before_key == after_key
    )
    actual["identity_match"] = identity_match
    if not identity_match:
        return _reason("DATABASE_STATE_IDENTITY_MISMATCH", expected=expected, actual=actual)

    before_value = before_snapshot.get("field_value")
    after_value = after_snapshot.get("field_value")
    actual["observed_before"] = before_value
    actual["observed_after"] = after_value
    if _state_token(before_value) != _state_token(from_state):
        return _reason("DATABASE_STATE_PRECONDITION_NOT_MET", expected=expected, actual=actual)

    reached_target = _state_token(after_value) == _state_token(to_state)
    if forbidden:
        return {
            "passed": not reached_target,
            "reason_code": "DATABASE_FORBIDDEN_STATE_TRANSITION_OBSERVED" if reached_target else "",
            "expected": expected,
            "actual": actual,
        }
    return {
        "passed": reached_target,
        "reason_code": "" if reached_target else "DATABASE_STATE_TRANSITION_NOT_OBSERVED",
        "expected": expected,
        "actual": actual,
    }


def install_database_state_transition_assertion() -> str:
    """Register the evaluator on the existing Assertion DSL."""
    if DATABASE_STATE_TRANSITION_ASSERTION_KIND in registered_assertion_kinds():
        return DATABASE_STATE_TRANSITION_ASSERTION_KIND
    return register_assertion_kind(
        DATABASE_STATE_TRANSITION_ASSERTION_KIND,
        evaluator=evaluate_database_state_transition,
        required_evidence_keys=(_REQUIRED_EVIDENCE_KEY,),
    )


__all__ = [
    "DATABASE_STATE_TRANSITION_ASSERTION_KIND",
    "DATABASE_STATE_TRANSITION_ORACLE_SCHEMA",
    "evaluate_database_state_transition",
    "install_database_state_transition_assertion",
]
