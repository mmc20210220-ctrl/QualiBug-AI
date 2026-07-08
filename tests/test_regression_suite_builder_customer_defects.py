from __future__ import annotations

import json

from ai_test_asset_center.regression_suite_builder import build_regression_suite


def test_regression_suite_builder_falls_back_to_customer_ready_defects(tmp_path) -> None:
    project = "enterprise-project"
    output_dir = tmp_path / "platform_outputs" / project / "real_project"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "real_project_defect_data.json").write_text(
        json.dumps(
            {
                "current_campaign_scope": {
                    "campaign_id": "CMP_REGRESSION",
                    "lineage_campaign_id": "CMP_LINEAGE",
                    "scope_id": "checkout-scope",
                    "environment_ref": "local-benchmark",
                    "source_hash": "a" * 64,
                    "source_snapshot_hash": "b" * 64,
                },
                "defects": [
                    {
                        "id": "DEF-001",
                        "title": "coupon disabled still valid",
                        "customer_delivery_status": "defect",
                        "bug_status": "reproduced",
                        "gate_passed": True,
                        "is_reproducible": True,
                        "risk_type": "invariant",
                        "severity": "P0",
                        "reproduction": {
                            "method": "POST",
                            "path": "/api/coupons/validate",
                            "steps": [
                                "curl -X POST \"http://localhost:8080/api/coupons/validate\" -H \"Content-Type: application/json\" -d '{\"code\":\"NEW100\",\"totalAmount\":99.00}' -v"
                            ],
                            "har_evidence": {"actor": "buyer"},
                        },
                        "expected_actual_comparison": {"expected": "非 ACTIVE 优惠券应返回 valid=false"},
                        "evidence_quality": {"level": "validated", "score": 90},
                        "evidence_status": {"final_review_status": "VALIDATED_CANDIDATE"},
                    },
                    {
                        "id": "CLUE-001",
                        "title": "not reproducible clue",
                        "customer_delivery_status": "clue",
                        "bug_status": "suspected",
                        "gate_passed": False,
                        "is_reproducible": False,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_regression_suite(project_id=project, root=tmp_path)

    release_items = result["modes"]["release"]["items"]

    assert result["summary"]["total_probe_count"] == 1
    assert release_items[0]["source"] == "customer_ready_defect_data"
    assert release_items[0]["issue_id"] == "DEF-001"
    assert release_items[0]["path"] == "/api/coupons/validate"
    assert release_items[0]["actor"] == "buyer"
    assert release_items[0]["request_body"] == {"code": "NEW100", "totalAmount": 99.0}
    assert release_items[0]["current_campaign_scope"]["campaign_id"] == "CMP_REGRESSION"
    assert release_items[0]["current_campaign_scope"]["scope_id"] == "checkout-scope"
    assert result["summary"]["current_campaign_scope"]["environment_ref"] == "local-benchmark"
    assert result["ci_gate"]["current_campaign_scope"]["source_hash"] == "a" * 64
    assert result["current_campaign_scope"]["source_snapshot_hash"] == "b" * 64


def test_regression_suite_builder_prefers_customer_ready_family_shelf_namespace(tmp_path) -> None:
    project = "enterprise-project"
    output_dir = tmp_path / "platform_outputs" / project / "real_project"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "real_project_defect_data.json").write_text(
        json.dumps(
            {
                "metrics": {"validated_bug_count": 3},
                "summary": {"validated_bug_count": 3},
                "defects": [
                    {
                        "id": "DISC-001",
                        "title": "raw discovery artifact should not drive regression",
                        "customer_delivery_status": "clue",
                        "bug_status": "suspected",
                        "gate_passed": False,
                        "is_reproducible": False,
                    }
                ],
                "customer_ready_family_shelf": {
                    "current_campaign_scope": {
                        "campaign_id": "CMP_REGRESSION",
                        "lineage_campaign_id": "CMP_LINEAGE",
                        "scope_id": "checkout-scope",
                        "environment_ref": "local-benchmark",
                        "source_hash": "a" * 64,
                        "source_snapshot_hash": "b" * 64,
                    },
                    "defects": [
                        {
                            "id": "DEF-READY-001",
                            "title": "coupon disabled still valid",
                            "customer_delivery_status": "defect",
                            "bug_status": "reproduced",
                            "gate_passed": True,
                            "is_reproducible": True,
                            "risk_type": "invariant",
                            "severity": "P0",
                            "reproduction": {
                                "method": "POST",
                                "path": "/api/coupons/validate",
                                "steps": [
                                    "curl -X POST \"http://localhost:8080/api/coupons/validate\" -H \"Content-Type: application/json\" -d '{\"code\":\"NEW100\",\"totalAmount\":99.00}' -v"
                                ],
                                "har_evidence": {"actor": "buyer"},
                            },
                            "expected_actual_comparison": {"expected": "非 ACTIVE 优惠券应返回 valid=false"},
                            "evidence_quality": {"level": "validated", "score": 90},
                            "evidence_status": {"final_review_status": "VALIDATED_CANDIDATE"},
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_regression_suite(project_id=project, root=tmp_path)

    release_items = result["modes"]["release"]["items"]

    assert result["summary"]["total_probe_count"] == 1
    assert release_items[0]["issue_id"] == "DEF-READY-001"
    assert release_items[0]["path"] == "/api/coupons/validate"
    assert release_items[0]["current_campaign_scope"]["campaign_id"] == "CMP_REGRESSION"
