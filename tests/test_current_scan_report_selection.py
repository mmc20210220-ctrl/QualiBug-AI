from __future__ import annotations

import json

from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


def test_load_current_scan_report_prefers_newer_intelligence_report_over_stale_scan_result(tmp_path) -> None:
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    project = "demo_project"
    output_dir = tmp_path / "platform_outputs" / project
    output_dir.mkdir(parents=True, exist_ok=True)
    stale_scan_path = output_dir / "scan_result.json"
    fresh_report_path = output_dir / "intelligence_report.json"

    stale_scan_path.write_text(
        json.dumps(
            {
                "scan_id": "scan_stale",
                "total_findings": 0,
                "real_findings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fresh_report_path.write_text(
        json.dumps(
            {
                "scan_id": "scan_fresh",
                "generated_at_utc": "2026-07-08T01:35:52Z",
                "total_findings": 3,
                "real_findings": [
                    {"risk_id": "BUG-CURRENT-1", "title": "最新缺陷 1"},
                    {"risk_id": "BUG-CURRENT-2", "title": "最新缺陷 2"},
                    {"risk_id": "BUG-CURRENT-3", "title": "最新缺陷 3"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = handler._load_current_scan_report(project, tmp_path)

    assert report["scan_id"] == "scan_fresh"
    assert report["total_findings"] == 3
    assert report["report_source_path"].endswith("platform_outputs/demo_project/intelligence_report.json")
