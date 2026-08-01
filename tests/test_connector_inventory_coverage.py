from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ai_test_asset_center import private_pilot_connector_handlers as handlers


def test_coverage_projection_is_rebuilt_from_persisted_sync_receipt(monkeypatch) -> None:
    run = {
        "status": "COMPLETE",
        "completed_at_utc": "2026-08-01T10:00:00Z",
        "materialized_item_count": 7,
        "unchanged_item_count": 2,
        "coverage_observation_count": 1,
        "knowledge_coverage_status": "PARTIAL_UNSUPPORTED",
        "coverage_observations": [
            {
                "remote_resource_id": "wiki:space1:mind-node",
                "resource_kind": "feishu-wiki-mindnote",
                "remote_object_type": "mindnote",
                "display_title": "订单流程脑图",
                "reason_code": "FEISHU_OBJECT_TYPE_UNSUPPORTED",
                "retry_trigger": "ADAPTER_CAPABILITY_CHANGE",
                "content_materialized": False,
                "source_occurrence_created": False,
                "customer_source_modified": False,
            }
        ],
    }

    monkeypatch.setattr(
        handlers,
        "load_connector_sync_run",
        lambda *args, **kwargs: dict(run),
    )

    coverage = handlers._coverage_projection(
        "enterprise-project",
        "feishu-prod",
        {"last_successful_sync_epoch_id": "sync-1"},
        Path("/unused"),
    )

    assert coverage["status"] == "PARTIAL_UNSUPPORTED"
    assert coverage["complete"] is False
    assert coverage["discovered_count"] == 10
    assert coverage["covered_count"] == 9
    assert coverage["unsupported_count"] == 1
    assert coverage["coverage_ratio"] == 0.9
    assert coverage["source_content_returned"] is False
    assert coverage["customer_material_mutation_executed"] is False
    unsupported = coverage["unsupported_resources"][0]
    assert unsupported["display_title"] == "订单流程脑图"
    assert unsupported["content_materialized"] is False
    assert unsupported["source_occurrence_created"] is False
    assert unsupported["customer_source_modified"] is False


def test_coverage_projection_without_completed_sync_is_explicit() -> None:
    coverage = handlers._coverage_projection(
        "enterprise-project",
        "feishu-prod",
        {},
        Path("/unused"),
    )

    assert coverage == {
        "status": "NOT_AVAILABLE",
        "complete": False,
        "discovered_count": 0,
        "covered_count": 0,
        "unsupported_count": 0,
        "coverage_ratio": 0.0,
        "unsupported_resources": [],
        "source_content_returned": False,
        "customer_material_mutation_executed": False,
    }


def test_connector_inventory_exposes_receipt_backed_health_without_secrets(
    monkeypatch,
) -> None:
    checked_at = datetime.now(timezone.utc).replace(microsecond=0)
    checked_at_text = checked_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = {
        "connector_instance_id": "website-docs",
        "connector_type": "website",
        "status": "ACTIVE",
        "last_successful_sync_at_utc": checked_at_text,
    }
    coverage = {
        "status": "COMPLETE",
        "discovered_count": 3,
        "covered_count": 3,
        "unsupported_count": 0,
        "coverage_ratio": 1.0,
        "latest_sync": {
            "status": "COMPLETE",
            "materialized_success_count": 2,
            "unchanged_success_count": 1,
            "failure_count": 0,
            "semantic_refresh_status": "NO_CHANGE",
            "acl_propagation_status": "COMPLETE",
        },
    }
    monkeypatch.setattr(
        handlers,
        "list_connector_instances",
        lambda *args, **kwargs: {"connector_instances": [raw], "summary": {}},
    )
    monkeypatch.setattr(
        handlers,
        "_profile_index",
        lambda *args, **kwargs: {
            "website-docs": {
                "connector_instance_id": "website-docs",
                "credential_status": "ACTIVE",
                "reauthorization_required": False,
                "credentials_configured": True,
                "plaintext_returned": False,
            }
        },
    )
    monkeypatch.setattr(
        handlers,
        "connector_auto_sync_status",
        lambda *args, **kwargs: {
            "enabled": True,
            "state": "healthy",
            "last_success_at_utc": checked_at_text,
            "refresh_interval_seconds": 3600,
            "failure_count": 0,
        },
    )
    monkeypatch.setattr(handlers, "_coverage_projection", lambda *args, **kwargs: coverage)
    monkeypatch.setattr(
        handlers,
        "latest_connector_tenant_acceptance_summary",
        lambda *args, **kwargs: {"status": "NOT_RUN"},
    )

    inventory = handlers._connector_inventory("enterprise-project", Path("/unused"))

    row = inventory["connectors"][0]
    health = row["health"]
    assert health["status"] == "HEALTHY"
    assert health["evidence"]["source"] == "connector_sync_receipt"
    assert health["metrics"]["unchanged_reuse_ratio"] == 1 / 3
    assert health["source_content_returned"] is False
    assert health["credentials_returned"] is False
    assert health["raw_cursor_returned"] is False
    assert health["customer_material_mutation_executed"] is False
    assert inventory["summary"]["health_attention_connector_count"] == 0
    assert inventory["governance"]["health_projection_uses_persisted_sync_receipt"] is True
    assert inventory["governance"]["health_projection_returns_credentials"] is False


def test_connector_inventory_uses_canonical_expiry_projection_for_health(
    monkeypatch,
) -> None:
    raw = {
        "connector_instance_id": "website-docs",
        "connector_type": "website",
        "status": "ACTIVE",
    }
    monkeypatch.setattr(
        handlers,
        "list_connector_instances",
        lambda *args, **kwargs: {"connector_instances": [raw], "summary": {}},
    )
    monkeypatch.setattr(
        handlers,
        "_profile_index",
        lambda *args, **kwargs: {
            "website-docs": {
                "connector_instance_id": "website-docs",
                "profile_ref": "vault-ref://connectors/website-docs",
                "credential_status": "ACTIVE",
                "reauthorization_required": False,
                "credentials_configured": True,
            }
        },
    )
    monkeypatch.setattr(
        handlers,
        "connector_credential_expiry_status",
        lambda *args, **kwargs: {
            "status": "EXPIRING",
            "credential_expires_at_utc": "2026-08-02T12:00:00Z",
            "reauthorization_required": False,
            "reauthorization_reason": "",
        },
    )
    monkeypatch.setattr(
        handlers,
        "connector_auto_sync_status",
        lambda *args, **kwargs: {
            "enabled": True,
            "state": "scheduled",
            "refresh_interval_seconds": 3600,
            "failure_count": 0,
        },
    )
    monkeypatch.setattr(
        handlers,
        "_coverage_projection",
        lambda *args, **kwargs: {
            "status": "NOT_AVAILABLE",
            "discovered_count": 0,
            "covered_count": 0,
            "unsupported_count": 0,
            "coverage_ratio": 0.0,
        },
    )
    monkeypatch.setattr(
        handlers,
        "latest_connector_tenant_acceptance_summary",
        lambda *args, **kwargs: {"status": "NOT_RUN"},
    )

    inventory = handlers._connector_inventory("enterprise-project", Path("/unused"))

    assert inventory["connectors"][0]["health"]["status"] == "AUTHORIZATION_EXPIRING"
