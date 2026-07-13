from __future__ import annotations

import json

import ai_test_asset_center.private_pilot_service as private_pilot_service
from ai_test_asset_center.display_ready_formatter import format_findings_display_ready, sanitize_customer_evidence_payload
from ai_test_asset_center.private_pilot_server import install_customer_delivery_gate_patch
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler, _normalize_command_center_envelope


def _identity(item_id: str) -> dict[str, str]:
    return {
        "candidate_id": f"candidate-{item_id}",
        "slice_id": f"slice-{item_id}",
        "obligation_id": f"obligation-{item_id}",
        "experiment_id": f"experiment-{item_id}",
        "execution_id": f"execution-{item_id}",
        "evidence_id": f"evidence-{item_id}",
        "finding_id": f"finding-{item_id}",
    }


def _legacy_ready_item() -> dict:
    return {
        "id": "BUG-1",
        "candidate_id": "candidate-BUG-1",
        "slice_id": "slice-BUG-1",
        "obligation_id": "obligation-BUG-1",
        "experiment_id": "experiment-BUG-1",
        "execution_id": "execution-BUG-1",
        "evidence_id": "evidence-BUG-1",
        "finding_id": "finding-BUG-1",
        "title": "支付金额守恒失败",
        "severity": "P1",
        "bug_status": "reproduced",
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "validated_candidate",
        "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "VALIDATED_CANDIDATE",
            "missing_requirements": [],
        },
        "expected": "订单金额应等于支付金额",
        "actual": "订单金额 100，支付金额 1",
        "raw_evidence": {
            "has_real_evidence": True,
            "timestamp": "2026-07-06T12:00:00Z",
            "request_raw": {"method": "POST", "path": "/api/payments"},
            "response_raw": {"status_code": 200, "body": {"paid_amount": 1}},
            "sandbox_write": {
                "cleanup": {"status": "completed", "receipt_ref": "audit://cleanup/BUG-1"}
            },
        },
        "reproduction": {
            "method": "POST",
            "path": "/api/payments",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"paid_amount": 1}},
        },
    }


def _legacy_light_gate_only_item() -> dict:
    item = _legacy_ready_item()
    item["id"] = "LEGACY-LIGHT-GATE"
    item.pop("evidence_status")
    return item


def test_private_pilot_command_center_envelope_splits_legacy_risks_under_strict_gate() -> None:
    install_customer_delivery_gate_patch()
    ready = _legacy_ready_item()
    clue = _legacy_ready_item()
    clue["id"] = "CLUE-1"
    clue["bug_status"] = "risk_clue"

    payload = _normalize_command_center_envelope({"ok": True, "data": {"risks": [ready, clue]}})
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["BUG-1"]
    assert [item["id"] for item in data["clues"]] == ["CLUE-1"]
    assert data["risks"] == data["defects"]
    assert data["value_metrics"]["ready_bug_count"] == 1
    assert data["value_metrics"]["clue_count"] == 1
    assert data["executive_summary"]["ready_bugs"] == 1
    assert data["executive_summary"]["internal_clues"] == 1


def test_private_pilot_command_center_envelope_downgrades_old_light_gate_findings() -> None:
    install_customer_delivery_gate_patch()
    payload = _normalize_command_center_envelope({"ok": True, "data": {"risks": [_legacy_light_gate_only_item()]}})
    data = payload["data"]

    assert data["defects"] == []
    assert [item["id"] for item in data["clues"]] == ["LEGACY-LIGHT-GATE"]
    assert "BUSINESS_EVIDENCE_NOT_VALIDATED" in data["clues"][0]["customer_delivery_gate_reasons"]
    assert data["value_metrics"]["ready_bug_count"] == 0
    assert data["executive_summary"]["ready_bugs"] == 0


def test_private_pilot_command_center_envelope_preserves_existing_tracks_under_strict_gate() -> None:
    install_customer_delivery_gate_patch()
    ready = _legacy_ready_item()
    clue = _legacy_ready_item()
    clue["id"] = "CLUE-1"
    clue["bug_status"] = "risk_clue"

    payload = _normalize_command_center_envelope({"ok": True, "data": {"defects": [ready], "clues": [clue], "risks": [ready, clue]}})
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["BUG-1"]
    assert [item["id"] for item in data["clues"]] == ["CLUE-1"]
    assert data["risks"] == data["defects"]


