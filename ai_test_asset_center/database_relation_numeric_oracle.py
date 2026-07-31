"""Exact cross-table numeric assertions over root row and approved FK aggregate receipts."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
from .database_relation_numeric_experiment_projection import ASSERTION_KIND
from .database_relation_observer_runtime import (
    EVIDENCE_KEY as RELATION_EVIDENCE_KEY,
    OBSERVER_ID as DIRECT_RELATION_OBSERVER_ID,
)
from .database_state_transition_oracle import _normalized_phase_row, _snapshot_value

ORACLE_SCHEMA = "qualibug.database-relation-numeric-oracle.v1"
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


def _normalized_relation_key(value: Any) -> list[dict[str, str]]:
    return [
        {
            "child_database_field_name": _text(
                _dict(raw).get("child_database_field_name")
            ),
            "parent_database_field_name": _text(
                _dict(raw).get("parent_database_field_name")
            ),
        }
        for raw in _list(value)
        if isinstance(raw, dict)
        and _text(raw.get("child_database_field_name"))
        and _text(raw.get("parent_database_field_name"))
    ]


def _root_after(
    observations: dict[str, Any],
    *,
    contract_ref: str,
    draft_id: str,
    table_ref: str,
    field_name: str,
) -> tuple[dict[str, Any], str]:
    rows: list[dict[str, Any]] = []
    for raw in _list(observations.get(_ROOT_RECEIPT_KEY)):
        row = _normalized_phase_row(raw)
        if (
            _text(row.get("observer_contract_ref")) == contract_ref
            and _text(row.get("phase")).upper() == "AFTER"
            and _text(row.get("draft_id")) == draft_id
        ):
            rows.append(row)
    if len(rows) != 1:
        return {"candidate_count": len(rows)}, (
            "DATABASE_RELATION_ROOT_SNAPSHOT_MISSING"
            if len(rows) < 1
            else "DATABASE_RELATION_ROOT_SNAPSHOT_AMBIGUOUS"
        )
    return _snapshot_value(
        rows[0],
        field_name=field_name,
        expected_table_ref=table_ref,
    )


def _relation_after(
    observations: dict[str, Any],
    *,
    relation_ref: str,
    draft_id: str,
    alias: str,
) -> tuple[dict[str, Any], str]:
    matches: list[dict[str, Any]] = []
    for raw in _list(observations.get(_RELATION_RECEIPT_KEY)):
        row = _dict(raw)
        if (
            _text(row.get("relation_observer_contract_ref")) == relation_ref
            and _text(row.get("observation_phase")).upper() == "AFTER"
            and _text(row.get("draft_id")) == draft_id
        ):
            matches.append(row)
    if len(matches) != 1:
        return {"candidate_count": len(matches)}, (
            "DATABASE_RELATION_AGGREGATE_SNAPSHOT_MISSING"
            if len(matches) < 1
            else "DATABASE_RELATION_AGGREGATE_SNAPSHOT_AMBIGUOUS"
        )
    receipt = matches[0]
    payload = _dict(
        _dict(receipt.get("evidence")).get(RELATION_EVIDENCE_KEY)
    )
    aggregate_requests = [
        {
            "aggregate": _text(row.get("aggregate")).upper(),
            "database_field_id": _text(row.get("database_field_id")),
            "database_field_name": _text(row.get("database_field_name")),
            "alias": _text(row.get("alias")),
        }
        for row in _list(payload.get("aggregate_requests"))
        if isinstance(row, dict)
    ]
    actual = {
        "draft_id": _text(receipt.get("draft_id")),
        "phase_receipt_id": _text(receipt.get("receipt_id")),
        "phase_receipt_status": _text(receipt.get("status")).upper(),
        "source_observer_id": _text(receipt.get("observer_id")),
        "campaign_id": _text(receipt.get("campaign_id")),
        "execution_id": _text(receipt.get("execution_id")),
        "relation_observer_ref": _text(payload.get("relation_observer_ref")),
        "root_observer_ref": _text(payload.get("root_observer_ref")),
        "database_relationship_id": _text(
            payload.get("database_relationship_id")
        ),
        "parent_table_ref": _text(payload.get("parent_table_ref")),
        "child_table_ref": _text(payload.get("child_table_ref")),
        "child_table_name": _text(payload.get("child_table_name")),
        "relation_key": _normalized_relation_key(payload.get("relation_key")),
        "relation_parameter_fingerprints": _list(
            payload.get("relation_parameter_fingerprints")
        ),
        "aggregate_requests": aggregate_requests,
        "aggregate_alias": alias,
        "aggregate_value": _dict(payload.get("aggregate_values")).get(alias),
        "aggregate_fingerprint": _text(payload.get("aggregate_fingerprint")),
        "client_side_filter_used": (
            payload.get("client_side_filter_used") is True
        ),
        "raw_rows_retained": payload.get("raw_rows_retained") is True,
        "payload_oracle_verdict_emitted": payload.get(
            "oracle_verdict_emitted"
        ),
    }
    if actual["source_observer_id"] != DIRECT_RELATION_OBSERVER_ID:
        return actual, "DATABASE_RELATION_AGGREGATE_SOURCE_INVALID"
    if (
        actual["phase_receipt_status"] != "OBSERVED"
        or not actual["phase_receipt_id"]
    ):
        return actual, "DATABASE_RELATION_AGGREGATE_RECEIPT_NOT_OBSERVED"
    if not actual["campaign_id"] or not actual["execution_id"]:
        return actual, "DATABASE_RELATION_AGGREGATE_LINEAGE_MISSING"
    if (
        receipt.get("oracle_verdict_emitted") is not False
        or payload.get("oracle_verdict_emitted") is not False
    ):
        return actual, "DATABASE_RELATION_OBSERVER_AUTHORITY_INVALID"
    if actual["relation_observer_ref"] != relation_ref:
        return actual, "DATABASE_RELATION_AGGREGATE_SCOPE_MISMATCH"
    if (
        not actual["relation_key"]
        or not actual["relation_parameter_fingerprints"]
    ):
        return actual, "DATABASE_RELATION_IDENTITY_SCOPE_MISSING"
    if actual["client_side_filter_used"] or actual["raw_rows_retained"]:
        return actual, "DATABASE_RELATION_AGGREGATE_EVIDENCE_POLICY_INVALID"
    if alias not in _dict(payload.get("aggregate_values")):
        return actual, "DATABASE_RELATION_AGGREGATE_VALUE_MISSING"
    if not actual["aggregate_fingerprint"]:
        return actual, "DATABASE_RELATION_AGGREGATE_FINGERPRINT_MISSING"
    return actual, ""


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
    raise ValueError("database_relation_comparison_operator_unsupported")


def _aggregate_request_matches(
    request: dict[str, Any],
    *,
    aggregate: str,
    alias: str,
    child_field_id: str,
    child_field_name: str,
) -> bool:
    if (
        _text(request.get("aggregate")).upper() != aggregate
        or _text(request.get("alias")) != alias
    ):
        return False
    request_field_id = _text(request.get("database_field_id"))
    request_field_name = _text(request.get("database_field_name"))
    if aggregate == "COUNT" and not child_field_id and not child_field_name:
        return not request_field_id and not request_field_name
    return (
        request_field_id == child_field_id
        and request_field_name == child_field_name
    )


def evaluate_database_relation_conservation(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    env = _dict(envelope)
    spec = _dict(env.get("spec"))
    observations = _dict(env.get("observations"))
    binding = _dict(spec.get("database_relation_binding"))

    relation_ref = _text(spec.get("database_relation_observer_ref"))
    relation_draft_id = _text(spec.get("database_relation_draft_id"))
    expected_relationship_id = _text(
        spec.get("database_relationship_id")
        or binding.get("database_relationship_id")
    )
    expected_relation_key = _normalized_relation_key(
        spec.get("relation_key") or binding.get("relation_key")
    )
    root_ref = _text(spec.get("root_observer_contract_ref"))
    root_draft_id = _text(
        spec.get("root_database_draft_id")
        or binding.get("root_database_draft_id")
    )
    root_table_ref = _text(spec.get("root_table_ref"))
    root_field_id = _text(spec.get("root_database_field_id"))
    root_field_name = _text(spec.get("root_database_field_name"))
    child_table_ref = _text(spec.get("child_table_ref"))
    child_field_id = _text(spec.get("child_database_field_id"))
    child_field_name = _text(spec.get("child_database_field_name"))
    aggregate = _text(spec.get("aggregate")).upper()
    alias = _text(spec.get("aggregate_alias") or "related_value")
    operator = _text(spec.get("comparison_operator") or "EQ").upper()
    aggregate_on_left = spec.get("aggregate_on_left") is True

    expected = {
        "schema": ORACLE_SCHEMA,
        "database_relation_observer_ref": relation_ref,
        "database_relation_draft_id": relation_draft_id,
        "database_relationship_id": expected_relationship_id,
        "relation_key": expected_relation_key,
        "root_observer_contract_ref": root_ref,
        "root_database_draft_id": root_draft_id,
        "root_table_ref": root_table_ref,
        "root_database_field_id": root_field_id,
        "root_database_field_name": root_field_name,
        "child_table_ref": child_table_ref,
        "child_database_field_id": child_field_id,
        "child_database_field_name": child_field_name,
        "aggregate": aggregate,
        "aggregate_alias": alias,
        "comparison_operator": operator,
        "aggregate_on_left": aggregate_on_left,
        "comparison_phase": "AFTER",
    }
    actual: dict[str, Any] = {
        "schema": ORACLE_SCHEMA,
        "root_snapshot": {},
        "relation_snapshot": {},
        "lineage_match": False,
        "identity_match": False,
        "relation_key_match": False,
        "aggregate_request_match": False,
        "root_value": None,
        "aggregate_value": None,
        "left_value": None,
        "right_value": None,
        "difference": None,
        "observer_performed_oracle_verdict": False,
    }
    if not all(
        (
            relation_ref,
            relation_draft_id,
            expected_relationship_id,
            expected_relation_key,
            root_ref,
            root_draft_id,
            root_table_ref,
            root_field_id,
            root_field_name,
            child_table_ref,
            aggregate,
            alias,
        )
    ):
        return _reason(
            "DATABASE_RELATION_ASSERTION_SPEC_INCOMPLETE",
            expected,
            actual,
        )
    child_field_pair_complete = bool(child_field_id) == bool(child_field_name)
    if not child_field_pair_complete or (
        aggregate != "COUNT" and not child_field_id
    ):
        return _reason(
            "DATABASE_RELATION_ASSERTION_SPEC_INCOMPLETE",
            expected,
            actual,
        )

    root_snapshot, root_reason = _root_after(
        observations,
        contract_ref=root_ref,
        draft_id=root_draft_id,
        table_ref=root_table_ref,
        field_name=root_field_name,
    )
    relation_snapshot, relation_reason = _relation_after(
        observations,
        relation_ref=relation_ref,
        draft_id=relation_draft_id,
        alias=alias,
    )
    actual["root_snapshot"] = root_snapshot
    actual["relation_snapshot"] = relation_snapshot
    if root_reason or relation_reason:
        return _reason(root_reason or relation_reason, expected, actual)
    if _text(relation_snapshot.get("root_observer_ref")) != root_ref:
        return _reason(
            "DATABASE_RELATION_ROOT_OBSERVER_SCOPE_MISMATCH",
            expected,
            actual,
        )
    if (
        _text(relation_snapshot.get("parent_table_ref")) != root_table_ref
        or _text(relation_snapshot.get("child_table_ref"))
        != child_table_ref
    ):
        return _reason(
            "DATABASE_RELATION_TABLE_SCOPE_MISMATCH",
            expected,
            actual,
        )
    if (
        _text(relation_snapshot.get("database_relationship_id"))
        != expected_relationship_id
    ):
        return _reason(
            "DATABASE_RELATION_FOREIGN_KEY_SCOPE_MISMATCH",
            expected,
            actual,
        )

    observed_relation_key = _normalized_relation_key(
        relation_snapshot.get("relation_key")
    )
    root_identity_key = [
        _text(value)
        for value in _list(root_snapshot.get("identity_key"))
        if _text(value)
    ]
    observed_parent_key = [
        _text(row.get("parent_database_field_name"))
        for row in observed_relation_key
    ]
    relation_key_match = bool(
        observed_relation_key == expected_relation_key
        and observed_parent_key == root_identity_key
    )
    actual["relation_key_match"] = relation_key_match
    if not relation_key_match:
        return _reason(
            "DATABASE_RELATION_KEY_SCOPE_MISMATCH",
            expected,
            actual,
        )

    requests = [
        _dict(row)
        for row in _list(relation_snapshot.get("aggregate_requests"))
        if isinstance(row, dict)
    ]
    request_matches = [
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
    actual["aggregate_request_match"] = (
        len(request_matches) == 1 and len(requests) == 1
    )
    if not actual["aggregate_request_match"]:
        return _reason(
            "DATABASE_RELATION_AGGREGATE_REQUEST_SCOPE_MISMATCH",
            expected,
            actual,
        )

    lineage_match = bool(
        _text(root_snapshot.get("campaign_id"))
        == _text(relation_snapshot.get("campaign_id"))
        and _text(root_snapshot.get("execution_id"))
        == _text(relation_snapshot.get("execution_id"))
    )
    actual["lineage_match"] = lineage_match
    if not lineage_match:
        return _reason(
            "DATABASE_RELATION_RECEIPT_LINEAGE_MISMATCH",
            expected,
            actual,
        )
    root_identity = _list(
        root_snapshot.get("identity_parameter_fingerprints")
    )
    relation_identity = _list(
        relation_snapshot.get("relation_parameter_fingerprints")
    )
    identity_match = bool(
        root_identity and root_identity == relation_identity
    )
    actual["identity_match"] = identity_match
    if not identity_match:
        return _reason(
            "DATABASE_RELATION_ROOT_IDENTITY_MISMATCH",
            expected,
            actual,
        )

    root_value = _decimal(root_snapshot.get("field_value"))
    aggregate_value = _decimal(relation_snapshot.get("aggregate_value"))
    actual["root_value"] = _decimal_text(root_value)
    actual["aggregate_value"] = _decimal_text(aggregate_value)
    if root_value is None or aggregate_value is None:
        return _reason(
            "DATABASE_RELATION_NUMERIC_VALUE_INVALID",
            expected,
            actual,
        )
    tolerance_raw = spec.get("tolerance")
    tolerance = (
        Decimal("0")
        if tolerance_raw in (None, "")
        else _decimal(tolerance_raw)
    )
    if tolerance is None or tolerance < 0:
        return _reason(
            "DATABASE_RELATION_TOLERANCE_INVALID",
            expected,
            actual,
        )

    left = aggregate_value if aggregate_on_left else root_value
    right = root_value if aggregate_on_left else aggregate_value
    actual["left_value"] = _decimal_text(left)
    actual["right_value"] = _decimal_text(right)
    actual["difference"] = _decimal_text(left - right)
    actual["tolerance"] = _decimal_text(tolerance)
    try:
        passed = _compare(left, right, operator, tolerance)
    except ValueError:
        return _reason(
            "DATABASE_RELATION_COMPARISON_OPERATOR_UNSUPPORTED",
            expected,
            actual,
        )
    return {
        "passed": passed,
        "reason_code": (
            "" if passed else "DATABASE_RELATION_CONSERVATION_VIOLATED"
        ),
        "expected": expected,
        "actual": actual,
    }


def install_database_relation_numeric_assertion() -> str:
    if ASSERTION_KIND in registered_assertion_kinds():
        return ASSERTION_KIND
    return register_assertion_kind(
        ASSERTION_KIND,
        evaluator=evaluate_database_relation_conservation,
        required_evidence_keys=(
            _ROOT_RECEIPT_KEY,
            _RELATION_RECEIPT_KEY,
        ),
    )


__all__ = [
    "ASSERTION_KIND",
    "ORACLE_SCHEMA",
    "evaluate_database_relation_conservation",
    "install_database_relation_numeric_assertion",
]
