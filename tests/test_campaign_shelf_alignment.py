from __future__ import annotations

import json
import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

import ai_test_asset_center.__main__ as main_module
from ai_test_asset_center.private_pilot_service import _augment_continuous_discovery_campaign


def test_augment_continuous_discovery_campaign_exposes_current_and_family_counts() -> None:
    payload = {
        "campaign": {"campaign_id": "CMP_ACTIVE", "lineage_campaign_id": "CMP_BASE"},
        "summary": {"campaign_state": "active", "confirmed_slice_count": 1},
        "current_run": {"confirmed_slice_count": 1},
    }
    augmented = _augment_continuous_discovery_campaign(
        payload,
        current_report_breakdown={"total_findings": 2, "category_counts": {"state_machine": 2}},
        delivery_defects=[{"id": "BUG-1"}, {"id": "BUG-2"}],
        current_campaign_customer_ready_defect_count=1,
        current_campaign_bundle_finding_count_raw=3,
    )
    summary = augmented["summary"]
    current_run = augmented["current_run"]

    assert summary["confirmed_slice_count"] == 1
    assert summary["current_campaign_confirmed_slice_count"] == 1
    assert summary["current_campaign_customer_ready_defect_count"] == 1
    assert summary["current_campaign_bundle_finding_count_raw"] == 3
    assert summary["family_customer_ready_defect_count"] == 2
    assert summary["family_report_real_finding_count"] == 2
    assert summary["family_historical_carryover_defect_count"] == 1
    assert summary["confirmed_shelf_alignment_status"] == "family_expands_beyond_current_campaign"
    assert summary["confirmed_shelf_reporting_scope"] == "campaign_confirmed=current_campaign; defect_shelf=family_aggregated"
    assert current_run["current_campaign_confirmed_slice_count"] == 1
    assert current_run["current_campaign_customer_ready_defect_count"] == 1
    assert current_run["current_campaign_bundle_finding_count_raw"] == 3
    assert current_run["family_customer_ready_defect_count"] == 2


def test_augment_continuous_discovery_campaign_falls_back_to_campaign_confirmed_count() -> None:
    payload = {
        "campaign": {
            "campaign_id": "CMP_ACTIVE",
            "lineage_campaign_id": "CMP_BASE",
            "confirmed_slice_count": 5,
        },
        "summary": {"campaign_state": "active"},
        "current_run": {},
    }
    augmented = _augment_continuous_discovery_campaign(
        payload,
        current_report_breakdown={"total_findings": 2, "category_counts": {"state_machine": 2}},
        delivery_defects=[{"id": "BUG-1"}, {"id": "BUG-2"}],
        current_campaign_customer_ready_defect_count=2,
        current_campaign_bundle_finding_count_raw=5,
    )
    summary = augmented["summary"]
    current_run = augmented["current_run"]

    assert summary["current_campaign_confirmed_slice_count"] == 5
    assert summary["current_campaign_customer_ready_defect_count"] == 2
    assert summary["family_customer_ready_defect_count"] == 2
    assert summary["family_historical_carryover_defect_count"] == 0
    assert summary["confirmed_shelf_alignment_status"] == "current_campaign_exceeds_family_shelf"
    assert current_run["current_campaign_confirmed_slice_count"] == 5
    assert current_run["current_campaign_bundle_finding_count_raw"] == 5


def test_persist_customer_ready_static_artifacts_overwrites_campaign_projection_with_enriched_snapshot(tmp_path, monkeypatch) -> None:
    project = "enterprise-project"
    scan_result_path = tmp_path / "platform_outputs" / project / "scan_result.json"
    scan_result_path.parent.mkdir(parents=True, exist_ok=True)
    scan_result_path.write_text(json.dumps({"project": project, "total_findings": 2}, ensure_ascii=False), encoding="utf-8")
    real_project_path = tmp_path / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    real_project_path.parent.mkdir(parents=True, exist_ok=True)
    real_project_path.write_text(
        json.dumps(
            {
                "continuous_discovery_campaign": {
                    "summary": {"confirmed_slice_count": 1},
                    "current_run": {"confirmed_slice_count": 1},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    snapshot = {
        "project": project,
        "generated_at_utc": "2026-07-07T20:20:00Z",
        "defects": [{"id": "BUG-1", "title": "重复支付"}],
        "clues": [],
        "risks": [{"id": "BUG-1", "title": "重复支付"}],
        "value_metrics": {"ready_bug_count": 1},
        "executive_summary": {"ready_bugs": 1},
        "scan_meta": {"ready_bug_count": 1},
        "data_contract": {"display_key": "defects"},
        "continuous_discovery_campaign": {
            "summary": {
                "confirmed_slice_count": 1,
                "current_campaign_confirmed_slice_count": 1,
                "current_campaign_customer_ready_defect_count": 1,
                "current_campaign_bundle_finding_count_raw": 1,
                "family_customer_ready_defect_count": 1,
                "family_report_real_finding_count": 2,
                "family_historical_carryover_defect_count": 0,
                "confirmed_shelf_alignment_status": "aligned",
            },
            "current_run": {
                "confirmed_slice_count": 1,
                "current_campaign_confirmed_slice_count": 1,
                "current_campaign_customer_ready_defect_count": 1,
                "current_campaign_bundle_finding_count_raw": 1,
                "family_customer_ready_defect_count": 1,
            },
        },
    }
    monkeypatch.setattr(main_module, "_customer_ready_static_snapshot", lambda project_id, root: dict(snapshot))

    main_module._persist_customer_ready_static_artifacts(project, tmp_path, {"project": project, "total_findings": 2})
    saved_real_project = json.loads(real_project_path.read_text(encoding="utf-8"))

    assert saved_real_project["continuous_discovery_campaign"]["summary"]["confirmed_slice_count"] == 1
    assert saved_real_project["continuous_discovery_campaign"]["summary"]["family_customer_ready_defect_count"] == 1
    assert saved_real_project["continuous_discovery_campaign"]["summary"]["current_campaign_customer_ready_defect_count"] == 1
    assert saved_real_project["continuous_discovery_campaign"]["summary"]["current_campaign_bundle_finding_count_raw"] == 1
    assert saved_real_project["continuous_discovery_campaign"]["summary"]["family_report_real_finding_count"] == 2
    assert saved_real_project["continuous_discovery_campaign"]["summary"]["family_historical_carryover_defect_count"] == 0
    assert saved_real_project["continuous_discovery_campaign"]["current_run"]["family_customer_ready_defect_count"] == 1