def test_v12_findings_keep_confirmed_delivery_fields_for_command_center() -> None:
    install_customer_delivery_gate_patch()
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    findings = handler._v12_findings({
        "findings": [
            {
                **_identity("V12-1"),
                "risk_id": "V12-1",
                "title": "[V12 StateOracle] 非法取消",
                "severity": "P0",
                "category": "state_machine",
                "confirmation_status": "confirmed",
                "gate_passed": True,
                "bug_status": "reproduced",
                "execution_status": "executed",
                "customer_delivery_status": "defect",
                "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
                "evidence_status": {
                    "semantic_verdict": "SEMANTIC_CONFIRMED",
                    "business_evidence_status": "VALIDATED",
                    "final_review_status": "VALIDATED_CANDIDATE",
                    "missing_requirements": [],
                },
                "evidence": {
                    "request": "POST /api/orders/{id}/cancel",
                    "response": "HTTP 200",
                    "assertion": "已关闭订单不应允许取消",
                    "timestamp": "2026-07-07T07:00:00Z",
                    "target": "http://127.0.0.1:8080/api/orders/1/cancel",
                    "actor": "readonly",
                    "reproduction_steps": ["GET /api/orders -> HTTP 200", "POST /api/orders/{id}/cancel -> HTTP 200"],
                },
                    "raw_evidence": {
                        "has_real_evidence": True,
                        "timestamp": "2026-07-07T07:00:00Z",
                        "request_raw": {"method": "POST", "path": "/api/orders/{id}/cancel"},
                        "response_raw": {"status_code": 200, "body": {"status": "CANCELLED"}},
                        "sandbox_write": {"cleanup": {"status": "completed", "receipt_ref": "audit://cleanup/V12-1"}},
                },
                "reproduction": {
                    "method": "POST",
                    "path": "/api/orders/{id}/cancel",
                    "is_synthetic": False,
                    "har_evidence": {"status_code": 200, "response_body": {"status": "CANCELLED"}},
                },
            }
        ]
    })

    payload = _normalize_command_center_envelope({"ok": True, "data": {"risks": findings}})
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["V12-1"]
    assert data["clues"] == []
    assert data["value_metrics"]["ready_bug_count"] == 1


def test_v12_findings_prefer_raw_request_identity_when_top_level_method_path_missing() -> None:
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    findings = handler._v12_findings({
        "findings": [
            {
                "risk_id": "V12-RAW-1",
                "title": "[V12 StateOracle] order: CREATED -> /api/orders/{id}/cancel",
                "confirmation_status": "confirmed",
                "gate_passed": True,
                "bug_status": "reproduced",
                "raw_evidence": {
                    "request_raw": {
                        "method": "POST",
                        "path": "/api/orders/ord_123/cancel",
                    },
                    "response_raw": {
                        "status_code": 200,
                        "body": {"status": "CANCELLED"},
                    },
                },
                "reproduction": {
                    "method": "POST",
                    "path": "/api/orders/ord_123/cancel",
                },
                "evidence_status": {
                    "semantic_verdict": "SEMANTIC_CONFIRMED",
                    "business_evidence_status": "VALIDATED",
                    "final_review_status": "VALIDATED_CANDIDATE",
                },
            }
        ]
    })

    assert findings[0]["_api_method"] == "POST"
    assert findings[0]["_api_path"] in {"/api/orders/{id}/cancel", "/api/orders/ord_123/cancel"}


