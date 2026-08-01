"""Receipt-backed connector health and freshness projection.

This module derives operator-facing state from the existing connector instance, masked profile,
auto-sync attempt, coverage projection, and latest sync receipt.  It does not perform network
access, mutate connector state, or infer remote deletion, permission loss, or business meaning.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping

CONNECTOR_HEALTH_SCHEMA = "qualibug.connector-health-projection.v1"
_FRESHNESS_STALE_MULTIPLIER = 2
_BAD_SEMANTIC_STATUSES = {
    "BLOCKED_SYNC_INCOMPLETE",
    "FAILED",
    "PARTIAL",
    "PENDING_VALIDATION",
}


class ConnectorHealthProjectionError(RuntimeError):
    """Connector health evidence could not be projected safely."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _integer(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ConnectorHealthProjectionError("health_integer_invalid")
    if isinstance(value, float) and not value.is_integer():
        raise ConnectorHealthProjectionError("health_integer_invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ConnectorHealthProjectionError("health_integer_invalid") from None
    if parsed < 0:
        raise ConnectorHealthProjectionError("health_integer_negative")
    return parsed


def _number(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ConnectorHealthProjectionError("health_number_invalid")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ConnectorHealthProjectionError("health_number_invalid") from None
    if not math.isfinite(parsed):
        raise ConnectorHealthProjectionError("health_number_invalid")
    return parsed


def _utc(value: Any, field: str) -> datetime:
    raw = _text(value, 80)
    if not raw:
        raise ConnectorHealthProjectionError(f"{field}_missing")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConnectorHealthProjectionError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise ConnectorHealthProjectionError(f"{field}_timezone_required")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _coverage_ratio(coverage: Mapping[str, Any]) -> float | None:
    explicit = _number(coverage.get("coverage_ratio"))
    if explicit is not None:
        if not 0.0 <= explicit <= 1.0:
            raise ConnectorHealthProjectionError("coverage_ratio_out_of_range")
        return explicit
    discovered = _integer(coverage.get("discovered_count"))
    covered = _integer(coverage.get("covered_count"))
    if discovered <= 0:
        return None
    if covered > discovered:
        raise ConnectorHealthProjectionError("coverage_counts_inconsistent")
    return covered / discovered


def _freshness(
    *,
    last_successful: datetime | None,
    checked_at: datetime,
    refresh_interval_seconds: int,
) -> dict[str, Any]:
    if last_successful is None or refresh_interval_seconds <= 0:
        return {
            "status": "UNKNOWN",
            "last_successful_sync_at_utc": (
                _utc_text(last_successful) if last_successful else ""
            ),
            "age_seconds": None,
            "refresh_interval_seconds": (
                refresh_interval_seconds if refresh_interval_seconds > 0 else None
            ),
            "stale_after_seconds": None,
        }
    age_seconds = max(0, int((checked_at - last_successful).total_seconds()))
    stale_after = refresh_interval_seconds * _FRESHNESS_STALE_MULTIPLIER
    if age_seconds <= refresh_interval_seconds:
        status = "FRESH"
    elif age_seconds <= stale_after:
        status = "DUE"
    else:
        status = "STALE"
    return {
        "status": status,
        "last_successful_sync_at_utc": _utc_text(last_successful),
        "age_seconds": age_seconds,
        "refresh_interval_seconds": refresh_interval_seconds,
        "stale_after_seconds": stale_after,
    }


def project_connector_health(
    *,
    connector_instance: Mapping[str, Any],
    connection_profile: Mapping[str, Any] | None = None,
    auto_sync: Mapping[str, Any] | None = None,
    coverage: Mapping[str, Any] | None = None,
    latest_sync: Mapping[str, Any] | None = None,
    now_utc: Any = None,
) -> dict[str, Any]:
    """Project bounded health state from already persisted connector evidence."""
    instance = _mapping(connector_instance)
    profile = _mapping(connection_profile)
    auto = _mapping(auto_sync)
    coverage_row = _mapping(coverage)
    sync = _mapping(latest_sync)
    checked_at = _utc(now_utc, "connector_health_check_time") if now_utc else datetime.now(timezone.utc)

    credential_status = _text(profile.get("credential_status"), 64).upper() or "UNKNOWN"
    reauthorization_required = profile.get("reauthorization_required") is True
    last_successful_raw = _text(
        instance.get("last_successful_sync_at_utc") or auto.get("last_success_at_utc"),
        80,
    )
    last_successful = _utc(last_successful_raw, "last_successful_sync_at_utc") if last_successful_raw else None
    refresh_interval = _integer(auto.get("refresh_interval_seconds"))
    freshness = _freshness(
        last_successful=last_successful,
        checked_at=checked_at,
        refresh_interval_seconds=refresh_interval,
    )

    discovered = _integer(coverage_row.get("discovered_count"))
    covered = _integer(coverage_row.get("covered_count"))
    unsupported = _integer(coverage_row.get("unsupported_count"))
    materialized = _integer(sync.get("materialized_success_count"))
    unchanged = _integer(sync.get("unchanged_success_count"))
    materialized_total = materialized + unchanged
    reuse_ratio = unchanged / materialized_total if materialized_total else None
    coverage_ratio = _coverage_ratio(coverage_row)
    semantic_status = _text(sync.get("semantic_refresh_status"), 80) or "NOT_RECORDED"
    auto_state = _text(auto.get("state"), 32) or "UNKNOWN"
    instance_status = _text(instance.get("status"), 32).upper() or "UNKNOWN"
    latest_sync_status = _text(sync.get("status"), 40).upper()

    attention: list[str] = []
    if reauthorization_required or credential_status in {
        "EXPIRED",
        "REVOKED",
        "REAUTHORIZATION_REQUIRED",
    }:
        attention.append("AUTHORIZATION_EXPIRED")
    elif credential_status == "PERMISSION_INSUFFICIENT":
        attention.append("PERMISSION_INSUFFICIENT")
    elif credential_status == "EXPIRING":
        attention.append("AUTHORIZATION_EXPIRING")
    if auto_state == "retrying":
        attention.append("SYNC_RETRYING")
    if freshness["status"] == "STALE":
        attention.append("SYNC_STALE")
    elif freshness["status"] == "DUE":
        attention.append("SYNC_DUE")
    if not last_successful:
        attention.append("SYNC_NOT_MEASURED")
    if _text(coverage_row.get("status"), 60) == "PARTIAL_UNSUPPORTED" or unsupported > 0:
        attention.append("UNSUPPORTED_RESOURCES")
    if semantic_status in _BAD_SEMANTIC_STATUSES:
        attention.append("DOWNSTREAM_REFRESH_INCOMPLETE")
    if _text(coverage_row.get("status"), 60) in {"UNKNOWN", "NOT_AVAILABLE"}:
        attention.append("COVERAGE_NOT_MEASURED")

    if attention and attention[0] == "AUTHORIZATION_EXPIRED":
        status = "REAUTHORIZATION_REQUIRED"
        recommended_action = "REAUTHORIZE_CONNECTOR"
    elif attention and attention[0] == "PERMISSION_INSUFFICIENT":
        status = "PERMISSION_INSUFFICIENT"
        recommended_action = "REAUTHORIZE_CONNECTOR"
    elif attention and attention[0] == "AUTHORIZATION_EXPIRING":
        status = "AUTHORIZATION_EXPIRING"
        recommended_action = "REAUTHORIZE_CONNECTOR"
    elif instance_status == "DISABLED":
        status = "DISABLED"
        recommended_action = "ENABLE_CONNECTOR"
    elif instance_status == "PAUSED":
        status = "PAUSED"
        recommended_action = "RESUME_CONNECTOR"
    elif instance.get("active_sync_epoch_id") or auto_state == "running":
        status = "SYNCING"
        recommended_action = "WAIT_FOR_SYNC"
    elif freshness["status"] == "STALE":
        status = "STALE"
        recommended_action = "RUN_SYNC"
    elif not last_successful:
        status = "NOT_SYNCED"
        recommended_action = "RUN_CONNECTION_TEST"
    elif semantic_status in _BAD_SEMANTIC_STATUSES:
        status = "DOWNSTREAM_DEGRADED"
        recommended_action = "REVIEW_SEMANTIC_REFRESH"
    elif freshness["status"] == "DUE":
        status = "DUE"
        recommended_action = "RUN_SYNC"
    elif _text(coverage_row.get("status"), 60) == "PARTIAL_UNSUPPORTED" or unsupported > 0:
        status = "PARTIAL_COVERAGE"
        recommended_action = "REVIEW_UNSUPPORTED_RESOURCES"
    elif auto_state == "retrying":
        status = "RETRYING"
        recommended_action = "WAIT_FOR_AUTOMATIC_RETRY"
    elif latest_sync_status and latest_sync_status != "COMPLETE":
        status = "DEGRADED"
        recommended_action = "REVIEW_LAST_SYNC"
    else:
        status = "HEALTHY"
        recommended_action = "NONE"

    return {
        "schema": CONNECTOR_HEALTH_SCHEMA,
        "connector_instance_id": _text(instance.get("connector_instance_id"), 160),
        "connector_type": _text(instance.get("connector_type"), 160),
        "status": status,
        "recommended_action": recommended_action,
        "attention_reasons": list(dict.fromkeys(attention)),
        "credential_status": credential_status,
        "reauthorization_required": reauthorization_required,
        "reauthorization_reason": _text(profile.get("reauthorization_reason"), 300),
        "freshness": freshness,
        "metrics": {
            "last_attempt_at_utc": _text(auto.get("last_attempt_at_utc"), 80),
            "discovered_resource_count": discovered,
            "covered_resource_count": covered,
            "unsupported_resource_count": unsupported,
            "coverage_ratio": coverage_ratio,
            "failure_count": _integer(sync.get("failure_count"), _integer(auto.get("failure_count"))),
            "retry_count": _integer(
                auto.get("retry_count"),
                _integer(auto.get("failure_count")),
            ),
            "materialized_resource_count": materialized,
            "unchanged_resource_count": unchanged,
            "unchanged_reuse_ratio": reuse_ratio,
            "semantic_refresh_status": semantic_status,
            "semantic_event_count": _integer(sync.get("semantic_event_count")),
            "semantic_changed_source_count": _integer(sync.get("semantic_changed_source_count")),
            "acl_propagation_status": _text(sync.get("acl_propagation_status"), 80) or "NOT_RECORDED",
        },
        "evidence": {
            "source": "connector_sync_receipt",
            "measured": bool(last_successful and sync),
            "coverage_receipt_present": bool(coverage_row),
            "latest_sync_receipt_present": bool(sync),
            "checked_at_utc": _utc_text(checked_at),
        },
        "source_content_returned": False,
        "credentials_returned": False,
        "raw_cursor_returned": False,
        "customer_material_mutation_executed": False,
    }


__all__ = [
    "CONNECTOR_HEALTH_SCHEMA",
    "ConnectorHealthProjectionError",
    "project_connector_health",
]
