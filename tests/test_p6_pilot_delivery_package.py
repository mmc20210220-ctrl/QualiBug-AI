from __future__ import annotations

import json
from pathlib import Path


OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: P6 Delivery API
  version: 1.0.0
paths:
  /api/refunds:
    post:
      responses:
        '201': {description: created}
""".strip()


def _complete_result() -> dict:
    return {
        "project": "p6_delivery_project",
        "p3_seed_bug_benchmark": {"found_count": 1, "total_seed_defects": 1, "detection_rate": 1.0},
        "p4_customer_value_scorecard": {
            "customer_safe": True,
            "board_metrics": {"p0_found": 1, "seed_defects_found": 1, "seed_defects_total": 1},
        },
        "p4_pilot_success_gate": {
            "decision": "procurement_ready",
            "pilot_success": True,
            "executive_readout_ready": True,
            "procurement_motion_ready": True,
            "warnings": [],
        },
        "p5_executive_readout_pack": {"customer_safe": True, "executive_readout_ready": True, "procurement_motion_ready": True},
        "p5_evidence_story_pack": {"customer_safe": True, "story_count": 1},
        "evidence_bundle": {"status": "persisted", "bundle_id": "bundle_1"},
        "release_gate": {"verdict": "review_required"},
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
        {"method": "POST", "path": "/api/refunds", "status": 201, "body": {"refund_id": "r_1"}}
    ]


def _manifest(tmp_path: Path) -> dict:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    return register_source_asset(
        "p6_delivery_project",
        "p6-delivery-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "qa", "role": "qa"},
    )


def test_p6_pilot_delivery_package_marks_procurement_package() -> None:
    from ai_test_asset_center.p6_pilot_delivery_package import build_p6_pilot_delivery_package

    package = build_p6_pilot_delivery_package(_complete_result())

    assert package["schema_version"] == "p6-pilot-delivery-package-v1"
    assert package["customer_safe"] is True
    assert package["delivery_decision"] == "deliverable_for_procurement"
    assert package["external_delivery_allowed"] is True
    assert package["procurement_package"] is True
    assert package["executive_readout_package"] is True
    assert package["missing_outputs"] == []
    assert package["blockers"] == []
    assert "p5_executive_readout_pack" in package["customer_shareable_keys"]
    assert "evidence_bundle" in package["internal_only_keys"]
    assert "Do not include raw evidence bundle content in the customer package by default." in package["non_goals"]


def test_p6_pilot_delivery_package_blocks_missing_core_outputs() -> None:
    from ai_test_asset_center.p6_pilot_delivery_package import build_p6_pilot_delivery_package

    result = _complete_result()
    del result["p5_evidence_story_pack"]
    package = build_p6_pilot_delivery_package(result)
    blocker_codes = {item["code"] for item in package["blockers"]}

    assert package["delivery_decision"] == "not_deliverable"
    assert package["external_delivery_allowed"] is False
    assert "p5_evidence_story_pack" in package["missing_outputs"]
    assert "DELIVERY_CORE_OUTPUTS_MISSING" in blocker_codes


def test_p6_pilot_delivery_package_blocks_not_ready_gate() -> None:
    from ai_test_asset_center.p6_pilot_delivery_package import build_p6_pilot_delivery_package

    result = _complete_result()
    result["p4_pilot_success_gate"]["executive_readout_ready"] = False
    package = build_p6_pilot_delivery_package(result)
    blocker_codes = {item["code"] for item in package["blockers"]}

    assert package["delivery_decision"] == "not_deliverable"
    assert "EXECUTIVE_READOUT_NOT_READY" in blocker_codes


def test_scan_output_contains_p6_pilot_delivery_package(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    result = scan(
        "p6_delivery_project",
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

    package = result["p6_pilot_delivery_package"]
    assert package["schema_version"] == "p6-pilot-delivery-package-v1"
    assert package["customer_safe"] is True
    assert "p5_executive_readout_pack" in package["customer_shareable_keys"]
    assert "evidence_bundle" in package["internal_only_keys"]


def test_scan_result_file_contains_p6_pilot_delivery_package(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    scan(
        "p6_delivery_project",
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
    saved = json.loads((tmp_path / "platform_outputs" / "p6_delivery_project" / "scan_result.json").read_text(encoding="utf-8"))

    assert saved["p6_pilot_delivery_package"]["schema_version"] == "p6-pilot-delivery-package-v1"
    assert saved["p6_pilot_delivery_package"]["customer_safe"] is True
    assert "p5_evidence_story_pack" in saved["p6_pilot_delivery_package"]["customer_shareable_keys"]