def test_v12_confirmed_semantic_violation_survives_display_ready_formatter_gate() -> None:
    install_customer_delivery_gate_patch()
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    findings = handler._v12_findings({
        "findings": [
            {
                **_identity("V12-SEM-1"),
                "risk_id": "V12-SEM-1",
                "title": "[V12 IdempotencyOracle] 重复支付",
                "severity": "P0",
                "category": "concurrency",
                "confirmation_status": "confirmed",
                "gate_passed": True,
                "bug_status": "reproduced",
                "execution_status": "executed",
                "customer_delivery_status": "defect",
                "expected": "重复POST /api/payments/pay 应返回幂等响应(409/相同结果)",
                "actual": "2次请求均返回成功",
                "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
                "evidence_status": {
                    "semantic_verdict": "SEMANTIC_CONFIRMED",
                    "business_evidence_status": "VALIDATED",
                    "final_review_status": "VALIDATED_CANDIDATE",
                    "missing_requirements": [],
                },
                "evidence": {
                    "request": "POST /api/payments/pay",
                    "response": "HTTP 201",
                    "assertion": "重复支付应被系统阻止或幂等返回",
                    "timestamp": "2026-07-07T07:29:42Z",
                    "target": "http://127.0.0.1:8080/api/payments/pay",
                    "actor": "readonly",
                    "reproduction_steps": [
                        "GET /api/orders -> HTTP 200",
                        "POST /api/payments/pay -> HTTP 201",
                        "POST /api/payments/pay -> HTTP 201",
                    ],
                },
                    "raw_evidence": {
                        "has_real_evidence": True,
                        "timestamp": "2026-07-07T07:29:42Z",
                        "request_raw": {"method": "POST", "path": "/api/payments/pay", "actor": "readonly"},
                        "response_raw": {"status_code": 201, "body": {"status": "SUCCESS"}},
                        "sandbox_write": {"cleanup": {"status": "completed", "receipt_ref": "audit://cleanup/V12-SEM-1"}},
                },
                "reproduction": {
                    "method": "POST",
                    "path": "/api/payments/pay",
                    "is_synthetic": False,
                    "har_evidence": {"status_code": 201, "response_body": {"status": "SUCCESS"}},
                },
            }
        ]
    })

    display_risks, _ = format_findings_display_ready(findings, {}, {"raw_total": 1})
    payload = _normalize_command_center_envelope({
        "ok": True,
        "data": {"risks": sanitize_customer_evidence_payload(display_risks)},
    })
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["V12-SEM-1"]
    assert data["clues"] == []


def test_non_ui_risks_do_not_get_ui_badges_or_ui_stats() -> None:
    install_customer_delivery_gate_patch()
    payload = _normalize_command_center_envelope({"ok": True, "data": {"risks": [_legacy_ready_item()]}})
    data = payload["data"]

    defect = data["defects"][0]
    assert defect["id"] == "BUG-1"
    assert "verification_badge" not in defect
    assert "verification_label" not in defect
    assert data["value_metrics"]["ui_total"] == 0
    assert data["executive_summary"]["ui_candidate_findings"] == 0
    assert data["scan_meta"]["ui_high_confidence_candidates"] == 0


