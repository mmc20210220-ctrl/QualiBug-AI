from __future__ import annotations

import json
from pathlib import Path


OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: P4 Scorecard API
  version: 1.0.0
paths:
  /api/refunds:
    post:
      responses:
        '201': {description: created}
""".strip()


def _scan_result() -> dict:
    return {
        "project": "p4_scorecard_project",
        "execution_status": "completed",
        "runtime_contract": {"status": "approved"},
        "evidence_bundle": {"status": "persisted"},
        "release_gate": {"verdict": "review_required"},
        "p3_seed_bug_benchmark": {
            "schema_version": "p3-seed-bug-benchmark-v1",
            "grade": "passed",
            "total_seed_defects": 3,
            "found_count": 3,
            "missed_count": 0,
            "detection_rate": 1.0,
            "observed_http_calls": 5,
            "findings": [
                {"seed_id": "BUG_P0_REFUND", "title": "Unpaid order can be refunded", "severity": "P0", "kind": "should_reject_but_succeeded", "status": "found"},
                {"seed_id": "BUG_P1_AMOUNT", "title": "Payment amount mismatch", "severity": "P1", "kind": "field_mismatch", "status": "found"},
                {"seed_id": "BUG_P2_DELETED", "title": "Deleted order readable", "severity": "P2", "kind": "should_reject_but_succeeded", "status": "found"},
            ],
            "missed": [],
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
        "p4_scorecard_project",
        "p4-scorecard-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "qa", "role": "qa"},
    )


def test_p4_customer_value_scorecard_summarizes_management_value() -> None:
    from ai_test_asset_center.p4_customer_value_scorecard import build_p4_customer_value_scorecard

    scorecard = build_p4_customer_value_scorecard(_scan_result())

    assert scorecard["schema_version"] == "p4-customer-value-scorecard-v1"
    assert scorecard["customer_safe"] is True
    assert scorecard["value_level"] == "critical_value_proven"
    assert scorecard["board_metrics"]["seed_defects_total"] == 3
    assert scorecard["board_metrics"]["seed_defects_found"] == 3
    assert scorecard["board_metrics"]["p0_found"] == 1
    assert scorecard["board_metrics"]["p1_found"] == 1
    assert scorecard["severity_distribution"] == {"P0": 1, "P1": 1, "P2": 1}
    assert "100.0%" in scorecard["executive_summary_zh"]
    assert "100.0%" in scorecard["executive_summary_en"]
    assert scorecard["execution_context"]["runtime_status"] == "approved"
    assert scorecard["customer_safe_findings"][0]["seed_id"] == "BUG_P0_REFUND"


def test_p4_customer_value_scorecard_handles_no_value_proof() -> None:
    from ai_test_asset_center.p4_customer_value_scorecard import build_p4_customer_value_scorecard

    result = _scan_result()
    result["p3_seed_bug_benchmark"] = {
        "total_seed_defects": 2,
        "found_count": 0,
        "missed_count": 2,
        "detection_rate": 0.0,
        "findings": [],
        "missed": [
            {"seed_id": "BUG_A", "title": "A", "severity": "P0", "status": "missed"},
            {"seed_id": "BUG_B", "title": "B", "severity": "P1", "status": "missed"},
        ],
    }
    scorecard = build_p4_customer_value_scorecard(result)

    assert scorecard["value_level"] == "not_proven"
    assert scorecard["board_metrics"]["seed_defects_missed"] == 2
    assert scorecard["missed_severity_distribution"] == {"P0": 1, "P1": 1}
    assert "Do not present this as a value proof yet." in scorecard["next_actions"]


def test_scan_output_contains_p4_customer_value_scorecard(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    result = scan(
        "p4_scorecard_project",
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

    assert result["p3_seed_bug_benchmark"]["found_count"] == 1
    scorecard = result["p4_customer_value_scorecard"]
    assert scorecard["schema_version"] == "p4-customer-value-scorecard-v1"
    assert scorecard["value_level"] == "critical_value_proven"
    assert scorecard["board_metrics"]["p0_found"] == 1


def test_scan_result_file_contains_p4_customer_value_scorecard(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _manifest(tmp_path)
    scan(
        "p4_scorecard_project",
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
    saved = json.loads((tmp_path / "platform_outputs" / "p4_scorecard_project" / "scan_result.json").read_text(encoding="utf-8"))

    assert saved["p4_customer_value_scorecard"]["value_level"] == "critical_value_proven"
    assert saved["p4_customer_value_scorecard"]["customer_safe"] is True
