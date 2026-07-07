from __future__ import annotations

import json
import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

import ai_test_asset_center.__main__ as main_module


def test_persist_customer_ready_static_artifacts_preserves_current_report_breakdown(tmp_path, monkeypatch) -> None:
    project = "enterprise-project"
    scan_result_path = tmp_path / "platform_outputs" / project / "scan_result.json"
    scan_result_path.parent.mkdir(parents=True, exist_ok=True)
    scan_result_path.write_text(
        json.dumps({"project": project, "total_findings": 3, "total_candidates": 0}, ensure_ascii=False),
        encoding="utf-8",
    )
    real_project_path = tmp_path / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    real_project_path.parent.mkdir(parents=True, exist_ok=True)
    real_project_path.write_text(
        json.dumps({"continuous_discovery_campaign": {"summary": {"confirmed_slice_count": 18}}}, ensure_ascii=False),
        encoding="utf-8",
    )

    snapshot = {
        "project": project,
        "generated_at_utc": "2026-07-07T18:20:00Z",
        "defects": [{"id": "BUG-1", "title": "重复支付"}],
        "clues": [{"id": "CLUE-1", "title": "退款链路待补证"}],
        "risks": [{"id": "BUG-1", "title": "重复支付"}],
        "value_metrics": {
            "ready_bug_count": 1,
            "clue_count": 1,
            "current_report_breakdown": {"total_findings": 2, "category_counts": {"state_machine": 1, "concurrency": 1}},
        },
        "executive_summary": {
            "ready_bugs": 1,
            "internal_clues": 1,
            "current_report_breakdown": {"total_findings": 2, "category_counts": {"state_machine": 1, "concurrency": 1}},
        },
        "scan_meta": {
            "ready_bug_count": 1,
            "current_report_breakdown": {
                "total_findings": 2,
                "category_counts": {"state_machine": 1, "concurrency": 1},
                "report_source_path": "aggregated:demo",
            },
        },
        "data_contract": {
            "display_key": "defects",
            "current_report_breakdown": {"total_findings": 2, "category_counts": {"state_machine": 1, "concurrency": 1}},
        },
    }
    monkeypatch.setattr(main_module, "_customer_ready_static_snapshot", lambda project_id, root: dict(snapshot))

    result = {"project": project, "total_findings": 3}
    persisted = main_module._persist_customer_ready_static_artifacts(project, tmp_path, result)

    saved_scan = json.loads(scan_result_path.read_text(encoding="utf-8"))
    saved_real_project = json.loads(real_project_path.read_text(encoding="utf-8"))

    assert persisted["scan_meta"]["current_report_breakdown"]["category_counts"]["concurrency"] == 1
    assert saved_scan["customer_ready_snapshot"]["scan_meta"]["current_report_breakdown"]["report_source_path"] == "aggregated:demo"
    assert saved_real_project["scan_meta"]["current_report_breakdown"]["total_findings"] == 2
    assert result["customer_ready_snapshot"]["data_contract"]["current_report_breakdown"]["category_counts"]["state_machine"] == 1