def test_v12_multistep_confirmed_violation_derives_declared_request_from_claim_and_steps() -> None:
    install_customer_delivery_gate_patch()
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    findings = handler._v12_findings({
        "findings": [
            {
                **_identity("V12-MULTI-1"),
                "risk_id": "V12-MULTI-1",
                "title": "[V12 IdempotencyOracle] [来源约束不变量] order: CREATED -> /api/payments/pay",
                "severity": "P0",
                "category": "concurrency",
                "confirmation_status": "confirmed",
                "gate_passed": True,
                "bug_status": "reproduced",
                "execution_status": "executed",
                "customer_delivery_status": "defect",
                "expected": "重复POST /api/payments/pay 应返回幂等响应(409/相同结果)",
                "actual": "2次请求均返回成功",
                "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
                "evidence_status": {
                    "semantic_verdict": "SEMANTIC_CONFIRMED",
                    "business_evidence_status": "VALIDATED",
                    "final_review_status": "VALIDATED_CANDIDATE",
                    "missing_requirements": [],
                },
                "evidence": {
                    "request": "GET /api/orders",
                    "response": "HTTP 200",
                    "assertion": "重复POST /api/payments/pay 应返回幂等响应(409/相同结果)",
                    "timestamp": "2026-07-07T10:10:07Z",
                    "target": "http://127.0.0.1:8080/api/orders",
                    "actor": "readonly",
                    "reproduction_steps": [
                        "GET /api/orders -> HTTP 200",
                        "POST /api/payments/pay -> HTTP 201",
                        "POST /api/payments/pay -> HTTP 201",
                        "GET /api/orders -> HTTP 200",
                    ],
                },
                    "raw_evidence": {
                        "has_real_evidence": True,
                        "timestamp": "2026-07-07T10:10:07Z",
                        "request_raw": {"method": "GET", "path": "/api/orders", "actor": "readonly"},
                        "response_raw": {"status_code": 200, "body": {"status": "PAID"}},
                        "sandbox_write": {"cleanup": {"status": "completed", "receipt_ref": "audit://cleanup/V12-MULTI-1"}},
                },
                "reproduction": {
                    "method": "GET",
                    "path": "/api/orders",
                    "is_synthetic": False,
                    "har_evidence": {"status_code": 200, "response_body": {"status": "PAID"}},
                },
            }
        ]
    })

    assert findings[0]["_api_method"] == "POST"
    assert findings[0]["_api_path"] == "/api/payments/pay"

    display_risks, _ = format_findings_display_ready(findings, {}, {"raw_total": 1})
    payload = _normalize_command_center_envelope({
        "ok": True,
        "data": {"risks": sanitize_customer_evidence_payload(display_risks)},
    })
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["V12-MULTI-1"]
    assert data["clues"] == []


def test_external_validated_candidate_survives_display_and_command_center_gate() -> None:
    install_customer_delivery_gate_patch()
    findings = [
        {
            **_identity("EXT-VAL-1"),
            "risk_id": "EXT-VAL-1",
            "title": "退款写入破坏订单状态约束",
            "severity": "P1",
            "category": "data_integrity",
            "source": "external_signal:schemathesis",
            "confirmation_status": "validated_candidate",
            "bug_status": "reproduced",
            "gate_passed": True,
            "execution_status": "executed",
            "customer_delivery_status": "defect",
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "VALIDATED_CANDIDATE",
            "evidence_quality": {"level": "validated", "score": 90, "can_reproduce": True},
            "evidence_status": {
                "semantic_verdict": "SEMANTIC_CONFIRMED",
                "business_evidence_status": "VALIDATED",
                "final_review_status": "VALIDATED_CANDIDATE",
                "missing_requirements": [],
            },
            "expected": "业务不变量应保持成立",
            "actual": "订单状态从 PAID 变成 CANCELLED",
            "expected_behavior": "业务不变量应保持成立",
            "actual_behavior": "订单状态从 PAID 变成 CANCELLED",
            "method": "POST",
            "path": "/api/refunds",
            "_api_method": "POST",
            "_api_path": "/api/refunds",
            "db_evidence": {
                "before_db_snapshot": {"row_count": 0},
                "after_db_snapshot": {"row_count": 1},
                "db_assertion": "refund rows changed 0->1",
                "business_operation": "POST /api/refunds",
                "table": "refunds",
            },
            "business_invariant_evaluation": {
                "verdict": "failed",
                "reason": "订单状态从 PAID 变成 CANCELLED",
                "results": [
                    {
                        "kind": "business_invariant",
                        "name": "订单状态守恒",
                        "verdict": "failed",
                        "reason": "订单状态从 PAID 变成 CANCELLED",
                        "failed_fields": ["status"],
                    }
                ],
            },
            "failed_fields": ["status"],
            "failed_assertions": [
                {
                    "type": "business_invariant_violation",
                    "rule": "订单状态守恒",
                    "expected": "业务不变量应保持成立",
                    "actual": "订单状态从 PAID 变成 CANCELLED",
                }
            ],
            "runtime_replay": {"status": "executed", "http_status": 500},
                "raw_evidence": {
                    "has_real_evidence": True,
                    "timestamp": "2026-07-07T18:10:00Z",
                    "request_raw": {"method": "POST", "path": "/api/refunds"},
                    "response_raw": {"status_code": 500, "body": {"error": "boom"}},
                    "sandbox_write": {"cleanup": {"status": "completed", "receipt_ref": "audit://cleanup/EXT-VAL-1"}},
            },
            "timestamp": "2026-07-07T18:10:00Z",
            "last_verified_at": "2026-07-07T18:10:00Z",
            "har_evidence": {"method": "POST", "path": "/api/refunds", "status_code": 500, "response_body": {"error": "boom"}},
            "reproduction": {
                "method": "POST",
                "path": "/api/refunds",
                "is_synthetic": False,
                "har_evidence": {"status_code": 500, "response_body": {"error": "boom"}},
            },
            "reproduction_steps": ["POST /api/refunds -> HTTP 500"],
            "evidence": {
                "method": "POST",
                "path": "/api/refunds",
                "assertion": "订单状态守恒失败",
                "target": "POST /api/refunds",
                "reproduction_steps": ["POST /api/refunds -> HTTP 500"],
            },
            "external_evidence_adjudication": {
                "status": "validated_candidate",
                "has_runtime_replay": True,
                "has_db_evidence": True,
                "has_failed_invariant": True,
            },
        }
    ]

    display_risks, _ = format_findings_display_ready(findings, {}, {"raw_total": 1})
    assert display_risks[0]["bug_status"] == "reproduced"
    assert display_risks[0]["gate_passed"] is True

    payload = _normalize_command_center_envelope({
        "ok": True,
        "data": {"risks": sanitize_customer_evidence_payload(display_risks)},
    })
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["EXT-VAL-1"]
    assert data["clues"] == []
    assert data["value_metrics"]["ready_bug_count"] == 1


