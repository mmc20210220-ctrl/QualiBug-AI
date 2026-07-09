import json
from pathlib import Path

from ai_test_asset_center.customer_delivery_guard import build_customer_delivery_guard, persist_customer_delivery_guard


def _write_report(root: Path, project: str, payload: dict) -> None:
    report_dir = root / "platform_outputs" / project / "pipeline_reports"
    report_dir.mkdir(parents=True)
    (report_dir / "latest_pipeline_report.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_regression_run(root: Path, project: str, payload: dict) -> None:
    run_dir = root / "platform_outputs" / project / "regression_run"
    run_dir.mkdir(parents=True)
    (run_dir / "regression_run_result.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_delivery_guard_blocks_stale_safe_handoff_when_latest_regression_fails(tmp_path: Path) -> None:
    project = "stale_safe_project"
    _write_report(tmp_path, project, {
        "commercial_assets": {
            "commercial_handoff": {"safe_for_customer": True, "acceptance_status": "accepted"},
            "tracker_sync": {"payload_status": "ready", "payload_gate_status": "pass"},
            "delivery_package": {"release_verdict": "pass"},
        }
    })
    _write_regression_run(tmp_path, project, {
        "summary": {"passed_count": 1, "failed_count": 1, "needs_review_count": 0},
        "ci_feedback": {"gate_status": "failed", "ci_message": "最新回归失败"},
    })

    guard = persist_customer_delivery_guard(project, tmp_path)
    saved = json.loads((tmp_path / "platform_outputs" / project / "customer_delivery_guard.json").read_text(encoding="utf-8"))
    report_saved = json.loads((tmp_path / "platform_outputs" / project / "pipeline_reports" / "customer_delivery_guard.json").read_text(encoding="utf-8"))

    assert guard["customer_deliverable"] is False
    assert guard["status"] == "blocked_by_release_gate"
    assert guard["release_gate_overall_status"] == "fail"
    assert "release_gate_failed" in guard["block_reasons"]
    assert "commercial_handoff_not_safe" in guard["block_reasons"]
    assert guard["commercial_assets"]["commercial_handoff"]["safe_for_customer"] is False
    assert guard["commercial_assets"]["commercial_handoff"]["acceptance_status"] == "blocked_by_release_gate"
    assert guard["commercial_assets"]["tracker_sync"]["payload_status"] == "blocked_by_release_gate"
    assert guard["commercial_assets"]["delivery_package"]["release_gate_blocked"] is True
    assert saved["status"] == guard["status"]
    assert report_saved["status"] == guard["status"]


def test_delivery_guard_does_not_release_on_gate_pass_without_safe_handoff(tmp_path: Path) -> None:
    project = "pass_but_no_handoff"
    _write_report(tmp_path, project, {
        "release_gate": {
            "overall_status": "pass",
            "checks": [{"name": "修复后回归 Gate", "status": "pass", "detail": "回归通过", "source": "regression_run"}],
        }
    })

    guard = build_customer_delivery_guard(project, tmp_path)

    assert guard["release_gate_overall_status"] == "pass"
    assert guard["customer_deliverable"] is False
    assert guard["status"] == "hold_for_commercial_handoff"
    assert "commercial_handoff_not_safe" in guard["block_reasons"]
    assert guard["commercial_assets"]["delivery_package"]["customer_deliverable"] is False
    assert "A passed release gate alone is not a customer-delivery approval" in guard["honesty_rule"]


def test_delivery_guard_allows_only_gate_pass_and_explicit_safe_handoff(tmp_path: Path) -> None:
    project = "deliverable_project"
    _write_report(tmp_path, project, {
        "release_gate": {
            "overall_status": "pass",
            "checks": [{"name": "修复后回归 Gate", "status": "pass", "detail": "回归通过", "source": "regression_run"}],
        },
        "commercial_assets": {
            "commercial_handoff": {"safe_for_customer": True, "acceptance_status": "accepted"},
            "tracker_sync": {"payload_status": "ready", "payload_gate_status": "pass"},
            "delivery_package": {"release_verdict": "pass"},
        },
    })

    guard = build_customer_delivery_guard(project, tmp_path)

    assert guard["release_gate_overall_status"] == "pass"
    assert guard["safe_for_customer"] is True
    assert guard["customer_deliverable"] is True
    assert guard["status"] == "customer_deliverable"
    assert guard["block_reasons"] == ["customer_delivery_allowed"]
    assert guard["commercial_assets"]["delivery_package"]["customer_deliverable"] is True
