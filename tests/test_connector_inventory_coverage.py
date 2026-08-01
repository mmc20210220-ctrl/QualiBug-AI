from __future__ import annotations

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
