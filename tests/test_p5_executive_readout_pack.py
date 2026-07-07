from __future__ import annotations

import json
from pathlib import Path


OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: P5 Readout API
  version: 1.0.0
paths:
  /api/refunds:
    post:
      responses:
        '201': {description: created}
""".strip()


def _scan_result() -> dict:
    return {
        "project": "p5_readout_project",
        "p4_customer_value_scorecard": {
            "schema_version": "p4-customer-value-scorecard-v1",
            "value_level": "critical_value_proven",
            "customer_safe": True,
            "board_metrics": {
                "seed_defects_total": 3,
                "seed_defects_found": 3,
                "seed_defects_missed": 0,
                "detection_rate": 1.0,
                "p0_found": 1,
                "p1_found": 1,
                "observed_http_calls": 5,
            },
            "customer_safe_findings": [
                {"seed_id": "BUG_P0_REFUND", "title": "Unpaid order can be refunded", "severity": "P0", "kind": "should_reject_but_succeeded", "status": "found"},
                {"seed_id": "BUG_P1_AMOUNT", "title": "Payment amount mismatch", "severity": "P1", "kind": "field_mismatch", "status": "found"},
            ],
            "customer_safe_missed": [],
            "execution_context": {
                "runtime_status": "approved",
                "execution_status": "completed",
                "evidence_bundle_status": "persisted",
                "release_gate_verdict": "review_required",
            },
        },
        "p4_pilot_success_gate": {
            "schema_version": "p4-pilot-success-gate-v1",
            "decision": "procurement_ready",
            "pilot_success": True,
            "executive_readout_ready": True,
            "procurement_motion_ready": True,
            "blockers": [],
            "warnings": [],
            "next_actions": [
                "Schedule customer executive readout with the P4 value scorecard.",
                "Prepare customer-safe evidence stories for P0/P1 findings.",
                "Start procurement and deployment-scope discussion.",
            ],
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
        "p5_readout_project",
        "p5-readout-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "qa", "role": "qa"},
    )


def test_p5_executive_readout_pack_builds_customer_safe_material() -> None:
    from ai_test_asset_center.p5_executive_readout_pack import build_p5_executive_readout_pack

    pack = build_p5_executive_readout_pack(_scan_result())

    assert pack["schema_version"] == "p5-executive-readout-pack-v1"
    assert pack["customer_safe"] is True
    assert pack["decision"] == "procurement_ready"
    assert pack["pilot_success"] is True
    assert pack["executive_readout_ready"] is True
    assert pack["procurement_motion_ready"] is True
    assert "100.0%" in pack["executive_summary_zh"]
    assert "100.0%" in pack["executive_summary_en"]
    assert len(pack["meeting_agenda"]) >= 4
    assert len(pack["readout_sections"]) >= 5
    assert pack["customer_safe_findings"][0]["seed_id"] == "BUG_P0_REFUND"
    assert "Do not include raw request or response payloads in executive readout." in pack["non_goals"]


def test_p5_executive_readout_pack_handles_not_ready_decision() -> None:
    from ai_test_asset_center.p5_executive_readout_pack import build_p5_executive_readout_pack

    result = _scan_result()
    result["p4_pilot_success_gate"] = {
        "decision": "not_ready",
        "pilot_success": False,
        "executive_readout_ready": False,
        "procurement_motion_ready": False,
        "blockers": [{"code": "DETECTION_RATE_BELOW_SUCCESS_THRESHOLD", "detail": "low"}],
        "warnings": [],
        "next_actions": ["Do not present as pilot success yet."],
    }
    result["p4_customer_value_scorecard"]["board_metrics"]["detection_rate"] = 0.25
    pack = build_p5_executive_readout_pack(result)

    assert pack["decision"] == "not_ready"
    assert pack["pilot_success"] is False
    assert pack["procurement_motion_ready"] is False
    assert "不能作为成功案例" in pack["executive_summary_zh"]
    section_ids = {section["id"] for section in pack["readout_sections"]}
    assert "evidence_readiness" in section_ids


def test_scan_output_contains_p5_executive_readout_pack(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    result = scan(
        "p5_readout_project",
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

    assert result["p4_pilot_success_gate"]["pilot_success"] is True
    pack = result["p5_executive_readout_pack"]
    assert pack["schema_version"] == "p5-executive-readout-pack-v1"
    assert pack["customer_safe"] is True
    assert pack["executive_readout_ready"] is True
    assert pack["board_metrics"]["p0_found"] == 1


def test_scan_result_file_contains_p5_executive_readout_pack(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    scan(
        "p5_readout_project",
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
    saved = json.loads((tmp_path / "platform_outputs" / "p5_readout_project" / "scan_result.json").read_text(encoding="utf-8"))

    assert saved["p5_executive_readout_pack"]["schema_version"] == "p5-executive-readout-pack-v1"
    assert saved["p5_executive_readout_pack"]["customer_safe"] is True
    assert saved["p5_executive_readout_pack"]["executive_readout_ready"] is True
