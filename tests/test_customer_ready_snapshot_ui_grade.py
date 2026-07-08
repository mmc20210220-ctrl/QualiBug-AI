from __future__ import annotations

import json
import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

import ai_test_asset_center.__main__ as main_module
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


def test_customer_ready_snapshot_and_persisted_artifacts_preserve_ui_high_confidence_grade(monkeypatch, tmp_path) -> None:
    project = "enterprise-project"
    scan_result_path = tmp_path / "platform_outputs" / project / "scan_result.json"
    scan_result_path.parent.mkdir(parents=True, exist_ok=True)
    scan_result_path.write_text(json.dumps({"project": project}, ensure_ascii=False), encoding="utf-8")
    real_project_path = tmp_path / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    real_project_path.parent.mkdir(parents=True, exist_ok=True)
    real_project_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        PrivatePilotHandler,
        "_build_command_center",
        lambda self, project_id, root: {
            "ok": True,
            "data": {
                "defects": [
                    {
                        "id": "UI-HC-1",
                        "title": "订单详情页状态异常",
                        "severity": "P1",
                        "risk_type": "ui_execution",
                        "verification_badge": "ui_verified",
                        "verification_label": "已二次验真",
                        "candidate_tier": "high_confidence_ui_candidate",
                        "high_confidence_candidate": True,
                        "priority_label": "P1",
                        "priority_score": 132.0,
                        "evidence_quality": {"level": "cross_verified", "score": 85},
                    }
                ],
                "clues": [],
                "value_metrics": {
                    "ready_bug_count": 1,
                    "ui_candidate_total": 1,
                    "ui_verified_candidate_total": 1,
                    "ui_high_confidence_candidate_total": 1,
                },
                "executive_summary": {
                    "ready_bugs": 1,
                    "ui_candidate_findings": 1,
                    "ui_verified_candidates": 1,
                    "ui_high_confidence_candidates": 1,
                },
                "scan_meta": {
                    "ready_bug_count": 1,
                    "ui_candidate_findings": 1,
                    "ui_verified_candidates": 1,
                    "ui_high_confidence_candidates": 1,
                },
                "data_contract": {"display_key": "defects"},
            },
        },
    )

    snapshot = main_module._customer_ready_static_snapshot(project, tmp_path)
    assert snapshot["defects"][0]["verification_badge"] == "ui_verified"
    assert snapshot["defects"][0]["candidate_tier"] == "high_confidence_ui_candidate"
    assert snapshot["defects"][0]["high_confidence_candidate"] is True
    assert snapshot["defects"][0]["evidence_quality"]["level"] == "cross_verified"
    assert snapshot["executive_summary"]["ui_verified_candidates"] == 1
    assert snapshot["executive_summary"]["ui_high_confidence_candidates"] == 1
    assert snapshot["scan_meta"]["ui_verified_candidates"] == 1
    assert snapshot["scan_meta"]["ui_high_confidence_candidates"] == 1

    result = {"project": project}
    persisted = main_module._persist_customer_ready_static_artifacts(project, tmp_path, result)

    saved_scan = json.loads(scan_result_path.read_text(encoding="utf-8"))
    saved_real_project = json.loads(real_project_path.read_text(encoding="utf-8"))

    assert persisted["defects"][0]["verification_badge"] == "ui_verified"
    assert saved_scan["customer_ready_snapshot"]["defects"][0]["candidate_tier"] == "high_confidence_ui_candidate"
    assert saved_scan["customer_ready_snapshot"]["defects"][0]["evidence_quality"]["level"] == "cross_verified"
    assert saved_scan["customer_ready_snapshot"]["executive_summary"]["ui_verified_candidates"] == 1
    assert saved_scan["customer_ready_snapshot"]["scan_meta"]["ui_high_confidence_candidates"] == 1
    assert saved_real_project["defects"][0]["high_confidence_candidate"] is True
    assert saved_real_project["defects"][0]["verification_label"] == "已二次验真"
    assert saved_real_project["executive_summary"]["ui_verified_candidates"] == 1
    assert saved_real_project["scan_meta"]["ui_high_confidence_candidates"] == 1
