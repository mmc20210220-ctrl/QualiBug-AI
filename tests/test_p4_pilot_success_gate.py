from __future__ import annotations

import json
from pathlib import Path


OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: P4 Pilot Gate API
  version: 1.0.0
paths:
  /api/refunds:
    post:
      responses:
        '201': {description: created}
""".strip()


def _scorecard_result(*, evidence_status: str = "persisted", detection_rate: float = 1.0, p0_found: int = 1, missed: int = 0) -> dict:
    return {
        "project": "p4_pilot_gate_project",
        "p4_customer_value_scorecard": {
            "schema_version": "p4-customer-value-scorecard-v1",
            "value_level": "critical_value_proven" if p0_found else "value_proven",
            "customer_safe": True,
            "board_metrics": {
                "seed_defects_total": 3,
                "seed_defects_found": 3 - missed,
                "seed_defects_missed": missed,
                "detection_rate": detection_rate,
                "p0_found": p0_found,
                "p1_found": 1 if not p0_found else 0,
                "observed_http_calls": 5,
            },
            "execution_context": {
                "runtime_status": "approved",
                "execution_status": "completed",
                "evidence_bundle_status": evidence_status,
                "release_gate_verdict": "review_required",
            },
        },
    }


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
        {
            "method": "POST",
            "path": "/api/refunds",
            "status": 201,
            "body": {"refund_id": "r_1", "order_id": "ord_unpaid"},
        }
    ]


def _manifest(tmp_path: Path) -> dict:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    return register_source_asset(
        "p4_pilot_gate_project",
        "p4-pilot-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "qa", "role": "qa"},
    )


def test_p4_pilot_success_gate_marks_procurement_ready() -> None:
    from ai_test_asset_center.p4_pilot_success_gate import build_p4_pilot_success_gate

    gate = build_p4_pilot_success_gate(_scorecard_result())

    assert gate["schema_version"] == "p4-pilot-success-gate-v1"
    assert gate["decision"] == "procurement_ready"
    assert gate["pilot_success"] is True
    assert gate["executive_readout_ready"] is True
    assert gate["procurement_motion_ready"] is True
    assert gate["blockers"] == []
    assert gate["board_metrics"]["p0_found"] == 1


def test_p4_pilot_success_gate_marks_executive_ready_without_persisted_evidence() -> None:
    from ai_test_asset_center.p4_pilot_success_gate import build_p4_pilot_success_gate

    gate = build_p4_pilot_success_gate(_scorecard_result(evidence_status="not_created"))

    assert gate["decision"] == "executive_readout_ready"
    assert gate["pilot_success"] is True
    assert gate["procurement_motion_ready"] is False
    warning_codes = {item["code"] for item in gate["warnings"]}
    assert "EVIDENCE_BUNDLE_NOT_PERSISTED" in warning_codes


def test_p4_pilot_success_gate_blocks_low_detection_rate() -> None:
    from ai_test_asset_center.p4_pilot_success_gate import build_p4_pilot_success_gate

    gate = build_p4_pilot_success_gate(_scorecard_result(detection_rate=0.25, missed=2))
    blocker_codes = {item["code"] for item in gate["blockers"]}

    assert gate["decision"] == "not_ready"
    assert gate["pilot_success"] is False
    assert "DETECTION_RATE_BELOW_SUCCESS_THRESHOLD" in blocker_codes
    assert "Do not present as pilot success yet." in gate["next_actions"]


def test_scan_output_contains_p4_pilot_success_gate(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    result = scan(
        "p4_pilot_gate_project",
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

    assert result["p4_customer_value_scorecard"]["value_level"] == "critical_value_proven"
    gate = result["p4_pilot_success_gate"]
    assert gate["schema_version"] == "p4-pilot-success-gate-v1"
    assert gate["executive_readout_ready"] is True
    assert gate["pilot_success"] is True


def test_scan_result_file_contains_p4_pilot_success_gate(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    scan(
        "p4_pilot_gate_project",
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
    saved = json.loads((tmp_path / "platform_outputs" / "p4_pilot_gate_project" / "scan_result.json").read_text(encoding="utf-8"))

    assert saved["p4_pilot_success_gate"]["schema_version"] == "p4-pilot-success-gate-v1"
    assert saved["p4_pilot_success_gate"]["pilot_success"] is True
