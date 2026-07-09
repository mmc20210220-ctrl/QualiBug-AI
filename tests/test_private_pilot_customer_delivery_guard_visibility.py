import json
from pathlib import Path

from ai_test_asset_center.private_pilot_regression_run_visibility_patch import inject_regression_run


def test_command_center_uses_persisted_customer_delivery_guard(tmp_path: Path) -> None:
    project = "guard_project"
    guard_dir = tmp_path / "platform_outputs" / project
    guard_dir.mkdir(parents=True)
    (guard_dir / "customer_delivery_guard.json").write_text(
        json.dumps({
            "guard_version": "customer_delivery_guard.v1",
            "project_id": project,
            "status": "blocked_by_release_gate",
            "customer_deliverable": False,
            "release_gate_overall_status": "fail",
            "release_recommendation": "block_release",
            "safe_for_customer": False,
            "tracker_payload_status": "blocked_by_release_gate",
            "delivery_package_release_verdict": "fail",
            "block_reasons": ["release_gate_failed", "commercial_handoff_not_safe"],
            "honesty_rule": "Customer delivery is allowed only when the latest release gate passes and commercial_handoff.safe_for_customer is explicitly true.",
            "commercial_assets": {
                "commercial_handoff": {"safe_for_customer": False, "acceptance_status": "blocked_by_release_gate"},
                "tracker_sync": {"payload_status": "blocked_by_release_gate", "payload_gate_status": "fail"},
                "delivery_package": {"release_verdict": "fail", "customer_deliverable": False, "release_gate_blocked": True},
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    payload = {
        "data": {
            "project_id": project,
            "value_metrics": {},
            "executive_summary": {},
            "data_contract": {},
            "delivery_tracks": {"tracker_payload_status": "ready"},
            "commercial_assets": {
                "commercial_handoff": {"safe_for_customer": True, "acceptance_status": "accepted"},
                "tracker_sync": {"payload_status": "ready", "payload_gate_status": "pass"},
                "delivery_package": {"release_verdict": "pass", "customer_deliverable": True},
            },
        }
    }

    injected = inject_regression_run(payload, root=tmp_path)
    data = injected["data"]

    assert data["customer_delivery_guard"]["status"] == "blocked_by_release_gate"
    assert data["customer_delivery_guard"]["customer_deliverable"] is False
    assert data["commercial_assets"]["customer_deliverable"] is False
    assert data["commercial_assets"]["customer_delivery_status"] == "blocked_by_release_gate"
    assert data["commercial_assets"]["commercial_handoff"]["safe_for_customer"] is False
    assert data["commercial_assets"]["commercial_handoff"]["acceptance_status"] == "blocked_by_release_gate"
    assert data["commercial_assets"]["tracker_sync"]["payload_status"] == "blocked_by_release_gate"
    assert data["commercial_assets"]["delivery_package"]["release_verdict"] == "fail"
    assert data["commercial_assets"]["delivery_package"]["customer_deliverable"] is False
    assert data["delivery_tracks"]["customer_delivery_status"] == "blocked_by_release_gate"
    assert data["delivery_tracks"]["customer_deliverable"] is False
    assert data["delivery_tracks"]["tracker_payload_status"] == "blocked_by_release_gate"
    assert data["value_metrics"]["customer_delivery_guard_status"] == "blocked_by_release_gate"
    assert data["value_metrics"]["customer_deliverable"] is False
    assert data["value_metrics"]["safe_for_customer"] is False
    assert data["executive_summary"]["customer_delivery_guard_label"] == "客户交付未放行：blocked_by_release_gate"
    assert data["data_contract"]["customer_delivery_guard"]["display_key"] == "customer_delivery_guard"


def test_command_center_can_load_guard_from_pipeline_reports_ref(tmp_path: Path) -> None:
    project = "report_guard_project"
    guard_dir = tmp_path / "platform_outputs" / project / "pipeline_reports"
    guard_dir.mkdir(parents=True)
    (guard_dir / "customer_delivery_guard.json").write_text(
        json.dumps({
            "guard_version": "customer_delivery_guard.v1",
            "project_id": project,
            "status": "customer_deliverable",
            "customer_deliverable": True,
            "release_gate_overall_status": "pass",
            "safe_for_customer": True,
            "tracker_payload_status": "ready",
            "commercial_assets": {
                "commercial_handoff": {"safe_for_customer": True, "acceptance_status": "accepted"},
                "tracker_sync": {"payload_status": "ready", "payload_gate_status": "pass"},
                "delivery_package": {"release_verdict": "pass", "customer_deliverable": True},
            },
            "honesty_rule": "Guard is source of truth.",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    injected = inject_regression_run({"data": {"project_id": project, "value_metrics": {}, "executive_summary": {}, "data_contract": {}}}, root=tmp_path)
    data = injected["data"]

    assert data["customer_delivery_guard"]["status"] == "customer_deliverable"
    assert data["commercial_assets"]["customer_deliverable"] is True
    assert data["commercial_assets"]["commercial_handoff"]["safe_for_customer"] is True
    assert data["delivery_tracks"]["customer_deliverable"] is True
    assert data["value_metrics"]["customer_deliverable"] is True
    assert data["executive_summary"]["customer_delivery_guard_label"] == "客户交付已放行：门禁通过且 Handoff 明确安全"
