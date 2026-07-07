from __future__ import annotations

import json
from pathlib import Path


OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: P7 Sales API
  version: 1.0.0
paths:
  /api/refunds:
    post:
      responses:
        '201': {description: created}
""".strip()


def _complete_result(decision: str = "deliverable_for_procurement") -> dict:
    return {
        "project": "p7_sales_project",
        "p4_customer_value_scorecard": {
            "board_metrics": {
                "seed_defects_total": 1,
                "seed_defects_found": 1,
                "detection_rate": 1.0,
                "p0_found": 1,
                "p1_found": 0,
            }
        },
        "p4_pilot_success_gate": {"warnings": []},
        "p6_pilot_delivery_package": {
            "delivery_decision": decision,
            "external_delivery_allowed": decision in {"deliverable_for_procurement", "deliverable_for_executive_readout"},
            "procurement_package": decision == "deliverable_for_procurement",
            "executive_readout_package": decision in {"deliverable_for_procurement", "deliverable_for_executive_readout"},
            "customer_shareable_keys": ["p4_customer_value_scorecard", "p5_executive_readout_pack", "p5_evidence_story_pack"],
            "internal_only_keys": ["evidence_bundle"],
            "blockers": [] if decision != "not_deliverable" else [{"code": "DELIVERY_CORE_OUTPUTS_MISSING", "detail": "missing"}],
            "warnings": [],
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
        {"method": "POST", "path": "/api/refunds", "status": 201, "body": {"refund_id": "r_1"}}
    ]


def _manifest(tmp_path: Path) -> dict:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    return register_source_asset(
        "p7_sales_project",
        "p7-sales-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "qa", "role": "qa"},
    )


def test_p7_sales_handoff_marks_procurement_followup() -> None:
    from ai_test_asset_center.p7_sales_handoff_package import build_p7_sales_handoff_package

    handoff = build_p7_sales_handoff_package(_complete_result("deliverable_for_procurement"))

    assert handoff["schema_version"] == "p7-sales-handoff-package-v1"
    assert handoff["customer_safe"] is True
    assert handoff["handoff_ready"] is True
    assert handoff["procurement_ready"] is True
    assert handoff["sales_stage"] == "procurement_followup"
    assert handoff["customer_success_stage"] == "commercial_expansion"
    assert handoff["recommended_meeting_type"] == "procurement_scope_alignment"
    assert "Customer security/procurement owner" in handoff["required_attendees"]
    assert "evidence_bundle" in handoff["internal_only_keys"]
    assert "raw evidence" in handoff["crm_summary"]


def test_p7_sales_handoff_keeps_not_deliverable_internal() -> None:
    from ai_test_asset_center.p7_sales_handoff_package import build_p7_sales_handoff_package

    handoff = build_p7_sales_handoff_package(_complete_result("not_deliverable"))
    risk_codes = {item["code"] for item in handoff["risk_register"]}

    assert handoff["handoff_ready"] is False
    assert handoff["procurement_ready"] is False
    assert handoff["sales_stage"] == "internal_remediation"
    assert handoff["recommended_meeting_type"] == "internal_remediation_review"
    assert "DELIVERY_CORE_OUTPUTS_MISSING" in risk_codes
    assert "Keep the pilot package internal." in handoff["commercial_next_actions"]


def test_scan_output_contains_p7_sales_handoff_package(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    result = scan(
        "p7_sales_project",
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

    handoff = result["p7_sales_handoff_package"]
    assert handoff["schema_version"] == "p7-sales-handoff-package-v1"
    assert handoff["customer_safe"] is True
    assert handoff["recommended_meeting_type"] in {"procurement_scope_alignment", "executive_value_readout", "internal_remediation_review", "internal_pilot_qualification"}
    assert "evidence_bundle" in handoff["internal_only_keys"]


def test_scan_result_file_contains_p7_sales_handoff_package(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    scan(
        "p7_sales_project",
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
    saved = json.loads((tmp_path / "platform_outputs" / "p7_sales_project" / "scan_result.json").read_text(encoding="utf-8"))

    assert saved["p7_sales_handoff_package"]["schema_version"] == "p7-sales-handoff-package-v1"
    assert saved["p7_sales_handoff_package"]["customer_safe"] is True
    assert "crm_summary" in saved["p7_sales_handoff_package"]
