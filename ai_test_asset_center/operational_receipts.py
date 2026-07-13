"""Receipt-backed operational accounting for discovery execution attempts.

Only explicit HTTP-step and governed-write receipts are counted.  This module
never walks arbitrary result JSON looking for status-like fields.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


EXECUTION_OPERATIONAL_RECEIPT_SCHEMA = (
    "qualibug.execution-operational-receipt.v1"
)
EXECUTION_OPERATIONAL_SUMMARY_SCHEMA = (
    "qualibug.execution-operational-summary.v1"
)
_EXECUTION_STATUSES = frozenset({
    "EXECUTED",
    "BLOCKED",
    "DEFERRED",
    "HARNESS_FAILURE",
    "HARNESS_FAILED",
})
_CLEANUP_PHASES = frozenset({"cleanup", "fixture_cleanup"})
_CLEANUP_STATUSES = frozenset({"COMPLETED", "FAILED", "NOT_REQUIRED"})


class OperationalReceiptError(ValueError):
    """An execution receipt is missing or internally contradictory."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise OperationalReceiptError(f"{field}_invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise OperationalReceiptError(f"{field}_invalid") from exc
    if parsed < 0:
        raise OperationalReceiptError(f"{field}_invalid")
    return parsed


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_execution_operational_receipt(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    value = dict(_dict(receipt))
    if value.get("schema_version") != EXECUTION_OPERATIONAL_RECEIPT_SCHEMA:
        raise OperationalReceiptError("operational_receipt_schema_invalid")
    if not _text(value.get("receipt_id")):
        raise OperationalReceiptError("operational_receipt_id_missing")
    execution_status = _text(value.get("execution_status")).upper()
    if execution_status not in _EXECUTION_STATUSES:
        raise OperationalReceiptError("operational_execution_status_invalid")
    for field in (
        "scenario_attempt_count",
        "http_request_attempt_count",
        "production_http_request_count",
        "accepted_write_count",
        "accepted_non_cleanup_write_count",
        "accepted_cleanup_write_count",
    ):
        value[field] = _non_negative_int(value.get(field), field)
    if value["scenario_attempt_count"] != 1:
        raise OperationalReceiptError("scenario_attempt_count_must_equal_one")
    if value["production_http_request_count"] > value["http_request_attempt_count"]:
        raise OperationalReceiptError("production_http_request_count_exceeds_attempts")
    if (
        value["accepted_non_cleanup_write_count"]
        + value["accepted_cleanup_write_count"]
        != value["accepted_write_count"]
    ):
        raise OperationalReceiptError("accepted_write_count_mismatch")

    cleanup = dict(_dict(value.get("cleanup_outcome")))
    cleanup_status = _text(cleanup.get("status")).upper()
    if cleanup_status not in _CLEANUP_STATUSES:
        raise OperationalReceiptError("cleanup_outcome_status_invalid")
    for field in ("attempted_count", "completed_count", "failure_count"):
        cleanup[field] = _non_negative_int(cleanup.get(field), f"cleanup_{field}")
    if cleanup["completed_count"] > cleanup["attempted_count"]:
        raise OperationalReceiptError("cleanup_completed_count_exceeds_attempts")
    if cleanup_status == "FAILED" and cleanup["failure_count"] == 0:
        raise OperationalReceiptError("cleanup_failed_without_failure_count")
    if cleanup_status != "FAILED" and cleanup["failure_count"]:
        raise OperationalReceiptError("cleanup_failure_count_status_mismatch")
    value["cleanup_outcome"] = cleanup

    claimed = _text(value.get("receipt_fingerprint"))
    if not claimed:
        raise OperationalReceiptError("operational_receipt_fingerprint_missing")
    unsigned = {
        key: item
        for key, item in value.items()
        if key != "receipt_fingerprint"
    }
    if claimed != _fingerprint(unsigned):
        raise OperationalReceiptError("operational_receipt_fingerprint_mismatch")
    return value


def build_execution_operational_receipt(
    *,
    receipt_id: str,
    execution_status: str,
    steps: list[dict[str, Any]],
    cleanup_failures: int,
) -> dict[str, Any]:
    """Build one terminal operational receipt from explicit execution steps."""

    normalized_status = _text(execution_status).upper()
    if normalized_status not in _EXECUTION_STATUSES:
        raise OperationalReceiptError("operational_execution_status_invalid")
    http_attempts = 0
    production_requests = 0
    accepted_non_cleanup = 0
    accepted_cleanup = 0
    cleanup_attempted = 0
    cleanup_completed = 0

    for raw_step in _list(steps):
        step = _dict(raw_step)
        governance = _dict(step.get("governance_receipt"))
        phase = _text(step.get("phase")).lower()
        if governance:
            attempts = _non_negative_int(
                governance.get("http_attempt_count"),
                "governance_http_attempt_count",
            )
            production = _non_negative_int(
                governance.get("production_http_requests"),
                "governance_production_http_requests",
            )
            if production > attempts:
                raise OperationalReceiptError(
                    "governance_production_http_requests_exceed_attempts"
                )
            http_attempts += attempts
            production_requests += production
            accepted = governance.get("accepted") is True
            if phase in _CLEANUP_PHASES:
                if attempts:
                    cleanup_attempted += 1
                if accepted:
                    accepted_cleanup += 1
                    cleanup_completed += 1
            elif accepted:
                accepted_non_cleanup += 1
            continue

        # A direct HTTP adapter step is one request attempt.  A blocked or
        # unresolved plan row has no status_code field and therefore no request.
        if (
            _text(step.get("method"))
            and _text(step.get("path"))
            and "status_code" in step
        ):
            http_attempts += 1

    failure_count = _non_negative_int(cleanup_failures, "cleanup_failures")
    if failure_count:
        cleanup_status = "FAILED"
    elif accepted_non_cleanup and cleanup_attempted:
        cleanup_status = "COMPLETED"
    else:
        cleanup_status = "NOT_REQUIRED"
    return build_execution_operational_receipt_from_counts(
        receipt_id=receipt_id,
        execution_status=normalized_status,
        scenario_attempt_count=1,
        http_request_attempt_count=http_attempts,
        production_http_request_count=production_requests,
        accepted_non_cleanup_write_count=accepted_non_cleanup,
        accepted_cleanup_write_count=accepted_cleanup,
        cleanup_status=cleanup_status,
        cleanup_attempted_count=cleanup_attempted,
        cleanup_completed_count=cleanup_completed,
        cleanup_failure_count=failure_count,
    )


def build_execution_operational_receipt_from_counts(
    *,
    receipt_id: str,
    execution_status: str,
    scenario_attempt_count: int,
    http_request_attempt_count: int,
    production_http_request_count: int,
    accepted_non_cleanup_write_count: int,
    accepted_cleanup_write_count: int,
    cleanup_status: str,
    cleanup_attempted_count: int,
    cleanup_completed_count: int,
    cleanup_failure_count: int,
) -> dict[str, Any]:
    """Seal explicit adapter counters without scanning arbitrary result JSON."""

    accepted_non_cleanup = _non_negative_int(
        accepted_non_cleanup_write_count,
        "accepted_non_cleanup_write_count",
    )
    accepted_cleanup = _non_negative_int(
        accepted_cleanup_write_count,
        "accepted_cleanup_write_count",
    )
    payload: dict[str, Any] = {
        "schema_version": EXECUTION_OPERATIONAL_RECEIPT_SCHEMA,
        "receipt_id": _text(receipt_id),
        "execution_status": _text(execution_status).upper(),
        "scenario_attempt_count": _non_negative_int(
            scenario_attempt_count,
            "scenario_attempt_count",
        ),
        "http_request_attempt_count": _non_negative_int(
            http_request_attempt_count,
            "http_request_attempt_count",
        ),
        "production_http_request_count": _non_negative_int(
            production_http_request_count,
            "production_http_request_count",
        ),
        "accepted_write_count": accepted_non_cleanup + accepted_cleanup,
        "accepted_non_cleanup_write_count": accepted_non_cleanup,
        "accepted_cleanup_write_count": accepted_cleanup,
        "cleanup_outcome": {
            "status": _text(cleanup_status).upper(),
            "attempted_count": _non_negative_int(
                cleanup_attempted_count,
                "cleanup_attempted_count",
            ),
            "completed_count": _non_negative_int(
                cleanup_completed_count,
                "cleanup_completed_count",
            ),
            "failure_count": _non_negative_int(
                cleanup_failure_count,
                "cleanup_failure_count",
            ),
        },
    }
    if not payload["receipt_id"]:
        raise OperationalReceiptError("operational_receipt_id_missing")
    payload["receipt_fingerprint"] = _fingerprint(payload)
    return validate_execution_operational_receipt(payload)


def aggregate_execution_operational_receipts(
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate validated terminal receipts without scanning unrelated JSON."""

    rows = [validate_execution_operational_receipt(row) for row in _list(receipts)]
    receipt_ids = [_text(row.get("receipt_id")) for row in rows]
    if len(receipt_ids) != len(set(receipt_ids)):
        raise OperationalReceiptError("operational_receipt_id_duplicate")
    scenario_attempts = sum(row["scenario_attempt_count"] for row in rows)
    executed = sum(
        row["scenario_attempt_count"]
        for row in rows
        if row["execution_status"] == "EXECUTED"
    )
    cleanup_failures = sum(
        _dict(row.get("cleanup_outcome")).get("failure_count", 0)
        for row in rows
    )
    return {
        "schema_version": EXECUTION_OPERATIONAL_SUMMARY_SCHEMA,
        "receipt_count": len(rows),
        "receipt_ids": receipt_ids,
        "scenario_attempts": scenario_attempts,
        "executed_scenarios": executed,
        "observed_http_request_count": sum(
            row["http_request_attempt_count"] for row in rows
        ),
        "production_http_requests": sum(
            row["production_http_request_count"] for row in rows
        ),
        "accepted_write_count": sum(row["accepted_write_count"] for row in rows),
        "accepted_non_cleanup_write_count": sum(
            row["accepted_non_cleanup_write_count"] for row in rows
        ),
        "accepted_cleanup_write_count": sum(
            row["accepted_cleanup_write_count"] for row in rows
        ),
        "cleanup_failures": cleanup_failures,
        "execution_success_rate": (
            round(executed / scenario_attempts, 6) if scenario_attempts else 0.0
        ),
    }
