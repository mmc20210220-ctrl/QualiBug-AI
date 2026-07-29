from __future__ import annotations

from ai_test_asset_center import enterprise_pilot_runtime
from ai_test_asset_center.enterprise_knowledge_center._job_assets import enrich_job_assets


def test_generic_http_connector_with_job_external_ref_is_promoted_to_job_source(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        enterprise_pilot_runtime,
        "load_connector_registry",
        lambda project_id, root: {
            "connectors": [
                {
                    "connector_id": "conn-job",
                    "kind": "http_api",
                    "display_name": "订单中心 XXL-JOB",
                    "enabled": True,
                    "read_only": True,
                    "endpoint_ref": "http://localhost:8080/xxl-job-admin",
                    "external_ref": "job_platform:xxl_job",
                }
            ]
        },
    )

    asset = {
        "summary": {},
        "coverage_gaps": [],
        "source_inventory": [],
        "enterprise_understanding_model": {
            "operations": [],
            "metrics": {},
        },
    }

    enriched = enrich_job_assets(
        asset,
        project_id="job-connector-test",
        root=tmp_path,
        options={},
    )

    assert enriched["job_platform_sources"] == [
        {
            "connector_id": "conn-job",
            "kind": "xxl_job",
            "display_name": "订单中心 XXL-JOB",
            "endpoint_ref": "http://localhost:8080/xxl-job-admin",
            "external_ref": "job_platform:xxl_job",
            "read_only": True,
            "last_sync_at_utc": "",
            "last_sync_status": "not_synced",
            "status": "CONNECTED_READ_ONLY",
        }
    ]
    assert any(
        row.get("kind") == "JOB_PLATFORM_ADAPTER_NOT_REGISTERED"
        and row.get("connector_kind") == "xxl_job"
        for row in enriched["coverage_gaps"]
    )
    assert enriched["job_asset_summary"]["manual_job_editor_present"] is False
