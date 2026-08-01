import pytest

from ai_test_asset_center.connector_health_projection import (
    ConnectorHealthProjectionError,
    project_connector_health,
)


def _base() -> dict[str, object]:
    return {
        "connector_instance": {
            "connector_instance_id": "website-main",
            "connector_type": "website",
            "status": "ACTIVE",
            "last_successful_sync_at_utc": "2026-08-01T22:00:00Z",
        },
        "connection_profile": {
            "credential_status": "ACTIVE",
            "reauthorization_required": False,
            "credentials_configured": True,
        },
        "auto_sync": {
            "enabled": True,
            "state": "healthy",
            "refresh_interval_seconds": 900,
            "last_attempt_at_utc": "2026-08-02T00:00:00Z",
            "last_success_at_utc": "2026-08-01T22:00:00Z",
            "failure_count": 0,
        },
        "coverage": {
            "status": "PARTIAL_UNSUPPORTED",
            "discovered_count": 10,
            "covered_count": 8,
            "unsupported_count": 2,
            "coverage_ratio": 0.8,
        },
        "latest_sync": {
            "status": "COMPLETE",
            "semantic_refresh_status": "BLOCKED_SYNC_INCOMPLETE",
            "semantic_event_count": 1,
            "semantic_changed_source_count": 1,
            "acl_propagation_status": "RECORDED",
            "materialized_success_count": 1,
            "unchanged_success_count": 7,
            "failure_count": 0,
        },
        "now_utc": "2026-08-02T01:00:00Z",
    }


def test_health_is_receipt_backed_and_exposes_staleness_and_coverage_gap() -> None:
    health = project_connector_health(**_base())

    assert health["status"] == "STALE"
    assert health["freshness"]["status"] == "STALE"
    assert health["freshness"]["age_seconds"] == 10_800
    assert health["metrics"]["coverage_ratio"] == 0.8
    assert health["metrics"]["unchanged_reuse_ratio"] == 0.875
    assert "UNSUPPORTED_RESOURCES" in health["attention_reasons"]
    assert "SYNC_STALE" in health["attention_reasons"]
    assert health["evidence"]["source"] == "connector_sync_receipt"
    assert health["source_content_returned"] is False
    assert health["credentials_returned"] is False


def test_webhook_calibration_is_visible_without_claiming_source_loss() -> None:
    payload = _base()
    payload["webhook"] = {
        "supported": True,
        "enabled": True,
        "status": "CALIBRATION_REQUIRED",
        "state": {
            "calibration_required": True,
            "last_success_event": None,
            "last_failure_event": None,
        },
    }

    health = project_connector_health(**payload)

    assert health["status"] == "CALIBRATION_REQUIRED"
    assert health["recommended_action"] == "RUN_SYNC"
    assert "WEBHOOK_CALIBRATION_REQUIRED" in health["attention_reasons"]
    assert health["webhook"]["calibration_required"] is True
    assert health["customer_material_mutation_executed"] is False


def test_reauthorization_takes_precedence_over_sync_health() -> None:
    payload = _base()
    payload["connection_profile"] = {
        "credential_status": "EXPIRED",
        "reauthorization_required": True,
        "reauthorization_reason": "provider_revoked",
        "credentials_configured": True,
    }

    health = project_connector_health(**payload)

    assert health["status"] == "REAUTHORIZATION_REQUIRED"
    assert health["recommended_action"] == "REAUTHORIZE_CONNECTOR"
    assert "AUTHORIZATION_EXPIRED" in health["attention_reasons"]
    assert health["credential_status"] == "EXPIRED"


def test_permission_insufficient_is_not_hidden_as_a_healthy_sync() -> None:
    payload = _base()
    payload["connection_profile"] = {
        "credential_status": "permission_insufficient",
        "reauthorization_required": False,
        "credentials_configured": True,
    }

    health = project_connector_health(**payload)

    assert health["status"] == "PERMISSION_INSUFFICIENT"
    assert health["recommended_action"] == "REAUTHORIZE_CONNECTOR"
    assert "PERMISSION_INSUFFICIENT" in health["attention_reasons"]


def test_no_successful_sync_stays_explicitly_unmeasured() -> None:
    payload = _base()
    payload["connector_instance"] = {
        "connector_instance_id": "website-main",
        "connector_type": "website",
        "status": "ACTIVE",
    }
    payload["auto_sync"] = {
        "enabled": True,
        "state": "scheduled",
        "refresh_interval_seconds": 900,
        "failure_count": 0,
    }
    payload["coverage"] = {
        "status": "NOT_AVAILABLE",
        "discovered_count": 0,
        "covered_count": 0,
        "unsupported_count": 0,
        "coverage_ratio": 0.0,
    }
    payload["latest_sync"] = {}

    health = project_connector_health(**payload)

    assert health["status"] == "NOT_SYNCED"
    assert health["freshness"]["status"] == "UNKNOWN"
    assert health["evidence"]["measured"] is False
    assert "SYNC_NOT_MEASURED" in health["attention_reasons"]


def test_invalid_receipt_timestamp_fails_fast() -> None:
    payload = _base()
    payload["now_utc"] = "not-a-timestamp"

    with pytest.raises(ConnectorHealthProjectionError, match="connector_health_check_time_invalid"):
        project_connector_health(**payload)
