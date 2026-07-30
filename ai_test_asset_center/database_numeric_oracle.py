"""Exact numeric assertions over approved read-only database phase receipts.

The database Observer remains fact-only.  These registered Assertion DSL kinds
consume exact BEFORE/AFTER receipts that were already captured by the existing
Experiment Executor.  They never open a connection, execute SQL, infer a field
mapping, or author a delivery decision; Contract Oracle remains the only verdict
authority.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
from .database_state_transition_oracle import _phase_rows, _snapshot_value

DATABASE_NUMERIC_DELTA_ASSERTION_KIND = "database_numeric_delta"
DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND = "database_numeric_conservation"
DATABASE_NUMERIC_ORACLE_SCHEMA = "qualibug.database-numeric-oracle.v1"
_REQUIRED_EVIDENCE_KEY = "approved_database_observer_phase_receipts"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    return format(normalized, "f")


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


def _load_numeric_term(
    raw_term: Any,
    observations: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    term = _dict(raw_term)
    contract_ref = _text(term.get("database_observer_contract_ref"))
    table_ref = _text(term.get("database_table_ref"))
    field_name = _text(term.get("database_field_name"))
    before_draft_id = _text(term.get("before_draft_id"))
    after_draft_id = _text(term.get("after_draft_id"))
    actual: dict[str, Any] = {
        "term_id": _text(term.get("term_id")),
        "database_observer_contract_ref": contract_ref,
        "database_table_ref": table_ref,
        "database_table_name": _text(term.get("database_table_name")),
        "database_field_id": _text(term.get("database_field_id")),
        "database_field_name": field_name,
        "field_binding_id": _text(term.get("field_binding_id")),
        "before_snapshot": {},
        "after_snapshot": {},
        "lineage_match": False,
        "identity_match": False,
        "observed_before": None,
        "observed_after": None,
        "actual_delta": None,
        "observer_performed_oracle_verdict": False,
    }
    if not contract_ref or not table_ref or not field_name:
        return actual, "DATABASE_NUMERIC_TERM_SPEC_INCOMPLETE"

    before_rows, after_rows = _phase_rows(
        observations,
        contract_ref=contract_ref,
        before_draft_id=before_draft_id,
        after_draft_id=after_draft_id,
    )
    if len(before_rows) != 1 or len(after_rows) != 1:
        actual["before_candidate_count"] = len(before_rows)
        actual["after_candidate_count"] = len(after_rows)
        return (
            actual,
            "DATABASE_NUMERIC_SNAPSHOT_PAIR_MISSING"
            if len(before_rows) < 1 or len(after_rows) < 1
            else "DATABASE_NUMERIC_SNAPSHOT_PAIR_AMBIGUOUS",
        )

    before_snapshot, before_reason = _snapshot_value(
        before_rows[0],
        field_name=field_name,
        expected_table_ref=table_ref,
    )
    after_snapshot, after_reason = _snapshot_value(
        after_rows[0],
        field_name=field_name,
        expected_table_ref=table_ref,
    )
    actual["before_snapshot"] = before_snapshot
    actual["after_snapshot"] = after_snapshot
    if before_reason or after_reason:
        return actual, before_reason or after_reason

    lineage_match = bool(
        before_snapshot.get("campaign_id") == after_snapshot.get("campaign_id")
        and before_snapshot.get("execution_id") == after_snapshot.get("execution_id")
    )
    actual["lineage_match"] = lineage_match
    if not lineage_match:
        return actual, "DATABASE_NUMERIC_RECEIPT_LINEAGE_MISMATCH"

    before_identity = _list(before_snapshot.get("identity_parameter_fingerprints"))
    after_identity = _list(after_snapshot.get("identity_parameter_fingerprints"))
    before_key = _list(before_snapshot.get("identity_key"))
    after_key = _list(after_snapshot.get("identity_key"))
    identity_match = bool(
        before_identity
        and before_identity == after_identity
        and before_key
        and before_key == after_key
    )
    actual["identity_match"] = identity_match
    if not identity_match:
        return actual, "DATABASE_NUMERIC_IDENTITY_MISMATCH"

    before_value = _decimal(before_snapshot.get("field_value"))
    after_value = _decimal(after_snapshot.get("field_value"))
    actual["observed_before"] = before_snapshot.get("field_value")
    actual["observed_after"] = after_snapshot.get("field_value")
    if before_value is None or after_value is None:
        return actual, "DATABASE_NUMERIC_VALUE_NOT_NUMERIC"
    actual["observed_before_decimal"] = _decimal_text(before_value)
    actual["observed_after_decimal"] = _decimal_text(after_value)
    actual["actual_delta"] = _decimal_text(after_value - before_value)
    actual["campaign_id"] = _text(before_snapshot.get("campaign_id"))
    actual["execution_id"] = _text(before_snapshot.get("execution_id"))
    return actual, ""


def _same_execution(term_results: list[dict[str, Any]]) -> bool:
    lineages = {
        (_text(row.get("campaign_id")), _text(row.get("execution_id")))
        for row in term_results
    }
    return bool(lineages) and len(lineages) == 1 and all(lineages.pop())


def evaluate_database_numeric_delta(envelope: dict[str, Any]) -> dict[str, Any]:
    """Verify exact per-field numeric deltas from approved database snapshots."""
    env = _dict(envelope)
    spec = _dict(env.get("spec"))
    observations = _dict(env.get("observations"))
    terms = [_dict(row) for row in _list(spec.get("numeric_terms")) if _dict(row)]
    expected: dict[str, Any] = {
        "schema": DATABASE_NUMERIC_ORACLE_SCHEMA,
        "numeric_policy": "FIELD_DELTA",
        "terms": terms,
    }
    actual: dict[str, Any] = {
        "schema": DATABASE_NUMERIC_ORACLE_SCHEMA,
        "numeric_policy": "FIELD_DELTA",
        "term_results": [],
        "same_execution": False,
        "observer_performed_oracle_verdict": False,
    }
    if not terms:
        return _reason(
            "DATABASE_NUMERIC_ASSERTION_SPEC_INCOMPLETE",
            expected=expected,
            actual=actual,
        )

    results: list[dict[str, Any]] = []
    for term in terms:
        loaded, reason = _load_numeric_term(term, observations)
        results.append(loaded)
        if reason:
            actual["term_results"] = results
            return _reason(reason, expected=expected, actual=actual)
    actual["term_results"] = results
    actual["same_execution"] = _same_execution(results)
    if not actual["same_execution"]:
        return _reason(
            "DATABASE_NUMERIC_MULTI_TERM_LINEAGE_MISMATCH",
            expected=expected,
            actual=actual,
        )

    all_passed = True
    for term, result in zip(terms, results):
        before_value = _decimal(result.get("observed_before_decimal"))
        after_value = _decimal(result.get("observed_after_decimal"))
        delta = _decimal(result.get("actual_delta"))
        if before_value is None or after_value is None or delta is None:
            return _reason(
                "DATABASE_NUMERIC_VALUE_NOT_NUMERIC",
                expected=expected,
                actual=actual,
            )
        tolerance = _decimal(term.get("tolerance"))
        tolerance = tolerance if tolerance is not None else Decimal("0")
        if tolerance < 0:
            return _reason(
                "DATABASE_NUMERIC_TOLERANCE_INVALID",
                expected=expected,
                actual=actual,
            )
        expected_delta = _decimal(term.get("expected_delta"))
        expected_after = _decimal(term.get("expected_value"))
        direction = _text(term.get("expected_delta_direction")).casefold()
        checks: list[bool] = []
        if term.get("expected_delta") not in (None, ""):
            if expected_delta is None:
                return _reason(
                    "DATABASE_NUMERIC_EXPECTED_DELTA_INVALID",
                    expected=expected,
                    actual=actual,
                )
            checks.append(abs(delta - expected_delta) <= tolerance)
        if term.get("expected_value") not in (None, ""):
            if expected_after is None:
                return _reason(
                    "DATABASE_NUMERIC_EXPECTED_VALUE_INVALID",
                    expected=expected,
                    actual=actual,
                )
            checks.append(abs(after_value - expected_after) <= tolerance)
        if direction:
            if direction == "increase":
                checks.append(delta > tolerance)
            elif direction == "decrease":
                checks.append(delta < -tolerance)
            elif direction == "unchanged":
                checks.append(abs(delta) <= tolerance)
            else:
                return _reason(
                    "DATABASE_NUMERIC_DIRECTION_UNSUPPORTED",
                    expected=expected,
                    actual=actual,
                )
        if not checks:
            return _reason(
                "DATABASE_NUMERIC_DELTA_EXPECTATION_MISSING",
                expected=expected,
                actual=actual,
            )
        term_passed = all(checks)
        result["expected_delta"] = _decimal_text(expected_delta)
        result["expected_after"] = _decimal_text(expected_after)
        result["expected_direction"] = direction or None
        result["tolerance"] = _decimal_text(tolerance)
        result["term_passed"] = term_passed
        all_passed = all_passed and term_passed

    return {
        "passed": all_passed,
        "reason_code": "" if all_passed else "DATABASE_NUMERIC_DELTA_MISMATCH",
        "expected": expected,
        "actual": actual,
    }


def evaluate_database_numeric_conservation(envelope: dict[str, Any]) -> dict[str, Any]:
    """Verify an unchanged weighted sum inside one approved row contract."""
    env = _dict(envelope)
    spec = _dict(env.get("spec"))
    observations = _dict(env.get("observations"))
    terms = [_dict(row) for row in _list(spec.get("numeric_terms")) if _dict(row)]
    policy = _text(spec.get("numeric_policy") or "UNCHANGED_WEIGHTED_SUM").upper()
    expected: dict[str, Any] = {
        "schema": DATABASE_NUMERIC_ORACLE_SCHEMA,
        "numeric_policy": policy,
        "terms": terms,
    }
    actual: dict[str, Any] = {
        "schema": DATABASE_NUMERIC_ORACLE_SCHEMA,
        "numeric_policy": policy,
        "term_results": [],
        "same_execution": False,
        "single_contract_scope": False,
        "before_weighted_sum": None,
        "after_weighted_sum": None,
        "difference": None,
        "observer_performed_oracle_verdict": False,
    }
    if policy != "UNCHANGED_WEIGHTED_SUM" or not terms:
        return _reason(
            "DATABASE_NUMERIC_CONSERVATION_SPEC_UNSUPPORTED",
            expected=expected,
            actual=actual,
        )
    contract_refs = {
        _text(term.get("database_observer_contract_ref")) for term in terms
    }
    contract_refs.discard("")
    actual["single_contract_scope"] = len(contract_refs) == 1
    if not actual["single_contract_scope"]:
        return _reason(
            "DATABASE_NUMERIC_CROSS_CONTRACT_SCOPE_UNPROVEN",
            expected=expected,
            actual=actual,
        )

    results: list[dict[str, Any]] = []
    before_sum = Decimal("0")
    after_sum = Decimal("0")
    for term in terms:
        loaded, reason = _load_numeric_term(term, observations)
        results.append(loaded)
        if reason:
            actual["term_results"] = results
            return _reason(reason, expected=expected, actual=actual)
        coefficient = _decimal(term.get("coefficient"))
        coefficient = coefficient if coefficient is not None else Decimal("1")
        before_value = _decimal(loaded.get("observed_before_decimal"))
        after_value = _decimal(loaded.get("observed_after_decimal"))
        if before_value is None or after_value is None:
            actual["term_results"] = results
            return _reason(
                "DATABASE_NUMERIC_VALUE_NOT_NUMERIC",
                expected=expected,
                actual=actual,
            )
        loaded["coefficient"] = _decimal_text(coefficient)
        loaded["before_weighted_value"] = _decimal_text(before_value * coefficient)
        loaded["after_weighted_value"] = _decimal_text(after_value * coefficient)
        before_sum += before_value * coefficient
        after_sum += after_value * coefficient

    actual["term_results"] = results
    actual["same_execution"] = _same_execution(results)
    if not actual["same_execution"]:
        return _reason(
            "DATABASE_NUMERIC_MULTI_TERM_LINEAGE_MISMATCH",
            expected=expected,
            actual=actual,
        )
    identity_scopes = {
        (
            tuple(_list(_dict(row.get("before_snapshot")).get("identity_key"))),
            tuple(
                _list(
                    _dict(row.get("before_snapshot")).get(
                        "identity_parameter_fingerprints"
                    )
                )
            ),
        )
        for row in results
    }
    if len(identity_scopes) != 1:
        return _reason(
            "DATABASE_NUMERIC_CONSERVATION_IDENTITY_SCOPE_MISMATCH",
            expected=expected,
            actual=actual,
        )

    tolerance = _decimal(spec.get("tolerance"))
    tolerance = tolerance if tolerance is not None else Decimal("0")
    if tolerance < 0:
        return _reason(
            "DATABASE_NUMERIC_TOLERANCE_INVALID",
            expected=expected,
            actual=actual,
        )
    difference = after_sum - before_sum
    actual["before_weighted_sum"] = _decimal_text(before_sum)
    actual["after_weighted_sum"] = _decimal_text(after_sum)
    actual["difference"] = _decimal_text(difference)
    actual["tolerance"] = _decimal_text(tolerance)
    passed = abs(difference) <= tolerance
    return {
        "passed": passed,
        "reason_code": "" if passed else "DATABASE_NUMERIC_CONSERVATION_VIOLATED",
        "expected": expected,
        "actual": actual,
    }


def install_database_numeric_assertions() -> tuple[str, str]:
    """Register both numeric evaluators on the existing Assertion DSL."""
    installed: list[str] = []
    if DATABASE_NUMERIC_DELTA_ASSERTION_KIND not in registered_assertion_kinds():
        installed.append(
            register_assertion_kind(
                DATABASE_NUMERIC_DELTA_ASSERTION_KIND,
                evaluator=evaluate_database_numeric_delta,
                required_evidence_keys=(_REQUIRED_EVIDENCE_KEY,),
            )
        )
    else:
        installed.append(DATABASE_NUMERIC_DELTA_ASSERTION_KIND)
    if DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND not in registered_assertion_kinds():
        installed.append(
            register_assertion_kind(
                DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND,
                evaluator=evaluate_database_numeric_conservation,
                required_evidence_keys=(_REQUIRED_EVIDENCE_KEY,),
            )
        )
    else:
        installed.append(DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND)
    return installed[0], installed[1]


__all__ = [
    "DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND",
    "DATABASE_NUMERIC_DELTA_ASSERTION_KIND",
    "DATABASE_NUMERIC_ORACLE_SCHEMA",
    "evaluate_database_numeric_conservation",
    "evaluate_database_numeric_delta",
    "install_database_numeric_assertions",
]
