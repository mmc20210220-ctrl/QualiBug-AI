from __future__ import annotations

import json
import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

import ai_test_asset_center.__main__ as main_module
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


def test_command_center_api_payload_matches_snapshot_for_ui_high_confidence_grade(monkeypatch, tmp_path) -> None:
    project = "enterprise-project"
    scan_result_path = tmp_path / "platform_outputs" / project / "scan_result.json"
    scan_result_path.parent.mkdir(parents=True, exist_ok=True)
    scan_result_path.write_text(json.dumps({"project": project}, ensure_ascii=False), encoding="utf-8")
    real_project_path = tmp_path / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    real_project_path.parent.mkdir(parents=True, exist_ok=True)
    real_project_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

    command_center_data = {
        "defects": [
            {
                "id": "UI-HC-1",
                "title": "订单详情页状态异常",
                "severity": "P1",
                "risk_type": "ui_execution",
                "confidence_score": 0.85,
                "ui_candidate_gate": {"passed": True},
                "ui_verification": {"status": "verified", "reason": "sqlite_row_match"},
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
            "ui_total": 1,
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
        "data_contract": {
            "display_key": "defects",
            "frontend_entry": "frontend/src/api/client.ts:getFindings",
        },
    }

    monkeypatch.setattr(
        PrivatePilotHandler,
        "_build_command_center",
        lambda self, project_id, root: {"ok": True, "data": dict(command_center_data)},
    )

    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.path = f"/api/v1/projects/{project}/command-center"
    handler.headers = {}
    captured: dict[str, object] = {}

    def fake_json(payload, status=200, extra_headers=None):  # type: ignore[no-untyped-def]
        captured["payload"] = payload
        captured["status"] = status
        captured["headers"] = extra_headers
        return payload

    monkeypatch.setattr(handler, "_json", fake_json)
    monkeypatch.setattr(handler, "_project", lambda: project)
    monkeypatch.setattr(handler, "_root", lambda: tmp_path)
    monkeypatch.setattr(handler, "_require_actor", lambda: {"actor_type": "test"})
    monkeypatch.setattr(handler, "_require_project_scope", lambda project_id: True)

    handler.do_GET()

    api_payload = captured["payload"]
    assert captured["status"] == 200
    assert isinstance(api_payload, dict)
    assert api_payload["ok"] is True
    assert api_payload["data"]["defects"][0]["verification_badge"] == "ui_verified"
    assert api_payload["data"]["defects"][0]["candidate_tier"] == "high_confidence_ui_candidate"
    assert api_payload["data"]["defects"][0]["high_confidence_candidate"] is True
    assert api_payload["data"]["defects"][0]["evidence_quality"]["level"] == "cross_verified"
    assert api_payload["data"]["executive_summary"]["ui_verified_candidates"] == 1
    assert api_payload["data"]["scan_meta"]["ui_high_confidence_candidates"] == 1
    assert api_payload["data"]["data_contract"]["frontend_entry"] == "frontend/src/api/client.ts:getFindings"

    snapshot = main_module._customer_ready_static_snapshot(project, tmp_path)
    persisted = main_module._persist_customer_ready_static_artifacts(project, tmp_path, {"project": project})
    saved_scan = json.loads(scan_result_path.read_text(encoding="utf-8"))
    saved_real_project = json.loads(real_project_path.read_text(encoding="utf-8"))

    assert snapshot["defects"][0]["verification_badge"] == api_payload["data"]["defects"][0]["verification_badge"]
    assert snapshot["defects"][0]["candidate_tier"] == api_payload["data"]["defects"][0]["candidate_tier"]
    assert snapshot["defects"][0]["high_confidence_candidate"] is api_payload["data"]["defects"][0]["high_confidence_candidate"]
    assert snapshot["executive_summary"]["ui_verified_candidates"] == api_payload["data"]["executive_summary"]["ui_verified_candidates"]
    assert snapshot["scan_meta"]["ui_high_confidence_candidates"] == api_payload["data"]["scan_meta"]["ui_high_confidence_candidates"]

    assert persisted["defects"][0]["candidate_tier"] == "high_confidence_ui_candidate"
    assert saved_scan["customer_ready_snapshot"]["defects"][0]["verification_badge"] == "ui_verified"
    assert saved_scan["customer_ready_snapshot"]["scan_meta"]["ui_high_confidence_candidates"] == 1
    assert saved_real_project["defects"][0]["high_confidence_candidate"] is True
    assert saved_real_project["executive_summary"]["ui_verified_candidates"] == 1
    assert saved_real_project["scan_meta"]["ui_high_confidence_candidates"] == 1
