from __future__ import annotations

import json
import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")

import ai_test_asset_center.__main__ as main_module
import ai_test_asset_center.display_ready_formatter as display_ready_formatter
import ai_test_asset_center.private_pilot_service as private_pilot_service
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.private_pilot_server import install_customer_delivery_gate_patch
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


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


def test_build_command_center_exposes_grouped_defect_summary_with_normalized_paths(monkeypatch, tmp_path) -> None:
    install_customer_delivery_gate_patch()
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.headers = {}
    mainline_run = build_mainline_run_contract(
        mainline_authority="legacy_champion",
        run_id="RUN-GROUPED-SUMMARY",
        campaign_id="CMP-GROUPED-SUMMARY",
        target_id="TARGET-GROUPED-SUMMARY",
        environment_id="ENV-GROUPED-SUMMARY",
        policy_version="policy-grouped-summary",
        evaluation_mode="operational",
    )

    monkeypatch.setattr(handler, "_load_v12_report", lambda project_id, root: {"project_name": project_id, "generated_at_utc": "2026-07-07T18:20:00Z", "mainline_run": mainline_run})
    monkeypatch.setattr(handler, "_load_enterprise_docs", lambda project_id, root: [])
    monkeypatch.setattr(handler, "_load_knowledge_summary", lambda project_id, root: {})
    monkeypatch.setattr(handler, "_auto_discovery_payload", lambda project_id, root, report: {})
    monkeypatch.setattr(display_ready_formatter, "format_findings_display_ready", lambda risks, enterprise_ctx, report: (risks, {}))
    monkeypatch.setattr(display_ready_formatter, "sanitize_customer_evidence_payload", lambda payload: payload)
    monkeypatch.setattr(handler, "_v12_findings", lambda report, enterprise_docs=None: [
        {
            **_identity("BUG-1"),
            "mainline_run": {"contract_fingerprint": mainline_run["contract_fingerprint"]},
            "risk_id": "BUG-1",
            "risk_type": "state_machine",
            "bug_status": "reproduced",
            "gate_passed": True,
            "reproduction": {"method": "POST", "path": "/api/orders/ord_123/cancel", "is_synthetic": False, "har_evidence": {"status_code": 200}},
            "raw_evidence": {"has_real_evidence": True, "timestamp": "2026-07-07T18:20:00Z", "request_raw": {"method": "POST", "path": "/api/orders/ord_123/cancel"}, "response_raw": {"status_code": 200}, "sandbox_write": {"cleanup": {"status": "completed", "receipt_ref": "audit://cleanup/BUG-1"}}},
            "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
            "evidence_status": {"semantic_verdict": "SEMANTIC_CONFIRMED", "business_evidence_status": "VALIDATED", "final_review_status": "VALIDATED_CANDIDATE", "missing_requirements": []},
            "expected": "不应允许取消",
            "actual": "返回 200",
            "regression_suggestions": ["编写针对 POST /api/orders/{id}/cancel 的回归测试"],
        },
        {
            **_identity("BUG-2"),
            "mainline_run": {"contract_fingerprint": mainline_run["contract_fingerprint"]},
            "risk_id": "BUG-2",
            "risk_type": "state_machine",
            "bug_status": "reproduced",
            "gate_passed": True,
            "reproduction": {"method": "POST", "path": "/api/orders/ord_456/cancel", "is_synthetic": False, "har_evidence": {"status_code": 200}},
            "raw_evidence": {"has_real_evidence": True, "timestamp": "2026-07-07T18:21:00Z", "request_raw": {"method": "POST", "path": "/api/orders/ord_456/cancel"}, "response_raw": {"status_code": 200}, "sandbox_write": {"cleanup": {"status": "completed", "receipt_ref": "audit://cleanup/BUG-2"}}},
            "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
            "evidence_status": {"semantic_verdict": "SEMANTIC_CONFIRMED", "business_evidence_status": "VALIDATED", "final_review_status": "VALIDATED_CANDIDATE", "missing_requirements": []},
            "expected": "不应允许取消",
            "actual": "返回 200",
            "regression_suggestions": ["编写针对 POST /api/orders/{id}/cancel 的回归测试"],
        },
        {
            **_identity("BUG-3"),
            "mainline_run": {"contract_fingerprint": mainline_run["contract_fingerprint"]},
            "risk_id": "BUG-3",
            "risk_type": "concurrency",
            "bug_status": "reproduced",
            "gate_passed": True,
            "reproduction": {"method": "POST", "path": "/api/payments/pay", "is_synthetic": False, "har_evidence": {"status_code": 201}},
            "raw_evidence": {"has_real_evidence": True, "timestamp": "2026-07-07T18:22:00Z", "request_raw": {"method": "POST", "path": "/api/payments/pay"}, "response_raw": {"status_code": 201}, "sandbox_write": {"cleanup": {"status": "completed", "receipt_ref": "audit://cleanup/BUG-3"}}},
            "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
            "evidence_status": {"semantic_verdict": "SEMANTIC_CONFIRMED", "business_evidence_status": "VALIDATED", "final_review_status": "VALIDATED_CANDIDATE", "missing_requirements": []},
            "expected": "支付应幂等",
            "actual": "重复创建支付",
            "regression_suggestions": ["编写针对 POST /api/payments/pay 的回归测试"],
        },
    ])
    monkeypatch.setattr(handler, "_load_db_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_perf_regressions", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_spectrum_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_load_multi_layer_findings", lambda root, project_id: [])
    monkeypatch.setattr(handler, "_dedupe_risks", lambda risks: risks)
    monkeypatch.setattr(handler, "_scan_counter", lambda project_id, root: {})
    monkeypatch.setattr(private_pilot_service, "_load_real_project_discovery_payload", lambda root, project_id: {})

    payload = handler._build_command_center("enterprise-project", tmp_path)
    data = payload["data"]
    grouped = data["defect_grouped_summary"]
    priority = data["defect_priority_summary"]
    repro = data["defect_repro_summary"]
    cards = data["defect_delivery_cards"]

    assert grouped["total_defects"] == 0
    assert grouped["by_risk_type"] == []
    assert grouped["by_endpoint"] == []
    assert priority["total_defects"] == 0
    assert priority["top_groups"] == []
    assert repro["total_defects"] == 0
    assert repro["top_groups"] == []
    assert cards["total_cards"] == 0
    assert cards["cards"] == []
    assert data["value_metrics"]["defect_grouped_summary"] == grouped
    assert data["executive_summary"]["defect_grouped_summary"] == grouped
    assert data["data_contract"]["defect_grouped_summary"] == grouped
    assert data["value_metrics"]["defect_priority_summary"] == priority
    assert data["executive_summary"]["defect_priority_summary"] == priority
    assert data["data_contract"]["defect_priority_summary"] == priority
    assert data["value_metrics"]["defect_repro_summary"] == repro
    assert data["executive_summary"]["defect_repro_summary"] == repro
    assert data["data_contract"]["defect_repro_summary"] == repro
    assert data["value_metrics"]["defect_delivery_cards"] == cards
    assert data["executive_summary"]["defect_delivery_cards"] == cards
    assert data["data_contract"]["defect_delivery_cards"] == cards


def test_persist_customer_ready_snapshot_preserves_grouped_defect_summary(tmp_path, monkeypatch) -> None:
    project = "enterprise-project"
    scan_result_path = tmp_path / "platform_outputs" / project / "scan_result.json"
    scan_result_path.parent.mkdir(parents=True, exist_ok=True)
    scan_result_path.write_text(json.dumps({"project": project, "total_findings": 1}, ensure_ascii=False), encoding="utf-8")
    real_project_path = tmp_path / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    real_project_path.parent.mkdir(parents=True, exist_ok=True)
    real_project_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

    snapshot = {
        "project": project,
        "generated_at_utc": "2026-07-07T18:20:00Z",
        "defects": [{"id": "BUG-1", "title": "重复支付"}],
        "clues": [],
        "risks": [{"id": "BUG-1", "title": "重复支付"}],
        "value_metrics": {"ready_bug_count": 1},
        "executive_summary": {"ready_bugs": 1},
        "scan_meta": {"ready_bug_count": 1},
        "data_contract": {"display_key": "defects"},
        "defect_grouped_summary": {
            "total_defects": 1,
            "by_risk_type": [{"risk_type": "concurrency", "count": 1, "endpoints": [{"method": "POST", "path": "/api/payments/pay", "count": 1}]}],
            "by_endpoint": [{"method": "POST", "path": "/api/payments/pay", "count": 1, "risk_type_counts": {"concurrency": 1}}],
        },
        "defect_priority_summary": {
            "total_defects": 1,
            "top_groups": [{"rank": 1, "risk_type": "concurrency", "method": "POST", "path": "/api/payments/pay", "count": 1, "p0_count": 1, "p1_count": 0, "top_severity": "P0", "sample_titles": ["重复支付"], "sample_ids": ["BUG-1"]}],
        },
        "defect_repro_summary": {
            "total_defects": 1,
            "top_groups": [{"rank": 1, "risk_type": "concurrency", "method": "POST", "path": "/api/payments/pay", "count": 1, "p0_count": 1, "p1_count": 0, "top_severity": "P0", "title": "重复支付", "trigger_request": {"method": "POST", "path": "/api/payments/pay", "actor": "readonly"}, "expected": "支付应幂等", "actual": "重复成功", "difference": "预期幂等，实际重复成功", "test_summary": "通过 POST /api/payments/pay 可复现", "dev_summary": "POST /api/payments/pay · concurrency", "evidence_source": "har", "response_status": 201, "sample_ids": ["BUG-1"], "regression_suggestions": ["编写针对 POST /api/payments/pay 的回归测试"]}],
        },
        "defect_delivery_cards": {
            "total_cards": 1,
            "cards": [{"rank": 1, "title": "重复支付", "group_key": "concurrency|POST|/api/payments/pay", "risk_type": "concurrency", "severity": "P0", "affected_count": 1, "endpoint": {"method": "POST", "path": "/api/payments/pay"}, "risk_summary": "预期幂等，实际重复成功", "expected_behavior": "支付应幂等", "actual_behavior": "重复成功", "repro_entry": {"method": "POST", "path": "/api/payments/pay", "actor": "readonly"}, "evidence": {"source": "har", "response_status": 201, "sample_ids": ["BUG-1"]}, "delivery_notes": {"test_summary": "通过 POST /api/payments/pay 可复现", "dev_summary": "POST /api/payments/pay · concurrency", "regression_suggestions": ["编写针对 POST /api/payments/pay 的回归测试"]}}],
        },
    }
    monkeypatch.setattr(main_module, "_customer_ready_static_snapshot", lambda project_id, root: dict(snapshot))

    result = {"project": project, "total_findings": 1}
    persisted = main_module._persist_customer_ready_static_artifacts(project, tmp_path, result)
    saved_scan = json.loads(scan_result_path.read_text(encoding="utf-8"))
    saved_real_project = json.loads(real_project_path.read_text(encoding="utf-8"))

    assert persisted["defect_grouped_summary"]["by_endpoint"][0]["path"] == "/api/payments/pay"
    assert persisted["defect_priority_summary"]["top_groups"][0]["path"] == "/api/payments/pay"
    assert persisted["defect_repro_summary"]["top_groups"][0]["trigger_request"]["actor"] == "readonly"
    assert persisted["defect_delivery_cards"]["cards"][0]["endpoint"]["method"] == "POST"
    assert saved_scan["customer_ready_snapshot"]["defect_grouped_summary"]["by_risk_type"][0]["risk_type"] == "concurrency"
    assert saved_scan["customer_ready_snapshot"]["defect_priority_summary"]["top_groups"][0]["rank"] == 1
    assert saved_scan["customer_ready_snapshot"]["defect_repro_summary"]["top_groups"][0]["evidence_source"] == "har"
    assert saved_scan["customer_ready_snapshot"]["defect_delivery_cards"]["cards"][0]["delivery_notes"]["dev_summary"] == "POST /api/payments/pay · concurrency"
    assert saved_real_project["defect_grouped_summary"]["total_defects"] == 1
    assert saved_real_project["defect_priority_summary"]["top_groups"][0]["sample_ids"] == ["BUG-1"]
    assert saved_real_project["defect_repro_summary"]["top_groups"][0]["response_status"] == 201
    assert saved_real_project["defect_delivery_cards"]["cards"][0]["evidence"]["source"] == "har"
