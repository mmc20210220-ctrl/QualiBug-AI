from __future__ import annotations

import json

from ai_test_asset_center.regression_suite_builder import build_regression_suite


def test_regression_suite_builder_only_loads_approved_ui_high_confidence_candidates(tmp_path) -> None:
    project = "enterprise-project"
    workspace_dir = tmp_path / "platform_workspace" / project / "defect_discovery"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "ui_high_confidence_regression_candidates.json").write_text(
        json.dumps(
            {
                "version": "ui_high_confidence_regression_candidates_v1",
                "project_id": project,
                "items": [
                    {
                        "regression_probe_id": "UIREG_APPROVED",
                        "title": "已审批 UI 高可信候选",
                        "risk_type": "ui_execution",
                        "severity": "P1",
                        "method": "POST",
                        "path": "/ui/orders/1/cancel",
                        "approved": True,
                        "candidate_tier": "high_confidence_ui_candidate",
                        "high_confidence_candidate": True,
                        "verification_status": "verified",
                        "verification_badge": "ui_verified",
                        "confidence_score": 0.85,
                        "evidence_quality": {"level": "cross_verified", "score": 85},
                    },
                    {
                        "regression_probe_id": "UIREG_PENDING",
                        "title": "未审批 UI 高可信候选",
                        "risk_type": "ui_execution",
                        "severity": "P0",
                        "method": "POST",
                        "path": "/ui/orders/2/cancel",
                        "approved": False,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_regression_suite(project_id=project, root=tmp_path)
    release_items = result["modes"]["release"]["items"]
    probe_ids = {item["regression_probe_id"] for item in release_items}
    approved = next(item for item in release_items if item["regression_probe_id"] == "UIREG_APPROVED")

    assert "UIREG_APPROVED" in probe_ids
    assert "UIREG_PENDING" not in probe_ids
    assert result["summary"]["total_probe_count"] == 1
    assert approved["candidate_tier"] == "high_confidence_ui_candidate"
    assert approved["high_confidence_candidate"] is True
    assert approved["verification_status"] == "verified"
    assert approved["verification_badge"] == "ui_verified"
    assert approved["confidence_score"] == 0.85
    assert approved["evidence_quality"]["level"] == "cross_verified"
    assert approved["evidence_quality"]["score"] == 85
