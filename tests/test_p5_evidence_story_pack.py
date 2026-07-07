from __future__ import annotations

import json
from pathlib import Path


OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: P5 Story API
  version: 1.0.0
paths:
  /api/refunds:
    post:
      responses:
        '201': {description: created}
""".strip()


def _scan_result() -> dict:
    return {
        "project": "p5_story_project",
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
                {"seed_id": "BUG_P2_DELETED", "title": "Deleted order readable", "severity": "P2", "kind": "should_reject_but_succeeded", "status": "found"},
                {"seed_id": "BUG_P0_REFUND", "title": "Unpaid order can be refunded", "severity": "P0", "kind": "should_reject_but_succeeded", "status": "found"},
                {"seed_id": "BUG_P1_AMOUNT", "title": "Payment amount mismatch", "severity": "P1", "kind": "field_mismatch", "status": "found"},
            ],
            "execution_context": {
                "runtime_status": "approved",
                "execution_status": "completed",
                "evidence_bundle_status": "persisted",
                "release_gate_verdict": "review_required",
            },
        },
        "p5_executive_readout_pack": {
            "schema_version": "p5-executive-readout-pack-v1",
            "customer_safe": True,
            "executive_readout_ready": True,
            "procurement_motion_ready": True,
            "customer_safe_findings": [
                {"seed_id": "BUG_P2_DELETED", "title": "Deleted order readable", "severity": "P2", "kind": "should_reject_but_succeeded", "status": "found"},
                {"seed_id": "BUG_P0_REFUND", "title": "Unpaid order can be refunded", "severity": "P0", "kind": "should_reject_but_succeeded", "status": "found"},
                {"seed_id": "BUG_P1_AMOUNT", "title": "Payment amount mismatch", "severity": "P1", "kind": "field_mismatch", "status": "found"},
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
        "p5_story_project",
        "p5-story-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "qa", "role": "qa"},
    )


def test_p5_evidence_story_pack_builds_customer_safe_stories() -> None:
    from ai_test_asset_center.p5_evidence_story_pack import build_p5_evidence_story_pack

    pack = build_p5_evidence_story_pack(_scan_result())

    assert pack["schema_version"] == "p5-evidence-story-pack-v1"
    assert pack["customer_safe"] is True
    assert pack["story_count"] == 2
    assert pack["p0_story_count"] == 1
    assert pack["p1_story_count"] == 1
    assert pack["stories"][0]["seed_id"] == "BUG_P0_REFUND"
    assert pack["stories"][0]["priority"] == "executive_critical"
    assert pack["stories"][0]["customer_safe_evidence"]["raw_payload_included"] is False
    assert "problem" in pack["stories"][0]
    assert "business_impact" in pack["stories"][0]
    assert "recommended_action" in pack["stories"][0]
    assert "Do not expose raw request/response payloads in this pack." in pack["non_goals"]


def test_p5_evidence_story_pack_falls_back_to_any_finding_when_no_p0_p1() -> None:
    from ai_test_asset_center.p5_evidence_story_pack import build_p5_evidence_story_pack

    result = _scan_result()
    result["p4_customer_value_scorecard"]["customer_safe_findings"] = [
        {"seed_id": "BUG_P2_ONLY", "title": "P2 only", "severity": "P2", "kind": "status_mismatch", "status": "found"}
    ]
    result["p5_executive_readout_pack"]["customer_safe_findings"] = result["p4_customer_value_scorecard"]["customer_safe_findings"]
    pack = build_p5_evidence_story_pack(result)

    assert pack["story_count"] == 1
    assert pack["stories"][0]["seed_id"] == "BUG_P2_ONLY"
    assert pack["stories"][0]["priority"] == "standard"


def test_scan_output_contains_p5_evidence_story_pack(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    result = scan(
        "p5_story_project",
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

    pack = result["p5_evidence_story_pack"]
    assert pack["schema_version"] == "p5-evidence-story-pack-v1"
    assert pack["customer_safe"] is True
    assert pack["story_count"] >= 1
    assert pack["stories"][0]["severity"] == "P0"


def test_scan_result_file_contains_p5_evidence_story_pack(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    scan(
        "p5_story_project",
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
    saved = json.loads((tmp_path / "platform_outputs" / "p5_story_project" / "scan_result.json").read_text(encoding="utf-8"))

    assert saved["p5_evidence_story_pack"]["schema_version"] == "p5-evidence-story-pack-v1"
    assert saved["p5_evidence_story_pack"]["customer_safe"] is True
    assert saved["p5_evidence_story_pack"]["stories"][0]["customer_safe_evidence"]["raw_payload_included"] is False