def test_command_center_envelope_preserves_external_commercial_assets_for_customer_view() -> None:
    install_customer_delivery_gate_patch()
    payload = _normalize_command_center_envelope({
        "ok": True,
        "data": {
            "risks": [_legacy_ready_item()],
            "commercial_assets": {
                "status": "materialized",
                "finding_count": 1,
                "customer_ready_reproduction_count": 1,
                "commercial_handoff_status": "commercial_handoff_ready_with_validated_findings",
                "commercial_handoff_acceptance_status": "ready_for_customer_acceptance",
                "commercial_handoff_safe_for_customer": True,
                "external_tracker_sync_payload_status": "external_tracker_sync_payloads_blocked_or_empty",
                "external_tracker_sync_payload_gate_status": "external_tracker_sync_payload_gate_hold_only",
                "commercial_handoff_bundle_ref": "platform_outputs/demo/defect_discovery/external_commercial_handoff_bundle.json",
                "handoff_archive_manifest_ref": "platform_outputs/demo/defect_discovery/external_handoff_archive_manifest.json",
                "delivery_package": {
                    "status": "created",
                    "package_id": "delivery_demo_bundle",
                    "package_ref": "platform_outputs/demo/delivery_packages/delivery_demo_bundle.zip",
                    "release_verdict": "not_ready",
                    "evidence_bundle_id": "evb_demo",
                },
            },
        },
    })
    data = payload["data"]

    assert data["commercial_assets"]["status"] == "materialized"
    assert data["commercial_assets"]["commercial_handoff"]["status"] == "commercial_handoff_ready_with_validated_findings"
    assert data["commercial_assets"]["tracker_sync"]["payload_status"] == "external_tracker_sync_payloads_blocked_or_empty"
    assert data["commercial_assets"]["delivery_package"]["status"] == "created"
    assert data["commercial_assets"]["artifact_refs"]["delivery_package_ref"].endswith(".zip")
    assert data["scan_meta"]["commercial_handoff_status"] == "commercial_handoff_ready_with_validated_findings"
    assert data["scan_meta"]["delivery_package_status"] == "created"
    assert data["value_metrics"]["commercial_asset_materialized"] == 1
    assert data["value_metrics"]["commercial_delivery_package_created"] == 1
    assert data["executive_summary"]["commercial_handoff_status"] == "commercial_handoff_ready_with_validated_findings"
    assert data["executive_summary"]["delivery_package_status"] == "created"


