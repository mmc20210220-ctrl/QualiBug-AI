from __future__ import annotations

import json
from pathlib import Path


OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: P8 Ops API
  version: 1.0.0
paths:
  /api/refunds:
    post:
      responses:
        '201': {description: created}
""".strip()


def _handoff_result(stage: str = "procurement_followup", handoff_ready: bool = True) -> dict:
    return {
        "project": "p8_ops_project",
        "p7_sales_handoff_package": {
            "handoff_ready": handoff_ready,
            "procurement_ready": stage == "procurement_followup",
            "sales_stage": stage,
            "customer_success_stage": "commercial_expansion" if stage == "procurement_followup" else "evidence_hardening",
            "commercial_next_actions": [
                "Schedule procurement-scope alignment using the P6 delivery package.",
                "Confirm deployment model and commercial timeline.",
            ],
            "risk_register": [
                {"severity": "info", "code": "NO_DELIVERY_BLOCKERS_RECORDED", "detail": "No blockers."}
            ],
        },
    }


def _blocked_handoff_result() -> dict:
    result = _handoff_result(stage="internal_remediation", handoff_ready=False)
    result["p7_sales_handoff_package"]["risk_register"] = [
        {"severity": "blocker", "code": "DELIVERY_CORE_OUTPUTS_MISSING", "detail": "Missing P6 outputs."},
        {"severity": "warning", "code": "EVIDENCE_BUNDLE_NOT_READY", "detail": "Evidence not persisted."},
    ]
    return result


def _seed_defects() -> list[dict]:
    return [
        {
            "id": "BUG_REFUND_UNPAID_ORDER",
            "title": "Unpaid order can be refunded",
            "kind": "should_reject_but_succeeded",
            "method": "POST",
            "path": "/api/refunds",
            "severity": "P0",
        }
    ]


def _observations() -> list[dict]:
    return [
        {"method": "POST", "path": "/api/refunds", "status": 201, "body": {"refund_id": "r_1"}}
    ]


def _manifest(tmp_path: Path) -> dict:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    return register_source_asset(
        "p8_ops_project",
        "p8-ops-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "qa", "role": "qa"},
    )


def test_p8_operations_tracker_builds_procurement_task_board() -> None:
    from ai_test_asset_center.p8_pilot_operations_tracker import build_p8_pilot_operations_tracker

    tracker = build_p8_pilot_operations_tracker(_handoff_result())
    task_ids = {task["task_id"] for task in tracker["tasks"]}

    assert tracker["schema_version"] == "p8-pilot-operations-tracker-v1"
    assert tracker["customer_safe"] is True
    assert tracker["operating_status"] == "ready_for_customer_motion"
    assert tracker["handoff_ready"] is True
    assert tracker["procurement_ready"] is True
    assert "schedule_procurement_alignment" in task_ids
    assert "prepare_security_review_packet" in task_ids
    assert tracker["task_summary"]["tasks_by_owner_role"]["sales_lead"] >= 1
    assert "sales_lead" in tracker["raci_roles"]


def test_p8_operations_tracker_tracks_blocker_risks() -> None:
    from ai_test_asset_center.p8_pilot_operations_tracker import build_p8_pilot_operations_tracker

    tracker = build_p8_pilot_operations_tracker(_blocked_handoff_result())
    blocker_tasks = [task for task in tracker["tasks"] if task["blockers"]]
    task_ids = {task["task_id"] for task in tracker["tasks"]}

    assert tracker["operating_status"] == "blocked"
    assert tracker["handoff_ready"] is False
    assert "resolve_delivery_blockers" in task_ids
    assert blocker_tasks
    assert blocker_tasks[0]["priority"] == "P0"
    assert tracker["task_summary"]["blocked_tasks"] >= 1
    assert "Do not move blocked pilots into procurement motion." in tracker["non_goals"]


def test_scan_output_contains_p8_pilot_operations_tracker(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    result = scan(
        "p8_ops_project",
        root=tmp_path,
        api_doc_text=OPENAPI_TEXT,
        campaign_context={
            "source_manifest": manifest,
            "scope_id": "refund-scope",
            "environment_ref": "benchmark",
            "p3_seed_defects": _seed_defects(),
            "p3_http_observations": _observations(),
        },
    )

    tracker = result["p8_pilot_operations_tracker"]
    assert tracker["schema_version"] == "p8-pilot-operations-tracker-v1"
    assert tracker["customer_safe"] is True
    assert tracker["task_summary"]["total_tasks"] >= 1
    assert "sales_lead" in tracker["raci_roles"]


def test_scan_result_file_contains_p8_pilot_operations_tracker(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    scan(
        "p8_ops_project",
        root=tmp_path,
        api_doc_text=OPENAPI_TEXT,
        campaign_context={
            "source_manifest": manifest,
            "scope_id": "refund-scope",
            "environment_ref": "benchmark",
            "p3_seed_defects": _seed_defects(),
            "p3_http_observations": _observations(),
        },
    )
    saved = json.loads((tmp_path / "platform_outputs" / "p8_ops_project" / "scan_result.json").read_text(encoding="utf-8"))

    assert saved["p8_pilot_operations_tracker"]["schema_version"] == "p8-pilot-operations-tracker-v1"
    assert saved["p8_pilot_operations_tracker"]["customer_safe"] is True
    assert saved["p8_pilot_operations_tracker"]["task_summary"]["total_tasks"] >= 1
