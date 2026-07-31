"""Evaluate exact root-row delta versus approved FK aggregate delta facts.

The evaluator consumes only typed BEFORE/AFTER phase receipts already produced by
the existing database observers. It never opens a database connection, executes
SQL, selects a relation, infers a sign, or emits a delivery decision.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
from .database_relation_delta_experiment_projection import ASSERTION_KIND
from .database_relation_numeric_oracle import (
    _aggregate_request_matches,
    _normalized_relation_key,
)
from .database_relation_observer_runtime import (
    EVIDENCE_KEY as RELATION_EVIDENCE_KEY,
    OBSERVER_ID as DIRECT_RELATION_OBSERVER_ID,
)
from .database_state_transition_oracle import _phase_rows, _snapshot_value

ORACLE_SCHEMA = "qualibug.database-relation-delta-oracle.v1"
_ROOT_RECEIPT_KEY = "approved_database_observer_phase_receipts"
_RELATION_RECEIPT_KEY = "approved_database_relation_phase_receipts"


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
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def _reason(
    code: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> dict[str, Any]:
    return {
        "passed": None,
        "reason_code": code,
        "expected": expected,
        "actual": actual,
    }


def _declared_decimal(
    source: dict[str, Any],
    key: str,
    *,
    default: Decimal,
) -> tuple[Decimal | None, bool]:
    if key not in source or source.get(key) in (None, ""):
        return default, True
    parsed = _decimal(source.get(key))
    return parsed, parsed is not None


def _compare(
    left: Decimal,
    right: Decimal,
    operator: str,
    tolerance: Decimal,
) -> bool:
    op = _text(operator).upper()
    if op in {"EQ", "EQUALS", "=="}:
        return abs(left - right) <= tolerance
    if op in {"NEQ", "NE", "!="}:
        return abs(left - right) > tolerance
    if op in {"LTE", "LE", "<="}:
        return left <= right + tolerance
    if op in {"GTE", "GE", ">="}:
        return left + tolerance >= right
    if op in {"LT", "<"}:
        return left < right and abs(left - right) > tolerance
    if op in {"GT", ">"}:
        return left > right and abs(left - right) > tolerance
    raise ValueError("database_relation_delta_comparison_operator_unsupported")


def _normalized_requests(value: Any) -> list[dict[str, str]]:
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


def _relation_phase(
    observations: dict[str, Any],
    *,
    relation_ref: str,
    draft_id: str,
    phase: str,
    aggregate: str,
    alias: str,
    child_field_id: str,
    child_field_name: str,
    scope_count_alias: str,
) -> tuple[dict[str, Any], str]:
    target = _text(phase).upper()
    matches: list[dict[str, Any]] = []
    for raw in _list(observations.get(_RELATION_RECEIPT_KEY)):
        row = _dict(raw)
        if (
            _text(row.get("relation_observer_contract_ref")) == relation_ref
            and _text(row.get("draft_id")) == draft_id
            and _text(row.get("observation_phase")).upper() == target
        ):
            matches.append(row)
    if len(matches) != 1:
        return {"candidate_count": len(matches), "phase": target}, (
            "DATABASE_RELATION_DELTA_PHASE_RECEIPT_MISSING"
            if len(matches) < 1
            else "DATABASE_RELATION_DELTA_PHASE_RECEIPT_AMBIGUOUS"
        )

    receipt = matches[0]
    payload = _dict(_dict(receipt.get("evidence")).get(RELATION_EVIDENCE_KEY))
    requests = _normalized_requests(payload.get("aggregate_requests"))
    actual = {
        "draft_id": _text(receipt.get("draft_id")),
        "phase": target,
        "phase_receipt_id": _text(receipt.get("phase_receipt_id") or receipt.get("receipt_id")),
        "receipt_id": _text(receipt.get("receipt_id")),
        "phase_receipt_status": _text(receipt.get("status")).upper(),
        "source_observer_id": _text(receipt.get("observer_id")),
        "campaign_id": _text(receipt.get("campaign_id")),
        "execution_id": _text(receipt.get("execution_id")),
        "relation_observer_ref": _text(payload.get("relation_observer_ref")),
        "root_observer_ref": _text(payload.get("root_observer_ref")),
        "database_relationship_id": _text(payload.get("database_relationship_id")),
        "parent_table_ref": _text(payload.get("parent_table_ref")),
        "child_table_ref": _text(payload.get("child_table_ref")),
        "child_table_name": _text(payload.get("child_table_name")),
        "relation_key": _normalized_relation_key(payload.get("relation_key")),
        "relation_parameter_fingerprints": [
            _text(value)
            for value in _list(payload.get("relation_parameter_fingerprints"))
            if _text(value)
        ],
        "aggregate_requests": requests,
        "aggregate_alias": alias,
        "aggregate_value": _dict(payload.get("aggregate_values")).get(alias),
        "scope_count_alias": scope_count_alias,
        "scope_count": (
            _dict(payload.get("aggregate_values")).get(scope_count_alias)
            if scope_count_alias
            else None
        ),
        "aggregate_fingerprint": _text(payload.get("aggregate_fingerprint")),
        "client_side_filter_used": payload.get("client_side_filter_used") is True,
        "raw_rows_retained": payload.get("raw_rows_retained") is True,
        "payload_oracle_verdict_emitted": payload.get("oracle_verdict_emitted"),
        "aggregate_request_match": False,
    }
    if actual["source_observer_id"] != DIRECT_RELATION_OBSERVER_ID:
        return actual, "DATABASE_RELATION_DELTA_SOURCE_INVALID"
    if actual["phase_receipt_status"] != "OBSERVED" or not actual["phase_receipt_id"]:
        return actual, "DATABASE_RELATION_DELTA_RECEIPT_NOT_OBSERVED"
    if not actual["campaign_id"] or not actual["execution_id"]:
        return actual, "DATABASE_RELATION_DELTA_LINEAGE_MISSING"
    if (
        receipt.get("oracle_verdict_emitted") is not False
        or payload.get("oracle_verdict_emitted") is not False
    ):
        return actual, "DATABASE_RELATION_DELTA_OBSERVER_AUTHORITY_INVALID"
    if actual["relation_observer_ref"] != relation_ref:
        return actual, "DATABASE_RELATION_DELTA_OBSERVER_SCOPE_MISMATCH"
    if not actual["relation_key"] or not actual["relation_parameter_fingerprints"]:
        return actual, "DATABASE_RELATION_DELTA_IDENTITY_SCOPE_MISSING"
    if actual["client_side_filter_used"] or actual["raw_rows_retained"]:
        return actual, "DATABASE_RELATION_DELTA_EVIDENCE_POLICY_INVALID"
    if alias not in _dict(payload.get("aggregate_values")):
        return actual, "DATABASE_RELATION_DELTA_AGGREGATE_VALUE_MISSING"
    if not actual["aggregate_fingerprint"]:
        return actual, "DATABASE_RELATION_DELTA_AGGREGATE_FINGERPRINT_MISSING"

    primary_matches = [
        row
        for row in requests
        if _aggregate_request_matches(
            row,
            aggregate=aggregate,
            alias=alias,
            child_field_id=child_field_id,
            child_field_name=child_field_name,
        )
    ]
    scope_matches: list[dict[str, Any]] = []
    expected_request_count = 1
    if scope_count_alias:
        expected_request_count = 2
        scope_matches = [
            row
            for row in requests
            if _text(row.get("aggregate")).upper() == "COUNT"
            and _text(row.get("alias")) == scope_count_alias
            and not _text(row.get("database_field_id"))
            and not _text(row.get("database_field_name"))
        ]
        if scope_count_alias not in _dict(payload.get("aggregate_values")):
            return actual, "DATABASE_RELATION_DELTA_SCOPE_COUNT_MISSING"
    request_match = bool(
        len(requests) == expected_request_count
        and len(primary_matches) == 1
        and (not scope_count_alias or len(scope_matches) == 1)
    )
    actual["aggregate_request_match"] = request_match
    if not request_match:
        return actual, "DATABASE_RELATION_DELTA_AGGREGATE_REQUEST_SCOPE_MISMATCH"
    return actual, ""


def _aggregate_decimal(
    snapshot: dict[str, Any],
    *,
    aggregate: str,
) -> tuple[Decimal | None, bool, str]:
    value = _decimal(snapshot.get("aggregate_value"))
    if value is not None:
        return value, False, ""
    scope_count = _decimal(snapshot.get("scope_count"))
    if aggregate == "SUM" and scope_count == Decimal("0"):
        return Decimal("0"), True, ""
    if aggregate in {"SUM", "MIN", "MAX"} and scope_count is None:
        return None, False, "DATABASE_RELATION_DELTA_SCOPE_COUNT_INVALID"
    return None, False, "DATABASE_RELATION_DELTA_AGGREGATE_VALUE_NOT_NUMERIC"


def evaluate_database_relation_delta_conservation(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    env = _dict(envelope)
    spec = _dict(env.get("spec"))
    observations = _dict(env.get("observations"))
    binding = _dict(spec.get("database_relation_delta_binding"))

    relation_ref = _text(spec.get("database_relation_observer_ref"))
    relationship_id = _text(
        spec.get("database_relationship_id") or binding.get("database_relationship_id")
    )
    expected_relation_key = _normalized_relation_key(
        spec.get("relation_key") or binding.get("relation_key")
    )
    relation_before_draft_id = _text(
        spec.get("relation_before_draft_id") or binding.get("relation_before_draft_id")
    )
    relation_after_draft_id = _text(
        spec.get("relation_after_draft_id") or binding.get("relation_after_draft_id")
    )
    relation_pair_id = _text(spec.get("relation_pair_id") or binding.get("relation_pair_id"))
    root_ref = _text(spec.get("root_observer_contract_ref"))
    root_before_draft_id = _text(
        spec.get("root_before_draft_id") or binding.get("root_before_draft_id")
    )
    root_after_draft_id = _text(
        spec.get("root_after_draft_id") or binding.get("root_after_draft_id")
    )
    root_table_ref = _text(spec.get("root_table_ref"))
    root_field_id = _text(spec.get("root_database_field_id"))
    root_field_name = _text(spec.get("root_database_field_name"))
    child_table_ref = _text(spec.get("child_table_ref"))
    child_field_id = _text(spec.get("child_database_field_id"))
    child_field_name = _text(spec.get("child_database_field_name"))
    aggregate = _text(spec.get("aggregate")).upper()
    alias = _text(spec.get("aggregate_alias") or "related_value")
    scope_count_alias = _text(spec.get("scope_count_alias"))
    operator = _text(spec.get("comparison_operator") or "EQ").upper()
    aggregate_on_left = spec.get("aggregate_on_left") is True

    expected = {
        "schema": ORACLE_SCHEMA,
        "database_relation_observer_ref": relation_ref,
        "database_relationship_id": relationship_id,
        "relation_key": expected_relation_key,
        "relation_pair_id": relation_pair_id,
        "relation_before_draft_id": relation_before_draft_id,
        "relation_after_draft_id": relation_after_draft_id,
        "root_observer_contract_ref": root_ref,
        "root_before_draft_id": root_before_draft_id,
        "root_after_draft_id": root_after_draft_id,
        "root_table_ref": root_table_ref,
        "root_database_field_id": root_field_id,
        "root_database_field_name": root_field_name,
        "child_table_ref": child_table_ref,
        "child_database_field_id": child_field_id,
        "child_database_field_name": child_field_name,
        "aggregate": aggregate,
        "aggregate_alias": alias,
        "scope_count_alias": scope_count_alias,
        "comparison_operator": operator,
        "aggregate_on_left": aggregate_on_left,
        "left_coefficient": spec.get("left_coefficient", 1),
        "right_coefficient": spec.get("right_coefficient", 1),
        "comparison_phase_pair": "BEFORE_AFTER",
    }
    actual: dict[str, Any] = {
        "schema": ORACLE_SCHEMA,
        "root_before_snapshot": {},
        "root_after_snapshot": {},
        "relation_before_snapshot": {},
        "relation_after_snapshot": {},
        "lineage_match": False,
        "root_identity_match": False,
        "relation_identity_match": False,
        "cross_observer_identity_match": False,
        "relation_scope_match": False,
        "aggregate_request_match": False,
        "root_before": None,
        "root_after": None,
        "root_delta": None,
        "relation_before": None,
        "relation_after": None,
        "relation_delta": None,
        "left_delta": None,
        "right_delta": None,
        "weighted_left_delta": None,
        "weighted_right_delta": None,
        "difference": None,
        "observer_performed_oracle_verdict": False,
    }
    required = (
        relation_ref,
        relationship_id,
        expected_relation_key,
        relation_pair_id,
        relation_before_draft_id,
        relation_after_draft_id,
        root_ref,
        root_before_draft_id,
        root_after_draft_id,
        root_table_ref,
        root_field_id,
        root_field_name,
        child_table_ref,
        aggregate,
        alias,
    )
    if not all(required):
        return _reason("DATABASE_RELATION_DELTA_ASSERTION_SPEC_INCOMPLETE", expected, actual)
    child_pair_complete = bool(child_field_id) == bool(child_field_name)
    if (
        not child_pair_complete
        or (aggregate != "COUNT" and not child_field_id)
        or (aggregate in {"SUM", "MIN", "MAX"} and not scope_count_alias)
    ):
        return _reason("DATABASE_RELATION_DELTA_ASSERTION_SPEC_INCOMPLETE", expected, actual)

    before_rows, after_rows = _phase_rows(
        observations,
        contract_ref=root_ref,
        before_draft_id=root_before_draft_id,
        after_draft_id=root_after_draft_id,
    )
    if len(before_rows) != 1 or len(after_rows) != 1:
        actual["root_before_candidate_count"] = len(before_rows)
        actual["root_after_candidate_count"] = len(after_rows)
        return _reason(
            (
                "DATABASE_RELATION_DELTA_ROOT_PAIR_MISSING"
                if len(before_rows) < 1 or len(after_rows) < 1
                else "DATABASE_RELATION_DELTA_ROOT_PAIR_AMBIGUOUS"
            ),
            expected,
            actual,
        )
    root_before_snapshot, root_before_reason = _snapshot_value(
        before_rows[0],
        field_name=root_field_name,
        expected_table_ref=root_table_ref,
    )
    root_after_snapshot, root_after_reason = _snapshot_value(
        after_rows[0],
        field_name=root_field_name,
        expected_table_ref=root_table_ref,
    )
    actual["root_before_snapshot"] = root_before_snapshot
    actual["root_after_snapshot"] = root_after_snapshot
    if root_before_reason or root_after_reason:
        return _reason(root_before_reason or root_after_reason, expected, actual)

    relation_before, relation_before_reason = _relation_phase(
        observations,
        relation_ref=relation_ref,
        draft_id=relation_before_draft_id,
        phase="BEFORE",
        aggregate=aggregate,
        alias=alias,
        child_field_id=child_field_id,
        child_field_name=child_field_name,
        scope_count_alias=scope_count_alias,
    )
    relation_after, relation_after_reason = _relation_phase(
        observations,
        relation_ref=relation_ref,
        draft_id=relation_after_draft_id,
        phase="AFTER",
        aggregate=aggregate,
        alias=alias,
        child_field_id=child_field_id,
        child_field_name=child_field_name,
        scope_count_alias=scope_count_alias,
    )
    actual["relation_before_snapshot"] = relation_before
    actual["relation_after_snapshot"] = relation_after
    actual["aggregate_request_match"] = bool(
        relation_before.get("aggregate_request_match")
        and relation_after.get("aggregate_request_match")
    )
    if relation_before_reason or relation_after_reason:
        return _reason(relation_before_reason or relation_after_reason, expected, actual)

    all_lineages = {
        (
            _text(root_before_snapshot.get("campaign_id")),
            _text(root_before_snapshot.get("execution_id")),
        ),
        (
            _text(root_after_snapshot.get("campaign_id")),
            _text(root_after_snapshot.get("execution_id")),
        ),
        (
            _text(relation_before.get("campaign_id")),
            _text(relation_before.get("execution_id")),
        ),
        (
            _text(relation_after.get("campaign_id")),
            _text(relation_after.get("execution_id")),
        ),
    }
    lineage = next(iter(all_lineages)) if len(all_lineages) == 1 else ("", "")
    lineage_match = bool(len(all_lineages) == 1 and all(lineage))
    actual["lineage_match"] = lineage_match
    if not lineage_match:
        return _reason("DATABASE_RELATION_DELTA_RECEIPT_LINEAGE_MISMATCH", expected, actual)

    root_before_key = [
        _text(value)
        for value in _list(root_before_snapshot.get("identity_key"))
        if _text(value)
    ]
    root_after_key = [
        _text(value)
        for value in _list(root_after_snapshot.get("identity_key"))
        if _text(value)
    ]
    root_before_identity = [
        _text(value)
        for value in _list(root_before_snapshot.get("identity_parameter_fingerprints"))
        if _text(value)
    ]
    root_after_identity = [
        _text(value)
        for value in _list(root_after_snapshot.get("identity_parameter_fingerprints"))
        if _text(value)
    ]
    root_identity_match = bool(
        root_before_key
        and root_before_key == root_after_key
        and root_before_identity
        and root_before_identity == root_after_identity
    )
    actual["root_identity_match"] = root_identity_match
    if not root_identity_match:
        return _reason("DATABASE_RELATION_DELTA_ROOT_IDENTITY_MISMATCH", expected, actual)

    relation_before_identity = _list(relation_before.get("relation_parameter_fingerprints"))
    relation_after_identity = _list(relation_after.get("relation_parameter_fingerprints"))
    relation_identity_match = bool(
        relation_before_identity
        and relation_before_identity == relation_after_identity
    )
    actual["relation_identity_match"] = relation_identity_match
    if not relation_identity_match:
        return _reason("DATABASE_RELATION_DELTA_RELATION_IDENTITY_MISMATCH", expected, actual)

    cross_identity_match = bool(
        root_before_identity == relation_before_identity
        and root_after_identity == relation_after_identity
    )
    actual["cross_observer_identity_match"] = cross_identity_match
    if not cross_identity_match:
        return _reason(
            "DATABASE_RELATION_DELTA_CROSS_OBSERVER_IDENTITY_MISMATCH",
            expected,
            actual,
        )

    observed_before_key = _normalized_relation_key(relation_before.get("relation_key"))
    observed_after_key = _normalized_relation_key(relation_after.get("relation_key"))
    parent_key = [
        _text(row.get("parent_database_field_name"))
        for row in expected_relation_key
    ]
    relation_scope_match = bool(
        observed_before_key == expected_relation_key
        and observed_after_key == expected_relation_key
        and parent_key == root_before_key
        and _text(relation_before.get("database_relationship_id")) == relationship_id
        and _text(relation_after.get("database_relationship_id")) == relationship_id
        and _text(relation_before.get("root_observer_ref")) == root_ref
        and _text(relation_after.get("root_observer_ref")) == root_ref
        and _text(relation_before.get("parent_table_ref")) == root_table_ref
        and _text(relation_after.get("parent_table_ref")) == root_table_ref
        and _text(relation_before.get("child_table_ref")) == child_table_ref
        and _text(relation_after.get("child_table_ref")) == child_table_ref
    )
    actual["relation_scope_match"] = relation_scope_match
    if not relation_scope_match:
        return _reason("DATABASE_RELATION_DELTA_RELATION_SCOPE_MISMATCH", expected, actual)

    root_before_value = _decimal(root_before_snapshot.get("field_value"))
    root_after_value = _decimal(root_after_snapshot.get("field_value"))
    actual["root_before"] = _decimal_text(root_before_value)
    actual["root_after"] = _decimal_text(root_after_value)
    if root_before_value is None or root_after_value is None:
        return _reason("DATABASE_RELATION_DELTA_ROOT_VALUE_NOT_NUMERIC", expected, actual)

    relation_before_value, before_empty_zero, before_value_reason = _aggregate_decimal(
        relation_before,
        aggregate=aggregate,
    )
    relation_after_value, after_empty_zero, after_value_reason = _aggregate_decimal(
        relation_after,
        aggregate=aggregate,
    )
    actual["relation_before"] = _decimal_text(relation_before_value)
    actual["relation_after"] = _decimal_text(relation_after_value)
    actual["relation_before_empty_sum_normalized_to_zero"] = before_empty_zero
    actual["relation_after_empty_sum_normalized_to_zero"] = after_empty_zero
    if before_value_reason or after_value_reason:
        return _reason(before_value_reason or after_value_reason, expected, actual)
    if relation_before_value is None or relation_after_value is None:
        return _reason("DATABASE_RELATION_DELTA_AGGREGATE_VALUE_NOT_NUMERIC", expected, actual)

    left_coefficient, left_valid = _declared_decimal(
        spec,
        "left_coefficient",
        default=Decimal("1"),
    )
    right_coefficient, right_valid = _declared_decimal(
        spec,
        "right_coefficient",
        default=Decimal("1"),
    )
    tolerance, tolerance_valid = _declared_decimal(
        spec,
        "tolerance",
        default=Decimal("0"),
    )
    if (
        not left_valid
        or left_coefficient is None
        or not right_valid
        or right_coefficient is None
    ):
        return _reason("DATABASE_RELATION_DELTA_COEFFICIENT_INVALID", expected, actual)
    if not tolerance_valid or tolerance is None or tolerance < 0:
        return _reason("DATABASE_RELATION_DELTA_TOLERANCE_INVALID", expected, actual)

    root_delta = root_after_value - root_before_value
    relation_delta = relation_after_value - relation_before_value
    left_delta = relation_delta if aggregate_on_left else root_delta
    right_delta = root_delta if aggregate_on_left else relation_delta
    weighted_left = left_delta * left_coefficient
    weighted_right = right_delta * right_coefficient
    difference = weighted_left - weighted_right
    actual.update(
        {
            "root_delta": _decimal_text(root_delta),
            "relation_delta": _decimal_text(relation_delta),
            "left_delta": _decimal_text(left_delta),
            "right_delta": _decimal_text(right_delta),
            "left_coefficient": _decimal_text(left_coefficient),
            "right_coefficient": _decimal_text(right_coefficient),
            "weighted_left_delta": _decimal_text(weighted_left),
            "weighted_right_delta": _decimal_text(weighted_right),
            "difference": _decimal_text(difference),
            "tolerance": _decimal_text(tolerance),
        }
    )
    try:
        passed = _compare(weighted_left, weighted_right, operator, tolerance)
    except ValueError:
        return _reason(
            "DATABASE_RELATION_DELTA_COMPARISON_OPERATOR_UNSUPPORTED",
            expected,
            actual,
        )
    return {
        "passed": passed,
        "reason_code": "" if passed else "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED",
        "expected": expected,
        "actual": actual,
    }


def install_database_relation_delta_assertion() -> str:
    if ASSERTION_KIND in registered_assertion_kinds():
        return ASSERTION_KIND
    return register_assertion_kind(
        ASSERTION_KIND,
        evaluator=evaluate_database_relation_delta_conservation,
        required_evidence_keys=(
            _ROOT_RECEIPT_KEY,
            _RELATION_RECEIPT_KEY,
        ),
    )


__all__ = [
    "ASSERTION_KIND",
    "ORACLE_SCHEMA",
    "evaluate_database_relation_delta_conservation",
    "install_database_relation_delta_assertion",
]