def test_build_command_center_normalizes_external_commercial_assets_without_scope_errors(monkeypatch, tmp_path) -> None:
    install_customer_delivery_gate_patch()
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.headers = {}

    monkeypatch.setattr(handler, "_load_v12_report", lambda project_id, root: {
        "project_name": project_id,
        "generated_at_utc": "2026-07-07T18:20:00Z",
        "external_commercial_assets": {
            "status": "materialized",
            "finding_count": 2,
            "customer_ready_reproduction_count": 2,
            "commercial_handoff_status": "commercial_handoff_ready_with_validated_findings",
            "commercial_handoff_acceptance_status": "ready",
            "commercial_handoff_safe_for_customer": True,
            "external_tracker_sync_payload_status": "external_tracker_sync_payloads_created",
            "external_tracker_sync_payload_gate_status": "ready",
            "delivery_package": {
                "status": "created",
                "package_id": "PKG-42",
                "package_ref": "qualibug://delivery/pkg-42.zip",
                "release_verdict": "ready",
                "evidence_bundle_id": "BUNDLE-42",
            },
        },
    })
    monkeypatch.setattr(handler, "_load_enterprise_docs", lambda project_id, root: [])
    monkeypatch.setattr(handler, "_load_knowledge_summary", lambda project_id, root: {})
    monkeypatch.setattr(handler, "_auto_discovery_payload", lambda project_id, root, report: {})
    monkeypatch.setattr(handler, "_v12_findings", lambda report, enterprise_docs=None: [])
    monkeypatch.setattr(handler, "_load_db_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_perf_regressions", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_spectrum_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_multi_layer_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_dedupe_risks", lambda risks: risks)
    monkeypatch.setattr(handler, "_scan_counter", lambda project_id, root: {})
    monkeypatch.setattr(private_pilot_service, "_load_real_project_discovery_payload", lambda root, project_id: {})

    payload = handler._build_command_center("enterprise-project", tmp_path)
    data = payload["data"]

    assert data["commercial_assets"]["status"] == "materialized"
    assert data["commercial_assets"]["delivery_package"]["status"] == "created"
    assert data["scan_meta"]["commercial_handoff_status"] == "commercial_handoff_ready_with_validated_findings"
    assert data["value_metrics"]["commercial_asset_materialized"] == 1


def test_command_center_envelope_filters_summary_only_entries() -> None:
    install_customer_delivery_gate_patch()
    payload = _normalize_command_center_envelope({
        "ok": True,
        "data": {
            "defects": [
                {
                    "id": "SUMMARY-1",
                    "title": "[SOURCE_GROUNDED_DISCOVERY] 5 个发现（详情需运行扫描获取）",
                    "_summary_only": True,
                    "bug_status": "suspected",
                    "gate_passed": False,
                },
                _legacy_ready_item(),
            ],
            "clues": [
                {
                    "id": "SUMMARY-2",
                    "title": "[SOURCE_GROUNDED_DISCOVERY] 5 个发现（详情需运行扫描获取）",
                    "_summary_only": True,
                    "bug_status": "suspected",
                    "gate_passed": False,
                }
            ],
            "risks": [_legacy_ready_item()],
        },
    })
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["BUG-1"]
    assert data["clues"] == []


def test_load_v12_report_does_not_restore_history_bundle_into_current_scope(tmp_path) -> None:
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    project = "demo_project"
    scan_dir = tmp_path / "platform_outputs" / project
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "scan_result.json").write_text(
        json.dumps(
            {
                "project_id": project,
                "generated_at_utc": "2026-07-07T18:00:00Z",
                "total_findings": 0,
                "real_findings": [],
                "campaign": {
                    "campaign_id": "CMP_BASE",
                    "source_snapshot_hash": "snap-1",
                    "source_hash": "src-1",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    bundle_dir = tmp_path / "platform_workspace" / project / "evidence_bundles" / "evb_demo"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "manifest.json").write_text(
        json.dumps(
            {
                "bundle_id": "evb_demo",
                "project_id": project,
                "campaign_id": "CMP_RERUN",
                "created_at_utc": "2026-07-07T18:05:00Z",
                "evidence_level": "runtime_captured",
                "source_manifest": {"source_hash": "src-1"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "campaign.json").write_text(
        json.dumps(
            {
                "campaign_id": "CMP_RERUN",
                "lineage_campaign_id": "CMP_BASE",
                "source_snapshot_hash": "snap-1",
                "source_hash": "src-1",
                "confirmed_slice_count": 2,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "findings.json").write_text(
        json.dumps(
            [
                {
                    "risk_id": "BUG-1",
                    "title": "重复支付未幂等",
                    "severity": "P0",
                    "confirmation_status": "confirmed",
                    "gate_passed": True,
                    "bug_status": "reproduced",
                    "evidence": {"method": "POST", "path": "/api/payments/pay"},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = handler._load_v12_report(project, tmp_path)

    assert report["report_source_path"].endswith(
        "platform_outputs/demo_project/scan_result.json"
    )
    assert report["real_findings"] == []
    assert report["total_findings"] == 0


def test_load_v12_report_does_not_union_current_report_with_history_bundle(tmp_path) -> None:
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    project = "demo_project"
    scan_dir = tmp_path / "platform_outputs" / project
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "intelligence_report.json").write_text(
        json.dumps(
            {
                "project_id": project,
                "generated_at_utc": "2026-07-07T18:10:00Z",
                "total_findings": 1,
                "real_findings": [
                    {
                        "risk_id": "BUG-CURRENT",
                        "title": "[V12 IdempotencyOracle] order: CREATED -> /api/payments/pay",
                        "severity": "P0",
                        "confirmation_status": "confirmed",
                        "gate_passed": True,
                        "bug_status": "reproduced",
                        "execution_status": "executed",
                        "evidence": {"method": "POST", "path": "/api/payments/pay"},
                    }
                ],
                "campaign": {
                    "campaign_id": "CMP_RERUN",
                    "lineage_campaign_id": "CMP_BASE",
                    "source_snapshot_hash": "snap-1",
                    "source_hash": "src-1",
                    "scope_id": "scope-a",
                    "environment_ref": "env-a",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    bundle_dir = tmp_path / "platform_workspace" / project / "evidence_bundles" / "evb_demo"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "manifest.json").write_text(
        json.dumps(
            {
                "bundle_id": "evb_demo",
                "project_id": project,
                "campaign_id": "CMP_RERUN",
                "created_at_utc": "2026-07-07T18:05:00Z",
                "evidence_level": "runtime_captured",
                "source_manifest": {"source_hash": "src-1"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "campaign.json").write_text(
        json.dumps(
            {
                "campaign_id": "CMP_RERUN",
                "lineage_campaign_id": "CMP_BASE",
                "source_snapshot_hash": "snap-1",
                "source_hash": "src-1",
                "scope_id": "scope-a",
                "environment_ref": "env-a",
                "confirmed_slice_count": 2,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "findings.json").write_text(
        json.dumps(
            [
                {
                    "risk_id": "BUG-HISTORY",
                    "title": "[V12 StateOracle] order: PAID -> /api/orders/{id}/cancel",
                    "severity": "P0",
                    "confirmation_status": "confirmed",
                    "gate_passed": True,
                    "bug_status": "reproduced",
                    "execution_status": "executed",
                    "evidence": {"method": "POST", "path": "/api/orders/{id}/cancel"},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = handler._load_v12_report(project, tmp_path)

    assert report["report_source_path"].endswith(
        "platform_outputs/demo_project/intelligence_report.json"
    )
    assert len(report["real_findings"]) == 1
    assert report["real_findings"][0]["risk_id"] == "BUG-CURRENT"


def test_formatter_does_not_collapse_distinct_uppercase_status_tokens_as_ids() -> None:
    display_risks, _ = format_findings_display_ready(
        [
            {
                "risk_id": "BUG-PENDING-PAY",
                "title": "[V12 IdempotencyOracle] [来源约束不变量] order: PENDING_PAYMENT -> /api/payments/pay",
                "severity": "P0",
                "confirmation_status": "confirmed",
                "gate_passed": True,
                "bug_status": "reproduced",
                "execution_status": "executed",
                "evidence": {
                    "request": "POST /api/payments/pay",
                    "response": "HTTP 201",
                    "assertion": "待支付订单不应重复支付成功",
                    "timestamp": "2026-07-07T18:00:00Z",
                    "target": "http://127.0.0.1:8080/api/payments/pay",
                    "actor": "readonly",
                    "reproduction_steps": ["POST /api/payments/pay -> HTTP 201"],
                },
                "raw_evidence": {
                    "has_real_evidence": True,
                    "timestamp": "2026-07-07T18:00:00Z",
                    "request_raw": {"method": "POST", "path": "/api/payments/pay", "actor": "readonly"},
                    "response_raw": {"status_code": 201, "body": {"status": "SUCCESS"}},
                },
                "reproduction": {
                    "method": "POST",
                    "path": "/api/payments/pay",
                    "is_synthetic": False,
                    "har_evidence": {"status_code": 201, "response_body": {"status": "SUCCESS"}},
                },
            },
            {
                "risk_id": "BUG-REFUND-PAY",
                "title": "[V12 IdempotencyOracle] [来源约束不变量] order: REFUND_REQUESTED -> /api/payments/pay",
                "severity": "P0",
                "confirmation_status": "confirmed",
                "gate_passed": True,
                "bug_status": "reproduced",
                "execution_status": "executed",
                "evidence": {
                    "request": "POST /api/payments/pay",
                    "response": "HTTP 201",
                    "assertion": "退款申请中的订单不应重复支付成功",
                    "timestamp": "2026-07-07T18:01:00Z",
                    "target": "http://127.0.0.1:8080/api/payments/pay",
                    "actor": "readonly",
                    "reproduction_steps": ["POST /api/payments/pay -> HTTP 201"],
                },
                "raw_evidence": {
                    "has_real_evidence": True,
                    "timestamp": "2026-07-07T18:01:00Z",
                    "request_raw": {"method": "POST", "path": "/api/payments/pay", "actor": "readonly"},
                    "response_raw": {"status_code": 201, "body": {"status": "SUCCESS"}},
                },
                "reproduction": {
                    "method": "POST",
                    "path": "/api/payments/pay",
                    "is_synthetic": False,
                    "har_evidence": {"status_code": 201, "response_body": {"status": "SUCCESS"}},
                },
            },
        ],
        {},
        {"raw_total": 2},
    )

    titles = {item["title"] for item in display_risks}
    assert len(display_risks) == 2
    assert "order: PENDING_PAYMENT -> /api/payments/pay" in titles
    assert "order: REFUND_REQUESTED -> /api/payments/pay" in titles


def test_formatter_preserves_summary_only_marker_for_envelope_filtering() -> None:
    install_customer_delivery_gate_patch()
    display_risks, _ = format_findings_display_ready([
        {
            "risk_id": "layer_source_grounded_discovery",
            "title": "[SOURCE_GROUNDED_DISCOVERY] 5 个发现（详情需运行扫描获取）",
            "severity": "P2",
            "risk_type": "multi_layer_source_grounded_discovery",
            "_summary_only": True,
        },
    ], {}, {"raw_total": 1})
    payload = _normalize_command_center_envelope({
        "ok": True,
        "data": {"risks": sanitize_customer_evidence_payload(display_risks) + [_legacy_ready_item()]},
    })
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["BUG-1"]
    assert data["clues"] == []


def test_private_pilot_command_center_keeps_db_snapshot_only_defect_under_strict_gate() -> None:
    install_customer_delivery_gate_patch()
    ready = _legacy_ready_item()
    ready["id"] = "DB-ONLY-1"
    ready["raw_evidence"]["response_raw"] = {}
    ready["raw_evidence"]["db_snapshot"] = {
        "table": "orders",
        "assertion": "orders row count changed 1->2",
        "before": {"row_count": 1},
        "after": {"row_count": 2},
    }
    ready["reproduction"]["har_evidence"] = {}

    payload = _normalize_command_center_envelope({"ok": True, "data": {"risks": [ready]}})
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["DB-ONLY-1"]
    assert data["clues"] == []
